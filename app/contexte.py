"""Budget de contexte : estimation prudente au lancement, contrôle réel avant les appels les plus lourds.

1. Au lancement, la longueur des contributions est inconnue : on l'estime avec des longueurs
   prudentes par type de contribution (protocole.estimation_tokens), et l'on compte le budget de
   sortie complet de chaque appel. Le résultat est un niveau : ok, attention ou depassement.
2. Avant les objections et la synthèse, le prompt réellement construit est mesuré (et corrigé par
   l'écart constaté entre estimation et comptage réel des appels précédents). S'il ne tient pas,
   les contributions sont compactées en deux niveaux ; s'il ne tient toujours pas, l'appel n'a pas lieu.

Le comptage reste une estimation (~3,5 caractères par token en français) : MARGE garde une réserve.
"""
from __future__ import annotations

from typing import Callable

from .agents import prefixe_commun
from .dossier import estimer_tokens

MARGE = 0.95                 # part du contexte réellement utilisable
SEUIL_ATTENTION = 0.80
CONTEXTE_DEFAUT = 32768
# Longueurs prudentes des contributions (tokens), pour l'estimation au lancement
LONGUEURS_DEFAUT = {"cadrage": 900, "avis": 1200, "contre_analyse": 1300, "objections": 900, "reponse": 600}
CONSIGNES = 1800             # instructions, mandat et schéma JSON d'un appel
NIVEAUX_COMPACTAGE = (0, 1, 2)


def _modele(config: dict, nom: str) -> dict:
    return config["modeles"].get(nom, {})


def contexte_max(config: dict, nom: str) -> int:
    return int(_modele(config, nom).get("contexte_max") or CONTEXTE_DEFAUT)


def budget_sortie(config: dict, nom: str, etape: str) -> int:
    sortie = int(_modele(config, nom).get("max_tokens") or 4096)
    if etape == "synthese":
        sortie = int(config.get("protocole", {}).get("synthese_max_tokens") or sortie)
    return sortie


def niveau(taux: float) -> str:
    if taux >= MARGE:
        return "depassement"
    return "attention" if taux >= SEUIL_ATTENTION else "ok"


def estimer_seance(dossier_texte: str, composition, config: dict) -> dict:
    """Estimation prudente, avant toute contribution, des appels les plus exigeants de la séance."""
    longueurs = {**LONGUEURS_DEFAUT, **(config.get("protocole", {}).get("estimation_tokens") or {})}
    prefixe = estimer_tokens(prefixe_commun(dossier_texte))
    n = len(composition.experts)
    president = composition.membre("president")
    contradicteur = composition.membre("contradicteur")

    def etape(nom: str, membre, contributions: int) -> dict:
        entree = prefixe + CONSIGNES + contributions
        sortie = budget_sortie(config, membre.modele, nom)
        total, maxi = entree + sortie, contexte_max(config, membre.modele)
        return {"etape": nom, "membre": membre.id, "modele": membre.modele, "entree": entree, "sortie": sortie,
                "total": total, "contexte_max": maxi, "taux": round(total / maxi, 3), "niveau": niveau(total / maxi)}

    etapes = [etape("cadrage", president, 0)]
    # Avis : l'expert dont le modèle a le plus petit contexte
    if n:
        expert = min((composition.membre(e) for e in composition.experts),
                     key=lambda m: contexte_max(config, m.modele) - budget_sortie(config, m.modele, "avis"))
        etapes.append(etape("avis", expert, longueurs["cadrage"]))
    etapes.append(etape("objections", contradicteur,
                        longueurs["cadrage"] + longueurs["contre_analyse"] + n * longueurs["avis"]))
    etapes.append(etape("synthese", president,
                        longueurs["cadrage"] + n * longueurs["avis"] + longueurs["contre_analyse"]
                        + longueurs["objections"] + n * longueurs["reponse"]))
    pire = max(etapes, key=lambda e: e["taux"])
    return {"niveau": pire["niveau"], "plus_exigeante": pire, "etapes": etapes, "dossier_tokens": prefixe,
            "note": "Estimation prudente avant les contributions ; contrôle réel avant les objections et la synthèse."}


def mesurer(messages: list[dict]) -> int:
    return sum(estimer_tokens(m["content"]) for m in messages) + 8 * len(messages)


def ajuster(config: dict, nom: str, etape: str, construire: Callable[[int], list[dict]],
            calibration: float = 1.0) -> dict:
    """Cherche le premier niveau de compactage dont le prompt tient dans le contexte du modèle.

    Retourne {niveau, messages, entree, sortie, contexte_max, tient}. `tient` est faux si même le
    compactage maximal dépasse : l'appel ne doit alors pas avoir lieu.
    """
    maxi, sortie = contexte_max(config, nom), budget_sortie(config, nom, etape)
    resultat = {}
    for n in NIVEAUX_COMPACTAGE:
        messages = construire(n)
        entree = round(mesurer(messages) * max(1.0, calibration))
        resultat = {"niveau": n, "messages": messages, "entree": entree, "sortie": sortie, "contexte_max": maxi,
                    "tient": entree + sortie <= maxi * MARGE}
        if resultat["tient"]:
            return resultat
    return resultat


# --------------------------------------------------------------------------- compactage
_CHAMPS_AVIS = {
    0: None,
    1: ("position", "option_preferee", "resume", "arguments", "conditions_changement", "confiance"),
    2: ("position", "option_preferee", "resume"),
}
_CHAMPS_CONTRE = {0: None, 1: ("resume", "hypotheses_fragiles", "angles_morts", "critique_composition"),
                  2: ("resume", "hypotheses_fragiles")}
_CHAMPS_REPONSE = {0: None, 1: None, 2: ("position_revisee", "option_revisee", "resume")}


def _garder(contribution: dict | None, champs) -> dict:
    contribution = contribution or {}
    if champs is None:
        return contribution
    garde = {k: v for k, v in contribution.items() if k in champs}
    if "arguments" in garde:                     # arguments : texte et sources, sans le reste
        garde["arguments"] = [{"texte": a.get("texte", ""), "sources": a.get("sources", [])}
                              for a in garde["arguments"]][:4]
    return garde


def compacter(niveau_compactage: int, avis: dict, contre_analyse: dict | None = None,
              reponses: dict | None = None) -> tuple[dict, dict, dict]:
    """Version réduite des contributions. Niveau 0 : intégrales ; 1 : essentiel argumenté ; 2 : positions."""
    return ({k: _garder(v, _CHAMPS_AVIS[niveau_compactage]) for k, v in avis.items()},
            _garder(contre_analyse, _CHAMPS_CONTRE[niveau_compactage]),
            {k: _garder(v, _CHAMPS_REPONSE[niveau_compactage]) for k, v in (reponses or {}).items()})
