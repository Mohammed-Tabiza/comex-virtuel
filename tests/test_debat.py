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

    def __init__(self, pannes=(), remplacements=None, tronques=()):
        config = charger_config()
        config["mode"] = "simulation"
        config["protocole"]["simulation_duree"] = [0, 0]
        super().__init__(config)
        self.pannes = set(pannes)                  # {(agent, etape)}
        self.remplacements = remplacements or {}   # {(agent, etape): [réponse 1, réponse 2...]}
        self.tronques = set(tronques)              # {(agent, etape)} : le « serveur » coupe le message
        self.appels: list[tuple[str, str, str]] = []

    async def generer(self, agent_id, etape, messages, on_flux):
        self.appels.append((agent_id, etape, messages[-1]["content"]))
        if (agent_id, etape) in self.pannes:
            raise RuntimeError("panne simulée")
        meta = {"modele": "test", "libelle": "Test", "duree": 0}
        if (agent_id, etape) in self.tronques:
            meta |= {"simulation": False, "tokens_estimes": 15462, "tokens_entree": 2050}
        file = self.remplacements.get((agent_id, etape))
        if file:
            return file.pop(0), meta
        return simulation.reponse(agent_id, etape), meta


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


class GardeFousTests(unittest.TestCase):
    """Cas observés lors de la séance réelle 7a178548 (Nemotron limité à 4 096 tokens par Ollama)."""

    def test_un_message_tronque_rend_la_seance_incomplete(self):
        modeles = ModelesTest(tronques={("president", "synthese"), ("risques", "reponse")})
        etat = derouler(modeles, "gf000001")
        lacunes = " ".join(etat["lacunes"])
        self.assertIn("Message tronqué : Président (synthèse pour le décideur) n'a lu que 2050 tokens sur ~15462", lacunes)
        self.assertIn("Message tronqué : Risques (réponse aux objections)", lacunes)
        self.assertEqual(statut_final(etat), "incomplete")

    def test_une_reponse_qui_ignore_ses_objections_est_une_lacune(self):
        hors_sujet = {"reponses": [{"objection_id": "objection_1", "attitude": "maintient", "texte": "..."}],
                      "reactions_pairs": [], "position_revisee": "favorable_sous_conditions",
                      "option_revisee": "C", "resume": "..."}
        modeles = ModelesTest(remplacements={("operations", "reponse"): [hors_sujet]})
        etat = derouler(modeles, "gf000002")
        self.assertIn("Opérations n'a pas répondu à O3 (identifiants cités à la place : objection_1).", etat["lacunes"])
        self.assertEqual(statut_final(etat), "incomplete")

    def test_une_synthese_qui_omet_des_options_ou_cite_hors_dossier_est_signalee(self):
        incomplete = simulation.reponse("president", "synthese")
        option_c = incomplete["options"][2]
        option_c["criteres"] = option_c["criteres"][:2]
        option_c["preuves"][0]["sources"] = ["Réplique du contradicteur O2"]
        incomplete["options"] = [option_c]
        etat = derouler(ModelesTest(remplacements={("president", "synthese"): [incomplete]}), "gf000003")
        lacunes = " ".join(etat["lacunes"])
        self.assertIn("La synthèse n'évalue pas l'option A", lacunes)
        self.assertIn("La synthèse n'évalue pas l'option B", lacunes)
        self.assertIn("La synthèse n'évalue pas l'option C sur : Capacité d'exécution en 2027", lacunes)
        self.assertIn("1 source(s) qui ne sont pas des sections du dossier : Réplique du contradicteur O2", lacunes)

    def test_la_synthese_de_simulation_passe_les_controles(self):
        from app.graph import ecarts_synthese
        refs = charger_dossier(DOSSIER).refs
        self.assertEqual(ecarts_synthese(simulation.SYNTHESE, simulation.CADRAGE, refs), [])

    def test_un_appel_qui_depasse_contexte_max_est_refuse_avant_envoi(self):
        from app.llm import ErreurModele
        config = charger_config()
        config["mode"] = "reel"
        for m in config["modeles"].values():
            m["contexte_max"] = 4096
        modeles = Modeles(config)
        messages = [{"role": "system", "content": "x" * 20000}, {"role": "user", "content": "y"}]
        with self.assertRaisesRegex(ErreurModele, "contexte_max 4096"):
            asyncio.run(modeles.generer("president", "synthese", messages, None))


if __name__ == "__main__":
    unittest.main()
