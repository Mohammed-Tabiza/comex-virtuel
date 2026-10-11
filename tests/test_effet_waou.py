"""Tests du garde-fou de confidentialité, du rejeu de séance et de l'option révisée."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import server
from app.agents import EXPERTS, SCHEMAS
from app.config import agents_sur_modeles_externes, charger_config
from app.dossier import charger_dossier
from app.runtime import Seance
from app.simulation import REPONSES

RACINE = Path(__file__).resolve().parents[1]


def config_test(mode="reel", hebergement="externe"):
    return {
        "mode": mode,
        "modeles": {"m": {"libelle": "Modèle test", "famille": "test", "hebergement": hebergement, "base_url": "http://x"}},
        "agents": {a: {"modele": "m"} for a in ["president", "contradicteur", *EXPERTS]},
        "protocole": {"budget_dossier_tokens": 15000},
    }


def creer_dossier(racine: Path, nom: str, meta: str | None) -> Path:
    rep = racine / nom
    rep.mkdir()
    (rep / "00_question.md").write_text("# Q\n\n## Question posée\n\nFaut-il ?\n", encoding="utf-8")
    if meta is not None:
        (rep / "_dossier.yaml").write_text(meta, encoding="utf-8")
    return rep


class ConfidentialiteTests(unittest.TestCase):
    def test_un_dossier_est_confidentiel_sauf_declaration_explicite(self):
        with tempfile.TemporaryDirectory() as tmp:
            racine = Path(tmp)
            self.assertTrue(charger_dossier(creer_dossier(racine, "sans_meta", None)).confidentiel)
            self.assertTrue(charger_dossier(creer_dossier(racine, "ambigu", "confidentiel: non\n")).confidentiel)
            self.assertFalse(charger_dossier(creer_dossier(racine, "public", "confidentiel: false\n")).confidentiel)
        self.assertFalse(charger_dossier(RACINE / "seances/assistant-ia-2027").confidentiel)

    def test_hebergement_externe_par_defaut_et_valeur_controlee(self):
        with tempfile.TemporaryDirectory() as tmp:
            chemin = Path(tmp) / "config.yaml"
            cfg = config_test()
            del cfg["modeles"]["m"]["hebergement"]
            chemin.write_text(json.dumps(cfg), encoding="utf-8")       # JSON est du YAML valide
            with patch.dict("os.environ", {"COMEX_MODE": "reel"}):
                self.assertEqual(charger_config(chemin)["modeles"]["m"]["hebergement"], "externe")
                cfg["modeles"]["m"]["hebergement"] = "cloud"
                chemin.write_text(json.dumps(cfg), encoding="utf-8")
                with self.assertRaises(ValueError):
                    charger_config(chemin)
        self.assertEqual(len(agents_sur_modeles_externes(config_test())), 8)
        self.assertEqual(agents_sur_modeles_externes(config_test(hebergement="interne")), [])

    def test_seance_confidentielle_refusee_si_un_modele_est_externe(self):
        async def verifier():
            with tempfile.TemporaryDirectory() as tmp, patch.object(server, "DOSSIERS", Path(tmp)):
                creer_dossier(Path(tmp), "reel", None)
                server.ETAT["config"] = config_test()
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://t") as c:
                    r = await c.post("/api/seances", json={"dossier": "reel"})
                    self.assertEqual(r.status_code, 403)
                    self.assertEqual(r.json()["detail"]["code"], "confidentialite")
                    self.assertTrue((await c.get("/api/dossiers/reel")).json()["blocage"])
                dossier = charger_dossier(Path(tmp) / "reel")
                server.ETAT["config"] = config_test(hebergement="interne")
                self.assertIsNone(server._blocage_confidentialite(dossier))
                server.ETAT["config"] = config_test(mode="simulation")
                self.assertIsNone(server._blocage_confidentialite(dossier))
        asyncio.run(verifier())


class RejeuTests(unittest.TestCase):
    def test_une_seance_terminee_devient_rejouable(self):
        async def verifier():
            with tempfile.TemporaryDirectory() as tmp, patch.object(server, "DOSSIERS", Path(tmp)):
                creer_dossier(Path(tmp), "demo", "confidentiel: false\n")
                server.ETAT["config"] = config_test(mode="simulation")
                seance = Seance(id="abcdef12", dossier=charger_dossier(Path(tmp) / "demo"))
                await seance.publier("phase", phase="cadrage")
                await seance.publier("seance_terminee", statut="terminee", erreurs=[])
                seance.statut = "terminee"
                seance.decision_humaine = "Option C retenue."
                server._sauvegarder(seance, {})
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://t") as c:
                    liste = (await c.get("/api/dossiers/demo/rejeux")).json()
                    self.assertEqual([r["id"] for r in liste], ["abcdef12"])
                    self.assertTrue(liste[0]["decision"])
                    rejeu = (await c.get("/api/dossiers/demo/rejeux/abcdef12")).json()
                    self.assertEqual([e["type"] for e in rejeu["evenements"]], ["phase", "seance_terminee"])
                    self.assertIn("president", rejeu["config"]["agents"])
                    cr = await c.get("/api/dossiers/demo/rejeux/abcdef12/compte-rendu.md")
                    self.assertIn("Option C retenue.", cr.text)
                    self.assertEqual((await c.get("/api/dossiers/demo/rejeux/..%2Fx")).status_code, 404)
                    self.assertEqual((await c.get("/api/dossiers/demo/rejeux/00000000")).status_code, 404)
        asyncio.run(verifier())

    def test_une_seance_en_erreur_n_est_pas_rejouable(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(server, "DOSSIERS", Path(tmp)):
            creer_dossier(Path(tmp), "demo", None)
            server.ETAT["config"] = config_test(mode="simulation")
            seance = Seance(id="abcdef13", dossier=charger_dossier(Path(tmp) / "demo"))
            seance.statut = "erreur"
            server._sauvegarder(seance, {})
            self.assertFalse((Path(tmp) / "demo" / "rejeux").exists())


class OptionReviseeTests(unittest.TestCase):
    def test_reponse_indique_l_option_revisee(self):
        self.assertIn("option_revisee", SCHEMAS["reponse"]["required"])
        for agent, reponse in REPONSES.items():
            self.assertIn("option_revisee", reponse, agent)


if __name__ == "__main__":
    unittest.main()
