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
from app.llm import Modeles, Sortie
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
        self.schemas: dict = {}

    async def generer(self, agent_id, etape, messages, on_flux, schema=None):
        self.appels.append((agent_id, etape, messages[-1]["content"]))
        self.schemas[(agent_id, etape)] = schema
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
        with self.assertRaisesRegex(ErreurModele, "contexte effectif 4096"):
            asyncio.run(modeles.generer("president", "synthese", messages, None))


class ModelesFlux(Modeles):
    """Mode réel sans réseau : les textes renvoyés par le « serveur » sont fournis à l'avance."""

    def __init__(self, textes: list[str]):
        config = charger_config()
        config["mode"] = "reel"
        super().__init__(config)
        self.textes, self.messages_recus, self.reflexions = list(textes), [], []

    async def _flux(self, client, modele, thinking, etape, messages, mode, on_flux, schema):
        self.messages_recus.append(messages)
        self.reflexions.append(thinking)
        texte = self.textes.pop(0)
        if isinstance(texte, Sortie):
            return texte
        return Sortie(contenu=texte, fin="stop", usage={"tokens_entree": 10, "tokens_sortie": 10})


class SchemaTests(unittest.TestCase):
    """Cas observés lors de la séance réelle 51538050 (Nemotron, contexte 32k)."""

    def setUp(self):
        import json
        self.json = json
        bonne = simulation.reponse("president", "synthese")
        mauvaise = {("recommendation" if k == "recommandation" else k): v for k, v in bonne.items()}
        self.bonne, self.mauvaise = bonne, mauvaise

    def test_un_champ_traduit_est_un_ecart_au_schema(self):
        from app.agents import ecarts_schema, schema_synthese
        schema = schema_synthese(simulation.CADRAGE)
        self.assertEqual(ecarts_schema(self.bonne, schema), [])
        ecarts = ecarts_schema(self.mauvaise, schema)
        self.assertIn("réponse : champ obligatoire « recommandation » absent", ecarts)
        self.assertIn("réponse : champ inconnu « recommendation »", ecarts)

    def test_une_reponse_non_conforme_est_relancee_puis_acceptee(self):
        modeles = ModelesFlux([self.json.dumps(self.mauvaise), self.json.dumps(self.bonne)])
        messages = [{"role": "system", "content": "dossier"}, {"role": "user", "content": "synthèse"}]
        resultat, _ = asyncio.run(modeles.generer("president", "synthese", messages, None))
        self.assertIn("recommandation", resultat)
        relance = modeles.messages_recus[1][-1]["content"]
        self.assertIn("ne respecte pas le schéma", relance)
        self.assertIn("« recommandation » absent", relance)

    def test_deux_reponses_non_conformes_sont_une_erreur(self):
        from app.llm import ErreurModele
        modeles = ModelesFlux([self.json.dumps(self.mauvaise)] * 2)
        with self.assertRaisesRegex(ErreurModele, "non conforme au schéma après deux tentatives"):
            asyncio.run(modeles.generer("president", "synthese", [{"role": "user", "content": "x"}], None))

    def test_la_synthese_est_contrainte_par_le_cadrage(self):
        from app.agents import prompt_synthese, schema_synthese
        schema = schema_synthese(simulation.CADRAGE)
        option = schema["properties"]["options"]["items"]["properties"]
        self.assertEqual(option["id"]["enum"], ["A", "B", "C"])
        self.assertEqual(option["criteres"]["items"]["properties"]["critere"]["enum"], simulation.CADRAGE["criteres"])
        self.assertNotIn("enum", SCHEMAS_SYNTHESE_ID())             # le schéma générique n'est pas modifié
        self.assertIn('"Valeur démontrée"', prompt_synthese(simulation.CADRAGE, {}, {}, [], {}))
        modeles = ModelesTest()
        derouler(modeles, "sc000001")
        self.assertEqual(modeles.schemas[("president", "synthese")], schema)

    def test_les_differences_typographiques_ne_sont_pas_des_lacunes(self):
        from app.agents import normaliser
        self.assertEqual(normaliser("Taux d’adoption réel hors pilote"), normaliser("Taux d'adoption réel  hors pilote"))
        self.assertEqual(normaliser("Budget IT 2027 ≤ 2 M€"), normaliser("budget it 2027 ≤ 2 M€"))

    def test_les_reponses_de_simulation_respectent_leur_schema(self):
        from app.agents import SCHEMAS, ecarts_schema, schema_synthese
        for etape in ("cadrage", "contre_analyse", "objections", "replique"):
            self.assertEqual(ecarts_schema(simulation.reponse("contradicteur", etape), SCHEMAS[etape]), [], etape)
        for agent in EXPERTS:
            for etape in ("avis", "reponse"):
                self.assertEqual(ecarts_schema(simulation.reponse(agent, etape), SCHEMAS[etape]), [], (agent, etape))
        self.assertEqual(ecarts_schema(self.bonne, schema_synthese(simulation.CADRAGE)), [])


class ReflexionTests(unittest.TestCase):
    """Cas observés lors des séances réelles 5a4904c3 et c15324b9 (Nemotron « thinking » sous Ollama)."""

    def setUp(self):
        import json
        self.json = json
        self.cadrage = json.dumps(simulation.CADRAGE, ensure_ascii=False)
        self.messages = [{"role": "user", "content": "cadrage"}]

    def test_json_ecrit_dans_la_reflexion_est_recupere(self):
        # 5a4904c3 : réponse vide, JSON complet dans le canal de réflexion (précédé d'une ébauche inachevée)
        reflexion = 'Ébauche : {"question_reformulee": "brouillon", "options": [{"id": "A"' + "\n" + self.cadrage
        modeles = ModelesFlux([Sortie(contenu="", raisonnement=reflexion, fin="stop")])
        resultat, _ = asyncio.run(modeles.generer("president", "cadrage", self.messages, None))
        self.assertEqual(resultat, simulation.CADRAGE)
        self.assertEqual(len(modeles.messages_recus), 1)

    def test_reponse_coupee_relancee_sans_reflexion(self):
        # c15324b9 : la réflexion épuise max_tokens et la réponse s'arrête au milieu du JSON
        coupee = Sortie(contenu='{"question_reformulee": "Quel', raisonnement="We need to produce JSON... " * 200,
                        fin="length")
        modeles = ModelesFlux([coupee, self.cadrage])
        resultat, usage = asyncio.run(modeles.generer("president", "cadrage", self.messages, None))
        self.assertEqual(resultat, simulation.CADRAGE)
        self.assertEqual(modeles.reflexions, [True, False])          # président : thinking true dans config.yaml
        self.assertTrue(usage["reflexion_coupee"])

    def test_deux_reponses_coupees_donnent_une_cause_exacte(self):
        from app.llm import ErreurModele
        coupee = Sortie(contenu='{"recommandation": "Option', raisonnement="x" * 33083, fin="length")
        modeles = ModelesFlux([coupee, coupee])
        with self.assertRaisesRegex(ErreurModele, r"budget de sortie de 6144 tokens ou fenêtre de contexte atteint, dont 33083 caractères de réflexion"):
            asyncio.run(modeles.generer("president", "synthese", self.messages, None))

    def test_thinking_false_coupe_la_reflexion_d_ollama(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        async def flux_vide():
            if False:
                yield None

        async def parametres(famille, thinking):
            create = AsyncMock(side_effect=lambda **kw: flux_vide())
            client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
            modeles = object.__new__(Modeles)
            modeles.config = {"protocole": {}}
            await modeles._flux(client, {"model": "m", "famille": famille}, thinking, "avis", [], "consigne", None)
            return create.call_args.kwargs

        self.assertEqual(asyncio.run(parametres("ollama", False))["reasoning_effort"], "none")
        self.assertNotIn("reasoning_effort", asyncio.run(parametres("ollama", True)))
        qwen = asyncio.run(parametres("qwen", False))
        self.assertNotIn("reasoning_effort", qwen)
        self.assertFalse(qwen["extra_body"]["chat_template_kwargs"]["enable_thinking"])

    def test_dernier_json_ignore_les_fragments(self):
        from app.agents import SCHEMAS
        from app.llm import dernier_json
        texte = 'Plan {"a": 1} puis brouillon {"question_reformulee": "x", "options": [{"id": "A"}] … fin'
        self.assertIsNone(dernier_json(texte, SCHEMAS["cadrage"]))
        self.assertEqual(dernier_json(texte), {"id": "A"})
        self.assertEqual(dernier_json("aucun JSON"), None)


class ModelesLents(ModelesFlux):
    """Le « serveur » met `lenteurs[i]` secondes à répondre à la tentative i."""

    def __init__(self, textes, lenteurs):
        super().__init__(textes)
        self.lenteurs = list(lenteurs)
        self.config["protocole"]["delai_max_appel"] = 0.2

    async def _flux(self, *args, **kwargs):
        await asyncio.sleep(self.lenteurs.pop(0))
        return await super()._flux(*args, **kwargs)


class SeanceLongueTests(unittest.TestCase):
    """Cas observés lors des séances réelles e8cb440a (Ministral, 4 h 31) et 1d11bf86 / b4ef4fb4."""

    def setUp(self):
        import json
        self.cadrage = json.dumps(simulation.CADRAGE, ensure_ascii=False)
        self.messages = [{"role": "user", "content": "cadrage"}]

    def test_un_appel_trop_long_est_interrompu_puis_relance(self):
        modeles = ModelesLents([self.cadrage], lenteurs=[5, 0])
        resultat, _ = asyncio.run(modeles.generer("president", "cadrage", self.messages, None))
        self.assertEqual(resultat, simulation.CADRAGE)

    def test_deux_appels_trop_longs_sont_une_erreur(self):
        from app.llm import ErreurModele
        modeles = ModelesLents([], lenteurs=[5, 5])
        with self.assertRaisesRegex(ErreurModele, "Délai maximal de 0.2 s dépassé deux fois"):
            asyncio.run(modeles.generer("president", "cadrage", self.messages, None))

    def test_une_fenetre_pleine_est_nommee(self):
        from app.llm import ErreurModele
        coupee = Sortie(contenu='{"recommandation": "A', fin="length",
                        usage={"tokens_entree": 14162, "tokens_sortie": 2200})
        modeles = ModelesFlux([coupee, coupee])
        modeles.contextes_reels["gemma"] = 16384
        modeles.config["agents"]["president"]["modele"] = "gemma"
        with self.assertRaisesRegex(ErreurModele, r"fenêtre de contexte de gemma pleine \(14162 tokens lus \+ 2200"):
            asyncio.run(modeles.generer("president", "cadrage", self.messages, None))

    def test_le_vrai_num_ctx_d_ollama_l_emporte_sur_contexte_max(self):
        from unittest.mock import MagicMock, patch
        from app import llm

        def show(url, json, timeout):
            reponse = MagicMock()
            reponse.json.return_value = {"parameters": "num_ctx 16384\ntemperature 1"} \
                if "16k" in json["model"] else {"parameters": "temperature 1"}
            return reponse

        config = charger_config()
        config["mode"] = "reel"
        config["modeles"] = {"g": {"libelle": "G", "famille": "ollama", "base_url": "http://x:11434/v1",
                                   "model": "gemma-16k", "contexte_max": 32768, "hebergement": "interne"},
                             "n": {"libelle": "N", "famille": "ollama", "base_url": "http://x:11434/v1",
                                   "model": "nemotron", "contexte_max": 32768, "hebergement": "interne"}}
        config["agents"] = {"president": {"modele": "g"}, "finance": {"modele": "n"}}
        modeles = llm.Modeles(config)
        with patch.object(llm.httpx, "post", side_effect=show):
            messages = modeles.verifier_contextes()
        self.assertEqual(modeles.contexte_de("g", config["modeles"]["g"]), 16384)
        self.assertEqual(modeles.contexte_de("n", config["modeles"]["n"]), 4096)
        self.assertIn("contexte_max 32768 déclaré, mais Ollama applique 16384", " ".join(messages))
        self.assertIn("valeur par défaut d'Ollama supposée", " ".join(messages))
        self.assertEqual(llm.contexte_ollama("http://127.0.0.1:9/v1", "m", delai=0.2)[0], None)   # serveur absent

    def test_references_avec_trait_d_union_insecable(self):
        from app.dossier import refs_citees
        from app.graph import ecarts_synthese
        dossier = charger_dossier(DOSSIER)
        self.assertEqual(refs_citees("capacité inconnue [D0‑§6] et [D1–§2]"), {"D0-§6", "D1-§2"})
        self.assertIsNotNone(dossier.section("D0‑§6"))
        synthese = simulation.reponse("president", "synthese")
        synthese["options"][0]["preuves"][0]["sources"] = ["D0‑§6"]
        self.assertEqual(ecarts_synthese(synthese, simulation.CADRAGE, dossier.refs), [])

    def test_le_president_recoit_les_positions_finales(self):
        from app.graph import positions_des_membres
        modeles = ModelesTest()
        etat = derouler(modeles, "pos00001")
        positions = positions_des_membres(etat)
        self.assertEqual(set(positions), set(EXPERTS))
        self.assertEqual(positions["finance"]["option_initiale"], simulation.AVIS["finance"]["option_preferee"])
        self.assertEqual(positions["finance"]["option_finale"], simulation.REPONSES["finance"]["option_revisee"])
        consigne = next(c for a, e, c in modeles.appels if e == "synthese")
        self.assertIn("Positions des membres, initiales et finales", consigne)


class CondensationTests(unittest.TestCase):
    """Séance 5992576d : message de synthèse à 92 % de la fenêtre de contexte."""

    def test_avis_et_reponses_condenses_pour_la_synthese(self):
        from app.agents import condenser_avis, condenser_reponses
        avis = condenser_avis({"finance": {**simulation.AVIS["finance"], "resume": "L'**Option A** est viable."}})
        self.assertEqual(set(avis["finance"]), {"position", "option_preferee", "confiance", "resume", "arguments",
                                                "conditions_changement"})
        self.assertEqual(avis["finance"]["resume"], "L'Option A est viable.")
        long_accord = "Je suis d'accord avec la stratégie. " + "Développement très long. " * 30
        reponses = condenser_reponses({"rh": {**simulation.REPONSES["rh"], "reactions_pairs": [
            {"membre": "strategie", "accord": "accord", "texte": long_accord},
            {"membre": "finance", "accord": "desaccord", "texte": long_accord},
            {"membre": "rh", "accord": "accord", "texte": "Je suis d'accord avec moi-même."}]}})
        reactions = reponses["rh"]["reactions_pairs"]
        self.assertEqual([x["membre"] for x in reactions], ["strategie", "finance"])   # réaction à soi retirée
        self.assertEqual(reactions[0]["texte"], "Je suis d'accord avec la stratégie.")
        self.assertEqual(reactions[1]["texte"], long_accord)                           # désaccord gardé en entier

    def test_le_message_de_synthese_est_plus_court(self):
        import json
        from app.agents import _role, prompt_synthese
        from app.dossier import estimer_tokens
        avis = {e: simulation.AVIS[e] for e in EXPERTS}
        reponses = {e: simulation.REPONSES[e] for e in EXPERTS}
        brut = sum(estimer_tokens(json.dumps(x, ensure_ascii=False, indent=1)) for x in (avis, reponses))
        prompt = prompt_synthese(simulation.CADRAGE, avis, simulation.CONTRE_ANALYSE,
                                 simulation.OBJECTIONS["objections"], reponses)
        self.assertLess(estimer_tokens(prompt) - estimer_tokens(_role("president")), brut + 2500)
        self.assertNotIn('"hypotheses"', prompt.split("## Contre-analyse")[0])


def SCHEMAS_SYNTHESE_ID():
    from app.agents import SCHEMAS
    return SCHEMAS["synthese"]["properties"]["options"]["items"]["properties"]["id"]


if __name__ == "__main__":
    unittest.main()
