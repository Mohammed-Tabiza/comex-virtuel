"""Lot 4 : traçabilité (consignes, empreintes, composition dans le compte rendu) et compatibilité des rejeux."""
import asyncio
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import graph as G
from app import server
from app.dossier import charger_dossier
from app.llm import Modeles
from app.rapport import compte_rendu, difference_mots
from app.runtime import SEANCES, Seance
from app.trace import VERSION_REJEU, empreinte, lire_rejeu
from tests.test_protocole import config_simulation

RACINE = Path(__file__).resolve().parents[1]
ANCIEN = RACINE / "seances/assistant-ia-2027/rejeux/a40b8624.json"
MANDAT = "Concentre-toi sur la trésorerie 2027 et le coût complet, pas sur la valeur déclarée."


class CompatibiliteTests(unittest.TestCase):
    def test_le_rejeu_de_version_1_reste_lisible_a_l_identique(self):
        brut = json.loads(ANCIEN.read_text(encoding="utf-8"))
        self.assertNotIn("version_format", brut)                         # vrai rejeu d'avant les comités
        r = lire_rejeu(brut)
        self.assertEqual(r["version_format"], 1)
        self.assertEqual(r["config"]["experts"], ["strategie", "finance", "clients", "operations", "risques", "rh"])
        self.assertEqual(r["evenements"], brut["evenements"])             # journal inchangé : rejoué à l'identique
        self.assertEqual(r["gabarit"]["nom"], "COMEX")
        self.assertEqual((r["consignes"], r["revisions"], r["composition"]), ([], [], None))
        self.assertNotIn("version_format", json.loads(ANCIEN.read_text(encoding="utf-8")))   # fichier non modifié

    def test_api_rejeu_de_version_1(self):
        async def verifier():
            server.ETAT.update(config=config_simulation(), bibliotheque=None)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://t") as c:
                liste = (await c.get("/api/dossiers/assistant-ia-2027/rejeux")).json()
                ancien = next(x for x in liste if x["id"] == "a40b8624")
                self.assertEqual((ancien["version_format"], ancien["experts"], ancien["gabarit"]), (1, 6, "COMEX"))
                r = (await c.get("/api/dossiers/assistant-ia-2027/rejeux/a40b8624")).json()
                self.assertEqual(len(r["config"]["experts"]), 6)
                self.assertEqual((await c.get("/api/dossiers/assistant-ia-2027/rejeux/a40b8624/consignes/0")).status_code, 404)
                self.assertIn("Compte rendu", (await c.get("/api/dossiers/assistant-ia-2027/rejeux/a40b8624/compte-rendu.md")).text)
        asyncio.run(verifier())

    def test_compte_rendu_sans_composition(self):
        seance = Seance(id="vieux001", dossier=charger_dossier(RACINE / "seances/assistant-ia-2027"))
        texte = compte_rendu(seance, {})
        self.assertNotIn("## Composition du comité", texte)
        self.assertIn("## Décision humaine", texte)


class DifferenceTests(unittest.TestCase):
    def test_difference_mot_a_mot(self):
        self.assertEqual(difference_mots("Coûts et valeur créée.", "Coûts et trésorerie."),
                         "Coûts et ~~valeur créée.~~ **trésorerie.**")
        self.assertEqual(difference_mots("a b", "a b"), "a b")


class RejeuV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(RACINE / "seances/assistant-ia-2027", self.tmp / "assistant-ia-2027",
                        ignore=shutil.ignore_patterns("rejeux", "resultats"))
        self.addCleanup(shutil.rmtree, self.tmp)
        patcher = patch.object(server, "DOSSIERS", self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _derouler(self):
        async def scenario():
            cfg = config_simulation()
            server.ETAT.update(config=cfg, bibliotheque=None)
            G.MODELES = Modeles(cfg)
            server.ETAT["graphe"] = G.construire_graphe()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://t") as c:
                sid = (await c.post("/api/seances", json={"dossier": "assistant-ia-2027",
                                                           "mandats": {"finance": MANDAT}})).json()["seance_id"]
                while SEANCES[sid].statut != "attente_validation":
                    await asyncio.sleep(0.02)
                await c.post(f"/api/seances/{sid}/validation", json={"action": "valider", "composition_revisee": {
                    "selection": ["finance", "risques", "operations", "rh"], "mandats": {"finance": MANDAT},
                    "motif": "Décision budgétaire."}})
                while SEANCES[sid].statut not in ("terminee", "erreur"):
                    await asyncio.sleep(0.02)
                for tache in list(server.ETAT["taches"]):
                    await tache
                await c.post(f"/api/seances/{sid}/decision", json={"texte": "Option C retenue."})
                consigne0 = (await c.get(f"/api/dossiers/assistant-ia-2027/rejeux/{sid}/consignes/0")).json()
                return sid, consigne0
        return asyncio.run(scenario())

    def test_rejeu_v2_complet_et_consignes_reliees_aux_contributions(self):
        sid, consigne0 = self._derouler()
        rejeu = json.loads((self.tmp / "assistant-ia-2027/rejeux" / f"{sid}.json").read_text(encoding="utf-8"))
        self.assertEqual(rejeu["version_format"], VERSION_REJEU)
        dossier = charger_dossier(self.tmp / "assistant-ia-2027")
        self.assertEqual(rejeu["dossier_empreinte"], dossier.empreinte)
        self.assertEqual(len(rejeu["prefixe_empreintes"]), 1)              # préfixe commun à tous les appels
        self.assertEqual(rejeu["config"]["experts"], ["finance", "risques", "operations", "rh"])
        self.assertEqual(rejeu["gabarit"]["id"], "comex")
        self.assertEqual(rejeu["revisions"][0]["retraits"], ["strategie", "clients"])
        self.assertEqual(rejeu["decision_humaine"], "Option C retenue.")
        self.assertEqual(consigne0["etape"], "cadrage")

        # Chaque contribution renvoie à la consigne exacte qui l'a produite
        contributions = [e for e in rejeu["evenements"] if e["type"] == "contribution"]
        self.assertEqual(len(contributions), len(rejeu["consignes"]))
        for e in contributions:
            c = rejeu["consignes"][e["meta"]["consigne"]["index"]]
            self.assertEqual((c["agent"], c["etape"]), (e["agent"], e["etape"]))
            self.assertEqual(c["consigne_empreinte"], empreinte(c["consigne"]))
            self.assertEqual(e["meta"]["role"]["version"], c["role_version"])

        # Le mandat modifié figure dans la consigne de Finance ; aucun autre expert ne le reçoit.
        # L'animateur (porteurs des critères) et le contradicteur (critique de la composition) voient
        # les mandats des membres : c'est voulu.
        finance = [c for c in rejeu["consignes"] if c["agent"] == "finance" and c["etape"] == "avis"]
        self.assertEqual(len(finance), 1)
        self.assertIn(MANDAT, finance[0]["consigne"])
        self.assertTrue(finance[0]["mandat_modifie"])
        autres_experts = [c for c in rejeu["consignes"] if c["agent"] not in ("finance", "president", "contradicteur")]
        self.assertTrue(autres_experts)
        self.assertFalse(any(MANDAT in c["consigne"] for c in autres_experts))
        vus = {c["agent"] for c in rejeu["consignes"] if MANDAT in c["consigne"]}
        self.assertEqual(vus, {"finance", "president", "contradicteur"})
        membre = next(m for m in rejeu["composition"]["membres"] if m["id"] == "finance")
        self.assertEqual(membre["mandat"], MANDAT)
        self.assertIn("Question clé", membre["mandat_bibliotheque"])

    def test_compte_rendu_avec_la_composition(self):
        sid, _ = self._derouler()
        texte = (self.tmp / "assistant-ia-2027/resultats" / f"{sid}.md").read_text(encoding="utf-8")
        for attendu in ("# Compte rendu du comité virtuel (COMEX)", "## Composition du comité",
                        "| Direction financière | Expertise | finance (v1) |", "**modifié**",
                        "### Mandats modifiés", "~~", "**Concentre-toi",
                        "### Révisions de l'équipe à la validation", "retrait de Stratégie, Clients", "Motif : Décision budgétaire.",
                        "### Couverture des critères", "### Composition contestée par le contradicteur",
                        "Option C retenue."):
            self.assertIn(attendu, texte)


if __name__ == "__main__":
    unittest.main()
