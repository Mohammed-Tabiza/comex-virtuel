"""Traçabilité : consignes envoyées, empreintes, format des rejeux.

Chaque appel à un modèle enregistre sa consigne complète (la partie propre au rôle et à l'étape),
avec le rôle d'origine, sa version et le modèle. Le dossier, identique pour tous les appels, n'est
enregistré qu'une fois sous forme d'empreinte. On peut ainsi dire, pour chaque avis, quelle consigne
exacte l'a produit, même si la bibliothèque de rôles a évolué depuis.

Rejeux :
- version 1 (avant les comités configurables) : agents enregistrés, pas de composition ni de consignes ;
- version 2 : composition complète, révisions, consignes, empreintes du dossier et du préfixe commun.
`lire_rejeu` ramène les deux versions à la même forme, sans modifier le fichier.
"""
from __future__ import annotations

import hashlib
from datetime import datetime

VERSION_REJEU = 2
FONCTIONS = ("president", "contradicteur")


def empreinte(texte: str) -> str:
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()[:16]


def enregistrer_consigne(seance, membre, etape: str, messages: list[dict], compactage: dict | None) -> dict:
    """Mémorise la consigne d'un appel et renvoie la référence à placer dans les métadonnées."""
    systeme, consigne = messages[0]["content"], messages[-1]["content"]
    entree = {
        "index": len(seance.consignes),
        "agent": membre.id, "etape": etape,
        "role_id": membre.role_id, "role_version": membre.role_version, "mandat_modifie": membre.mandat_modifie,
        "modele": membre.modele, "thinking": membre.thinking,
        "prefixe_empreinte": empreinte(systeme),
        "consigne_empreinte": empreinte(consigne),
        "compactage": (compactage or {}).get("niveau", 0),
        "consigne": consigne,
    }
    seance.consignes.append(entree)
    return {"index": entree["index"], "empreinte": entree["consigne_empreinte"], "caracteres": len(consigne)}


def construire_rejeu(seance, *, vue_composition: dict, modeles: dict, mode: str, rapport: str,
                     revisions_etat: list | None = None, compactages: list | None = None) -> dict:
    """Rejeu au format version 2 : tout ce qu'il faut pour rejouer et comprendre la séance."""
    prefixes = sorted({c["prefixe_empreinte"] for c in seance.consignes})
    return {
        "version_format": VERSION_REJEU,
        "id": seance.id,
        "dossier": seance.dossier.nom,
        "dossier_empreinte": empreinte(seance.dossier.texte),
        "dossier_tokens": seance.dossier.tokens_estimes,
        "prefixe_empreintes": prefixes,             # une seule attendue : préfixe commun, cache vLLM préservé
        "date": datetime.fromtimestamp(seance.debut).isoformat(timespec="seconds"),
        "mode": mode,
        "duree": seance.evenements[-1]["t"] if seance.evenements else 0,
        "modeles": sorted({a["libelle_modele"] for a in vue_composition["agents"].values()}),
        "decision_humaine": seance.decision_humaine,
        "config": {"agents": vue_composition["agents"], "experts": vue_composition["experts"], "modeles": modeles},
        "gabarit": vue_composition["gabarit"],
        "composition": seance.composition.vers_dict(),
        "revisions": seance.revisions or (revisions_etat or []),
        "compactages": compactages or [],
        "estimation_contexte": seance.estimation,
        "consignes": seance.consignes,
        "compte_rendu": rapport,
        "evenements": seance.evenements,
    }


def lire_rejeu(data: dict) -> dict:
    """Ramène un rejeu de version 1 ou 2 à la forme courante (sans toucher au fichier)."""
    rejeu = dict(data)
    version = int(rejeu.get("version_format") or 1)
    rejeu["version_format"] = version
    config = dict(rejeu.get("config") or {})
    agents = config.get("agents") or {}
    if not config.get("experts"):
        # Version 1 : la liste des experts n'était pas enregistrée ; ce sont les membres hors fonctions du protocole
        config["experts"] = [a for a in agents if a not in FONCTIONS]
    for agent_id, agent in agents.items():
        agent.setdefault("fonction", agent_id if agent_id in FONCTIONS else "expert")
    rejeu["config"] = config
    rejeu.setdefault("composition", None)
    rejeu.setdefault("revisions", [])
    rejeu.setdefault("compactages", [])
    rejeu.setdefault("consignes", [])
    rejeu.setdefault("gabarit", {"id": "comex", "version": None, "nom": "COMEX"} if version == 1 else None)
    return rejeu


def resume_rejeu(data: dict) -> dict:
    """Entrée de la liste des rejeux."""
    r = lire_rejeu(data)
    return {"id": r.get("id"), "date": r.get("date"), "mode": r.get("mode"), "duree": r.get("duree"),
            "modeles": r.get("modeles"), "decision": bool(r.get("decision_humaine")),
            "version_format": r["version_format"], "gabarit": (r.get("gabarit") or {}).get("nom"),
            "experts": len(r["config"]["experts"]), "revisions": len(r["revisions"])}
