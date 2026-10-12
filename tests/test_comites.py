"""Lot 1 : bibliothèque de rôles, gabarits et résolution de la composition d'une séance."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agents import AGENTS, EXPERTS
from app.comites import (Bibliotheque, Composition, ErreurBibliotheque, ErreurComposition,
                         modeles_externes, normaliser, normaliser_affectation, resoudre_composition)
from app.config import charger_config

RACINE = Path(__file__).resolve().parents[1]


def config(*modeles, **affectation):
    """Configuration minimale : modeles = (nom, lignee, hebergement)."""
    return {"modeles": {n: {"libelle": n.upper(), "lignee": l, "hebergement": h} for n, l, h in modeles},
            "affectation": affectation}


DEUX = config(("a", "qwen", "interne"), ("b", "mistral", "externe"),
              president="a", contradicteur="a", experts=["a", "b"])


class BibliothequeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.rep = self.tmp / "comites"
        shutil.copytree(RACINE / "comites", self.rep)
        self.addCleanup(shutil.rmtree, self.tmp)

    def ecrire(self, chemin: str, texte: str):
        (self.rep / chemin).write_text(texte, encoding="utf-8")

    def test_la_bibliotheque_livree_est_valide(self):
        bib = Bibliotheque.charger(RACINE / "comites")
        self.assertEqual(set(bib.fonctions), {"president", "contradicteur"})
        self.assertIn("comex", bib.gabarits)
        catalogue = bib.catalogue()
        self.assertIn("Question clé", catalogue["roles"]["finance"]["mandat_complet"])
        json.dumps(catalogue)                                     # sérialisable pour l'API

    def test_erreurs_de_role(self):
        cas = {
            "roles/finance.yaml": ("id: autre\nversion: 1\nnom: F\ntitre: F\ninitiales: F\nmandat: m\ncriteres: [x]\n", "identifiant"),
            "roles/rh.yaml": ("id: rh\nversion: 0\nnom: R\ntitre: R\ninitiales: R\nmandat: m\ncriteres: [x]\n", "version"),
            "roles/clients.yaml": ("id: clients\nversion: 1\nnom: C\ntitre: C\ninitiales: C\nmandat: m\n", "critère"),
            "roles/strategie.yaml": ("id: strategie\nversion: 1\nnom: S\ntitre: S\ninitiales: S\n", "mandat"),
            "roles/operations.yaml": ("id: operations\nversion: 1\nnom: O\ntitre: O\ninitiales: OPSX\nmandat: m\ncriteres: [x]\n", "initiales"),
        }
        for fichier, (contenu, attendu) in cas.items():
            with self.subTest(fichier=fichier):
                original = (self.rep / fichier).read_text(encoding="utf-8")
                self.ecrire(fichier, contenu)
                with self.assertRaisesRegex(ErreurBibliotheque, attendu):
                    Bibliotheque.charger(self.rep)
                self.ecrire(fichier, original)

    def test_un_role_ne_peut_pas_usurper_une_fonction_du_protocole(self):
        self.ecrire("roles/president.yaml", "id: president\nversion: 1\nnom: P\ntitre: P\ninitiales: P\nmandat: m\ncriteres: [x]\n")
        with self.assertRaisesRegex(ErreurBibliotheque, "réservés"):
            Bibliotheque.charger(self.rep)

    def test_fonction_du_protocole_manquante(self):
        (self.rep / "fonctions/contradicteur.yaml").unlink()
        with self.assertRaisesRegex(ErreurBibliotheque, "contradicteur"):
            Bibliotheque.charger(self.rep)

    def test_erreurs_de_gabarit(self):
        base = ("id: test\nversion: 1\nnom: T\ncriteres: [x]\n")
        cas = [
            ("obligatoires: [inconnu]\nselection_defaut: [finance, rh, clients]\n", "inconnus"),
            ("obligatoires: [finance]\noptionnels: [rh, clients]\nselection_defaut: [rh, clients, strategie]\n", "obligatoires"),
            ("obligatoires: [finance]\noptionnels: [rh]\nselection_defaut: [finance, rh, clients]\n", "rôles du gabarit"),
            ("obligatoires: [finance]\noptionnels: [finance, rh, clients]\nselection_defaut: [finance, rh, clients]\n", "à la fois"),
            ("optionnels: [finance, rh]\nselection_defaut: [finance, rh]\n", "entre 3 et"),
            ("optionnels: [finance, rh, clients]\nselection_defaut: [finance, rh, clients]\nexperts: {min: 3, max: 9}\n", "bornes"),
            ("optionnels: [finance, rh, clients]\nselection_defaut: [finance, rh, clients]\nfonctions: {secretaire: {}}\n", "fonctions inconnues"),
        ]
        for contenu, attendu in cas:
            with self.subTest(attendu=attendu):
                self.ecrire("gabarits/test.yaml", base + contenu)
                with self.assertRaisesRegex(ErreurBibliotheque, attendu):
                    Bibliotheque.charger(self.rep)
        self.ecrire("gabarits/test.yaml", "id: test\nversion: 1\noptionnels: [finance, rh, clients]\n"
                                           "selection_defaut: [finance, rh, clients]\n")
        with self.assertRaisesRegex(ErreurBibliotheque, "critère"):
            Bibliotheque.charger(self.rep)


class CompositionTests(unittest.TestCase):
    def setUp(self):
        self.bib = Bibliotheque.charger(RACINE / "comites")

    def test_composition_par_defaut_du_comex(self):
        c = resoudre_composition(self.bib, DEUX, "comex")
        self.assertEqual(c.ids()[0], "president")
        self.assertEqual(c.ids()[-1], "contradicteur")
        self.assertEqual(c.experts, ["strategie", "finance", "clients", "operations", "risques", "rh"])
        self.assertEqual(c.membre("president").titre, "Président et rapporteur")
        finance = c.membre("finance")
        self.assertEqual((finance.role_id, finance.role_version), ("finance", 1))
        self.assertIn("Question clé", finance.mandat)
        self.assertFalse(finance.mandat_modifie)

    def test_affectation_a_tour_de_role_et_exceptions(self):
        c = resoudre_composition(self.bib, DEUX, "comex")
        self.assertEqual([c.membre(e).modele for e in c.experts], ["a", "b", "a", "b", "a", "b"])
        cfg = {**DEUX, "affectation": {**DEUX["affectation"], "par_role": {"finance": "a"}}}
        c = resoudre_composition(self.bib, cfg, "comex")
        self.assertEqual([c.membre(e).modele for e in c.experts], ["a", "a", "b", "a", "b", "a"])

    def test_le_contradicteur_change_de_lignee_quand_c_est_possible(self):
        c = resoudre_composition(self.bib, DEUX, "comex")
        self.assertEqual(c.membre("contradicteur").modele, "b")
        self.assertTrue(c.membre("contradicteur").note_affectation)
        cfg = {**DEUX, "affectation": {**DEUX["affectation"], "famille_differente_contradicteur": "ignorer"}}
        self.assertEqual(resoudre_composition(self.bib, cfg, "comex").membre("contradicteur").modele, "a")

    def test_fonctionne_avec_un_seul_modele(self):
        cfg = config(("seul", "qwen", "interne"), president="seul", contradicteur="seul", experts=["seul"])
        c = resoudre_composition(self.bib, cfg, "comex")
        self.assertEqual({m.modele for m in c.membres}, {"seul"})
        self.assertIn("seule famille", c.membre("contradicteur").note_affectation)

    def test_selection_mandats_et_modeles_choisis_par_le_decideur(self):
        c = resoudre_composition(self.bib, DEUX, "comex", selection=["risques", "finance", "operations"],
                                 mandats={"finance": "Concentre-toi sur la trésorerie 2027."},
                                 affectations={"risques": "b", "contradicteur": "a"})
        self.assertEqual(c.experts, ["risques", "finance", "operations"])
        finance = c.membre("finance")
        self.assertTrue(finance.mandat_modifie)
        self.assertEqual(finance.mandat, "Concentre-toi sur la trésorerie 2027.")
        self.assertIn("Question clé", finance.mandat_bibliotheque)
        self.assertEqual(c.membre("risques").modele, "b")
        self.assertEqual(c.membre("contradicteur").modele, "a")
        self.assertEqual(c.membre("contradicteur").note_affectation, "Modèle choisi par le décideur.")

    def test_regles_du_gabarit(self):
        cas = [
            ({"selection": ["finance", "risques"]}, "entre 3 et 6"),
            ({"selection": ["finance", "risques", "rh", "clients", "operations", "strategie", "finance"]}, "qu'une fois"),
            ({"selection": ["strategie", "clients", "rh"]}, "obligatoire"),
            ({"selection": ["finance", "risques", "inconnu"]}, "inconnus"),
            ({"mandats": {"rh": "x"}, "selection": ["finance", "risques", "clients"]}, "absent du comité"),
            ({"mandats": {"finance": "  "}}, "vide"),
            ({"mandats": {"finance": "x" * 2001}}, "trop long"),
            ({"affectations": {"finance": "zz"}}, "Modèle inconnu"),
        ]
        for options, attendu in cas:
            with self.subTest(attendu=attendu):
                with self.assertRaisesRegex(ErreurComposition, attendu):
                    resoudre_composition(self.bib, DEUX, "comex", **options)
        with self.assertRaisesRegex(ErreurComposition, "Gabarit inconnu"):
            resoudre_composition(self.bib, DEUX, "absent")

    def test_retrait_d_un_role_obligatoire_avec_justification(self):
        c = resoudre_composition(self.bib, DEUX, "comex", selection=["finance", "clients", "rh"],
                                 retraits_justifies={"risques": "Sujet sans enjeu réglementaire, validé par le DG."})
        self.assertNotIn("risques", c.experts)
        self.assertIn("risques", c.retraits_justifies)

    def test_un_expert_hors_gabarit_peut_sieger(self):
        # Le gabarit est une présélection : tout rôle d'expert de la bibliothèque est accepté
        self.assertIn("rh", resoudre_composition(self.bib, DEUX, "comex", selection=["finance", "risques", "rh"]).experts)

    def test_couverture_des_criteres(self):
        c = resoudre_composition(self.bib, DEUX, "comex", selection=["finance", "risques", "operations"])
        couverture = c.couverture(["Valeur creee", "RISQUES ET CONFORMITÉ", "Impact sur les clients"])
        self.assertEqual(couverture["Valeur creee"], ["finance"])
        self.assertEqual(couverture["RISQUES ET CONFORMITÉ"], ["risques"])
        self.assertEqual(couverture["Impact sur les clients"], [])
        self.assertEqual(normaliser("  Capacité  d'exécution "), "capacite d execution")

    def test_aller_retour_json(self):
        c = resoudre_composition(self.bib, DEUX, "comex", mandats={"finance": "Mandat modifié."})
        copie = Composition.depuis_dict(json.loads(json.dumps(c.vers_dict())))
        self.assertEqual(copie, c)

    def test_modeles_externes_de_la_composition(self):
        c = resoudre_composition(self.bib, DEUX, "comex")
        self.assertEqual({e["modele"] for e in modeles_externes(c, DEUX)}, {"b"})


class ConfigurationTests(unittest.TestCase):
    def test_ancien_format_agents_toujours_accepte(self):
        ancien = {"modeles": {"m1": {}, "m2": {}},
                  "agents": {"president": {"modele": "m1", "thinking": True}, "finance": {"modele": "m2"},
                             "contradicteur": {"modele": "m2", "thinking": True}}}
        regle = normaliser_affectation(ancien)
        self.assertEqual((regle["president"], regle["contradicteur"]), ("m1", "m2"))
        self.assertEqual(regle["par_role"], {"finance": "m2"})
        self.assertEqual(set(regle["thinking"]), {"president", "contradicteur"})

    def test_modele_inconnu_dans_l_affectation(self):
        with self.assertRaisesRegex(ValueError, "inconnus"):
            normaliser_affectation(config(("a", "x", "interne"), president="a", experts=["zz"]))

    def test_la_configuration_livree_conserve_la_repartition_du_comex(self):
        with patch.dict("os.environ", {"COMEX_MODE": "simulation"}):
            cfg = charger_config(RACINE / "config.yaml")
        self.assertEqual(set(cfg["agents"]), set(AGENTS))
        self.assertEqual({a for a, r in cfg["agents"].items() if r["thinking"]}, {"president", "contradicteur"})
        self.assertEqual([cfg["agents"][e]["modele"] for e in EXPERTS], ["gemma", "gemma", "gemma", "gpt_oss", "gpt_oss", "gemma"])


if __name__ == "__main__":
    unittest.main()
