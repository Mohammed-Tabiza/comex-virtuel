"""Graphe LangGraph : le protocole de séance du COMEX.

    cadrage ─▶ validation humaine ─┬─▶ recadrer ─▶ cadrage
                                   └─▶ 6 avis experts ‖ contre-analyse   (en parallèle, à l'aveugle)
                                         ─▶ objections du contradicteur
                                         ─▶ réponses des experts visés   (en parallèle)
                                         ─▶ synthèse du président ─▶ fin (arbitrage humain dans l'interface)

Principes : chaque contribution est attribuée et jamais réécrite ; le contexte de chaque appel
est construit explicitement (un expert ne voit pas l'avis des autres au premier tour).
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
from .dossier import refs_citees
from .llm import Modeles
from .runtime import seance_de

MODELES: Modeles | None = None      # initialisé par le serveur au démarrage

LIBELLES = {
    "cadrage": "Cadrage de la question",
    "avis": "Analyse indépendante",
    "contre_analyse": "Contre-analyse indépendante",
    "objections": "Formulation des objections",
    "reponse": "Réponse aux objections",
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
    synthese: dict
    erreurs: Annotated[list, operator.add]


class Tache(TypedDict, total=False):
    """Contexte transmis à un agent lancé en parallèle (via Send) : uniquement ce qu'il a le droit de voir."""
    agent_id: str
    cadrage: dict
    complement: str
    avis_propre: dict
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
    await seance.publier("contribution", agent=agent_id, etape=etape, contenu=resultat, meta=meta)
    await seance.publier("agent_statut", agent=agent_id, statut="termine", etape=etape,
                         libelle=LIBELLES[etape], duree=meta["duree"])
    return {**resultat, "_meta": meta}, None


def _erreur(message: str | None) -> dict:
    return {"erreurs": [message]} if message else {}


# --------------------------------------------------------------------------- nœuds
async def cadrage(state: Etat, config: RunnableConfig):
    await seance_de(config).publier("phase", phase="cadrage")
    res, err = await appeler(config, "president", "cadrage", A.prompt_cadrage(state.get("complement", "")))
    return {"cadrage": res, **_erreur(err)} if res else _erreur(err)


async def validation(state: Etat):
    # Pause : la séance attend la décision humaine (reprise via Command(resume=...))
    reponse = interrupt({"cadrage": sans_meta(state.get("cadrage"))})
    return {"action_validation": reponse.get("action", "valider"), "complement": reponse.get("complement", "")}


async def expert(tache: Tache, config: RunnableConfig):
    agent_id = tache["agent_id"]
    res, err = await appeler(config, agent_id, "avis",
                             A.prompt_avis(agent_id, tache["cadrage"], tache.get("complement", "")))
    return {"avis": {f"{agent_id}:initial": res}, **_erreur(err)} if res else _erreur(err)


async def contre_analyse(tache: Tache, config: RunnableConfig):
    res, err = await appeler(config, "contradicteur", "contre_analyse", A.prompt_contre_analyse(tache["cadrage"]))
    return {"contre_analyse": res, **_erreur(err)} if res else _erreur(err)


async def challenge(state: Etat, config: RunnableConfig):
    await seance_de(config).publier("phase", phase="contradiction")
    avis = {cle.split(":")[0]: sans_meta(v) for cle, v in state.get("avis", {}).items()}
    res, err = await appeler(config, "contradicteur", "objections",
                             A.prompt_objections(sans_meta(state["cadrage"]), sans_meta(state.get("contre_analyse")), avis))
    if not res:
        return {"objections": [], **_erreur(err)}
    # On ne garde que les objections adressées à un expert qui a effectivement rendu un avis
    objections = [o for o in res.get("objections", []) if o.get("cible") in avis]
    return {"objections": objections}


async def reponse_expert(tache: Tache, config: RunnableConfig):
    agent_id = tache["agent_id"]
    res, err = await appeler(config, agent_id, "reponse",
                             A.prompt_reponse(agent_id, tache["avis_propre"], tache["objections"]))
    return {"reponses": {f"{agent_id}:reponse": res}, **_erreur(err)} if res else _erreur(err)


async def synthese(state: Etat, config: RunnableConfig):
    await seance_de(config).publier("phase", phase="synthese")
    avis = {cle.split(":")[0]: sans_meta(v) for cle, v in state.get("avis", {}).items()}
    reponses = {cle.split(":")[0]: sans_meta(v) for cle, v in state.get("reponses", {}).items()}
    res, err = await appeler(config, "president", "synthese",
                             A.prompt_synthese(sans_meta(state["cadrage"]), avis, sans_meta(state.get("contre_analyse")),
                                               state.get("objections", []), reponses))
    return {"synthese": res, **_erreur(err)} if res else _erreur(err)


# --------------------------------------------------------------------------- routage
def apres_cadrage(state: Etat):
    return "validation" if state.get("cadrage") else END


def apres_validation(state: Etat):
    if state.get("action_validation") == "recadrer":
        return "cadrage"
    base = {"cadrage": sans_meta(state["cadrage"]), "complement": state.get("complement", "")}
    return [Send("expert", {**base, "agent_id": e}) for e in A.EXPERTS] + [Send("contre_analyse", base)]


def apres_challenge(state: Etat):
    par_cible: dict[str, list] = {}
    for o in state.get("objections", []):
        par_cible.setdefault(o["cible"], []).append(o)
    envois = [Send("reponse_expert", {"agent_id": cible, "objections": objs,
                                      "avis_propre": sans_meta(state["avis"].get(f"{cible}:initial"))})
              for cible, objs in par_cible.items()]
    return envois or "synthese"


def construire_graphe():
    g = StateGraph(Etat)
    g.add_node("cadrage", cadrage)
    g.add_node("validation", validation)
    g.add_node("expert", expert)
    g.add_node("contre_analyse", contre_analyse)
    g.add_node("challenge", challenge)
    g.add_node("reponse_expert", reponse_expert)
    g.add_node("synthese", synthese)

    g.add_edge(START, "cadrage")
    g.add_conditional_edges("cadrage", apres_cadrage, ["validation", END])
    g.add_conditional_edges("validation", apres_validation, ["cadrage", "expert", "contre_analyse"])
    g.add_edge("expert", "challenge")              # challenge attend la fin de toutes les branches parallèles
    g.add_edge("contre_analyse", "challenge")
    g.add_conditional_edges("challenge", apres_challenge, ["reponse_expert", "synthese"])
    g.add_edge("reponse_expert", "synthese")
    g.add_edge("synthese", END)
    # Prototype : sauvegarde en mémoire. Pour reprendre une séance après redémarrage,
    # remplacer par SqliteSaver / PostgresSaver (paquets langgraph-checkpoint-sqlite / -postgres).
    return g.compile(checkpointer=InMemorySaver())
