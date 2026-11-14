"""Tests du protocole de débat : couverture des objections, échanges entre pairs, réplique, lacunes."""
import asyncio
import unittest
from pathlib import Path

from langgraph.types import Command

from app import graph as G
from app import simulation
from app.agents import EXPERTS, prompt_reponse
from app.config import charger_config
from app.dossier import charger_dossier
from app.llm import Modeles
from app.rapport import compte_rendu
from app.runtime import SEANCES, Seance
from app.server import statut_final

DOSSIER = Path(__file__).resolve().parents[1] / "seances/assistant-ia-2027"


class ModelesTest(Modeles):
    """Simulation instantanée, avec pannes et réponses de remplacement injectables."""

    def __init__(self, pannes=(), remplacements=None):
        config = charger_config()
        config["mode"] = "simulation"
        config["protocole"]["simulation_duree"] = [0, 0]
        super().__init__(config)
        self.pannes = set(pannes)                  # {(agent, etape)}
        self.remplacements = remplacements or {}   # {(agent, etape): [réponse 1, réponse 2...]}
        self.appels: list[tuple[str, str, str]] = []

    async def generer(self, agent_id, etape, messages, on_flux):
        self.appels.append((agent_id, etape, messages[-1]["content"]))
        if (agent_id, etape) in self.pannes:
            raise RuntimeError("panne simulée")
        file = self.remplacements.get((agent_id, etape))
        if file:
            return file.pop(0), {"modele": "test", "libelle": "Test", "duree": 0}
        return simulation.reponse(agent_id, etape), {"modele": "test", "libelle": "Test", "duree": 0}


def derouler(modeles: ModelesTest, seance_id: str) -> dict:
    async def run():
        G.MODELES = modeles
        SEANCES[seance_id] = Seance(id=seance_id, dossier=charger_dossier(DOSSIER))
        graphe = G.construire_graphe()
        cfg = {"configurable": {"thread_id": seance_id}}
        await graphe.ainvoke({"complement": ""}, cfg)
        await graphe.ainvoke(Command(resume={"action": "valider"}), cfg)
        return (await graphe.aget_state(cfg)).values
    return asyncio.run(run())


class DebatTests(unittest.TestCase):
    def test_seance_complete_tous_les_experts_sont_challenges_et_repondent(self):
        modeles = ModelesTest()
        etat = derouler(modeles, "deb00001")
        self.assertEqual({o["cible"] for o in etat["objections"]}, set(EXPERTS))
        self.assertEqual({c.split(":")[0] for c in etat["reponses"]}, set(EXPERTS))
        self.assertEqual({e["objection_id"] for e in etat["replique"]["evaluations"]},
                         {o["id"] for o in etat["objections"]})
        self.assertEqual(etat["lacunes"], [])
        self.assertEqual(statut_final(etat), "terminee")
        # Une seule passe d'objections suffit quand la couverture est complète
        self.assertEqual(sum(1 for a, e, _ in modeles.appels if e == "objections"), 1)
        # Ordre du protocole : réponses, puis réplique, puis synthèse
        etapes = [e for _, e, _ in modeles.appels]
        self.assertLess(max(i for i, e in enumerate(etapes) if e == "reponse"), etapes.index("replique"))
        self.assertLess(etapes.index("replique"), etapes.index("synthese"))

    def test_les_experts_oublies_sont_challenges_par_une_relance(self):
        premiere = {"objections": [o for o in simulation.OBJECTIONS["objections"] if o["cible"] not in ("risques", "clients")]
                    + [{"id": "O9", "cible": "Inconnu", "texte": "x", "sources": []}]}
        relance = {"objections": [{"id": "O1", "cible": "Risques", "texte": "Objection complémentaire.", "sources": []},
                                  {"id": "O8", "cible": "clients", "texte": "Autre objection.", "sources": []}]}
        modeles = ModelesTest(remplacements={("contradicteur", "objections"): [premiere, relance]})
        etat = derouler(modeles, "deb00002")
        objections = etat["objections"]
        self.assertEqual({o["cible"] for o in objections}, set(EXPERTS))
        self.assertEqual(len({o["id"] for o in objections}), len(objections))      # identifiants uniques
        appels = [c for a, e, c in modeles.appels if e == "objections"]
        self.assertEqual(len(appels), 2)
        self.assertIn("non encore challengés", appels[1])
        self.assertTrue(any("cible inconnue" in x for x in etat["erreurs"]))       # l'objection écartée est tracée

    def test_chaque_expert_voit_les_avis_des_autres_au_second_tour(self):
        modeles = ModelesTest()
        derouler(modeles, "deb00003")
        consigne_finance = next(c for a, e, c in modeles.appels if a == "finance" and e == "reponse")
        self.assertIn("Avis initiaux des autres membres", consigne_finance)
        for autre in ("strategie", "risques", "rh"):
            self.assertIn(f'"{autre}"', consigne_finance)
        self.assertIn("reactions_pairs", prompt_reponse("rh", {}, [], {}))

    def test_une_seance_degradee_est_signalee_incomplete(self):
        modeles = ModelesTest(pannes={("operations", "avis"), ("contradicteur", "contre_analyse"),
                                      ("contradicteur", "objections")})
        etat = derouler(modeles, "deb00004")
        self.assertTrue(etat.get("synthese"))
        self.assertEqual(statut_final(etat), "incomplete")
        lacunes = " ".join(etat["lacunes"])
        self.assertIn("Opérations n'a pas rendu d'avis", lacunes)
        self.assertIn("contre-analyse", lacunes)
        self.assertIn("débat contradictoire n'a pas eu lieu", lacunes)
        consigne_synthese = next(c for a, e, c in modeles.appels if e == "synthese")
        self.assertIn("Lacunes du débat", consigne_synthese)
        rapport = compte_rendu(SEANCES["deb00004"], etat)
        self.assertIn("Débat incomplet", rapport)

    def test_un_expert_qui_ne_repond_pas_est_une_lacune(self):
        modeles = ModelesTest(pannes={("rh", "reponse"), ("contradicteur", "replique")})
        etat = derouler(modeles, "deb00005")
        self.assertIn("RH n'a pas répondu aux objections qui lui étaient adressées.", etat["lacunes"])
        self.assertTrue(any("réplique" in x for x in etat["lacunes"]))
        self.assertEqual(statut_final(etat), "incomplete")

    def test_le_rapport_montre_replique_et_echanges(self):
        etat = derouler(ModelesTest(), "deb00006")
        rapport = compte_rendu(SEANCES["deb00006"], etat)
        self.assertIn("Réplique du contradicteur — **non résolue**", rapport)
        self.assertIn("## Échanges entre membres", rapport)


if __name__ == "__main__":
    unittest.main()
