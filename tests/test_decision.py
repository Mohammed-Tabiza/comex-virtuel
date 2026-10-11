import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx

from app.agents import SCHEMAS
from app.dossier import charger_dossier, refs_citees
from app.rapport import compte_rendu
from app.llm import Modeles
from app.runtime import Seance
from app.server import app
from app.simulation import CADRAGE, SYNTHESE, reponse


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.dossier = charger_dossier(Path(__file__).resolve().parents[1] / "seances/assistant-ia-2027")

    def test_comparison_covers_every_option_and_criterion_with_real_sources(self):
        self.assertEqual(len(SYNTHESE["options"]), len(CADRAGE["options"]))
        for option in SYNTHESE["options"]:
            self.assertEqual({c["critere"] for c in option["criteres"]}, set(CADRAGE["criteres"]))
            self.assertTrue(option["preuves"])
            for proof in option["preuves"]:
                self.assertTrue(proof["sources"])
            for criterion in option["criteres"]:
                self.assertIn(criterion["evaluation"], {"favorable", "mitige", "defavorable", "inconnu"})
            self.assertFalse(refs_citees(option) - self.dossier.refs)
        schema = SCHEMAS["synthese"]["properties"]["options"]["items"]
        self.assertEqual(set(schema["required"]), set(SYNTHESE["options"][0]))
        result = reponse("president", "synthese")
        result["options"][0]["preuves"].clear()
        self.assertTrue(SYNTHESE["options"][0]["preuves"])

    def test_report_includes_comparison_and_accepts_older_synthesis(self):
        seance = Seance(id="test", dossier=self.dossier)
        report = compte_rendu(seance, {"cadrage": CADRAGE, "synthese": SYNTHESE})
        for text in ("## Comparaison des options", "#### Critères de décision", "#### Preuves documentées", "#### Objections", "#### Incertitudes", "[D2-§1]"):
            self.assertIn(text, report)
        older = {"options": [{"nom": "Option historique", "pour": [], "contre": []}]}
        self.assertIn("Option historique", compte_rendu(seance, {"synthese": older}))

    def test_source_endpoint_returns_exact_passage_and_rejects_unknown_reference(self):
        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/dossiers/assistant-ia-2027/sections/D1-§3")
                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertEqual(data["texte"], self.dossier.section("D1-§3").texte)
                self.assertTrue(data["fichier"].endswith(".md"))
                self.assertTrue(data["document"])
                self.assertEqual((await client.get("/api/dossiers/assistant-ia-2027/sections/D99-§1")).status_code, 404)
        asyncio.run(check())

    def test_detailed_synthesis_has_its_own_output_budget(self):
        async def check():
            async def empty_stream():
                if False:
                    yield None
            create = AsyncMock(side_effect=lambda **kwargs: empty_stream())
            client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
            models = object.__new__(Modeles)
            models.config = {"protocole": {"synthese_max_tokens": 6144}}
            model = {"model": "gemma4:31b-cloud", "max_tokens": 3072}
            await models._flux(client, model, False, "synthese", [], "consigne", AsyncMock())
            self.assertEqual(create.call_args.kwargs["max_tokens"], 6144)
            await models._flux(client, model, False, "avis", [], "consigne", AsyncMock())
            self.assertEqual(create.call_args.kwargs["max_tokens"], 3072)
        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
