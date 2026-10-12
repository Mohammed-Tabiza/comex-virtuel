"""Membres du COMEX, schémas de contribution et construction des prompts."""
import json

from pathlib import Path

from .comites import Bibliotheque

# Membres du comité par défaut (COMEX), lus dans la bibliothèque comites/.
# Le moteur de séance s'appuie encore sur ces tables ; elles seront remplacées par la composition
# propre à chaque séance (lot 2).
BIBLIOTHEQUE = Bibliotheque.charger(Path(__file__).resolve().parent.parent / "comites")
_GABARIT = BIBLIOTHEQUE.gabarits["comex"]


def _agent(role, surcharge=None) -> dict:
    surcharge = surcharge or {}
    return {"nom": surcharge.get("nom", role.nom), "titre": surcharge.get("titre", role.titre),
            "initiales": role.initiales, "mandat": role.mandat_complet()}


AGENTS = {"president": _agent(BIBLIOTHEQUE.fonctions["president"], _GABARIT.fonctions.get("president")),
          **{r: _agent(BIBLIOTHEQUE.roles[r]) for r in _GABARIT.selection_defaut},
          "contradicteur": _agent(BIBLIOTHEQUE.fonctions["contradicteur"], _GABARIT.fonctions.get("contradicteur"))}

EXPERTS = list(_GABARIT.selection_defaut)

# --------------------------------------------------------------------------- schémas
_TXT = {"type": "string"}
_LISTE = {"type": "array", "items": _TXT}
_POSITION = {"type": "string", "enum": ["favorable", "favorable_sous_conditions", "reserve", "defavorable"]}
_ARG = {
    "type": "object",
    "properties": {"texte": _TXT, "sources": _LISTE},
    "required": ["texte", "sources"], "additionalProperties": False,
}


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


SCHEMAS = {
    "cadrage": _obj({
        "question_reformulee": _TXT,
        "decision_attendue": _TXT,
        "options": {"type": "array", "items": _obj({"id": _TXT, "nom": _TXT, "description": _TXT})},
        "criteres": _LISTE,
        "contraintes": _LISTE,
        "informations_manquantes": _LISTE,
        "questions_pour_humain": _LISTE,
        "suffisant": {"type": "boolean"},
    }),
    "avis": _obj({
        "position": _POSITION,
        "option_preferee": _TXT,
        "resume": _TXT,
        "arguments": {"type": "array", "items": _ARG},
        "hypotheses": _LISTE,
        "risques": _LISTE,
        "inconnues": _LISTE,
        "conditions_changement": _LISTE,
        "confiance": {"type": "integer", "minimum": 1, "maximum": 5},
    }),
    "contre_analyse": _obj({
        "resume": _TXT,
        "hypotheses_fragiles": {"type": "array", "items": _ARG},
        "scenarios_echec": _LISTE,
        "angles_morts": _LISTE,
    }),
    "objections": _obj({
        "objections": {"type": "array", "items": _obj({
            "id": _TXT,
            "cible": {"type": "string", "enum": EXPERTS},
            "texte": _TXT,
            "sources": _LISTE,
        })},
    }),
    "reponse": _obj({
        "reponses": {"type": "array", "items": _obj({
            "objection_id": _TXT,
            "attitude": {"type": "string", "enum": ["maintient", "ajuste", "concede"]},
            "texte": _TXT,
        })},
        "position_revisee": _POSITION,
        "option_revisee": _TXT,
        "resume": _TXT,
    }),
    "synthese": _obj({
        "recommandation": _TXT,
        "conditions": _LISTE,
        "options": {"type": "array", "items": _obj({
            "nom": _TXT, "pour": _LISTE, "contre": _LISTE,
            "criteres": {"type": "array", "items": _obj({
                "critere": _TXT,
                "evaluation": {"type": "string", "enum": ["favorable", "mitige", "defavorable", "inconnu"]},
                "justification": _TXT, "sources": _LISTE,
            })},
            "preuves": {"type": "array", "items": _ARG},
            "objections": _LISTE, "incertitudes": _LISTE,
        })},
        "accords": _LISTE,
        "desaccords": {"type": "array", "items": _obj({"sujet": _TXT, "positions": _TXT})},
        "hypotheses_fragiles": _LISTE,
        "informations_a_obtenir": _LISTE,
    }),
}

# --------------------------------------------------------------------------- prompts

def prefixe_commun(dossier_texte: str) -> str:
    """Message système IDENTIQUE pour tous les agents et toutes les étapes.

    Ne rien y mettre de propre à un rôle : c'est ce qui permet à vLLM
    (--enable-prefix-caching) de ne calculer le dossier qu'une seule fois.
    """
    return (
        "Tu participes à une séance d'un COMEX virtuel consultatif. Plusieurs membres analysent "
        "la même question, chacun selon son mandat, pour préparer l'arbitrage d'un décideur humain.\n\n"
        "Règles communes :\n"
        "- Appuie chaque argument sur le dossier en citant les sections sous la forme [D1-§2].\n"
        "- Distingue clairement les faits du dossier de tes hypothèses.\n"
        "- Si une information manque, signale-la au lieu de l'inventer.\n"
        "- Sois précis et concis : des phrases courtes, des chiffres quand le dossier en donne.\n"
        "- Rédige en français.\n"
        "- Réponds uniquement par un objet JSON conforme au schéma demandé, sans texte autour.\n\n"
        "=== DOSSIER DE SÉANCE ===\n\n" + dossier_texte
    )


def _bloc(titre: str, contenu) -> str:
    if not isinstance(contenu, str):
        contenu = json.dumps(contenu, ensure_ascii=False, indent=1)
    return f"## {titre}\n{contenu}\n"


def _role(agent_id: str) -> str:
    a = AGENTS[agent_id]
    return f"## Ton rôle : {a['titre']}\n{a['mandat']}\n"


def _schema(etape: str) -> str:
    return _bloc("Format de réponse (schéma JSON)", SCHEMAS[etape])


def prompt_cadrage(complement_humain: str) -> str:
    texte = _role("president") + (
        "## Tâche : cadrage\n"
        "Reformule la question à trancher, la décision attendue, les options (reprends celles du dossier, "
        "avec des identifiants courts A, B, C...), les critères de décision et les contraintes. "
        "Liste les informations manquantes et les questions à poser au décideur. "
        "Mets suffisant à false seulement si une information est indispensable pour que l'analyse ait un sens.\n"
    )
    if complement_humain:
        texte += _bloc("Précisions apportées par le décideur", complement_humain)
    return texte + _schema("cadrage")


def prompt_avis(agent_id: str, cadrage: dict, complement: str) -> str:
    texte = _role(agent_id) + _bloc("Cadrage validé par le décideur", cadrage)
    if complement:
        texte += _bloc("Précisions du décideur", complement)
    return texte + (
        "## Tâche : avis initial\n"
        "Donne ton analyse indépendante selon ton mandat. Tu ne connais pas l'avis des autres membres. "
        "Indique l'option que tu préfères (identifiant), ta position, tes arguments sourcés, tes hypothèses, "
        "les risques, ce que tu ignores et ce qui te ferait changer d'avis. confiance va de 1 (faible) à 5.\n"
    ) + _schema("avis")


def prompt_contre_analyse(cadrage: dict) -> str:
    return _role("contradicteur") + _bloc("Cadrage validé", cadrage) + (
        "## Tâche : contre-analyse indépendante\n"
        "Sans connaître les avis des membres, identifie les hypothèses les plus fragiles du dossier, "
        "les scénarios dans lesquels chaque option échouerait, et les angles morts du cadrage.\n"
    ) + _schema("contre_analyse")


def prompt_objections(cadrage: dict, contre_analyse: dict, avis: dict) -> str:
    return _role("contradicteur") + _bloc("Cadrage", cadrage) + _bloc("Ta contre-analyse", contre_analyse) + \
        _bloc("Avis des membres", avis) + (
        "## Tâche : objections\n"
        "Formule de 3 à 6 objections précises, chacune adressée à un membre (champ cible). "
        "Vise les arguments les plus importants pour la décision, pas les détails. "
        "Numérote les identifiants O1, O2...\n"
    ) + _schema("objections")


def prompt_reponse(agent_id: str, avis_propre: dict, objections: list) -> str:
    return _role(agent_id) + _bloc("Ton avis initial", avis_propre) + \
        _bloc("Objections qui te sont adressées", objections) + (
        "## Tâche : réponse aux objections\n"
        "Pour chaque objection, indique si tu maintiens ta position, l'ajustes ou concèdes, avec une justification "
        "sourcée. Concéder un point fondé est une qualité, pas une faiblesse. Donne ta position révisée et l'identifiant "
        "de l'option que tu préfères désormais (option_revisee), qui peut rester la même.\n"
    ) + _schema("reponse")


def prompt_synthese(cadrage: dict, avis: dict, contre_analyse: dict, objections: list, reponses: dict) -> str:
    return _role("president") + _bloc("Cadrage", cadrage) + _bloc("Avis initiaux", avis) + \
        _bloc("Contre-analyse", contre_analyse) + _bloc("Objections", objections) + \
        _bloc("Réponses aux objections", reponses) + (
        "## Tâche : synthèse pour le décideur\n"
        "Restitue les options avec leurs arguments pour et contre, les accords, les désaccords persistants "
        "(en nommant les positions), les hypothèses fragiles et les informations à obtenir. "
        "Formule une recommandation conditionnelle : ce qu'il faudrait décider, et à quelles conditions. "
        "Pour chaque option du cadrage, évalue chaque critère validé (favorable, mitige, defavorable ou inconnu), "
        "avec une justification concise et les références des sections qui l'étayent. "
        "Sépare les preuves documentées des objections et des incertitudes. Une preuve est un fait du dossier "
        "accompagné de ses sources, pas un avis d'agent. Une source existante ne garantit pas qu'elle prouve "
        "l'affirmation : vérifie le contenu. Sans preuve, utilise inconnu et une liste de sources vide. "
        "Relie les objections aux échanges (O1, O2...) et indique celles qui restent non résolues. "
        "N'invente ni score numérique ni pondération ; conserve les désaccords et les informations manquantes. "
        "Ne présente jamais un nombre d'avis favorables comme une preuve.\n"
    ) + _schema("synthese")
