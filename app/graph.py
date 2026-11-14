"""Graphe LangGraph : le protocole de séance du COMEX.

    cadrage ─▶ validation humaine ─┬─▶ recadrer ─▶ cadrage
                                   └─▶ 6 avis experts ‖ contre-analyse   (en parallèle, à l'aveugle)
                                         ─▶ objections du contradicteur (au moins une par expert,
                                            relance pour les experts oubliés)
                                         ─▶ réponses des experts         (en parallèle ; chacun voit
                                            désormais les avis des autres et y réagit)
                                         ─▶ réplique du contradicteur    (statut de chaque objection)
                                         ─▶ synthèse du président ─▶ fin (arbitrage humain dans l'interface)

Principes : chaque contribution est attribuée et jamais réécrite ; le contexte de chaque appel
est construit explicitement (un expert ne voit pas l'avis des autres au premier tour). Les
contributions manquantes sont listées (lacunes) : une séance lacunaire n'est pas présentée comme complète.
"""
import operator
import re
import time
from typing import Annotated, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from . import agents as A
from .dossier import REF, refs_citees
from .llm import Modeles
from .runtime import seance_de

MODELES: Modeles | None = None      # initialisé par le serveur au démarrage
# En dessous de cette part des tokens estimés, le serveur a tronqué le message sans le dire
# (ex. Ollama et son contexte par défaut de 4 096 tokens). Mesuré : 1,0 à 1,1 sain, 0,13 à 0,35 tronqué.
SEUIL_TRONCATURE = 0.7

LIBELLES = {
    "cadrage": "Cadrage de la question",
    "avis": "Analyse indépendante",
    "contre_analyse": "Contre-analyse indépendante",
    "objections": "Formulation des objections",
    "reponse": "Réponse aux objections",
    "replique": "Réplique aux réponses",
    "synthese": "Synthèse pour le décideur",
}


# --------------------------------------------------------------------------- état
def fusion_contributions(existant: dict | None, nouveau: dict | None) -> dict:
    """Reducer des écritures parallèles : la première version d'une contribution est conservée.

    Une révision volontaire porte un nouvel identifiant (ex. finance:reponse), elle n'écrase rien.
    """
    resultat = dict(existant or {})
    for cle, valeur in (nouveau or {}).items():
        resultat.setdefault(cle, valeur)
    return resultat


class Etat(TypedDict, total=False):
    cadrage: dict
    complement: str
    action_validation: str
    avis: Annotated[dict, fusion_contributions]
    contre_analyse: dict
    objections: list
    reponses: Annotated[dict, fusion_contributions]
    replique: dict
    synthese: dict
    lacunes: list
    tronques: Annotated[list, operator.add]
    erreurs: Annotated[list, operator.add]


class Tache(TypedDict, total=False):
    """Contexte transmis à un agent lancé en parallèle (via Send) : uniquement ce qu'il a le droit de voir."""
    agent_id: str
    cadrage: dict
    complement: str
    avis_propre: dict
    autres_avis: dict
    objections: list


_CHAINE = re.compile(r'"((?:[^"\\]|\\.)*)("?)(\s*:)?')


def lisible(json_partiel: str) -> str:
    """Valeurs textuelles d'un JSON en cours d'écriture (sans les clés ni les énumérations)."""
    if "</think>" in json_partiel:
        json_partiel = json_partiel.split("</think>", 1)[1]
    valeurs = [m.group(1).replace('\\"', '"') for m in _CHAINE.finditer(json_partiel)
               if not m.group(3) and len(m.group(1)) > 2 and not re.fullmatch(r"[a-z_]+", m.group(1))]
    return " · ".join(valeurs)


def sans_meta(contribution: dict | None) -> dict:
    return {k: v for k, v in (contribution or {}).items() if k != "_meta"}


# --------------------------------------------------------------------------- appel d'un agent
async def appeler(config: RunnableConfig, agent_id: str, etape: str, consigne: str):
    seance = seance_de(config)
    messages = [
        {"role": "system", "content": A.prefixe_commun(seance.dossier.texte)},   # préfixe commun (cache vLLM)
        {"role": "user", "content": consigne},                                    # partie propre au rôle
    ]
    await seance.publier("agent_statut", agent=agent_id, statut="analyse", etape=etape, libelle=LIBELLES[etape])

    tampons = {"reflexion": "", "redaction": ""}
    etat_flux = {"nature": None, "dernier": 0.0}

    async def on_flux(nature: str, texte: str):
        tampons[nature] += texte
        if nature != etat_flux["nature"]:
            etat_flux["nature"] = nature
            await seance.publier("agent_statut", agent=agent_id, statut=nature, etape=etape, libelle=LIBELLES[etape])
        if time.time() - etat_flux["dernier"] > 0.35:       # limite le débit d'événements vers l'interface
            etat_flux["dernier"] = time.time()
            texte_affiche = lisible(tampons[nature]) if nature == "redaction" else tampons[nature]
            await seance.publier("agent_flux", agent=agent_id, nature=nature,
                                 extrait=texte_affiche[-240:], caracteres=len(tampons[nature]))

    try:
        resultat, meta = await MODELES.generer(agent_id, etape, messages, on_flux)
    except Exception as e:  # noqa: BLE001
        await seance.publier("agent_statut", agent=agent_id, statut="erreur", etape=etape, message=str(e)[:300])
        return None, f"{agent_id}/{etape} : {e}"

    # Vérification des sources : chaque référence citée doit exister dans le dossier
    citees = refs_citees(resultat)
    meta["sources"] = sorted(citees & seance.dossier.refs)
    meta["sources_invalides"] = sorted(citees - seance.dossier.refs)
    lus, estimes = meta.get("tokens_entree"), meta.get("tokens_estimes")
    if not meta.get("simulation") and lus and estimes and lus < SEUIL_TRONCATURE * estimes:
        meta["tronque"] = True
    await seance.publier("contribution", agent=agent_id, etape=etape, contenu=resultat, meta=meta)
    await seance.publier("agent_statut", agent=agent_id, statut="termine", etape=etape,
                         libelle=LIBELLES[etape], duree=meta["duree"])
    return {**resultat, "_meta": meta}, None


def _erreur(message: str | None) -> dict:
    return {"erreurs": [message]} if message else {}


def _suivi(res: dict | None, err: str | None, agent_id: str, etape: str) -> dict:
    """Erreur éventuelle et troncature détectée d'un appel, à fusionner dans l'état."""
    suivi = _erreur(err)
    meta = (res or {}).get("_meta", {})
    if meta.get("tronque"):
        suivi["tronques"] = [{"agent": agent_id, "etape": etape, "lus": meta["tokens_entree"],
                              "estimes": meta["tokens_estimes"]}]
    return suivi


# --------------------------------------------------------------------------- nœuds
async def cadrage(state: Etat, config: RunnableConfig):
    await seance_de(config).publier("phase", phase="cadrage")
    res, err = await appeler(config, "president", "cadrage", A.prompt_cadrage(state.get("complement", "")))
    suivi = _suivi(res, err, "president", "cadrage")
    return {"cadrage": res, **suivi} if res else suivi


async def validation(state: Etat):
    # Pause : la séance attend la décision humaine (reprise via Command(resume=...))
    reponse = interrupt({"cadrage": sans_meta(state.get("cadrage"))})
    return {"action_validation": reponse.get("action", "valider"), "complement": reponse.get("complement", "")}


async def expert(tache: Tache, config: RunnableConfig):
    agent_id = tache["agent_id"]
    res, err = await appeler(config, agent_id, "avis",
                             A.prompt_avis(agent_id, tache["cadrage"], tache.get("complement", "")))
    suivi = _suivi(res, err, agent_id, "avis")
    return {"avis": {f"{agent_id}:initial": res}, **suivi} if res else suivi


async def contre_analyse(tache: Tache, config: RunnableConfig):
    res, err = await appeler(config, "contradicteur", "contre_analyse", A.prompt_contre_analyse(tache["cadrage"]))
    suivi = _suivi(res, err, "contradicteur", "contre_analyse")
    return {"contre_analyse": res, **suivi} if res else suivi


def _avis_par_expert(state: Etat) -> dict:
    return {cle.split(":")[0]: sans_meta(v) for cle, v in state.get("avis", {}).items()}


def _recevables(brutes: list, avis: dict, deja: list) -> tuple[list, list]:
    """Objections adressées à un expert qui a rendu un avis ; identifiants rendus uniques.

    Renvoie (objections retenues, messages d'incident pour les objections écartées).
    """
    retenues, incidents = [], []
    ids = {o["id"] for o in deja}
    for o in brutes:
        cible = str(o.get("cible", "")).strip().lower()
        if cible not in avis:
            incidents.append(f"contradicteur/objections : objection {o.get('id', '?')} écartée, cible inconnue "
                             f"ou sans avis ({o.get('cible')!r})")
            continue
        o = {**o, "cible": cible}
        if not o.get("id") or o["id"] in ids:
            o["id"] = f"O{len(deja) + len(retenues) + 1}"
            while o["id"] in ids:
                o["id"] += "b"
        ids.add(o["id"])
        retenues.append(o)
    return retenues, incidents


async def challenge(state: Etat, config: RunnableConfig):
    await seance_de(config).publier("phase", phase="contradiction")
    avis = _avis_par_expert(state)
    cadrage_, contre = sans_meta(state["cadrage"]), sans_meta(state.get("contre_analyse"))
    erreurs: list = []
    res, err = await appeler(config, "contradicteur", "objections", A.prompt_objections(cadrage_, contre, avis))
    suivi = _suivi(res, err, "contradicteur", "objections")
    erreurs += suivi.get("erreurs", [])
    tronques = suivi.get("tronques", [])
    objections, incidents = _recevables((res or {}).get("objections", []), avis, [])
    erreurs += incidents

    # Couverture : chaque expert ayant rendu un avis doit être challengé ; relance ciblée sinon.
    manquants = {e: v for e, v in avis.items() if e not in {o["cible"] for o in objections}}
    if manquants and res:
        res2, err2 = await appeler(config, "contradicteur", "objections",
                                   A.prompt_objections_complement(cadrage_, contre, manquants, objections,
                                                                  len(objections) + 1))
        suivi = _suivi(res2, err2, "contradicteur", "objections")
        erreurs += suivi.get("erreurs", [])
        tronques += suivi.get("tronques", [])
        complement, incidents = _recevables((res2 or {}).get("objections", []), manquants, objections)
        objections += complement
        erreurs += incidents
    return {"objections": objections, "erreurs": erreurs, "tronques": tronques}


async def reponse_expert(tache: Tache, config: RunnableConfig):
    agent_id = tache["agent_id"]
    res, err = await appeler(config, agent_id, "reponse",
                             A.prompt_reponse(agent_id, tache["avis_propre"], tache["objections"],
                                              tache.get("autres_avis", {})))
    suivi = _suivi(res, err, agent_id, "reponse")
    return {"reponses": {f"{agent_id}:reponse": res}, **suivi} if res else suivi


async def replique(state: Etat, config: RunnableConfig):
    await seance_de(config).publier("phase", phase="replique")
    reponses = {cle.split(":")[0]: sans_meta(v) for cle, v in state.get("reponses", {}).items()}
    res, err = await appeler(config, "contradicteur", "replique",
                             A.prompt_replique(state.get("objections", []), reponses))
    suivi = _suivi(res, err, "contradicteur", "replique")
    return {"replique": res, **suivi} if res else suivi


def lacunes_du_debat(state: Etat) -> list[str]:
    """Ce qui manque pour que le débat soit complet : chaque absence est nommée."""
    lacunes = []
    avis = _avis_par_expert(state)
    for e in A.EXPERTS:
        if e not in avis:
            lacunes.append(f"{A.AGENTS[e]['nom']} n'a pas rendu d'avis : son mandat n'est pas couvert.")
    if not state.get("contre_analyse"):
        lacunes.append("La contre-analyse indépendante du contradicteur est absente.")
    objections = state.get("objections", [])
    if avis and not objections:
        lacunes.append("Aucune objection n'a été formulée : le débat contradictoire n'a pas eu lieu.")
    elif objections:
        cibles = {o["cible"] for o in objections}
        for e in avis:
            if e not in cibles:
                lacunes.append(f"{A.AGENTS[e]['nom']} n'a reçu aucune objection.")
        repondants = {cle.split(":")[0] for cle in state.get("reponses", {})}
        for e in sorted(cibles - repondants):
            lacunes.append(f"{A.AGENTS[e]['nom']} n'a pas répondu aux objections qui lui étaient adressées.")
        # Une réponse doit traiter les objections reçues, désignées par leur identifiant (O4...)
        for cle, contribution in state.get("reponses", {}).items():
            e = cle.split(":")[0]
            attendus = [o["id"] for o in objections if o["cible"] == e]
            bruts = [str(r.get("objection_id") or "") for r in (contribution or {}).get("reponses", [])]
            cites = {_norm_id(c) for c in bruts}
            oublies = [i for i in attendus if _norm_id(i) not in cites]
            if oublies:
                autres = sorted({c for c in bruts if c and _norm_id(c) not in {_norm_id(i) for i in attendus}})
                lacunes.append(f"{A.AGENTS[e]['nom']} n'a pas répondu à {', '.join(oublies)}"
                               + (f" (identifiants cités à la place : {', '.join(autres)})." if autres else "."))
        if not state.get("replique"):
            lacunes.append("La réplique du contradicteur est absente : le statut des objections n'est pas évalué.")
    lacunes += [_lacune_troncature(t) for t in state.get("tronques", [])]
    return lacunes


def _norm_id(valeur) -> str:
    return str(valeur or "").strip().upper()


def _norm(texte) -> str:
    return " ".join(str(texte or "").lower().split())


def _lacune_troncature(t: dict) -> str:
    return (f"Message tronqué : {A.AGENTS[t['agent']]['nom']} ({LIBELLES[t['etape']].lower()}) n'a lu que "
            f"{t['lus']} tokens sur ~{t['estimes']}. Sa contribution repose sur un contexte incomplet : "
            "augmentez le contexte du modèle (num_ctx d'Ollama).")


def ecarts_synthese(synthese: dict, cadrage: dict, refs: set[str]) -> list[str]:
    """Ce que la synthèse omet ou invente par rapport au cadrage validé et au dossier."""
    ecarts = []
    par_id = {_norm_id(o.get("id")): o for o in synthese.get("options", [])}
    criteres = cadrage.get("criteres", [])
    for option in cadrage.get("options", []):
        evaluee = par_id.get(_norm_id(option["id"]))
        if not evaluee:
            ecarts.append(f"La synthèse n'évalue pas l'option {option['id']} ({option['nom']}).")
            continue
        couverts = {_norm(c.get("critere")) for c in evaluee.get("criteres", [])}
        oublies = [c for c in criteres if _norm(c) not in couverts]
        if oublies:
            ecarts.append(f"La synthèse n'évalue pas l'option {option['id']} sur : {' ; '.join(oublies)}.")
    sources = [s for o in synthese.get("options", [])
               for bloc in (o.get("criteres", []), o.get("preuves", [])) for x in bloc for s in x.get("sources", [])]
    hors_dossier = sorted({str(s) for s in sources
                           if not ((m := REF.fullmatch(str(s).strip())) and m.group(1) in refs)})
    if hors_dossier:
        ecarts.append(f"La synthèse cite {len(hors_dossier)} source(s) qui ne sont pas des sections du dossier : "
                      f"{' ; '.join(hors_dossier[:5])}.")
    return ecarts


async def synthese(state: Etat, config: RunnableConfig):
    await seance_de(config).publier("phase", phase="synthese")
    avis = _avis_par_expert(state)
    reponses = {cle.split(":")[0]: sans_meta(v) for cle, v in state.get("reponses", {}).items()}
    lacunes = lacunes_du_debat(state)
    res, err = await appeler(config, "president", "synthese",
                             A.prompt_synthese(sans_meta(state["cadrage"]), avis, sans_meta(state.get("contre_analyse")),
                                               state.get("objections", []), reponses,
                                               sans_meta(state.get("replique")), lacunes))
    suivi = _suivi(res, err, "president", "synthese")
    if not res:
        return {"lacunes": lacunes, **suivi}
    # Contrôles a posteriori : la synthèse elle-même peut être tronquée ou incomplète
    lacunes = lacunes + [_lacune_troncature(t) for t in suivi.get("tronques", [])] + \
        ecarts_synthese(sans_meta(res), sans_meta(state["cadrage"]), seance_de(config).dossier.refs)
    return {"synthese": res, "lacunes": lacunes, **suivi}


# --------------------------------------------------------------------------- routage
def apres_cadrage(state: Etat):
    return "validation" if state.get("cadrage") else END


def apres_validation(state: Etat):
    if state.get("action_validation") == "recadrer":
        return "cadrage"
    base = {"cadrage": sans_meta(state["cadrage"]), "complement": state.get("complement", "")}
    return [Send("expert", {**base, "agent_id": e}) for e in A.EXPERTS] + [Send("contre_analyse", base)]


def _resume_avis(avis: dict) -> dict:
    return {k: avis.get(k) for k in ("position", "option_preferee", "resume", "arguments")}


def apres_challenge(state: Etat):
    avis = _avis_par_expert(state)
    par_cible: dict[str, list] = {}
    for o in state.get("objections", []):
        par_cible.setdefault(o["cible"], []).append(o)
    envois = [Send("reponse_expert", {
        "agent_id": cible, "objections": objs, "avis_propre": avis[cible],
        "autres_avis": {e: _resume_avis(v) for e, v in avis.items() if e != cible},
    }) for cible, objs in par_cible.items()]
    return envois or "synthese"


def construire_graphe():
    g = StateGraph(Etat)
    g.add_node("cadrage", cadrage)
    g.add_node("validation", validation)
    g.add_node("expert", expert)
    g.add_node("contre_analyse", contre_analyse)
    g.add_node("challenge", challenge)
    g.add_node("reponse_expert", reponse_expert)
    g.add_node("replique", replique)
    g.add_node("synthese", synthese)

    g.add_edge(START, "cadrage")
    g.add_conditional_edges("cadrage", apres_cadrage, ["validation", END])
    g.add_conditional_edges("validation", apres_validation, ["cadrage", "expert", "contre_analyse"])
    g.add_edge("expert", "challenge")              # challenge attend la fin de toutes les branches parallèles
    g.add_edge("contre_analyse", "challenge")
    g.add_conditional_edges("challenge", apres_challenge, ["reponse_expert", "synthese"])
    g.add_edge("reponse_expert", "replique")       # la réplique attend toutes les réponses
    g.add_edge("replique", "synthese")
    g.add_edge("synthese", END)
    # Prototype : sauvegarde en mémoire. Pour reprendre une séance après redémarrage,
    # remplacer par SqliteSaver / PostgresSaver (paquets langgraph-checkpoint-sqlite / -postgres).
    return g.compile(checkpointer=InMemorySaver())
