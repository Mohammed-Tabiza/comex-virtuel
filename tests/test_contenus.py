"""Lot 6 : bibliothèque de rôles et gabarits, dossier « plateforme d'inférence LLM » et son scénario."""
import asyncio
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import graph as G
from app import server
from app import simulation
from app import simulation_inference as INF
from app.agents import SCHEMAS
from app.comites import Bibliotheque, normaliser, resoudre_composition
from app.dossier import charger_dossier, refs_citees
from app.llm import Modeles
from app.runtime import SEANCES
from tests.test_protocole import config_simulation

RACINE = Path(__file__).resolve().parents[1]
BIB = Bibliotheque.charger(RACINE / "comites")


def conforme(valeur, schema, chemin="$"):
    """Vérifie une valeur contre le sous-ensemble de JSON Schema utilisé par l'application."""
    erreurs = []
    if "enum" in schema and valeur not in schema["enum"]:
        erreurs.append(f"{chemin} : {valeur!r} hors énumération")
    t = schema.get("type")
    if t == "object":
        if not isinstance(valeur, dict):
            return [f"{chemin} : objet attendu"]
        erreurs += [f"{chemin} : champ manquant {k}" for k in schema.get("required", []) if k not in valeur]
        if schema.get("additionalProperties") is False:
            erreurs += [f"{chemin} : champ inattendu {k}" for k in valeur if k not in schema["properties"]]
        for k, v in valeur.items():
            if k in schema.get("properties", {}):
                erreurs += conforme(v, schema["properties"][k], f"{chemin}.{k}")
    elif t == "array":
        if not isinstance(valeur, list):
            return [f"{chemin} : liste attendue"]
        for i, v in enumerate(valeur):
            erreurs += conforme(v, schema["items"], f"{chemin}[{i}]")
    elif t == "string" and not isinstance(valeur, str):
        erreurs.append(f"{chemin} : texte attendu")
    elif t == "integer" and not isinstance(valeur, int):
        erreurs.append(f"{chemin} : entier attendu")
    elif t == "boolean" and not isinstance(valeur, bool):
        erreurs.append(f"{chemin} : booléen attendu")
    return erreurs


class BibliothequeTests(unittest.TestCase):
    def test_gabarits_et_roles_livres(self):
        self.assertEqual(set(BIB.gabarits), {"comex", "architecture", "pilotage", "go_no_go"})
        self.assertEqual(len(BIB.roles), 15)
        self.assertEqual(BIB.gabarits["architecture"].fonctions["president"]["nom"], "Animateur")

    def test_la_presselection_de_chaque_gabarit_couvre_tous_ses_criteres(self):
        for g in BIB.gabarits.values():
            with self.subTest(gabarit=g.id):
                portes = {normaliser(c) for r in g.selection_defaut for c in BIB.roles[r].criteres}
                self.assertEqual([c for c in g.criteres if normaliser(c) not in portes], [])

    def test_chaque_critere_de_role_sert_dans_un_gabarit(self):
        criteres_gabarits = {normaliser(c) for g in BIB.gabarits.values() for c in g.criteres}
        orphelins = {(r.id, c) for r in BIB.roles.values() for c in r.criteres
                     if normaliser(c) not in criteres_gabarits and c != "Délai de mise à disposition"}
        self.assertEqual(orphelins, set())        # « Délai de mise à disposition » : critère propre à une décision

    def test_initiales_distinctes_dans_chaque_comite_par_defaut(self):
        for g in BIB.gabarits.values():
            initiales = [BIB.roles[r].initiales for r in g.selection_defaut] + ["PR", "CO"]
            self.assertEqual(len(initiales), len(set(initiales)), g.id)


class DossierInferenceTests(unittest.TestCase):
    def setUp(self):
        self.dossier = charger_dossier(RACINE / "seances/plateforme-inference-llm")

    def test_dossier(self):
        self.assertFalse(self.dossier.confidentiel)
        self.assertEqual(self.dossier.gabarit, "architecture")
        self.assertLess(self.dossier.tokens_estimes, 15000)
        self.assertEqual(len(self.dossier.documents), 5)

    def test_scenarios_conformes_aux_schemas_et_au_dossier(self):
        scenarios = {"plateforme-inference-llm": INF, "assistant-ia-2027": simulation}
        for nom, sc in scenarios.items():
            dossier = charger_dossier(RACINE / "seances" / nom)
            with self.subTest(scenario=nom):
                for etape, contenu in (("cadrage", sc.CADRAGE), ("contre_analyse", sc.CONTRE_ANALYSE),
                                       ("objections", sc.OBJECTIONS), ("synthese", sc.SYNTHESE)):
                    self.assertEqual(conforme(contenu, SCHEMAS[etape]), [], etape)
                for agent, avis in sc.AVIS.items():
                    self.assertEqual(conforme(avis, SCHEMAS["avis"]), [], agent)
                for agent, rep in sc.REPONSES.items():
                    self.assertEqual(conforme(rep, SCHEMAS["reponse"]), [], agent)
                tout = [sc.CADRAGE, sc.AVIS, sc.CONTRE_ANALYSE, sc.OBJECTIONS, sc.REPONSES, sc.SYNTHESE]
                self.assertEqual(refs_citees(tout) - dossier.refs, set())          # aucune source inventée

    def test_scenario_coherent_avec_le_comite_d_architecture(self):
        g = BIB.gabarits["architecture"]
        self.assertEqual(set(INF.AVIS), set(g.selection_defaut))
        cibles = {o["cible"] for o in INF.OBJECTIONS["objections"]}
        self.assertLessEqual(cibles, set(g.selection_defaut))
        self.assertEqual(set(INF.REPONSES), cibles)
        ids = {o["id"] for o in INF.CADRAGE["options"]}
        self.assertEqual(ids, {"A", "B", "C"})                                   # dont l'option hybride
        for a in INF.AVIS.values():
            self.assertIn(a["option_preferee"], ids)
        for option in INF.SYNTHESE["options"]:
            self.assertEqual([c["critere"] for c in option["criteres"]], INF.CADRAGE["criteres"])
        usuels = [normaliser(c) for c in g.criteres]
        self.assertEqual([c for c in INF.CADRAGE["criteres"] if normaliser(c) not in usuels],
                         ["Délai de mise à disposition"])                          # critère ajouté par la décision

    def test_reponse_generique_pour_un_role_sans_scenario(self):
        avis = simulation.reponse("achats", "avis", "plateforme-inference-llm")
        self.assertEqual(avis["resume"], simulation.GENERIQUE)
        self.assertEqual(simulation.reponse("finance", "avis")["option_preferee"], "C")       # Altaris par défaut
        self.assertEqual(simulation.reponse("president", "cadrage", "dossier-inconnu")["question_reformulee"],
                         simulation.GENERIQUE)


class SeanceArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copytree(RACINE / "seances/plateforme-inference-llm", self.tmp / "plateforme-inference-llm")
        self.addCleanup(shutil.rmtree, self.tmp)
        p = patch.object(server, "DOSSIERS", self.tmp)
        p.start()
        self.addCleanup(p.stop)

    def _seance(self, **demande):
        async def scenario():
            cfg = config_simulation()
            server.ETAT.update(config=cfg, bibliotheque=None)
            G.MODELES = Modeles(cfg)
            server.ETAT["graphe"] = G.construire_graphe()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://t") as c:
                detail = (await c.get("/api/dossiers/plateforme-inference-llm")).json()
                sid = (await c.post("/api/seances", json={"dossier": "plateforme-inference-llm", **demande})).json()["seance_id"]
                while SEANCES[sid].statut != "attente_validation":
                    await asyncio.sleep(0.02)
                attente = next(e for e in SEANCES[sid].evenements if e["type"] == "attente_validation")
                await c.post(f"/api/seances/{sid}/validation", json={"action": "valider"})
                while SEANCES[sid].statut not in ("terminee", "erreur"):
                    await asyncio.sleep(0.02)
                for t in list(server.ETAT["taches"]):
                    await t
                etat = (await server.ETAT["graphe"].aget_state({"configurable": {"thread_id": sid}})).values
                return detail, attente, SEANCES[sid], etat
        return asyncio.run(scenario())

    def test_seance_complete_avec_le_comite_d_architecture(self):
        detail, attente, seance, etat = self._seance()
        self.assertEqual(detail["gabarit"], "architecture")
        self.assertEqual(detail["comite"]["agents"]["president"]["titre"], "Animateur du comité d'architecture")
        self.assertEqual(attente["criteres_sans_porteur"], [])
        self.assertEqual(seance.statut, "terminee")
        self.assertEqual(set(k.split(":")[0] for k in etat["avis"]), set(BIB.gabarits["architecture"].selection_defaut))
        self.assertTrue(etat["synthese"]["recommandation"].startswith("Retenir l'option C"))
        self.assertEqual({k.split(":")[0] for k in etat["reponses"]}, {o["cible"] for o in INF.OBJECTIONS["objections"]})

    def test_role_ajoute_sans_scenario_et_critere_couvert(self):
        selection = ["architecte_entreprise", "exploitation", "rssi", "finops", "achats"]
        _, attente, seance, etat = self._seance(selection=selection)
        self.assertEqual(seance.statut, "terminee")
        self.assertEqual(etat["avis"]["achats:initial"]["resume"], simulation.GENERIQUE)
        # Les Achats portent le critère ajouté par la décision ; l'avis des équipes produit, absent, n'est pas sollicité
        self.assertIn("achats", attente["couverture"]["Délai de mise à disposition"])
        self.assertNotIn("equipes_produit:initial", etat["avis"])
        self.assertLessEqual({o["cible"] for o in etat["objections"]}, set(selection))


if __name__ == "__main__":
    unittest.main()
