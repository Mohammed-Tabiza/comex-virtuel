"""Chargement de config.yaml avec substitution des variables d'environnement."""
import os
import re
from pathlib import Path

import yaml

RACINE = Path(__file__).resolve().parent.parent
_VAR = re.compile(r"\$\{([A-Z0-9_]+)\}")
HEBERGEMENTS = ("interne", "externe")


def _substituer(valeur):
    if isinstance(valeur, str):
        return _VAR.sub(lambda m: os.environ.get(m.group(1), ""), valeur)
    if isinstance(valeur, dict):
        return {k: _substituer(v) for k, v in valeur.items()}
    if isinstance(valeur, list):
        return [_substituer(v) for v in valeur]
    return valeur


def charger_config(chemin: Path | None = None) -> dict:
    chemin = chemin or Path(os.environ.get("COMEX_CONFIG", RACINE / "config.yaml"))
    with open(chemin, encoding="utf-8") as f:
        config = _substituer(yaml.safe_load(f))
    config["mode"] = os.environ.get("COMEX_MODE", config.get("mode", "simulation"))

    # Contrôles de cohérence : chaque agent doit pointer vers un modèle déclaré (ancien format)
    for agent_id, reglage in (config.get("agents") or {}).items():
        if reglage["modele"] not in config["modeles"]:
            raise ValueError(f"L'agent {agent_id} utilise un modèle inconnu : {reglage['modele']}")
    # Hébergement : sans déclaration explicite, un modèle est considéré comme externe (hypothèse prudente)
    for nom, modele in config["modeles"].items():
        modele["hebergement"] = modele.get("hebergement", "externe")
        if modele["hebergement"] not in HEBERGEMENTS:
            raise ValueError(f"Modèle {nom} : hebergement doit valoir 'interne' ou 'externe'")
    # Bibliothèque de comités et règles d'affectation des modèles
    from .comites import Bibliotheque, normaliser_affectation
    config["bibliotheque"] = str(RACINE / config.get("bibliotheque", "comites"))
    bib = Bibliotheque.charger(Path(config["bibliotheque"]))
    config["affectation"] = normaliser_affectation(config)
    # L'ancien format « agents: » n'a servi qu'à déduire les règles : aucune table agent → modèle
    # globale ne subsiste, chaque séance porte sa propre composition.
    config.pop("agents", None)
    config["gabarit_defaut"] = config.get("gabarit_defaut") or ("comex" if "comex" in bib.gabarits else next(iter(bib.gabarits)))
    if config["gabarit_defaut"] not in bib.gabarits:
        raise ValueError(f"gabarit_defaut inconnu : {config['gabarit_defaut']}")
    if config["mode"] == "reel":
        regle = config["affectation"]
        for nom in {regle["president"], regle["contradicteur"], *regle["experts"], *regle["par_role"].values()}:
            if not config["modeles"][nom].get("base_url"):
                raise ValueError(f"Mode réel : base_url manquante pour le modèle {nom}")
    return config
