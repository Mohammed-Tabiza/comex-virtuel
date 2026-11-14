"""Membres du COMEX, schémas de contribution et construction des prompts."""
import copy
import json
import unicodedata

AGENTS = {
    "president": {
        "nom": "Président", "titre": "Président et rapporteur", "initiales": "PR",
        "mandat": "Tu cadres la question, identifies les options et les critères de décision, "
                  "puis tu restitues fidèlement accords et désaccords. Tu ne transformes jamais une "
                  "majorité d'avis en preuve, et tu ne tranches pas à la place de l'humain.",
    },
    "strategie": {
        "nom": "Stratégie", "titre": "Stratégie et marché", "initiales": "ST",
        "mandat": "Alignement stratégique, positionnement concurrentiel, scénarios d'avenir. "
                  "Question clé : pourquoi cette option plutôt qu'une autre ?",
    },
    "finance": {
        "nom": "Finance", "titre": "Direction financière", "initiales": "FI",
        "mandat": "Coûts, valeur créée, solidité des hypothèses économiques, sensibilité des résultats. "
                  "Question clé : à quelles conditions cela devient-il intéressant ?",
    },
    "clients": {
        "nom": "Clients", "titre": "Clients et métiers", "initiales": "CM",
        "mandat": "Besoin réel des utilisateurs et des clients, qualité de service, adoption. "
                  "Question clé : quelle valeur concrète pour les utilisateurs ?",
    },
    "operations": {
        "nom": "Opérations", "titre": "Opérations et SI", "initiales": "OP",
        "mandat": "Faisabilité technique, capacité d'exécution, dépendances, exploitation dans la durée. "
                  "Question clé : pouvons-nous le réaliser et l'exploiter ?",
    },
    "risques": {
        "nom": "Risques", "titre": "Risques et conformité", "initiales": "RC",
        "mandat": "Exposition juridique, contractuelle, sécurité et réglementaire, conditions de maîtrise. "
                  "Question clé : qu'est-ce qui pourrait rendre cette option inacceptable ?",
    },
    "rh": {
        "nom": "RH", "titre": "RH et organisation", "initiales": "RH",
        "mandat": "Compétences, charge, dialogue social, conduite du changement. "
                  "Question clé : que doit changer l'organisation pour réussir ?",
    },
    "contradicteur": {
        "nom": "Contradicteur", "titre": "Contradicteur indépendant", "initiales": "CO",
        "mandat": "Tu remets en cause les hypothèses et recherches les scénarios d'échec. Tu n'es pas "
                  "là pour être d'accord : ta valeur vient des failles que les autres n'ont pas vues. "
                  "Question clé : pourquoi la recommandation pourrait-elle être mauvaise ?",
    },
}

EXPERTS = ["strategie", "finance", "clients", "operations", "risques", "rh"]

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
        "reactions_pairs": {"type": "array", "items": _obj({
            "membre": {"type": "string", "enum": EXPERTS},
            "accord": {"type": "string", "enum": ["accord", "desaccord"]},
            "texte": _TXT,
        })},
        "position_revisee": _POSITION,
        "option_revisee": _TXT,
        "resume": _TXT,
    }),
    "replique": _obj({
        "evaluations": {"type": "array", "items": _obj({
            "objection_id": _TXT,
            "statut": {"type": "string", "enum": ["resolue", "partiellement_resolue", "non_resolue"]},
            "commentaire": _TXT,
        })},
        "resume": _TXT,
    }),
    "synthese": _obj({
        "recommandation": _TXT,
        "conditions": _LISTE,
        "options": {"type": "array", "items": _obj({
            "id": _TXT, "nom": _TXT, "pour": _LISTE, "contre": _LISTE,
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



def ecarts_schema(valeur, schema: dict, chemin: str = "") -> list[str]:
    """Écarts d'une réponse à son schéma JSON (sous-ensemble utilisé ici : object, array, string,
    integer, boolean, enum, required, additionalProperties). Liste vide si la réponse est conforme."""
    ici = chemin or "réponse"
    attendu = schema.get("type")
    if attendu == "object":
        if not isinstance(valeur, dict):
            return [f"{ici} : objet attendu"]
        ecarts = [f"{ici} : champ obligatoire « {c} » absent" for c in schema.get("required", []) if c not in valeur]
        proprietes = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            ecarts += [f"{ici} : champ inconnu « {c} »" for c in valeur if c not in proprietes]
        for c, sous in proprietes.items():
            if c in valeur:
                ecarts += ecarts_schema(valeur[c], sous, f"{chemin}.{c}" if chemin else c)
        return ecarts
    if attendu == "array":
        if not isinstance(valeur, list):
            return [f"{ici} : liste attendue"]
        return [e for i, x in enumerate(valeur) for e in ecarts_schema(x, schema.get("items", {}), f"{ici}[{i}]")]
    types = {"string": str, "boolean": bool, "integer": int}
    if attendu in types and (not isinstance(valeur, types[attendu])
                             or (attendu == "integer" and isinstance(valeur, bool))):
        return [f"{ici} : {attendu} attendu"]
    # Une différence de typographie (apostrophe courbe, espace insécable) n'est pas un écart
    if "enum" in schema and normaliser(valeur) not in {normaliser(v) for v in schema["enum"]}:
        return [f"{ici} : « {valeur} » n'est pas une valeur autorisée ({', '.join(map(str, schema['enum']))})"]
    return []


def normaliser(texte) -> str:
    """Forme comparable d'un libellé : sans différences de typographie, de casse ni d'espaces."""
    texte = unicodedata.normalize("NFKC", str(texte or ""))
    for typo, simple in (("’", "'"), ("‘", "'"), ("«", '"'), ("»", '"'), ("–", "-"), ("—", "-")):
        texte = texte.replace(typo, simple)
    return " ".join(texte.lower().split())


def schema_synthese(cadrage: dict) -> dict:
    """Schéma de synthèse contraint par le cadrage validé : identifiants d'option et libellés de critère
    imposés, pour qu'ils ne puissent pas être reformulés."""
    schema = copy.deepcopy(SCHEMAS["synthese"])
    option = schema["properties"]["options"]["items"]["properties"]
    ids = [o["id"] for o in cadrage.get("options", []) if o.get("id")]
    criteres = [c for c in cadrage.get("criteres", []) if c]
    if ids:
        option["id"] = {"type": "string", "enum": ids}
    if criteres:
        option["criteres"]["items"]["properties"]["critere"] = {"type": "string", "enum": criteres}
    return schema


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


def _schema(etape: str, schema: dict | None = None) -> str:
    return _bloc("Format de réponse (schéma JSON)", schema or SCHEMAS[etape])


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
    membres = ", ".join(avis)
    return _role("contradicteur") + _bloc("Cadrage", cadrage) + _bloc("Ta contre-analyse", contre_analyse) + \
        _bloc("Avis des membres", avis) + (
        "## Tâche : objections\n"
        f"Chaque membre qui a rendu un avis ({membres}) doit recevoir au moins une objection (champ cible) : "
        "personne n'échappe à la contradiction. Tu peux en adresser plusieurs au même membre si son argument "
        "pèse lourd dans la décision. Vise les arguments les plus importants pour la décision, pas les détails. "
        "Numérote les identifiants O1, O2...\n"
    ) + _schema("objections")


def prompt_objections_complement(cadrage: dict, contre_analyse: dict, avis_manquants: dict,
                                 deja: list, premier_numero: int) -> str:
    """Relance du contradicteur pour les membres qu'il n'a pas challengés au premier passage."""
    return _role("contradicteur") + _bloc("Cadrage", cadrage) + _bloc("Ta contre-analyse", contre_analyse) + \
        _bloc("Objections déjà formulées", deja) + _bloc("Avis des membres non encore challengés", avis_manquants) + (
        "## Tâche : objections complémentaires\n"
        "Ces membres n'ont reçu aucune objection. Formule au moins une objection précise pour chacun d'eux "
        "(champ cible), sur l'argument de son avis le plus déterminant pour la décision. "
        f"Numérote à partir de O{premier_numero}.\n"
    ) + _schema("objections")


def prompt_reponse(agent_id: str, avis_propre: dict, objections: list, autres_avis: dict) -> str:
    return _role(agent_id) + _bloc("Ton avis initial", avis_propre) + \
        _bloc("Objections qui te sont adressées", objections) + \
        _bloc("Avis initiaux des autres membres (désormais connus)", autres_avis) + (
        "## Tâche : réponse aux objections et aux autres membres\n"
        "Pour chaque objection, indique si tu maintiens ta position, l'ajustes ou concèdes, avec une justification "
        "sourcée. Concéder un point fondé est une qualité, pas une faiblesse. "
        "Réagis ensuite aux avis des autres membres qui touchent ton mandat (reactions_pairs) : dis avec qui tu es "
        "en accord ou en désaccord et pourquoi, au moins un désaccord s'il en existe un réel ; pas de politesse. "
        "Donne enfin ta position révisée et l'identifiant de l'option que tu préfères désormais "
        "(option_revisee), qui peut rester la même.\n"
    ) + _schema("reponse")


def prompt_replique(objections: list, reponses: dict) -> str:
    return _role("contradicteur") + _bloc("Tes objections", objections) + \
        _bloc("Réponses des membres", reponses) + (
        "## Tâche : réplique\n"
        "Pour chaque objection, juge si la réponse du membre la lève : resolue (réponse sourcée et convaincante), "
        "partiellement_resolue (ajustement insuffisant ou condition non vérifiable) ou non_resolue (position "
        "maintenue sans élément nouveau, ou absence de réponse). Justifie en une ou deux phrases. "
        "Ne cède pas par courtoisie : une concession formelle sans engagement vérifiable reste partielle.\n"
    ) + _schema("replique")


def prompt_synthese(cadrage: dict, avis: dict, contre_analyse: dict, objections: list, reponses: dict,
                    replique: dict | None = None, lacunes: list | None = None,
                    positions: dict | None = None) -> str:
    texte = _role("president") + _bloc("Cadrage", cadrage) + _bloc("Avis initiaux", avis) + \
        _bloc("Contre-analyse", contre_analyse) + _bloc("Objections", objections) + \
        _bloc("Réponses aux objections et réactions entre membres", reponses)
    if replique:
        texte += _bloc("Réplique du contradicteur (statut de chaque objection)", replique)
    if lacunes:
        texte += _bloc("Lacunes du débat (contributions manquantes)", lacunes)
    if positions:
        texte += _bloc("Positions des membres, initiales et finales (calculées à partir des contributions, "
                       "elles font foi)", positions)
    return texte + (
        "## Tâche : synthèse pour le décideur\n"
        "Restitue les options avec leurs arguments pour et contre, les accords, les désaccords persistants "
        "(en nommant les positions, telles que les donne le tableau des positions finales, sans en oublier ni en "
        "inverser), les hypothèses fragiles et les informations à obtenir. "
        "Formule une recommandation conditionnelle : ce qu'il faudrait décider, et à quelles conditions. "
        "Restitue TOUTES les options du cadrage, chacune avec son identifiant (id : A, B, C...), même celles que "
        "tu écartes. Pour chacune, évalue chaque critère validé en reprenant son libellé exact "
        "(favorable, mitige, defavorable ou inconnu), "
        "avec une justification concise et les références des sections du dossier qui l'étayent (forme D1-§2 ; "
        "jamais un nom d'agent ni un numéro d'objection). "
        "Sépare les preuves documentées des objections et des incertitudes. Une preuve est un fait du dossier "
        "accompagné de ses sources, pas un avis d'agent. Une source existante ne garantit pas qu'elle prouve "
        "l'affirmation : vérifie le contenu. Sans preuve, utilise inconnu et une liste de sources vide. "
        "Relie les objections aux échanges (O1, O2...) et indique celles qui restent non résolues, en partant de la "
        "réplique du contradicteur ; si tu t'en écartes, dis pourquoi. Restitue les désaccords entre membres "
        "exprimés dans leurs réactions. Si des contributions manquent, signale-le dans les incertitudes et ne "
        "présente jamais comme un accord un sujet sur lequel un membre absent ne s'est pas exprimé. "
        "N'invente ni score numérique ni pondération ; conserve les désaccords et les informations manquantes. "
        "Ne présente jamais un nombre d'avis favorables comme une preuve. "
        "Les noms de champs sont imposés par le schéma, en français (recommandation...) : ne les traduis pas.\n"
    ) + _schema("synthese", schema_synthese(cadrage))
