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
import unicodedata
from typing import Annotated, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from . import agents as A
from .chiffres import HYPOTHESE, chiffres_hors_dossier, releve
from .dossier import REF, normaliser_ref, refs_citees
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
    reserves: list
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
async def appeler(config: RunnableConfig, agent_id: str, etape: str, consigne: str, schema: dict | None = None):
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
        resultat, meta = await MODELES.generer(agent_id, etape, messages, on_flux, schema=schema)
    except Exception as e:  # noqa: BLE001
        await seance.publier("agent_statut", agent=agent_id, statut="erreur", etape=etape, message=str(e)[:300])
        return None, f"{agent_id}/{etape} : {e}"

    if etape == "reponse" and isinstance(resultat, dict):
        resultat["reactions_pairs"], corrections = A.reconcilier_reactions(resultat.get("reactions_pairs"), agent_id)
        if corrections:
            meta["reactions_corrigees"] = corrections
    # Vérification des sources : chaque référence citée doit exister dans le dossier
    citees = refs_citees(resultat)
    meta["sources"] = sorted(citees & seance.dossier.refs)
    meta["sources_invalides"] = sorted(citees - seance.dossier.refs)
    # Chiffres avancés sans être dans le dossier : calculs ou inventions, à vérifier
    meta["chiffres_hors_dossier"] = chiffres_hors_dossier(resultat, seance.dossier.texte)
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


def chiffres_du_debat(state: Etat, texte_dossier: str, avec_avis: bool = True) -> dict:
    """Chiffres absents du dossier, par auteur : avis et réponse de chaque expert, contributions du contradicteur."""
    reponses = state.get("reponses", {})
    par_auteur = {e: ([sans_meta(a)] if avec_avis else []) + [sans_meta(reponses.get(f"{e}:reponse"))]
                  for e, a in _avis_par_expert(state).items()}
    par_auteur["contradicteur"] = [sans_meta(state.get("contre_analyse")), state.get("objections", [])]
    return releve(par_auteur, texte_dossier)


def chiffres_par_objection(state: Etat, texte_dossier: str) -> dict:
    """{O2: [chiffres absents du dossier]} d'après le texte de la réponse à chaque objection."""
    par_objection = {}
    for cle, r in state.get("reponses", {}).items():
        for item in (r or {}).get("reponses", []):
            if c := chiffres_hors_dossier(item.get("texte", ""), texte_dossier):
                par_objection.setdefault(normaliser_id(item.get("objection_id")), []).extend(c)
    return par_objection


def normaliser_id(objection_id) -> str:
    return str(objection_id or "").strip().upper()


def reserves_de_replique(replique: dict | None, par_objection: dict) -> list[str]:
    """Objections jugées résolues alors que la réponse s'appuie sur des chiffres absents du dossier.

    Constaté (54b71524, rejoué) : même averti, le contradicteur sur un petit modèle juge « résolue » une objection
    sur la foi d'un ROI recalculé faux. Le contrôle est donc aussi fait par programme ; la réplique n'est pas
    réécrite, la réserve est transmise au président et au compte rendu."""
    reserves = []
    for ev in (replique or {}).get("evaluations", []):
        oid = normaliser_id(ev.get("objection_id"))
        # Une hypothèse de calcul (« avec 40 % d'adoption ») est assumée : elle seule ne met pas en réserve
        douteux = [c for c in par_objection.get(oid, []) if HYPOTHESE not in c.split(" — ")[0]]
        if ev.get("statut") == "resolue" and douteux:
            chiffres = ", ".join(c.split(" — ")[0] for c in douteux)
            reserves.append(f"{oid} jugée résolue par le contradicteur, mais la réponse s'appuie sur des chiffres "
                            f"absents du dossier ({chiffres}) : résolution à confirmer.")
    return reserves


async def replique(state: Etat, config: RunnableConfig):
    seance = seance_de(config)
    await seance.publier("phase", phase="replique")
    reponses = {cle.split(":")[0]: sans_meta(v) for cle, v in state.get("reponses", {}).items()}
    par_objection = chiffres_par_objection(state, seance.dossier.texte)
    res, err = await appeler(config, "contradicteur", "replique",
                             A.prompt_replique(state.get("objections", []), reponses, par_objection))
    suivi = _suivi(res, err, "contradicteur", "replique")
    if not res:
        return suivi
    reserves = reserves_de_replique(sans_meta(res), par_objection)
    if reserves:
        await seance.publier("reserves", reserves=reserves)
    return {"replique": res, "reserves": reserves, **suivi}


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


_norm = A.normaliser


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
                           if not ((m := REF.fullmatch(str(s).strip())) and normaliser_ref(m.group(1)) in refs)})
    if hors_dossier:
        ecarts.append(f"La synthèse cite {len(hors_dossier)} source(s) qui ne sont pas des sections du dossier : "
                      f"{' ; '.join(hors_dossier[:5])}.")
    return ecarts


# Noms sous lesquels un membre peut être désigné dans un texte (le modèle passe parfois à l'anglais).
ALIAS_MEMBRES = {"strategie": ["strategy", "strategique"], "finance": ["finances", "financier", "financiere"],
                 "clients": ["client", "customers", "metiers"], "operations": ["ops"],
                 "risques": ["risks", "conformite"], "rh": ["hr", "ressources humaines"]}
# « Finance (A : …) », « Finance et Opérations (Option A sous conditions) », « RH (Prudence/Option C) » ; pas
# « Opérations (Option C est insuffisante) » ni « (Considère l'Option A faisable) », qui parlent d'une option.
OPTION_CAMP = re.compile(r"\(\s*(?:[^()/]{0,30}/\s*)?(?:options?\s+)?([A-Z])\s*(?=[):,;.\-–—]|sous\b|avec\b)",
                         re.IGNORECASE)


def _sans_accents(texte: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(texte)) if unicodedata.category(c) != "Mn").lower()


def _membre(nom: str) -> str | None:
    nom = _sans_accents(nom).strip(" .:-")
    return next((e for e in A.EXPERTS
                 if nom in {e, _sans_accents(A.AGENTS[e]["nom"])} | set(ALIAS_MEMBRES.get(e, []))), None)


def _liste_de_membres(texte: str) -> list[str] | None:
    """Membres d'une liste « Stratégie, Clients et RH » ; None si le texte n'est pas qu'une liste de noms."""
    morceaux = [m for m in re.split(r"[,/&]|\bet\b|\band\b", _sans_accents(texte)) if m.strip(" .:-")]
    membres = [_membre(m) for m in morceaux]
    return membres if membres and all(membres) else None


def attributions_inexactes(synthese: dict, positions: dict, options: list) -> list[str]:
    """Désaccords de la synthèse qui prêtent à un membre une autre option que sa position finale.

    Constaté (séance be1d5427) : « Strategy (A …) vs. Finance (C …) » alors que la Finance est restée sur A et la
    Stratégie est passée à C, malgré le tableau des positions fourni au président. Chaque camp d'un désaccord
    (séparé par « vs », « contre » ou « ; ») est lu comme « membres (option …) »."""
    ids = {str(o.get("id", "")).strip().upper() for o in options}
    ecarts = []
    for d in synthese.get("desaccords", []):
        texte, debut, camps = str(d.get("positions", "")), 0, {}
        # Chaque « (option X » ferme un camp : ses membres sont le texte depuis la fin du camp précédent, quel que
        # soit le séparateur (« vs. », « contre », « ; », « . »). Constaté : « Finance (Option A : …). Stratégie (…) ».
        for m in OPTION_CAMP.finditer(texte):
            noms = re.sub(r"^(?:\s|[.;,\-–—*]|vs\.?|versus|contre)+", "", texte[debut:m.start()], flags=re.IGNORECASE)
            fin = texte.find(")", m.end())
            debut = fin + 1 if fin >= 0 else m.end()
            membres = _liste_de_membres(noms)
            if not membres or m.group(1).upper() not in ids:              # pas « membres (option …) »
                continue
            option = m.group(1).upper()
            for e in membres:
                camps[e] = camps.get(e, 0) + 1
                finale = str((positions.get(e) or {}).get("option_finale") or "").strip().upper()
                if finale and finale != option:
                    ecarts.append(f"La synthèse range {A.AGENTS[e]['nom']} sur l'option {option} dans le désaccord "
                                  f"« {d.get('sujet', '')} », alors que sa position finale est l'option {finale}.")
        # Constaté (c009e434) : « Risques (Option C : …) vs Risques (Option C : …) », avis initial contre position finale
        ecarts += [f"La synthèse oppose {A.AGENTS[e]['nom']} à lui-même dans le désaccord « {d.get('sujet', '')} » : "
                   f"un membre ne figure que dans un camp, avec sa position finale."
                   for e, n in camps.items() if n > 1]
    return ecarts


def positions_des_membres(state: Etat) -> dict:
    """Positions initiales et finales de chaque expert, calculées à partir des contributions.

    Données au président pour qu'il n'ait pas à les reconstituer (constaté : RH et Opérations inversés
    dans la synthèse de la séance 1d11bf86).
    """
    tableau = {}
    for e, avis in _avis_par_expert(state).items():
        rep_ = sans_meta(state.get("reponses", {}).get(f"{e}:reponse"))
        tableau[e] = {
            "option_initiale": avis.get("option_preferee"), "position_initiale": avis.get("position"),
            "option_finale": rep_.get("option_revisee") or avis.get("option_preferee"),
            "position_finale": rep_.get("position_revisee") or avis.get("position"),
        }
    return tableau


async def synthese(state: Etat, config: RunnableConfig):
    seance = seance_de(config)
    await seance.publier("phase", phase="synthese")
    avis = _avis_par_expert(state)
    reponses = {cle.split(":")[0]: sans_meta(v) for cle, v in state.get("reponses", {}).items()}
    lacunes = lacunes_du_debat(state)
    cadrage = sans_meta(state["cadrage"])
    positions = positions_des_membres(state)
    consigne = A.prompt_synthese(cadrage, avis, sans_meta(state.get("contre_analyse")),
                                 state.get("objections", []), reponses, sans_meta(state.get("replique")), lacunes,
                                 positions, chiffres_du_debat(state, seance.dossier.texte), state.get("reserves"))
    schema = A.schema_synthese(cadrage)
    res, err = await appeler(config, "president", "synthese", consigne, schema=schema)
    suivi = _suivi(res, err, "president", "synthese")
    if not res:
        return {"lacunes": lacunes, **suivi}
    attributions = attributions_inexactes(sans_meta(res), positions, cadrage.get("options", []))
    if attributions:
        # Constaté (be1d5427, rejouée) : même avec le tableau des positions et la consigne de format, le président
        # range un membre sur son option initiale. Une relance, avec les erreurs nommées, avant d'en faire une lacune.
        await seance.publier("relance", agent="president", etape="synthese",
                             message="Synthèse relancée : des désaccords attribuaient à des membres une autre option "
                                     "que leur position finale, ou les opposaient à eux-mêmes.", details=attributions)
        res2, err2 = await appeler(config, "president", "synthese",
                                   consigne + A.correction_attributions(attributions, positions), schema=schema)
        suivi2 = _suivi(res2, err2, "president", "synthese")
        attributions2 = attributions_inexactes(sans_meta(res2), positions, cadrage.get("options", [])) if res2 else None
        if res2 and len(attributions2) < len(attributions):    # on garde la meilleure des deux
            res, attributions, suivi = res2, attributions2, suivi2
        elif err2:
            suivi = {**suivi, "erreurs": suivi.get("erreurs", []) + suivi2["erreurs"]}
    # Contrôles a posteriori : la synthèse elle-même peut être tronquée ou incomplète
    lacunes = lacunes + [_lacune_troncature(t) for t in suivi.get("tronques", [])] +         ecarts_synthese(sans_meta(res), cadrage, seance.dossier.refs) + attributions
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
