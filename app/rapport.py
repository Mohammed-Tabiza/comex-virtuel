"""Compte rendu de séance en Markdown (dossier de décision exportable)."""
from datetime import datetime

from .agents import AGENTS

POSITIONS = {"favorable": "Favorable", "favorable_sous_conditions": "Favorable sous conditions",
             "reserve": "Réservé", "defavorable": "Défavorable"}


def _liste(items) -> str:
    return "\n".join(f"- {i}" for i in items) if items else "- (aucun)"


def compte_rendu(seance, etat: dict) -> str:
    cad = etat.get("cadrage") or {}
    syn = etat.get("synthese") or {}
    l = [f"# Compte rendu du COMEX virtuel : {seance.dossier.nom}",
         f"*Séance {seance.id}, {datetime.now():%d/%m/%Y %H:%M}. COMEX consultatif : ce document prépare un arbitrage humain.*",
         "", "## Question", cad.get("question_reformulee", ""), "",
         f"**Décision attendue :** {cad.get('decision_attendue', '')}", "", "## Options"]
    l += [f"- **{o['id']}. {o['nom']}** : {o['description']}" for o in cad.get("options", [])]

    l += ["", "## Recommandation conditionnelle", syn.get("recommandation", "(synthèse non disponible)"), "",
          "### Conditions", _liste(syn.get("conditions")), "", "## Positions des membres", "",
          "| Membre | Modèle | Option initiale | Position initiale | Option après contradiction | Position après contradiction |",
          "|---|---|---|---|---|---|"]
    for cle, avis in (etat.get("avis") or {}).items():
        agent = cle.split(":")[0]
        rep = (etat.get("reponses") or {}).get(f"{agent}:reponse") or {}
        l.append(f"| {AGENTS[agent]['titre']} | {avis['_meta']['libelle']} | {avis.get('option_preferee', '')} | "
                 f"{POSITIONS.get(avis.get('position'), '')} | {rep.get('option_revisee', '-') if rep else '-'} | "
                 f"{POSITIONS.get(rep.get('position_revisee'), '-')} |")

    l += ["", "## Comparaison des options"]
    for option in syn.get("options", []):
        l += ["", f"### {option['nom']}", "", "#### Arguments pour", _liste(option.get("pour")),
              "", "#### Arguments contre", _liste(option.get("contre")), "", "#### Critères de décision"]
        for critere in option.get("criteres", []):
            sources = " ".join(f"[{r}]" for r in critere.get("sources", []))
            l.append(f"- **{critere['critere']} — {critere['evaluation']}** : {critere['justification']} {sources}")
        l += ["", "#### Preuves documentées"]
        l += [f"- {p['texte']} " + " ".join(f"[{r}]" for r in p.get("sources", []))
              for p in option.get("preuves", [])] or ["- (aucune preuve fournie)"]
        l += ["", "#### Objections", _liste(option.get("objections")),
              "", "#### Incertitudes", _liste(option.get("incertitudes"))]
    l += ["", "## Accords", _liste(syn.get("accords")), "", "## Désaccords persistants"]
    l += [f"- **{d['sujet']}** : {d['positions']}" for d in syn.get("desaccords", [])] or ["- (aucun)"]
    l += ["", "## Hypothèses fragiles", _liste(syn.get("hypotheses_fragiles")), "",
          "## Informations à obtenir", _liste(syn.get("informations_a_obtenir")), "", "## Objections et réponses"]
    reponses = {}
    for r in (etat.get("reponses") or {}).values():
        for item in r.get("reponses", []):
            reponses[item["objection_id"]] = item
    for o in etat.get("objections") or []:
        r = reponses.get(o["id"])
        l.append(f"- **{o['id']} → {AGENTS[o['cible']]['nom']}** : {o['texte']}")
        if r:
            l.append(f"  - *{r['attitude']}* : {r['texte']}")
    l += ["", "## Décision humaine", seance.decision_humaine or "(non renseignée)"]
    if etat.get("erreurs"):
        l += ["", "## Incidents", _liste(etat["erreurs"])]
    return "\n".join(l) + "\n"
