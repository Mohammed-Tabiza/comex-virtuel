"""Serveur FastAPI : API de séance, flux SSE pour l'interface, fichiers statiques."""
import asyncio
import json
import re
import uuid
from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from langgraph.types import Command
from pydantic import BaseModel

from . import graph as G
from .agents import AGENTS, EXPERTS
from .config import RACINE, agents_sur_modeles_externes, charger_config
from .dossier import charger_dossier
from .llm import Modeles
from .rapport import compte_rendu
from .runtime import SEANCES, Seance

DOSSIERS = RACINE / "seances"
STATIQUE = RACINE / "static"
ETAT = {"config": None, "graphe": None, "taches": set()}


@asynccontextmanager
async def cycle_de_vie(app: FastAPI):
    ETAT["config"] = charger_config()
    G.MODELES = Modeles(ETAT["config"])
    ETAT["graphe"] = G.construire_graphe()
    print(f"COMEX virtuel prêt : mode {ETAT['config']['mode']}")
    yield


app = FastAPI(title="COMEX virtuel", lifespan=cycle_de_vie)


class NouvelleSeance(BaseModel):
    dossier: str


class Validation(BaseModel):
    action: str = "valider"           # valider | recadrer
    complement: str = ""


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
            await seance.publier("attente_validation", cadrage=cad)
        else:
            seance.statut = "terminee" if snap.values.get("synthese") else "erreur"
            await seance.publier("phase", phase="arbitrage" if seance.statut == "terminee" else "erreur")
            await seance.publier("seance_terminee", statut=seance.statut, erreurs=snap.values.get("erreurs", []))
            _sauvegarder(seance, snap.values)          # après les derniers événements : le rejeu est complet
    except Exception as e:  # noqa: BLE001
        seance.statut = "erreur"
        await seance.publier("erreur", message=f"{type(e).__name__} : {e}")


def _lancer(seance: Seance, entree) -> None:
    tache = asyncio.create_task(_executer(seance, entree))
    ETAT["taches"].add(tache)
    tache.add_done_callback(ETAT["taches"].discard)


def _sauvegarder(seance: Seance, etat: dict) -> None:
    sortie = DOSSIERS / seance.dossier.nom / "resultats"
    sortie.mkdir(exist_ok=True)
    rapport = compte_rendu(seance, etat)
    (sortie / f"{seance.id}.md").write_text(rapport, encoding="utf-8")
    (sortie / f"{seance.id}.json").write_text(
        json.dumps({"etat": etat, "decision_humaine": seance.decision_humaine}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    if seance.statut == "terminee":
        _enregistrer_rejeu(seance, rapport)


# --------------------------------------------------------------------------- rejeu
ID_SEANCE = re.compile(r"^[0-9a-f]{8}$")


def _enregistrer_rejeu(seance: Seance, rapport: str) -> None:
    """Enregistre le journal complet d'une séance terminée, pour la rejouer sans appeler les modèles."""
    dossier = DOSSIERS / seance.dossier.nom / "rejeux"
    dossier.mkdir(exist_ok=True)
    cfg = _config_publique()
    rejeu = {
        "id": seance.id,
        "dossier": seance.dossier.nom,
        "date": datetime.fromtimestamp(seance.debut).isoformat(timespec="seconds"),
        "mode": cfg["mode"],
        "duree": seance.evenements[-1]["t"] if seance.evenements else 0,
        "modeles": sorted({a["libelle_modele"] for a in cfg["agents"].values()}),
        "decision_humaine": seance.decision_humaine,
        "config": {"agents": cfg["agents"], "modeles": cfg["modeles"]},
        "compte_rendu": rapport,
        "evenements": seance.evenements,
    }
    (dossier / f"{seance.id}.json").write_text(json.dumps(rejeu, ensure_ascii=False), encoding="utf-8")


def _fichier_rejeu(nom: str, rejeu_id: str) -> Path:
    if not ID_SEANCE.match(rejeu_id):
        raise HTTPException(404, "Rejeu inconnu")
    chemin = DOSSIERS / Path(nom).name / "rejeux" / f"{rejeu_id}.json"
    if not chemin.is_file():
        raise HTTPException(404, "Rejeu inconnu")
    return chemin


# --------------------------------------------------------------------------- API
def _config_publique() -> dict:
    cfg = ETAT["config"]
    return {
        "mode": cfg["mode"],
        "experts": EXPERTS,
        "modeles": {nom: {"libelle": m["libelle"], "famille": m.get("famille", ""), "hebergement": m["hebergement"]}
                    for nom, m in cfg["modeles"].items()},
        "agents": {a: {**AGENTS[a], "modele": cfg["agents"][a]["modele"],
                       "libelle_modele": cfg["modeles"][cfg["agents"][a]["modele"]]["libelle"],
                       "famille": cfg["modeles"][cfg["agents"][a]["modele"]].get("famille", ""),
                       "hebergement": cfg["modeles"][cfg["agents"][a]["modele"]]["hebergement"],
                       "thinking": cfg["agents"][a].get("thinking", False)} for a in AGENTS},
        "budget_dossier_tokens": cfg["protocole"]["budget_dossier_tokens"],
    }


def _blocage_confidentialite(dossier) -> dict | None:
    """Raison du refus si un dossier confidentiel devait partir vers un modèle externe, sinon None."""
    cfg = ETAT["config"]
    if cfg["mode"] != "reel" or not dossier.confidentiel:
        return None                       # en simulation, aucune donnée n'est envoyée à un modèle
    externes = agents_sur_modeles_externes(cfg)
    if not externes:
        return None
    modeles = sorted({e["libelle"] for e in externes})
    return {
        "code": "confidentialite",
        "message": f"Dossier confidentiel : {len(externes)} agent(s) utilisent un modèle hébergé hors de "
                   f"l'entreprise ({', '.join(modeles)}). Affectez-les à des modèles internes dans config.yaml, "
                   "ou déclarez le dossier non confidentiel dans _dossier.yaml s'il ne contient aucune donnée réelle.",
        "agents": externes,
    }


@app.get("/api/config")
async def config_publique():
    return _config_publique()


@app.get("/api/dossiers")
async def dossiers():
    return [p.name for p in sorted(DOSSIERS.iterdir()) if p.is_dir() and any(p.glob("*.md"))]


@app.get("/api/dossiers/{nom}")
async def dossier_detail(nom: str):
    dossier = charger_dossier(DOSSIERS / Path(nom).name)
    return {**dossier.resume(), "blocage": _blocage_confidentialite(dossier)}


@app.get("/api/dossiers/{nom}/rejeux")
async def rejeux(nom: str):
    repertoire = DOSSIERS / Path(nom).name / "rejeux"
    resultat = []
    for chemin in sorted(repertoire.glob("*.json")) if repertoire.is_dir() else []:
        try:
            r = json.loads(chemin.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        resultat.append({k: r.get(k) for k in ("id", "date", "mode", "duree", "modeles")}
                        | {"decision": bool(r.get("decision_humaine"))})
    return sorted(resultat, key=lambda r: r["date"] or "", reverse=True)


@app.get("/api/dossiers/{nom}/rejeux/{rejeu_id}")
async def rejeu(nom: str, rejeu_id: str):
    return FileResponse(_fichier_rejeu(nom, rejeu_id), media_type="application/json")


@app.get("/api/dossiers/{nom}/rejeux/{rejeu_id}/compte-rendu.md")
async def rejeu_compte_rendu(nom: str, rejeu_id: str):
    r = json.loads(_fichier_rejeu(nom, rejeu_id).read_text(encoding="utf-8"))
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
    blocage = _blocage_confidentialite(dossier)
    if blocage:
        raise HTTPException(403, blocage)
    seance = Seance(id=uuid.uuid4().hex[:8], dossier=dossier)
    SEANCES[seance.id] = seance
    budget = ETAT["config"]["protocole"]["budget_dossier_tokens"]
    await seance.publier("dossier_charge", dossier=dossier.resume(), budget=budget,
                         depassement=dossier.tokens_estimes > budget, mode=ETAT["config"]["mode"])
    _lancer(seance, {"complement": ""})
    return {"seance_id": seance.id}


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
    await seance.publier("validation_humaine", action=corps.action, complement=corps.complement)
    if corps.action == "valider":
        await seance.publier("phase", phase="analyses")
    _lancer(seance, Command(resume={"action": corps.action, "complement": corps.complement}))
    return {"ok": True}


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
    return PlainTextResponse(compte_rendu(seance, await _etat(seance_id)), media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="comex-{seance_id}.md"'})


@app.get("/")
async def accueil():
    return FileResponse(STATIQUE / "index.html")
