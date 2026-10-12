"""Lot 3 : révision de l'équipe à la validation, couverture des critères, contrôle du contexte,
contestation de la composition par le contradicteur."""
import asyncio
import copy
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import contexte as C
from app import graph as G
from app import server
from app.agents import SCHEMAS, prompt_cadrage, prompt_contre_analyse, schemas_seance
from app.comites import Bibliotheque, couverture_cadrage, difference, resoudre_composition
from app.config import charger_config
from app.dossier import charger_dossier
from app.llm import Modeles
from app.runtime import SEANCES, Seance
from app.simulation import CADRAGE, CONTRE_ANALYSE

RACINE = Path(__file__).resolve().parents[1]
BIB = Bibliotheque.charger(RACINE / "comites")
DOSSIER = charger_dossier(RACINE / "seances/assistant-ia-2027")


def config_simulation(**modeles):
    with patch.dict("os.environ", {"COMEX_MODE": "simulation"}):
        cfg = charger_config(RACINE / "config.yaml")
    cfg["protocole"]["simulation_duree"] = [0, 0]
    for nom, reglages in modeles.items():
        cfg["modeles"][nom].update(reglages)
    return cfg


class CouvertureTests(unittest.TestCase):
    def test_couverture_par_role_et_par_designation_limitee_aux_presents(self):
        cfg = config_simulation()
        c = resoudre_composition(BIB, cfg, "comex", selection=["finance", "risques", "operations"])
        couverture = couverture_cadrage(c, CADRAGE)
        self.assertEqual(couverture["Valeur démontrée"], ["finance"])            # clients absent : filtré
        self.assertEqual(couverture["Capacité d'exécution en 2027"], ["operations"])
        self.assertEqual(couverture["Acceptabilité sociale"], [])               # rh absent : sans porteur
        # Un critère usuel repris mot pour mot est couvert par le rôle, même sans désignation
        cadrage = {"criteres": ["Risques et conformité"], "porteurs_criteres": []}
        self.assertEqual(couverture_cadrage(c, cadrage), {"Risques et conformité": ["risques"]})

    def test_difference_entre_deux_compositions(self):
        cfg = config_simulation()
        avant = resoudre_composition(BIB, cfg, "comex")
        apres = resoudre_composition(BIB, cfg, "comex", selection=["finance", "risques", "operations", "rh"],
                                     affectations={"rh": "gpt_oss"}, mandats={"finance": "Trésorerie d'abord."})
        d = difference(avant, apres)
        self.assertEqual(d["retraits"], ["strategie", "clients"])
        self.assertEqual(d["ajouts"], [])
        self.assertEqual(d["modeles"], {"rh": ["gemma", "gpt_oss"]})
        self.assertEqual(d["mandats"], ["finance"])


class PromptsTests(unittest.TestCase):
    def test_cadrage_et_contre_analyse_recoivent_la_composition(self):
        c = resoudre_composition(BIB, config_simulation(), "comex", selection=["finance", "risques", "rh"])
        experts = [c.membre(e) for e in c.experts]
        cadrage = prompt_cadrage(c.membre("president"), SCHEMAS["cadrage"], "", experts, c.criteres_gabarit)
        self.assertIn("Alignement stratégique", cadrage)                       # critères usuels du gabarit
        self.assertIn("- rh : RH et organisation", cadrage)
        self.assertIn("porteurs_criteres", cadrage)
        contre = prompt_contre_analyse(c.membre("contradicteur"), SCHEMAS["contre_analyse"], CADRAGE, experts)
        self.assertIn("Conteste aussi la composition", contre)
        self.assertIn("- finance : Direction financière", contre)
        self.assertIn("critique_composition", SCHEMAS["contre_analyse"]["required"])
        self.assertIn("critique_composition", CONTRE_ANALYSE)
        self.assertIn("porteurs_criteres", SCHEMAS["cadrage"]["required"])


class ContexteTests(unittest.TestCase):
    def test_estimation_prudente_au_lancement(self):
        cfg = config_simulation()
        petit = C.estimer_seance(DOSSIER.texte, resoudre_composition(BIB, cfg, "comex", selection=["finance", "risques", "rh"]), cfg)
        grand = C.estimer_seance(DOSSIER.texte, resoudre_composition(BIB, cfg, "comex"), cfg)
        synthese = lambda e: next(x for x in e["etapes"] if x["etape"] == "synthese")
        self.assertLess(synthese(petit)["entree"], synthese(grand)["entree"])
        self.assertEqual(synthese(grand)["sortie"], cfg["protocole"]["synthese_max_tokens"])
        self.assertEqual(grand["niveau"], "ok")
        cfg_court = config_simulation(gemma={"contexte_max": 12000})
        serre = C.estimer_seance(DOSSIER.texte, resoudre_composition(BIB, cfg_court, "comex"), cfg_court)
        self.assertEqual(serre["niveau"], "depassement")
        self.assertEqual(serre["plus_exigeante"]["etape"], "synthese")

    def test_compactage_par_niveaux(self):
        avis = {"finance": {"position": "reserve", "option_preferee": "C", "resume": "r", "hypotheses": ["h"] * 20,
                            "arguments": [{"texte": "a", "sources": ["D1-§1"]}] * 8, "risques": ["x"] * 20}}
        n1, _, _ = C.compacter(1, avis)
        n2, _, _ = C.compacter(2, avis)
        self.assertNotIn("hypotheses", n1["finance"])
        self.assertEqual(len(n1["finance"]["arguments"]), 4)
        self.assertEqual(set(n2["finance"]), {"position", "option_preferee", "resume"})
        self.assertEqual(C.compacter(0, avis)[0], avis)

    def test_ajuster_choisit_le_premier_niveau_qui_tient(self):
        cfg = {"modeles": {"m": {"contexte_max": 1000, "max_tokens": 200}}, "protocole": {}}
        tailles = {0: 5000, 1: 3000, 2: 1000}          # ≈ 1430, 860 et 290 tokens estimés
        construire = lambda n: [{"role": "user", "content": "x" * tailles[n]}]
        r = C.ajuster(cfg, "m", "avis", construire)
        self.assertEqual((r["niveau"], r["tient"]), (2, True))
        r = C.ajuster(cfg, "m", "avis", construire, calibration=4.0)          # comptage réel quatre fois plus lourd
        self.assertFalse(r["tient"])


class SimulationTests(unittest.TestCase):
    def test_la_simulation_respecte_le_schema_de_la_seance(self):
        modeles = Modeles(config_simulation())
        schema = schemas_seance(["finance", "risques", "operations"])["objections"]
        async def objections():
            async def rien(*_):
                pass
            return await modeles.generer("contradicteur", "objections", [], rien, modele="gpt_oss",
                                         thinking=True, schema=schema)
        resultat, _ = asyncio.run(objections())
        self.assertTrue(resultat["objections"])
        self.assertTrue({o["cible"] for o in resultat["objections"]} <= {"finance", "risques", "operations"})


class FakeSeance:
    """Exécute une séance en simulation à travers l'API, jusqu'à un statut donné."""

    def __init__(self, cfg):
        self.cfg = cfg

    async def __aenter__(self):
        server.ETAT.update(config=self.cfg, bibliotheque=None)
        G.MODELES = Modeles(self.cfg)
        server.ETAT["graphe"] = G.construire_graphe()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://t")
        return self

    async def __aexit__(self, *exc):
        await self.client.aclose()
        for tache in list(server.ETAT["taches"]):
            await tache

    async def attendre(self, seance_id, *statuts):
        for _ in range(400):
            if SEANCES[seance_id].statut in statuts:
                return SEANCES[seance_id]
            await asyncio.sleep(0.02)
        raise AssertionError(f"statut {SEANCES[seance_id].statut}")


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.sauvegarde = patch.object(server, "_sauvegarder", lambda *a: None)
        self.sauvegarde.start()
        self.addCleanup(self.sauvegarde.stop)

    def test_revision_de_l_equipe_a_la_validation(self):
        async def scenario():
            async with FakeSeance(config_simulation()) as f:
                r = await f.client.post("/api/seances", json={"dossier": "assistant-ia-2027"})
                self.assertIn("contexte", r.json())
                sid = r.json()["seance_id"]
                seance = await f.attendre(sid, "attente_validation")
                attente = next(e for e in seance.evenements if e["type"] == "attente_validation")
                self.assertEqual(attente["criteres_sans_porteur"], [])
                self.assertEqual(attente["comite"]["experts"], ["strategie", "finance", "clients", "operations", "risques", "rh"])

                # Révision invalide : la séance attend toujours
                r = await f.client.post(f"/api/seances/{sid}/validation",
                                        json={"composition_revisee": {"selection": ["strategie", "clients", "rh"]}})
                self.assertEqual(r.status_code, 422)
                self.assertEqual(seance.statut, "attente_validation")

                # Révision valide : 4 experts, RH sur un autre modèle
                r = await f.client.post(f"/api/seances/{sid}/validation", json={
                    "action": "valider", "composition_revisee": {
                        "selection": ["finance", "risques", "operations", "rh"],
                        "affectations": {"rh": "gpt_oss"}, "motif": "Décision budgétaire, pas commerciale."}})
                self.assertEqual(r.json(), {"ok": True, "revision": True})
                seance = await f.attendre(sid, "terminee", "erreur")
                etat = (await server.ETAT["graphe"].aget_state({"configurable": {"thread_id": sid}})).values
                self.assertEqual({k.split(":")[0] for k in etat["avis"]}, {"finance", "risques", "operations", "rh"})
                self.assertEqual(len(etat["revisions_composition"]), 1)
                self.assertEqual(etat["revisions_composition"][0]["retraits"], ["strategie", "clients"])
                self.assertEqual(etat["revisions_composition"][0]["modeles"], {"rh": ["gemma", "gpt_oss"]})
                self.assertEqual(seance.revisions[0]["motif"], "Décision budgétaire, pas commerciale.")
                self.assertEqual(seance.composition.membre("rh").modele, "gpt_oss")
                evt = next(e for e in seance.evenements if e["type"] == "composition_revisee")
                self.assertEqual(evt["criteres_sans_porteur"], [])           # rh et finance couvrent encore tout
                # L'équipe est définitive : la revalidation est refusée une fois les analyses lancées
                r = await f.client.post(f"/api/seances/{sid}/validation", json={"action": "valider"})
                self.assertEqual(r.status_code, 409)
        asyncio.run(scenario())

    def test_revision_bloquee_si_elle_viole_la_confidentialite(self):
        cfg = config_simulation()
        cfg["mode"] = "reel"
        cfg["modeles"]["gemma"]["hebergement"] = "interne"
        cfg["modeles"]["gpt_oss"]["hebergement"] = "externe"
        dossier = copy.copy(DOSSIER)
        dossier.confidentiel = True
        seance = Seance(id="conf0001", dossier=dossier)

        async def scenario():
            async with FakeSeance(cfg) as f:
                interne = {m: "gemma" for m in ["president", "contradicteur", "finance", "risques", "operations"]}
                seance.fixer_composition(resoudre_composition(BIB, cfg, "comex", selection=["finance", "risques", "operations"],
                                                              affectations=interne))
                seance.statut = "attente_validation"
                SEANCES[seance.id] = seance
                r = await f.client.post(f"/api/seances/{seance.id}/validation", json={"composition_revisee": {
                    "selection": ["finance", "risques", "operations"], "affectations": {**interne, "finance": "gpt_oss"}}})
                self.assertEqual(r.status_code, 403)
                self.assertEqual(seance.composition.membre("finance").modele, "gemma")
                self.assertEqual(seance.revisions, [])
        try:
            asyncio.run(scenario())
        finally:
            SEANCES.pop("conf0001", None)


class ControleSyntheseTests(unittest.TestCase):
    def setUp(self):
        self.sauvegarde = patch.object(server, "_sauvegarder", lambda *a: None)
        self.sauvegarde.start()
        self.addCleanup(self.sauvegarde.stop)

    def _seance(self, contexte_max):
        async def scenario():
            # Seul le président (gemma) voit son contexte réduit : cadrage et avis passent, la synthèse est contrôlée
            cfg = config_simulation(gemma={"contexte_max": contexte_max})
            cfg["affectation"]["par_role"] = {}
            cfg["affectation"]["experts"] = ["gpt_oss"]
            async with FakeSeance(cfg) as f:
                sid = (await f.client.post("/api/seances", json={"dossier": "assistant-ia-2027"})).json()["seance_id"]
                await f.attendre(sid, "attente_validation")
                await f.client.post(f"/api/seances/{sid}/validation", json={"action": "valider"})
                seance = await f.attendre(sid, "terminee", "erreur")
                etat = (await server.ETAT["graphe"].aget_state({"configurable": {"thread_id": sid}})).values
                return seance, etat
        return asyncio.run(scenario())

    def test_synthese_compactee_quand_le_prompt_complet_ne_tient_pas(self):
        # Prompt de synthèse ≈ 7 900 tokens intégral, ≈ 7 300 compacté (niveau 1), + 6 144 de sortie
        seance, etat = self._seance(contexte_max=14500)
        self.assertEqual(seance.statut, "terminee")
        compactage = next(e for e in seance.evenements if e["type"] == "contexte_compacte")
        self.assertEqual(compactage["etape"], "synthese")
        self.assertGreaterEqual(compactage["niveau"], 1)
        self.assertEqual(etat["compactages"][0]["etape"], "synthese")

    def test_arret_explicite_si_meme_compacte_le_prompt_depasse(self):
        # Même au niveau 2 (≈ 5 600 tokens), prompt + sortie dépassent 95 % de 10 500
        seance, etat = self._seance(contexte_max=10500)
        self.assertEqual(seance.statut, "erreur")
        self.assertNotIn("synthese", etat)
        depasse = next(e for e in seance.evenements if e["type"] == "contexte_depasse")
        self.assertIn("dépassent le contexte", depasse["message"])
        self.assertFalse(any(e["type"] == "contribution" and e["etape"] == "synthese" for e in seance.evenements))


if __name__ == "__main__":
    unittest.main()
