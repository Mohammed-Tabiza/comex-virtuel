"""Serveur FastAPI : API de séance, flux SSE pour l'interface, fichiers statiques."""
import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from langgraph.types import Command
from pydantic import BaseModel

from . import graph as G
from .agents import AGENTS, EXPERTS
from .config import RACINE, charger_config
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
            _sauvegarder(seance, snap.values)
            await seance.publier("phase", phase="arbitrage" if seance.statut == "terminee" else "erreur")
            await seance.publier("seance_terminee", statut=seance.statut, erreurs=snap.values.get("erreurs", []))
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
    (sortie / f"{seance.id}.md").write_text(compte_rendu(seance, etat), encoding="utf-8")
    (sortie / f"{seance.id}.json").write_text(
        json.dumps({"etat": etat, "decision_humaine": seance.decision_humaine}, ensure_ascii=False, indent=1),
        encoding="utf-8")


# --------------------------------------------------------------------------- API
@app.get("/api/config")
async def config_publique():
    cfg = ETAT["config"]
    return {
        "mode": cfg["mode"],
        "experts": EXPERTS,
        "agents": {a: {**AGENTS[a], "modele": cfg["agents"][a]["modele"],
                       "libelle_modele": cfg["modeles"][cfg["agents"][a]["modele"]]["libelle"],
                       "famille": cfg["modeles"][cfg["agents"][a]["modele"]].get("famille", ""),
                       "thinking": cfg["agents"][a].get("thinking", False)} for a in AGENTS},
        "budget_dossier_tokens": cfg["protocole"]["budget_dossier_tokens"],
    }


@app.get("/api/dossiers")
async def dossiers():
    return [p.name for p in sorted(DOSSIERS.iterdir()) if p.is_dir() and any(p.glob("*.md"))]


@app.get("/api/dossiers/{nom}")
async def dossier_detail(nom: str):
    return charger_dossier(DOSSIERS / Path(nom).name).resume()


@app.get("/api/dossiers/{nom}/sections/{ref}")
async def section(nom: str, ref: str):
    s = charger_dossier(DOSSIERS / Path(nom).name).section(ref)
    if not s:
        raise HTTPException(404, "Section inconnue")
    return {"ref": s.ref, "titre": s.titre, "texte": s.texte}


@app.post("/api/seances")
async def creer_seance(corps: NouvelleSeance):
    repertoire = DOSSIERS / Path(corps.dossier).name
    if not repertoire.is_dir():
        raise HTTPException(404, "Dossier introuvable")
    dossier = charger_dossier(repertoire)
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
    _sauvegarder(seance, await _etat(seance_id))
    await seance.publier("decision_enregistree", texte=corps.texte)
    return {"ok": True}


@app.get("/api/seances/{seance_id}/compte-rendu.md")
async def telecharger_compte_rendu(seance_id: str):
    seance = _seance(seance_id)
    return PlainTextResponse(compte_rendu(seance, await _etat(seance_id)), media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="comex-{seance_id}.md"'})


@app.get("/")
async def accueil():
    return FileResponse(STATIQUE / "index.html")
