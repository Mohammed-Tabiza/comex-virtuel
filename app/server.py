"""Serveur FastAPI : API de séance, flux SSE pour l'interface, fichiers statiques."""
import asyncio
import json
import re
import time
import uuid
from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from langgraph.types import Command
from pydantic import BaseModel

from . import graph as G
from .comites import (Bibliotheque, Composition, ErreurComposition, couverture_cadrage, difference,
                      modeles_externes, resoudre_composition)
from .contexte import estimer_seance
from .trace import construire_rejeu, lire_rejeu, resume_rejeu
from .config import RACINE, charger_config
from .dossier import charger_dossier
from .llm import Modeles
from .rapport import compte_rendu
from .runtime import SEANCES, Seance

DOSSIERS = RACINE / "seances"
STATIQUE = RACINE / "static"
ETAT = {"config": None, "graphe": None, "taches": set(), "bibliotheque": None}


@asynccontextmanager
async def cycle_de_vie(app: FastAPI):
    ETAT["config"] = charger_config()
    ETAT["bibliotheque"] = None
    G.MODELES = Modeles(ETAT["config"])
    ETAT["graphe"] = G.construire_graphe()
    print(f"COMEX virtuel prêt : mode {ETAT['config']['mode']}")
    yield


app = FastAPI(title="COMEX virtuel", lifespan=cycle_de_vie)


class NouvelleSeance(BaseModel):
    dossier: str
    # Composition du comité (toutes facultatives : par défaut, le gabarit du dossier et sa présélection)
    gabarit: str | None = None
    selection: list[str] | None = None
    mandats: dict[str, str] | None = None
    affectations: dict[str, str] | None = None
    retraits_justifies: dict[str, str] | None = None


class Revision(BaseModel):
    """Révision de l'équipe à la validation (le gabarit reste celui du lancement)."""
    selection: list[str]
    mandats: dict[str, str] | None = None
    affectations: dict[str, str] | None = None
    retraits_justifies: dict[str, str] | None = None
    motif: str = ""


class Validation(BaseModel):
    action: str = "valider"           # valider | recadrer
    complement: str = ""
    composition_revisee: Revision | None = None


class Decision(BaseModel):
    texte: str


def _seance(seance_id: str) -> Seance:
    if seance_id not in SEANCES:
        raise HTTPException(404, "Séance inconnue")
    return SEANCES[seance_id]


async def _etat(seance_id: str) -> dict:
    snap = await ETAT["graphe"].aget_state({"configurable": {"thread_id": seance_id}})
    return snap.values if snap else {}


async def _executer(seance: Seance, entree) -> None:
    cfg = {"configurable": {"thread_id": seance.id}}
    seance.statut = "en_cours"
    try:
        async for _ in ETAT["graphe"].astream(entree, cfg, stream_mode="updates"):
            pass
        snap = await ETAT["graphe"].aget_state(cfg)
        if snap.next:                                      # interrompu : validation humaine attendue
            seance.statut = "attente_validation"
            cad = {k: v for k, v in (snap.values.get("cadrage") or {}).items() if k != "_meta"}
            await seance.publier("phase", phase="validation")
            await seance.publier("attente_validation", cadrage=cad, **_etat_validation(seance, cad))
        else:
            seance.statut = "terminee" if snap.values.get("synthese") else "erreur"
            await seance.publier("phase", phase="arbitrage" if seance.statut == "terminee" else "erreur")
            await seance.publier("seance_terminee", statut=seance.statut, erreurs=snap.values.get("erreurs", []))
            _sauvegarder(seance, snap.values)          # après les derniers événements : le rejeu est complet
    except Exception as e:  # noqa: BLE001
        seance.statut = "erreur"
        await seance.publier("erreur", message=f"{type(e).__name__} : {e}")


def _etat_validation(seance: Seance, cadrage: dict) -> dict:
    """Ce que le décideur voit à la validation : équipe, couverture des critères, estimation du contexte."""
    couverture = couverture_cadrage(seance.composition, cadrage)
    return {"comite": _vue_composition(seance.composition), "couverture": couverture,
            "criteres_sans_porteur": [c for c, membres in couverture.items() if not membres],
            "contexte": seance.estimation}


def _lancer(seance: Seance, entree) -> None:
    tache = asyncio.create_task(_executer(seance, entree))
    ETAT["taches"].add(tache)
    tache.add_done_callback(ETAT["taches"].discard)


def _sauvegarder(seance: Seance, etat: dict) -> None:
    sortie = DOSSIERS / seance.dossier.nom / "resultats"
    sortie.mkdir(exist_ok=True)
    rapport = compte_rendu(seance, etat, ETAT["config"]["modeles"])
    (sortie / f"{seance.id}.md").write_text(rapport, encoding="utf-8")
    (sortie / f"{seance.id}.json").write_text(
        json.dumps({"etat": etat, "decision_humaine": seance.decision_humaine}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    if seance.statut == "terminee":
        _enregistrer_rejeu(seance, rapport, etat)


# --------------------------------------------------------------------------- rejeu
ID_SEANCE = re.compile(r"^[0-9a-f]{8}$")


def _enregistrer_rejeu(seance: Seance, rapport: str, etat: dict | None = None) -> None:
    """Enregistre le journal complet d'une séance terminée (format version 2), pour la rejouer sans appeler
    les modèles et retrouver la consigne exacte de chaque contribution."""
    dossier = DOSSIERS / seance.dossier.nom / "rejeux"
    dossier.mkdir(exist_ok=True)
    etat = etat or {}
    rejeu = construire_rejeu(seance, vue_composition=_vue_composition(seance.composition),
                             modeles=_config_publique()["modeles"], mode=ETAT["config"]["mode"], rapport=rapport,
                             revisions_etat=etat.get("revisions_composition"), compactages=etat.get("compactages"))
    (dossier / f"{seance.id}.json").write_text(json.dumps(rejeu, ensure_ascii=False), encoding="utf-8")


def _lire_rejeu(nom: str, rejeu_id: str) -> dict:
    return lire_rejeu(json.loads(_fichier_rejeu(nom, rejeu_id).read_text(encoding="utf-8")))


def _fichier_rejeu(nom: str, rejeu_id: str) -> Path:
    if not ID_SEANCE.match(rejeu_id):
        raise HTTPException(404, "Rejeu inconnu")
    chemin = DOSSIERS / Path(nom).name / "rejeux" / f"{rejeu_id}.json"
    if not chemin.is_file():
        raise HTTPException(404, "Rejeu inconnu")
    return chemin


# --------------------------------------------------------------------------- API
def _bibliotheque() -> Bibliotheque:
    if ETAT["bibliotheque"] is None:
        ETAT["bibliotheque"] = Bibliotheque.charger(Path(ETAT["config"].get("bibliotheque") or RACINE / "comites"))
    return ETAT["bibliotheque"]


def _vue_composition(composition: Composition) -> dict:
    """Vue publique d'une composition, au format attendu par l'interface et les rejeux."""
    modeles = ETAT["config"]["modeles"]
    agents = {}
    for m in composition.membres:
        reglages = modeles.get(m.modele, {})
        agents[m.id] = {"nom": m.nom, "titre": m.titre, "initiales": m.initiales, "mandat": m.mandat,
                        "fonction": m.fonction, "role_id": m.role_id, "role_version": m.role_version,
                        "mandat_modifie": m.mandat_modifie, "criteres": m.criteres,
                        "modele": m.modele, "libelle_modele": reglages.get("libelle", m.modele),
                        "famille": reglages.get("famille", ""), "hebergement": reglages.get("hebergement", "externe"),
                        "thinking": m.thinking, "note_affectation": m.note_affectation}
    return {"gabarit": {"id": composition.gabarit_id, "version": composition.gabarit_version,
                        "nom": composition.gabarit_nom},
            "experts": composition.experts, "agents": agents, "retraits_justifies": composition.retraits_justifies}


def _gabarit_du_dossier(dossier) -> str:
    return dossier.gabarit or ETAT["config"].get("gabarit_defaut") or "comex"


def _composer(dossier, demande: NouvelleSeance | None = None) -> Composition:
    demande = demande or NouvelleSeance(dossier=dossier.nom)
    return resoudre_composition(_bibliotheque(), ETAT["config"], demande.gabarit or _gabarit_du_dossier(dossier),
                                selection=demande.selection, mandats=demande.mandats,
                                affectations=demande.affectations, retraits_justifies=demande.retraits_justifies)


def _config_publique() -> dict:
    cfg = ETAT["config"]
    vue = _vue_composition(resoudre_composition(_bibliotheque(), cfg, cfg.get("gabarit_defaut") or "comex"))
    return {
        "mode": cfg["mode"],
        "gabarit_defaut": vue["gabarit"],
        "experts": vue["experts"],                         # comité par défaut ; chaque séance a le sien
        "modeles": {nom: {"libelle": m["libelle"], "famille": m.get("famille", ""), "hebergement": m["hebergement"]}
                    for nom, m in cfg["modeles"].items()},
        "agents": vue["agents"],
        "budget_dossier_tokens": cfg["protocole"]["budget_dossier_tokens"],
    }


def _blocage_confidentialite(dossier, composition: Composition) -> dict | None:
    """Raison du refus si un dossier confidentiel devait partir vers un modèle externe, sinon None.

    Le contrôle porte sur la composition réelle de la séance (membres et modèles choisis)."""
    cfg = ETAT["config"]
    if cfg["mode"] != "reel" or not dossier.confidentiel:
        return None                       # en simulation, aucune donnée n'est envoyée à un modèle
    externes = modeles_externes(composition, cfg)
    if not externes:
        return None
    modeles = sorted({e["libelle"] for e in externes})
    return {
        "code": "confidentialite",
        "message": f"Dossier confidentiel : {len(externes)} membre(s) du comité utilisent un modèle hébergé hors de "
                   f"l'entreprise ({', '.join(modeles)}). Affectez-les à des modèles internes, "
                   "ou déclarez le dossier non confidentiel dans _dossier.yaml s'il ne contient aucune donnée réelle.",
        "agents": externes,
    }


def _verifier(dossier, composition: Composition) -> None:
    """Refuse une composition non conforme (confidentialité, endpoint manquant)."""
    blocage = _blocage_confidentialite(dossier, composition)
    if blocage:
        raise HTTPException(403, blocage)
    sans_endpoint = _modeles_sans_endpoint(composition)
    if sans_endpoint:
        raise HTTPException(422, {"code": "modele", "message": f"base_url manquante pour : {', '.join(sans_endpoint)}"})


def _modeles_sans_endpoint(composition: Composition) -> list[str]:
    cfg = ETAT["config"]
    if cfg["mode"] != "reel":
        return []
    return sorted({m.modele for m in composition.membres if not cfg["modeles"][m.modele].get("base_url")})


@app.get("/api/config")
async def config_publique():
    return _config_publique()


@app.get("/api/comites")
async def comites():
    """Bibliothèque des rôles et gabarits de comité."""
    return _bibliotheque().catalogue()


@app.get("/api/dossiers")
async def dossiers():
    return [p.name for p in sorted(DOSSIERS.iterdir()) if p.is_dir() and any(p.glob("*.md"))]


@app.get("/api/dossiers/{nom}")
async def dossier_detail(nom: str):
    dossier = charger_dossier(DOSSIERS / Path(nom).name)
    composition = _composer(dossier)
    return {**dossier.resume(), "gabarit": _gabarit_du_dossier(dossier),
            "comite": _vue_composition(composition), "blocage": _blocage_confidentialite(dossier, composition),
            "contexte": estimer_seance(dossier.texte, composition, ETAT["config"])}


@app.post("/api/dossiers/{nom}/composition")
async def essayer_composition(nom: str, corps: NouvelleSeance):
    """Évalue une composition sans lancer de séance : erreurs, blocage, estimation du contexte, couverture
    des critères usuels du gabarit. Sert à l'écran de constitution."""
    dossier = charger_dossier(DOSSIERS / Path(nom).name)
    try:
        composition = _composer(dossier, corps)
    except ErreurComposition as e:
        return {"valide": False, "erreurs": e.erreurs}
    couverture = composition.couverture(composition.criteres_gabarit)
    return {"valide": True, "erreurs": [], "comite": _vue_composition(composition),
            "blocage": _blocage_confidentialite(dossier, composition),
            "contexte": estimer_seance(dossier.texte, composition, ETAT["config"]),
            "couverture_usuelle": couverture,
            "criteres_usuels_sans_porteur": [c for c, m in couverture.items() if not m]}


@app.get("/api/dossiers/{nom}/rejeux")
async def rejeux(nom: str):
    repertoire = DOSSIERS / Path(nom).name / "rejeux"
    resultat = []
    for chemin in sorted(repertoire.glob("*.json")) if repertoire.is_dir() else []:
        try:
            r = json.loads(chemin.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        resultat.append(resume_rejeu(r))
    return sorted(resultat, key=lambda r: r["date"] or "", reverse=True)


@app.get("/api/dossiers/{nom}/rejeux/{rejeu_id}")
async def rejeu(nom: str, rejeu_id: str):
    """Rejeu ramené au format courant (les rejeux de version 1 restent lisibles)."""
    return _lire_rejeu(nom, rejeu_id)


@app.get("/api/dossiers/{nom}/rejeux/{rejeu_id}/consignes/{index}")
async def rejeu_consigne(nom: str, rejeu_id: str, index: int):
    """Consigne exacte envoyée au modèle pour une contribution (index : métadonnée consigne.index)."""
    consignes = _lire_rejeu(nom, rejeu_id)["consignes"]
    if not 0 <= index < len(consignes):
        raise HTTPException(404, "Consigne inconnue")
    return consignes[index]


@app.get("/api/dossiers/{nom}/rejeux/{rejeu_id}/compte-rendu.md")
async def rejeu_compte_rendu(nom: str, rejeu_id: str):
    r = _lire_rejeu(nom, rejeu_id)
    return PlainTextResponse(r.get("compte_rendu", ""), media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="comex-{rejeu_id}.md"'})


@app.get("/api/dossiers/{nom}/sections/{ref}")
async def section(nom: str, ref: str):
    dossier = charger_dossier(DOSSIERS / Path(nom).name)
    s = dossier.section(ref)
    if not s:
        raise HTTPException(404, "Section inconnue")
    document = next(d for d in dossier.documents if s in d.sections)
    return {"ref": s.ref, "titre": s.titre, "texte": s.texte,
            "document": document.titre, "fichier": document.fichier}


@app.post("/api/seances")
async def creer_seance(corps: NouvelleSeance):
    repertoire = DOSSIERS / Path(corps.dossier).name
    if not repertoire.is_dir():
        raise HTTPException(404, "Dossier introuvable")
    dossier = charger_dossier(repertoire)
    try:
        composition = _composer(dossier, corps)
    except ErreurComposition as e:
        raise HTTPException(422, {"code": "composition", "message": str(e), "erreurs": e.erreurs})
    _verifier(dossier, composition)
    seance = Seance(id=uuid.uuid4().hex[:8], dossier=dossier)
    seance.fixer_composition(composition)          # figée au lancement, propre à cette séance
    seance.estimation = estimer_seance(dossier.texte, composition, ETAT["config"])
    SEANCES[seance.id] = seance
    budget = ETAT["config"]["protocole"]["budget_dossier_tokens"]
    await seance.publier("dossier_charge", dossier=dossier.resume(), budget=budget,
                         depassement=dossier.tokens_estimes > budget, mode=ETAT["config"]["mode"],
                         comite=_vue_composition(composition), contexte=seance.estimation)
    _lancer(seance, {"complement": ""})
    return {"seance_id": seance.id, "contexte": seance.estimation}


@app.get("/api/seances/{seance_id}/evenements")
async def evenements(seance_id: str, request: Request, since: int = 0):
    seance = _seance(seance_id)
    depuis = max(since, int(request.headers.get("last-event-id") or 0))

    async def flux():
        async for evt in seance.suivre(depuis):
            if await request.is_disconnected():
                break
            if evt is None:
                yield ": ping\n\n"
                continue
            yield f"id: {evt['id']}\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n"

    return StreamingResponse(flux(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/seances/{seance_id}/validation")
async def valider(seance_id: str, corps: Validation):
    seance = _seance(seance_id)
    if seance.statut != "attente_validation":
        raise HTTPException(409, "La séance n'attend pas de validation")
    if corps.action not in ("valider", "recadrer"):
        raise HTTPException(422, "action : valider ou recadrer")
    revision = None
    if corps.composition_revisee:
        # Révision de l'équipe : ajouter un expert ne demande pas de nouveau cadrage ; modifier options,
        # contraintes ou critères passe par « recadrer ». L'équipe devient définitive avant les analyses.
        r = corps.composition_revisee
        # Les membres conservés gardent leur modèle et leur mandat, sauf changement explicite
        ancienne = seance.composition
        gardes = set(r.selection) | {"president", "contradicteur"}
        mandats = {m.id: m.mandat for m in ancienne.membres if m.mandat_modifie and m.id in gardes} | (r.mandats or {})
        conserves = {m.id: (m.modele, m.note_affectation) for m in ancienne.membres if m.id in gardes}
        retraits = {k: v for k, v in ancienne.retraits_justifies.items() if k not in r.selection} | (r.retraits_justifies or {})
        try:
            nouvelle = resoudre_composition(_bibliotheque(), ETAT["config"], ancienne.gabarit_id,
                                            selection=r.selection, mandats=mandats, affectations=r.affectations,
                                            retraits_justifies=retraits, conserves=conserves)
        except ErreurComposition as e:
            raise HTTPException(422, {"code": "composition", "message": str(e), "erreurs": e.erreurs})
        _verifier(seance.dossier, nouvelle)
        ecarts = difference(seance.composition, nouvelle)
        if any(ecarts.values()):
            revision = {"numero": len(seance.revisions) + 1, "instant": round(time.time() - seance.debut, 2),
                        "motif": r.motif.strip(), "avant": seance.composition.vers_dict(),
                        "apres": nouvelle.vers_dict(), **ecarts}
            seance.revisions.append(revision)
            seance.fixer_composition(nouvelle)
            seance.estimation = estimer_seance(seance.dossier.texte, nouvelle, ETAT["config"])
            cad = {k: v for k, v in ((await _etat(seance_id)).get("cadrage") or {}).items() if k != "_meta"}
            await seance.publier("composition_revisee", numero=revision["numero"], motif=revision["motif"],
                                 **ecarts, **_etat_validation(seance, cad))
    await seance.publier("validation_humaine", action=corps.action, complement=corps.complement)
    if corps.action == "valider":
        await seance.publier("phase", phase="analyses")
    resume = {"action": corps.action, "complement": corps.complement}
    if revision:
        resume["revision"] = {k: v for k, v in revision.items() if k not in ("avant", "apres")}
    _lancer(seance, Command(resume=resume))
    return {"ok": True, "revision": revision is not None}


@app.post("/api/seances/{seance_id}/decision")
async def decision(seance_id: str, corps: Decision):
    seance = _seance(seance_id)
    seance.decision_humaine = corps.texte
    await seance.publier("decision_enregistree", texte=corps.texte)
    _sauvegarder(seance, await _etat(seance_id))
    return {"ok": True}


@app.get("/api/seances/{seance_id}/compte-rendu.md")
async def telecharger_compte_rendu(seance_id: str):
    seance = _seance(seance_id)
    return PlainTextResponse(compte_rendu(seance, await _etat(seance_id), ETAT["config"]["modeles"]), media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="comex-{seance_id}.md"'})


@app.get("/")
async def accueil():
    return FileResponse(STATIQUE / "index.html")
