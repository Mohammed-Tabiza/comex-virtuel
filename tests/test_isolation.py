"""Lot 2 : deux séances simultanées, deux comités différents, aucun échange entre elles."""
import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from langgraph.types import Command

from app import graph as G
from app import simulation
from app.comites import Bibliotheque, resoudre_composition
from app.dossier import charger_dossier
from app.llm import Modeles
from app.runtime import SEANCES, Seance

RACINE = Path(__file__).resolve().parents[1]
MANDAT_SEANCE_1 = "Mandat propre à la séance 1 : concentre-toi sur la trésorerie."


class ClientEnregistreur:
    """Faux endpoint compatible OpenAI : enregistre chaque appel et répond un JSON valide pour l'étape."""

    def __init__(self, nom: str):
        self.nom, self.appels = nom, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **params):
        schema = params["response_format"]["json_schema"]["schema"]
        etape = params["response_format"]["json_schema"]["name"]
        self.appels.append({"etape": etape, "model": params["model"], "schema": schema,
                            "systeme": params["messages"][0]["content"], "consigne": params["messages"][1]["content"]})
        await asyncio.sleep(0.01)                          # laisse les deux séances s'entrelacer
        if etape == "objections":
            cibles = schema["properties"]["objections"]["items"]["properties"]["cible"]["enum"]
            contenu = {"objections": [{"id": f"O{i + 1}", "cible": c, "texte": "Objection.", "sources": []}
                                      for i, c in enumerate(cibles)]}
        else:
            contenu = simulation.reponse("president" if etape in ("cadrage", "synthese") else "inconnu", etape)
        texte = json.dumps(contenu, ensure_ascii=False)

        async def flux():
            delta = SimpleNamespace(content=texte, model_extra={})
            yield SimpleNamespace(choices=[SimpleNamespace(delta=delta)], usage=None)
            yield SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50))
        return flux()


CONFIG = {
    "mode": "reel",
    "modeles": {
        "a": {"libelle": "Modèle A", "lignee": "qwen", "hebergement": "interne", "base_url": "http://a/v1", "model": "A"},
        "b": {"libelle": "Modèle B", "lignee": "mistral", "hebergement": "interne", "base_url": "http://b/v1", "model": "B"},
    },
    "affectation": {"president": "a", "contradicteur": "b", "experts": ["a", "b"]},
    "protocole": {"sortie_structuree": "json_schema"},
}


class IsolationTests(unittest.TestCase):
    def test_deux_seances_simultanees_avec_des_comites_differents(self):
        bib = Bibliotheque.charger(RACINE / "comites")
        dossier = charger_dossier(RACINE / "seances/assistant-ia-2027")
        # Séance 1 : 3 experts, tout sur le modèle A, mandat Finance modifié
        c1 = resoudre_composition(bib, CONFIG, "comex", selection=["finance", "risques", "operations"],
                                  mandats={"finance": MANDAT_SEANCE_1},
                                  affectations={m: "a" for m in ["president", "contradicteur", "finance", "risques", "operations"]})
        # Séance 2 : 4 experts différents, tout sur le modèle B
        c2 = resoudre_composition(bib, CONFIG, "comex", selection=["finance", "risques", "rh", "clients"],
                                  affectations={m: "b" for m in ["president", "contradicteur", "finance", "risques", "rh", "clients"]})

        modeles = Modeles(CONFIG)
        modeles.clients = {"a": ClientEnregistreur("a"), "b": ClientEnregistreur("b")}
        G.MODELES = modeles
        graphe = G.construire_graphe()

        async def derouler(seance_id: str, composition):
            seance = Seance(id=seance_id, dossier=dossier)
            seance.fixer_composition(composition)
            SEANCES[seance_id] = seance
            cfg = {"configurable": {"thread_id": seance_id}}
            await graphe.ainvoke({"complement": ""}, cfg)                    # s'arrête à la validation
            await graphe.ainvoke(Command(resume={"action": "valider"}), cfg)
            return (await graphe.aget_state(cfg)).values

        async def deux_seances():
            return await asyncio.gather(derouler("seance01", c1), derouler("seance02", c2))

        try:
            etat1, etat2 = asyncio.run(deux_seances())
        finally:
            SEANCES.pop("seance01", None)
            SEANCES.pop("seance02", None)
        appels_a, appels_b = modeles.clients["a"].appels, modeles.clients["b"].appels

        # Chaque séance n'a sollicité que ses propres experts, sur ses propres modèles
        self.assertEqual({k.split(":")[0] for k in etat1["avis"]}, {"finance", "risques", "operations"})
        self.assertEqual({k.split(":")[0] for k in etat2["avis"]}, {"finance", "risques", "rh", "clients"})
        self.assertEqual(sum(a["etape"] == "avis" for a in appels_a), 3)
        self.assertEqual(sum(a["etape"] == "avis" for a in appels_b), 4)
        self.assertEqual({a["model"] for a in appels_a}, {"A"})
        self.assertEqual({a["model"] for a in appels_b}, {"B"})
        self.assertTrue(etat1["synthese"] and etat2["synthese"])

        # Le mandat modifié n'apparaît que dans la séance 1, et seulement pour Finance
        finance_1 = [a for a in appels_a if a["etape"] == "avis" and MANDAT_SEANCE_1 in a["consigne"]]
        self.assertEqual(len(finance_1), 1)
        self.assertFalse(any(MANDAT_SEANCE_1 in a["consigne"] for a in appels_b))
        self.assertFalse(any("RH et organisation" in a["consigne"] for a in appels_a))

        # Les objections ne peuvent viser que les experts de la séance
        enum = lambda appels: next(a["schema"] for a in appels if a["etape"] == "objections")[
            "properties"]["objections"]["items"]["properties"]["cible"]["enum"]
        self.assertEqual(enum(appels_a), ["finance", "risques", "operations"])
        self.assertEqual(enum(appels_b), ["finance", "risques", "rh", "clients"])
        self.assertEqual({o["cible"] for o in etat2["objections"]}, {"finance", "risques", "rh", "clients"})

        # Le préfixe commun (dossier) reste identique d'un appel à l'autre : cache vLLM préservé
        self.assertEqual(len({a["systeme"] for a in appels_a + appels_b}), 1)

    def test_les_metadonnees_de_contribution_portent_le_role(self):
        bib = Bibliotheque.charger(RACINE / "comites")
        dossier = charger_dossier(RACINE / "seances/assistant-ia-2027")
        cfg = {**CONFIG, "mode": "simulation", "protocole": {"simulation_duree": [0, 0]}}
        G.MODELES = Modeles(cfg)
        seance = Seance(id="seance03", dossier=dossier)
        seance.fixer_composition(resoudre_composition(bib, cfg, "comex", mandats={"rh": "Mandat RH ad hoc."}))
        SEANCES["seance03"] = seance
        try:
            async def un_avis():
                return await G.appeler({"configurable": {"thread_id": "seance03"}}, "rh", "avis",
                                       lambda m, sch: "consigne")
            resultat, erreur = asyncio.run(un_avis())
        finally:
            SEANCES.pop("seance03", None)
        self.assertIsNone(erreur)
        self.assertEqual(resultat["_meta"]["role"], {"id": "rh", "version": 1, "mandat_modifie": True})


if __name__ == "__main__":
    unittest.main()
