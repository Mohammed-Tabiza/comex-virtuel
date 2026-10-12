"""Chargement du dossier de séance : fichiers Markdown numérotés, sections référencées [D1-§2].

Pas de base vectorielle : le dossier complet est placé en tête du prompt,
identique pour tous les agents, ce qui permet à vLLM de mettre ce préfixe en cache.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REF = re.compile(r"\[?(D\d+-§\d+)\]?")


@dataclass
class Section:
    ref: str
    titre: str
    texte: str


@dataclass
class Document:
    id: str
    fichier: str
    titre: str
    sections: list[Section] = field(default_factory=list)


@dataclass
class Dossier:
    nom: str
    documents: list[Document]
    texte: str            # dossier complet, tel qu'injecté dans les prompts
    tokens_estimes: int
    # Confidentiel par défaut : seul un dossier explicitement déclaré non confidentiel
    # (fichier _dossier.yaml) peut être envoyé à des modèles hébergés hors de l'entreprise.
    confidentiel: bool = True
    description: str = ""
    gabarit: str = ""          # comité présélectionné pour ce dossier (facultatif)

    @property
    def empreinte(self) -> str:
        from .trace import empreinte
        return empreinte(self.texte)

    @property
    def refs(self) -> set[str]:
        return {s.ref for d in self.documents for s in d.sections}

    def section(self, ref: str) -> Section | None:
        for d in self.documents:
            for s in d.sections:
                if s.ref == ref:
                    return s
        return None

    def resume(self) -> dict:
        return {
            "nom": self.nom,
            "tokens_estimes": self.tokens_estimes,
            "confidentiel": self.confidentiel,
            "description": self.description,
            "gabarit": self.gabarit,
            "documents": [
                {"id": d.id, "fichier": d.fichier, "titre": d.titre,
                 "sections": [{"ref": s.ref, "titre": s.titre} for s in d.sections]}
                for d in self.documents
            ],
        }


def estimer_tokens(texte: str) -> int:
    # Approximation suffisante pour un budget : ~3,5 caractères par token en français
    return int(len(texte) / 3.5)


def _parser_document(doc_id: str, chemin: Path) -> Document:
    lignes = chemin.read_text(encoding="utf-8").splitlines()
    titre = chemin.stem
    sections: list[Section] = []
    courante: Section | None = None
    for ligne in lignes:
        if ligne.startswith("# ") and titre == chemin.stem:
            titre = ligne[2:].strip()
        elif ligne.startswith("## "):
            courante = Section(ref=f"{doc_id}-§{len(sections) + 1}", titre=ligne[3:].strip(), texte="")
            sections.append(courante)
        elif courante is not None:
            courante.texte += ligne + "\n"
    for s in sections:
        s.texte = s.texte.strip()
    return Document(id=doc_id, fichier=chemin.name, titre=titre, sections=sections)


def charger_dossier(repertoire: Path) -> Dossier:
    fichiers = sorted(p for p in repertoire.glob("*.md") if not p.name.startswith("_"))
    if not fichiers:
        raise FileNotFoundError(f"Aucun fichier .md dans {repertoire}")
    documents = [_parser_document(f"D{i}", f) for i, f in enumerate(fichiers)]

    blocs = []
    for d in documents:
        blocs.append(f"### Document {d.id} : {d.titre}")
        for s in d.sections:
            blocs.append(f"[{s.ref}] {s.titre}\n{s.texte}")
    texte = "\n\n".join(blocs)
    meta = lire_meta(repertoire)
    return Dossier(nom=repertoire.name, documents=documents, texte=texte, tokens_estimes=estimer_tokens(texte),
                   confidentiel=meta["confidentiel"], description=meta["description"], gabarit=meta["gabarit"])


def lire_meta(repertoire: Path) -> dict:
    """Lit _dossier.yaml. Toute valeur autre que `confidentiel: false` laisse le dossier confidentiel."""
    chemin = repertoire / "_dossier.yaml"
    meta = (yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}) if chemin.exists() else {}
    return {"confidentiel": meta.get("confidentiel") is not False, "description": str(meta.get("description", "")),
            "gabarit": str(meta.get("gabarit") or "")}


def refs_citees(obj) -> set[str]:
    """Toutes les références [Dx-§y] présentes dans une contribution (récursif)."""
    trouvees: set[str] = set()
    if isinstance(obj, str):
        trouvees |= set(REF.findall(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            trouvees |= refs_citees(v)
    elif isinstance(obj, list):
        for v in obj:
            trouvees |= refs_citees(v)
    return trouvees
