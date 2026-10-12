"""Comités configurables : bibliothèque de rôles, gabarits de comité et composition d'une séance.

Trois niveaux :
- les rôles (comites/roles/*.yaml) décrivent une expertise : mandat, question clé, critères portés ;
- les fonctions du protocole (comites/fonctions/*.yaml) : président (ou animateur) et contradicteur,
  présentes dans tous les comités ;
- les gabarits (comites/gabarits/*.yaml) décrivent un type de comité : rôles obligatoires et
  optionnels, présélection, taille, critères par défaut.

`resoudre_composition` produit la composition d'une séance : liste figée des membres, avec le
mandat effectivement envoyé au modèle, la version du rôle d'origine et le modèle affecté.
"""
from __future__ import annotations

import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

FONCTIONS = ("president", "contradicteur")
EXPERTS_MAX = 6                 # au-delà, avis redondants et synthèse trop longue pour le contexte
MANDAT_MAX = 2000               # caractères, pour un mandat modifié à la main
THINKING_DEFAUT = ["president", "contradicteur"]


class ErreurBibliotheque(ValueError):
    """Fichier de rôle ou de gabarit invalide."""


class ErreurComposition(ValueError):
    """Composition demandée incompatible avec le gabarit ou la configuration."""

    def __init__(self, erreurs: list[str]):
        super().__init__(" ; ".join(erreurs))
        self.erreurs = erreurs


# --------------------------------------------------------------------------- modèles de données
@dataclass(frozen=True)
class Role:
    id: str
    version: int
    nom: str
    titre: str
    initiales: str
    mandat: str
    question_cle: str = ""
    criteres: tuple[str, ...] = ()
    fonction: bool = False

    def mandat_complet(self, mandat: str | None = None) -> str:
        """Texte du mandat tel qu'envoyé au modèle (mandat + question clé)."""
        texte = (mandat if mandat is not None else self.mandat).strip()
        if self.question_cle and mandat is None:
            texte += f" Question clé : {self.question_cle}"
        return texte


@dataclass(frozen=True)
class Gabarit:
    id: str
    version: int
    nom: str
    description: str
    obligatoires: tuple[str, ...]
    optionnels: tuple[str, ...]
    selection_defaut: tuple[str, ...]
    experts_min: int
    experts_max: int
    criteres: tuple[str, ...]
    fonctions: dict = field(default_factory=dict)    # surcharge du titre / nom des fonctions

    def resume(self) -> dict:
        return {"id": self.id, "version": self.version, "nom": self.nom, "description": self.description,
                "obligatoires": list(self.obligatoires), "optionnels": list(self.optionnels),
                "selection_defaut": list(self.selection_defaut),
                "experts": {"min": self.experts_min, "max": self.experts_max},
                "criteres": list(self.criteres), "fonctions": self.fonctions}


@dataclass
class Membre:
    id: str                     # identifiant dans la séance (= identifiant du rôle)
    fonction: str               # president | contradicteur | expert
    role_id: str
    role_version: int
    nom: str
    titre: str
    initiales: str
    mandat: str                 # texte effectivement envoyé au modèle
    mandat_modifie: bool
    mandat_bibliotheque: str    # texte de la bibliothèque, pour afficher la différence
    criteres: list[str]
    modele: str
    thinking: bool
    note_affectation: str = ""


@dataclass
class Composition:
    gabarit_id: str
    gabarit_version: int
    gabarit_nom: str
    membres: list[Membre]
    criteres_gabarit: list[str]
    retraits_justifies: dict[str, str] = field(default_factory=dict)

    @property
    def experts(self) -> list[str]:
        return [m.id for m in self.membres if m.fonction == "expert"]

    def membre(self, membre_id: str) -> Membre:
        for m in self.membres:
            if m.id == membre_id:
                return m
        raise KeyError(membre_id)

    def ids(self) -> list[str]:
        return [m.id for m in self.membres]

    def couverture(self, criteres: list[str]) -> dict[str, list[str]]:
        """Pour chaque critère, les experts qui le portent (comparaison sans casse ni accents)."""
        porteurs = {}
        for critere in criteres:
            cle = normaliser(critere)
            porteurs[critere] = [m.id for m in self.membres
                                 if m.fonction == "expert" and cle in {normaliser(c) for c in m.criteres}]
        return porteurs

    def vers_dict(self) -> dict:
        return {"version_format": 1, **asdict(self)}

    @classmethod
    def depuis_dict(cls, data: dict) -> "Composition":
        data = {k: v for k, v in data.items() if k != "version_format"}
        data["membres"] = [Membre(**m) for m in data["membres"]]
        return cls(**data)


def normaliser(texte: str) -> str:
    sans_accents = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    return " ".join(sans_accents.lower().replace("'", " ").split())


# --------------------------------------------------------------------------- bibliothèque
def _lire_yaml(chemin: Path) -> dict:
    try:
        data = yaml.safe_load(chemin.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ErreurBibliotheque(f"{chemin.name} : YAML invalide ({e})") from e
    if not isinstance(data, dict):
        raise ErreurBibliotheque(f"{chemin.name} : le fichier doit décrire un objet")
    return data


def _version(data: dict, chemin: Path) -> int:
    version = data.get("version")
    if not isinstance(version, int) or version < 1:
        raise ErreurBibliotheque(f"{chemin.name} : version doit être un entier ≥ 1")
    return version


def _role(chemin: Path, fonction: bool) -> Role:
    data = _lire_yaml(chemin)
    for champ in ("id", "nom", "titre", "initiales", "mandat"):
        if not str(data.get(champ) or "").strip():
            raise ErreurBibliotheque(f"{chemin.name} : champ « {champ} » manquant")
    if data["id"] != chemin.stem:
        raise ErreurBibliotheque(f"{chemin.name} : l'identifiant doit être « {chemin.stem} » (nom du fichier)")
    if len(str(data["initiales"])) > 3:
        raise ErreurBibliotheque(f"{chemin.name} : 3 initiales au maximum")
    criteres = data.get("criteres") or []
    if not isinstance(criteres, list) or not all(isinstance(c, str) and c.strip() for c in criteres):
        raise ErreurBibliotheque(f"{chemin.name} : criteres doit être une liste de textes")
    if not fonction and not criteres:
        raise ErreurBibliotheque(f"{chemin.name} : un rôle d'expert doit porter au moins un critère")
    return Role(id=data["id"], version=_version(data, chemin), nom=str(data["nom"]), titre=str(data["titre"]),
                initiales=str(data["initiales"]), mandat=" ".join(str(data["mandat"]).split()),
                question_cle=str(data.get("question_cle") or "").strip(), criteres=tuple(criteres), fonction=fonction)


@dataclass
class Bibliotheque:
    fonctions: dict[str, Role]
    roles: dict[str, Role]
    gabarits: dict[str, Gabarit]

    @classmethod
    def charger(cls, repertoire: Path) -> "Bibliotheque":
        if not repertoire.is_dir():
            raise ErreurBibliotheque(f"Bibliothèque de comités introuvable : {repertoire}")
        fonctions = {p.stem: _role(p, True) for p in sorted((repertoire / "fonctions").glob("*.yaml"))}
        manquantes = set(FONCTIONS) - set(fonctions)
        if manquantes:
            raise ErreurBibliotheque(f"Fonctions du protocole manquantes : {', '.join(sorted(manquantes))}")
        roles = {p.stem: _role(p, False) for p in sorted((repertoire / "roles").glob("*.yaml"))}
        collision = set(roles) & set(FONCTIONS)
        if collision:
            raise ErreurBibliotheque(f"Identifiants réservés aux fonctions du protocole : {', '.join(collision)}")
        bib = cls(fonctions=fonctions, roles=roles, gabarits={})
        for p in sorted((repertoire / "gabarits").glob("*.yaml")):
            bib.gabarits[p.stem] = bib._gabarit(p)
        if not bib.gabarits:
            raise ErreurBibliotheque("Aucun gabarit de comité défini")
        return bib

    def _gabarit(self, chemin: Path) -> Gabarit:
        data = _lire_yaml(chemin)
        nom_fichier = chemin.name
        if data.get("id") != chemin.stem:
            raise ErreurBibliotheque(f"{nom_fichier} : l'identifiant doit être « {chemin.stem} »")
        listes = {}
        for champ in ("obligatoires", "optionnels", "selection_defaut"):
            valeur = data.get(champ) or []
            if not isinstance(valeur, list):
                raise ErreurBibliotheque(f"{nom_fichier} : {champ} doit être une liste")
            inconnus = [r for r in valeur if r not in self.roles]
            if inconnus:
                raise ErreurBibliotheque(f"{nom_fichier} : rôles inconnus dans {champ} : {', '.join(inconnus)}")
            if len(set(valeur)) != len(valeur):
                raise ErreurBibliotheque(f"{nom_fichier} : doublon dans {champ}")
            listes[champ] = tuple(valeur)
        if set(listes["obligatoires"]) & set(listes["optionnels"]):
            raise ErreurBibliotheque(f"{nom_fichier} : un rôle ne peut être à la fois obligatoire et optionnel")
        if not set(listes["obligatoires"]) <= set(listes["selection_defaut"]):
            raise ErreurBibliotheque(f"{nom_fichier} : la présélection doit contenir les rôles obligatoires")
        if not set(listes["selection_defaut"]) <= set(listes["obligatoires"]) | set(listes["optionnels"]):
            raise ErreurBibliotheque(f"{nom_fichier} : la présélection ne peut contenir que des rôles du gabarit")
        bornes = data.get("experts") or {}
        mini, maxi = bornes.get("min", 3), bornes.get("max", EXPERTS_MAX)
        if not (isinstance(mini, int) and isinstance(maxi, int) and 1 <= mini <= maxi <= EXPERTS_MAX):
            raise ErreurBibliotheque(f"{nom_fichier} : bornes d'experts invalides (1 ≤ min ≤ max ≤ {EXPERTS_MAX})")
        if not mini <= len(listes["selection_defaut"]) <= maxi:
            raise ErreurBibliotheque(f"{nom_fichier} : la présélection doit compter entre {mini} et {maxi} experts")
        criteres = data.get("criteres") or []
        if not criteres or not all(isinstance(c, str) and c.strip() for c in criteres):
            raise ErreurBibliotheque(f"{nom_fichier} : au moins un critère est requis")
        fonctions = data.get("fonctions") or {}
        if set(fonctions) - set(FONCTIONS):
            raise ErreurBibliotheque(f"{nom_fichier} : fonctions inconnues : {', '.join(set(fonctions) - set(FONCTIONS))}")
        return Gabarit(id=data["id"], version=_version(data, chemin), nom=str(data.get("nom") or data["id"]),
                       description=str(data.get("description") or ""), obligatoires=listes["obligatoires"],
                       optionnels=listes["optionnels"], selection_defaut=listes["selection_defaut"],
                       experts_min=mini, experts_max=maxi, criteres=tuple(criteres), fonctions=fonctions)

    def catalogue(self) -> dict:
        """Vue publique de la bibliothèque (pour l'interface)."""
        def role(r: Role) -> dict:
            return {"id": r.id, "version": r.version, "nom": r.nom, "titre": r.titre, "initiales": r.initiales,
                    "mandat": r.mandat, "question_cle": r.question_cle, "mandat_complet": r.mandat_complet(),
                    "criteres": list(r.criteres)}
        return {"fonctions": {k: role(v) for k, v in self.fonctions.items()},
                "roles": {k: role(v) for k, v in self.roles.items()},
                "gabarits": {k: g.resume() for k, g in self.gabarits.items()}}


# --------------------------------------------------------------------------- affectation des modèles
def normaliser_affectation(config: dict) -> dict:
    """Règles d'affectation des modèles, avec compatibilité de l'ancien format `agents:`.

    Ancien format : agents: {president: {modele: x, thinking: true}, finance: {modele: y}, ...}
    Nouveau format : affectation: {president, contradicteur, experts: [...], par_role, thinking, ...}
    """
    modeles = config["modeles"]
    if "affectation" in config:
        regle = dict(config["affectation"] or {})
    elif "agents" in config:
        agents = config["agents"]
        defaut = next(iter(modeles))
        regle = {
            "president": agents.get("president", {}).get("modele", defaut),
            "contradicteur": agents.get("contradicteur", {}).get("modele", defaut),
            "experts": [defaut],
            "par_role": {a: r["modele"] for a, r in agents.items() if a not in FONCTIONS},
            "thinking": [a for a, r in agents.items() if r.get("thinking")],
        }
    else:
        raise ValueError("config.yaml : section « affectation » manquante")
    defaut = next(iter(modeles))
    regle.setdefault("president", defaut)
    regle.setdefault("contradicteur", regle["president"])
    regle["experts"] = regle.get("experts") or [regle["president"]]
    if isinstance(regle["experts"], str):
        regle["experts"] = [regle["experts"]]
    regle["par_role"] = dict(regle.get("par_role") or {})
    regle["thinking"] = list(regle.get("thinking", THINKING_DEFAUT))
    regle.setdefault("famille_differente_contradicteur", "preference")
    if regle["famille_differente_contradicteur"] not in ("preference", "ignorer"):
        raise ValueError("affectation.famille_differente_contradicteur : 'preference' ou 'ignorer'")
    utilises = [regle["president"], regle["contradicteur"], *regle["experts"], *regle["par_role"].values()]
    inconnus = sorted({m for m in utilises if m not in modeles})
    if inconnus:
        raise ValueError(f"affectation : modèles inconnus : {', '.join(inconnus)}")
    return regle


def lignee(config: dict, modele: str) -> str:
    """Famille d'entraînement d'un modèle (pour diversifier les avis). Par défaut, le nom du modèle."""
    return str(config["modeles"][modele].get("lignee") or modele)


def _modele_contradicteur(config: dict, regle: dict, modele_president: str) -> tuple[str, str]:
    choisi = regle["contradicteur"]
    if regle["famille_differente_contradicteur"] != "preference" or lignee(config, choisi) != lignee(config, modele_president):
        return choisi, ""
    autres = [m for m in config["modeles"] if lignee(config, m) != lignee(config, modele_president)]
    if not autres:
        return choisi, "Une seule famille de modèles disponible : le contradicteur partage celle du président."
    return autres[0], f"Modèle {autres[0]} retenu pour que le contradicteur ne partage pas la famille du président."


# --------------------------------------------------------------------------- résolution
def resoudre_composition(bib: Bibliotheque, config: dict, gabarit_id: str, *,
                         selection: list[str] | None = None,
                         mandats: dict[str, str] | None = None,
                         affectations: dict[str, str] | None = None,
                         retraits_justifies: dict[str, str] | None = None) -> Composition:
    """Construit la composition d'une séance à partir d'un gabarit et des choix du décideur.

    - selection : experts retenus (par défaut la présélection du gabarit), dans l'ordre d'affichage ;
      tout rôle d'expert de la bibliothèque est accepté, le gabarit n'étant qu'une présélection ;
    - mandats : texte de mandat remplaçant celui de la bibliothèque, par membre ;
    - affectations : modèle imposé, par membre ;
    - retraits_justifies : justification écrite pour chaque rôle obligatoire retiré.
    """
    if gabarit_id not in bib.gabarits:
        raise ErreurComposition([f"Gabarit inconnu : {gabarit_id}"])
    gabarit = bib.gabarits[gabarit_id]
    selection = list(gabarit.selection_defaut if selection is None else selection)
    mandats = {k: v for k, v in (mandats or {}).items()}
    affectations = dict(affectations or {})
    retraits = {k: str(v).strip() for k, v in (retraits_justifies or {}).items() if str(v).strip()}
    regle = normaliser_affectation(config)
    erreurs = []

    inconnus = [r for r in selection if r not in bib.roles]
    if inconnus:
        erreurs.append(f"Rôles inconnus : {', '.join(inconnus)}")
    if len(set(selection)) != len(selection):
        erreurs.append("Un rôle ne peut siéger qu'une fois")
    if not gabarit.experts_min <= len(selection) <= gabarit.experts_max:
        erreurs.append(f"Le comité « {gabarit.nom} » compte entre {gabarit.experts_min} et "
                       f"{gabarit.experts_max} experts ({len(selection)} demandés)")
    for obligatoire in gabarit.obligatoires:
        if obligatoire not in selection and obligatoire not in retraits:
            erreurs.append(f"Le rôle obligatoire « {bib.roles[obligatoire].titre} » ne peut être retiré "
                           "sans justification écrite")
    hors_comite = set(mandats) | set(affectations)
    hors_comite -= set(selection) | set(FONCTIONS)
    if hors_comite:
        erreurs.append(f"Mandat ou modèle fourni pour un rôle absent du comité : {', '.join(sorted(hors_comite))}")
    for membre_id, texte in mandats.items():
        if not str(texte).strip():
            erreurs.append(f"Mandat vide pour {membre_id}")
        elif len(str(texte)) > MANDAT_MAX:
            erreurs.append(f"Mandat trop long pour {membre_id} ({MANDAT_MAX} caractères au maximum)")
    for membre_id, modele in affectations.items():
        if modele not in config["modeles"]:
            erreurs.append(f"Modèle inconnu pour {membre_id} : {modele}")
    if erreurs:
        raise ErreurComposition(erreurs)

    def membre(role: Role, fonction: str, modele: str, note: str = "") -> Membre:
        surcharge = gabarit.fonctions.get(role.id, {}) if role.fonction else {}
        bibliotheque = role.mandat_complet()
        texte = " ".join(str(mandats[role.id]).split()) if role.id in mandats else bibliotheque
        if role.id in affectations:
            modele, note = affectations[role.id], "Modèle choisi par le décideur."
        return Membre(id=role.id, fonction=fonction, role_id=role.id, role_version=role.version,
                      nom=str(surcharge.get("nom", role.nom)), titre=str(surcharge.get("titre", role.titre)),
                      initiales=str(surcharge.get("initiales", role.initiales)), mandat=texte,
                      mandat_modifie=texte != bibliotheque, mandat_bibliotheque=bibliotheque,
                      criteres=list(role.criteres), modele=modele, thinking=role.id in regle["thinking"],
                      note_affectation=note)

    president = membre(bib.fonctions["president"], "president", regle["president"])
    modele_contra, note_contra = _modele_contradicteur(config, regle, president.modele)
    contradicteur = membre(bib.fonctions["contradicteur"], "contradicteur", modele_contra, note_contra)
    experts = []
    tour = 0
    for role_id in selection:
        if role_id in regle["par_role"]:
            modele = regle["par_role"][role_id]
        else:
            modele = regle["experts"][tour % len(regle["experts"])]
            tour += 1
        experts.append(membre(bib.roles[role_id], "expert", modele))
    return Composition(gabarit_id=gabarit.id, gabarit_version=gabarit.version, gabarit_nom=gabarit.nom,
                       membres=[president, *experts, contradicteur], criteres_gabarit=list(gabarit.criteres),
                       retraits_justifies={k: v for k, v in retraits.items() if k not in selection})


def modeles_externes(composition: Composition, config: dict) -> list[dict]:
    """Membres dont le modèle est hébergé hors de l'entreprise."""
    return [{"agent": m.id, "modele": m.modele, "libelle": config["modeles"][m.modele]["libelle"]}
            for m in composition.membres if config["modeles"][m.modele].get("hebergement", "externe") != "interne"]
