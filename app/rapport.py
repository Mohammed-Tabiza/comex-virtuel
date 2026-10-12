"""Compte rendu de séance en Markdown (dossier de décision exportable)."""
import difflib
from datetime import datetime

from .comites import couverture_cadrage


POSITIONS = {"favorable": "Favorable", "favorable_sous_conditions": "Favorable sous conditions",
             "reserve": "Réservé", "defavorable": "Défavorable"}


def _liste(items) -> str:
    return "\n".join(f"- {i}" for i in items) if items else "- (aucun)"


def _membre(seance, agent_id: str, champ: str) -> str:
    """Titre ou nom d'un membre, lu dans la composition de la séance (identifiant à défaut)."""
    try:
        return getattr(seance.composition.membre(agent_id), champ)
    except (AttributeError, KeyError):
        return agent_id


def difference_mots(avant: str, apres: str) -> str:
    """Différence lisible mot à mot : ~~retiré~~ et **ajouté**."""
    a, b = avant.split(), apres.split()
    morceaux = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            morceaux.append(" ".join(a[i1:i2]))
        if op in ("delete", "replace"):
            morceaux.append("~~" + " ".join(a[i1:i2]) + "~~")
        if op in ("insert", "replace"):
            morceaux.append("**" + " ".join(b[j1:j2]) + "**")
    return " ".join(morceaux)


def section_composition(seance, etat: dict, config_modeles: dict | None = None) -> list[str]:
    """Composition du comité : membres, versions des rôles, modèles, mandats modifiés, révisions, couverture."""
    c = getattr(seance, "composition", None)
    if c is None:
        return []
    modeles = config_modeles or {}
    l = ["", "## Composition du comité", "",
         f"Gabarit : **{c.gabarit_nom}** (version {c.gabarit_version}). Composition figée au lancement"
         + (", révisée à la validation." if (seance.revisions or etat.get("revisions_composition")) else "."), "",
         "| Membre | Fonction | Rôle (version) | Modèle | Hébergement | Mandat |", "|---|---|---|---|---|---|"]
    fonctions = {"president": "Animation", "contradicteur": "Contradiction", "expert": "Expertise"}
    for m in c.membres:
        reglages = modeles.get(m.modele, {})
        l.append(f"| {m.titre} | {fonctions.get(m.fonction, m.fonction)} | {m.role_id} (v{m.role_version}) | "
                 f"{reglages.get('libelle', m.modele)}{' · réflexion' if m.thinking else ''} | "
                 f"{reglages.get('hebergement', '-')} | {'**modifié**' if m.mandat_modifie else 'bibliothèque'} |")
    notes = [f"- {m.titre} : {m.note_affectation}" for m in c.membres if m.note_affectation]
    if notes:
        l += ["", "Affectation des modèles :", *notes]

    modifies = [m for m in c.membres if m.mandat_modifie]
    if modifies:
        l += ["", "### Mandats modifiés", "",
              "Texte effectivement envoyé au modèle, comparé à la bibliothèque (~~retiré~~, **ajouté**) :"]
        for m in modifies:
            l += ["", f"**{m.titre}** (rôle {m.role_id}, version {m.role_version})", "",
                  f"> {difference_mots(m.mandat_bibliotheque, m.mandat)}"]

    if c.retraits_justifies:
        l += ["", "### Rôles obligatoires retirés", ""]
        l += [f"- {role} : {justification}" for role, justification in c.retraits_justifies.items()]

    revisions = seance.revisions or etat.get("revisions_composition") or []
    if revisions:
        l += ["", "### Révisions de l'équipe à la validation", ""]
        for r in revisions:
            # Les membres retirés ne figurent plus dans la composition : leur nom vient de la composition d'avant
            noms = {m["id"]: m["nom"] for cle in ("avant", "apres") for m in (r.get(cle) or {}).get("membres", [])}
            nom = lambda i: noms.get(i) or _membre(seance, i, "nom")
            details = []
            if r.get("ajouts"):
                details.append("ajout de " + ", ".join(map(nom, r["ajouts"])))
            if r.get("retraits"):
                details.append("retrait de " + ", ".join(map(nom, r["retraits"])))
            if r.get("modeles"):
                details.append("modèle changé : " + ", ".join(f"{nom(k)} ({a} → {b})" for k, (a, b) in r["modeles"].items()))
            if r.get("mandats"):
                details.append("mandat modifié : " + ", ".join(map(nom, r["mandats"])))
            l.append(f"- Révision {r.get('numero', 1)} : {' ; '.join(details) or 'aucun changement'}"
                     + (f". Motif : {r['motif']}" if r.get("motif") else "."))

    couverture = couverture_cadrage(c, etat.get("cadrage"))
    if couverture:
        l += ["", "### Couverture des critères", "", "| Critère | Porté par |", "|---|---|"]
        for critere, membres in couverture.items():
            l.append(f"| {critere} | {', '.join(_membre(seance, m, 'nom') for m in membres) or '**sans porteur**'} |")

    critique = (etat.get("contre_analyse") or {}).get("critique_composition")
    if critique:
        l += ["", "### Composition contestée par le contradicteur", "", critique.get("commentaire", "")]
        if critique.get("angles_absents"):
            l += ["", "Angles absents :", _liste(critique["angles_absents"])]
        if critique.get("voix_surrepresentees"):
            l += ["", "Voix surreprésentées :", _liste(critique["voix_surrepresentees"])]

    if etat.get("compactages"):
        l += ["", "### Contrôle du contexte", ""]
        l += [f"- {x['etape']} : contributions compactées (niveau {x['niveau']}) pour tenir dans le contexte "
              f"({x['entree']} + {x['sortie']} tokens estimés sur {x['contexte_max']})." for x in etat["compactages"]]
    return l


def compte_rendu(seance, etat: dict, config_modeles: dict | None = None) -> str:
    cad = etat.get("cadrage") or {}
    syn = etat.get("synthese") or {}
    nom_comite = seance.composition.gabarit_nom if getattr(seance, "composition", None) else "COMEX"
    l = [f"# Compte rendu du comité virtuel ({nom_comite}) : {seance.dossier.nom}",
         f"*Séance {seance.id}, {datetime.now():%d/%m/%Y %H:%M}. Comité consultatif : ce document prépare un arbitrage humain.*",
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
        l.append(f"| {_membre(seance, agent, 'titre')} | {avis['_meta']['libelle']} | {avis.get('option_preferee', '')} | "
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
        l.append(f"- **{o['id']} → {_membre(seance, o['cible'], 'nom')}** : {o['texte']}")
        if r:
            l.append(f"  - *{r['attitude']}* : {r['texte']}")
    l += section_composition(seance, etat, config_modeles)
    l += ["", "## Décision humaine", seance.decision_humaine or "(non renseignée)"]
    if etat.get("erreurs"):
        l += ["", "## Incidents", _liste(etat["erreurs"])]
    return "\n".join(l) + "\n"
