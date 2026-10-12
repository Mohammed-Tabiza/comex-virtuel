"""Réponses préenregistrées pour le mode simulation (dossier d'exemple « assistant-ia-2027 »).

Elles permettent de tester l'interface et le protocole sans endpoint. Pour un autre dossier,
des réponses génériques sont produites. Ce ne sont PAS des sorties de modèle.
"""
import copy


def _arg(texte, *sources):
    return {"texte": texte, "sources": list(sources)}


CADRAGE = {
    "question_reformulee": "Quelle trajectoire retenir en 2027 pour l'assistant d'IA générative interne : "
                           "généraliser la plateforme interne, basculer vers le SaaS, ou étendre le pilote ?",
    "decision_attendue": "Choix de l'option et de l'enveloppe 2027 au COMEX du 15 décembre 2026 [D0-§2].",
    "options": [
        {"id": "A", "nom": "Généraliser la plateforme interne", "description": "2 400 utilisateurs fin 2027, 1,9 M€ d'investissement [D2-§1]."},
        {"id": "B", "nom": "Basculer vers le SaaS", "description": "Offre éditeur, 864 k€ par an au prix catalogue [D2-§2]."},
        {"id": "C", "nom": "Étendre le pilote", "description": "600 utilisateurs pendant 12 mois, 0,35 M€ [D2-§3]."},
    ],
    "criteres": ["Valeur démontrée", "Conformité contractuelle et réglementaire", "Capacité d'exécution en 2027",
                 "Coût complet sur 4 ans", "Acceptabilité sociale"],
    "porteurs_criteres": [
        {"critere": "Valeur démontrée", "membres": ["finance", "clients"]},
        {"critere": "Conformité contractuelle et réglementaire", "membres": ["risques"]},
        {"critere": "Capacité d'exécution en 2027", "membres": ["operations", "rh"]},
        {"critere": "Coût complet sur 4 ans", "membres": ["finance"]},
        {"critere": "Acceptabilité sociale", "membres": ["rh"]},
    ],
    "contraintes": ["Aucune donnée client traitée hors UE [D0-§5]", "Investissement 2027 plafonné à 2 M€ [D0-§5]",
                    "Pas de généralisation avant la fin de la consultation du CSE [D0-§5]"],
    "informations_manquantes": ["Adoption hors volontaires [D0-§6]", "Capacité au-delà de 250 utilisateurs simultanés [D0-§6]",
                                "Gains mesurés objectivement [D1-§3]", "Marge de négociation SaaS [D0-§6]"],
    "questions_pour_humain": ["Une renégociation des clauses de support avec l'éditeur SaaS est-elle envisageable avant décembre ?",
                              "Le départ d'un ETP plateforme en février 2027 peut-il être compensé par un prestataire ?"],
    "suffisant": True,
}

AVIS = {
    "strategie": {
        "position": "favorable_sous_conditions", "option_preferee": "A",
        "resume": "La plateforme interne est un actif différenciant auprès de clients exigeants sur la localisation des données ; la remplacer par un SaaS affaiblirait cet argument.",
        "arguments": [
            _arg("38 % du chiffre d'affaires dépend de clients imposant un traitement dans l'UE : la maîtrise de l'hébergement est un argument commercial [D3-§1].", "D3-§1"),
            _arg("La demande interne est forte : 78 % des participants veulent conserver l'outil [D1-§3].", "D1-§3"),
            _arg("Le SaaS crée une dépendance à un éditeur sans visibilité sur l'évolution du prix [D2-§2].", "D2-§2"),
        ],
        "hypotheses": ["Les grands comptes valoriseront une IA hébergée en interne lors des renouvellements."],
        "risques": ["Prendre du retard sur la concurrence en prolongeant encore l'expérimentation d'un an."],
        "inconnues": ["Positionnement des concurrents sur l'IA générative."],
        "conditions_changement": ["Un engagement de l'éditeur SaaS sur un traitement et un support intégralement UE."],
        "confiance": 3,
    },
    "finance": {
        "position": "favorable_sous_conditions", "option_preferee": "C",
        "resume": "Le cas d'affaires à 8,6 M€ ne tient pas : il suppose 100 % d'adoption et un gain déclaratif. Il faut des données avant d'engager 1,9 M€.",
        "arguments": [
            _arg("La valeur de 8,6 M€ suppose 100 % d'adoption, alors que seuls 62 % des volontaires sont actifs chaque semaine [D2-§4] [D1-§2].", "D2-§4", "D1-§2"),
            _arg("Avec 50 % d'adoption et 1 h réellement gagnée, la valeur tombe à environ 2 M€ par an, encore au-dessus du coût complet de l'option A (1,03 M€ par an) [D2-§1].", "D2-§1"),
            _arg("L'option A consomme 1,9 M€ d'une enveloppe plafonnée à 2 M€, sans marge pour les imprévus [D0-§5] [D2-§1].", "D0-§5", "D2-§1"),
            _arg("L'option C coûte 0,35 M€, dont une partie est réutilisable en option A [D2-§3].", "D2-§3"),
        ],
        "hypotheses": ["Le coût horaire chargé de 38 € est représentatif des populations utilisatrices."],
        "risques": ["Dépassement de l'enveloppe si deux serveurs GPU ne suffisent pas."],
        "inconnues": ["Gain de productivité mesuré objectivement", "Prix négociable de l'offre SaaS"],
        "conditions_changement": ["Une mesure objective d'au moins 1 h gagnée par semaine sur une population non volontaire."],
        "confiance": 4,
    },
    "clients": {
        "position": "favorable_sous_conditions", "option_preferee": "A",
        "resume": "Le besoin est réel au Service client, mais les réclamations du pilote montrent qu'un déploiement sans garde-fous de relecture dégraderait la qualité de service.",
        "arguments": [
            _arg("Les courriels clients représentent 41 % des requêtes du pilote [D1-§2].", "D1-§2"),
            _arg("Deux réclamations clients sont liées à des réponses insuffisamment relues [D1-§4].", "D1-§4"),
            _arg("Les juristes relisent systématiquement : le gain est faible sur les contrats complexes [D1-§4].", "D1-§4"),
        ],
        "hypotheses": ["Une règle de relecture sera respectée sans annuler le gain de temps."],
        "risques": ["Dégradation de la satisfaction des grands comptes."],
        "inconnues": ["Effet sur les délais de réponse et la satisfaction client."],
        "conditions_changement": ["Une hausse des réclamations constatée sur une population élargie."],
        "confiance": 3,
    },
    "operations": {
        "position": "reserve", "option_preferee": "C",
        "resume": "L'infrastructure n'a jamais été testée au-delà de 74 utilisateurs simultanés et se dégrade déjà. Généraliser avec un seul ETP au lieu de deux est risqué.",
        "arguments": [
            _arg("Le temps de réponse médian passe de 4 à 11 secondes au pic de 74 utilisateurs simultanés [D1-§2].", "D1-§2"),
            _arg("Aucun test de charge au-delà de 250 utilisateurs simultanés n'a été réalisé [D0-§6].", "D0-§6"),
            _arg("Un des deux ETP part en février 2027 ; le remplacement prendra 4 à 6 mois [D4-§3].", "D4-§3"),
            _arg("La journalisation ne détecte pas les données sensibles [D3-§3].", "D3-§3"),
        ],
        "hypotheses": ["Deux serveurs supplémentaires suffiraient pour 2 400 utilisateurs (non démontré)."],
        "risques": ["Saturation aux heures de pointe et désaffection des utilisateurs", "Exploitation dépendante d'une seule personne"],
        "inconnues": ["Taux de simultanéité attendu à 2 400 utilisateurs."],
        "conditions_changement": ["Un test de charge concluant et un plan de continuité des compétences."],
        "confiance": 4,
    },
    "risques": {
        "position": "favorable_sous_conditions", "option_preferee": "C",
        "resume": "L'option B est incompatible en l'état avec les engagements couvrant 38 % du chiffre d'affaires. A et C sont acceptables avec filtrage des données sensibles et formation obligatoire.",
        "arguments": [
            _arg("Le contrat type de l'éditeur prévoit un support hors UE avec accès possible aux données [D3-§2].", "D3-§2"),
            _arg("Une violation est un motif de résiliation pour des clients pesant 38 % du chiffre d'affaires [D3-§1].", "D3-§1"),
            _arg("Deux saisies de données sensibles ont eu lieu pendant le pilote [D1-§4] [D3-§3].", "D1-§4", "D3-§3"),
            _arg("Les utilisateurs doivent être formés au titre de la maîtrise de l'IA [D3-§4].", "D3-§4"),
        ],
        "hypotheses": ["La renégociation avec l'éditeur n'aboutira pas avant décembre."],
        "risques": ["Un incident de données lors d'un élargissement non préparé."],
        "inconnues": ["Issue d'une renégociation des clauses de support."],
        "conditions_changement": ["Un avenant garantissant traitement et support intégralement dans l'UE."],
        "confiance": 5,
    },
    "rh": {
        "position": "favorable_sous_conditions", "option_preferee": "C",
        "resume": "Le calendrier social rend une généralisation en 2027 tendue ; l'option C laisse le temps de consulter le CSE et de bâtir la formation.",
        "arguments": [
            _arg("L'information-consultation dure environ 2 mois, jusqu'à 5 en cas d'expertise [D4-§2].", "D4-§2"),
            _arg("53 % des collaborateurs du Service client sont inquiets, alors qu'ils sont la première population ciblée [D4-§1].", "D4-§1"),
            _arg("9 600 heures de formation ne sont pas prévues au plan 2027 [D4-§4].", "D4-§4"),
        ],
        "hypotheses": ["Le CSE ne s'opposera pas à une extension progressive et accompagnée."],
        "risques": ["Conflit social si le projet est perçu comme un plan d'automatisation."],
        "inconnues": ["Position du CSE."],
        "conditions_changement": ["Un accord rapide avec le CSE sur un cadre d'usage."],
        "confiance": 3,
    },
}

CONTRE_ANALYSE = {
    "resume": "Le dossier repose sur un pilote de volontaires évalué par déclaration : il prouve l'appétence, pas la valeur. Les trois options héritent de cette faiblesse.",
    "hypotheses_fragiles": [
        _arg("Les 2,1 h gagnées sont déclaratives et mesurées sur 52 % de volontaires : double biais de sélection [D1-§1] [D1-§3].", "D1-§1", "D1-§3"),
        _arg("La valeur de 8,6 M€ applique ce gain à 100 % des salariés [D2-§4].", "D2-§4"),
        _arg("Deux serveurs GPU pour 2 400 utilisateurs : dimensionnement jamais testé [D2-§1] [D0-§6].", "D2-§1", "D0-§6"),
        _arg("L'option C est présentée comme prudente, mais 600 utilisateurs dépassent aussi la capacité éprouvée [D2-§3] [D1-§2].", "D2-§3", "D1-§2"),
    ],
    "scenarios_echec": [
        "A : saturation dès le déploiement, latence, désaffection ; 1,9 M€ investis pour un outil sous-utilisé.",
        "B : la renégociation échoue après engagement ; retour coûteux vers une plateforme interne démantelée.",
        "C : le pilote étendu reproduit le biais des volontaires et ne tranche rien ; un an perdu sans apprentissage.",
    ],
    "angles_morts": [
        "Aucune option hybride : interne pour les données clients, SaaS pour les usages sans données sensibles.",
        "Le coût de ne rien généraliser en 2027 n'est pas chiffré.",
    ],
    "critique_composition": {
        "angles_absents": ["Personne ne parle pour les collaborateurs non volontaires, qui seraient la majorité des "
                           "utilisateurs en cas de généralisation [D1-§1] [D4-§1]."],
        "voix_surrepresentees": [],
        "commentaire": "Composition adaptée à une décision budgétaire ; l'angle des utilisateurs non volontaires "
                       "reste à porter explicitement.",
    },
}

OBJECTIONS = {"objections": [
    {"id": "O1", "cible": "finance", "texte": "Ton scénario à 50 % d'adoption et 1 h gagnée est aussi peu étayé que celui à 8,6 M€. Quelle mesure trancherait réellement ?", "sources": ["D2-§4"]},
    {"id": "O2", "cible": "strategie", "texte": "L'argument commercial de l'hébergement interne n'est appuyé par aucun retour client dans le dossier. C'est une hypothèse, pas un fait.", "sources": ["D3-§1"]},
    {"id": "O3", "cible": "operations", "texte": "Si l'infrastructure se dégrade à 74 utilisateurs simultanés, l'option C à 600 utilisateurs pose le même problème. Que proposes-tu concrètement ?", "sources": ["D1-§2", "D2-§3"]},
    {"id": "O4", "cible": "clients", "texte": "Une relecture obligatoire peut absorber le gain de temps déclaré. Ton option A sous condition reste-t-elle rentable ?", "sources": ["D1-§3", "D1-§4"]},
    {"id": "O5", "cible": "rh", "texte": "Tu supposes que le CSE acceptera une extension progressive. Passer à 600 personnes ne déclenche-t-il pas déjà la consultation ?", "sources": ["D0-§5", "D4-§2"]},
]}

REPONSES = {
    "finance": {"reponses": [{"objection_id": "O1", "attitude": "concede",
                              "texte": "Exact, le scénario à 50 % est illustratif. Condition proposée : mesurer pendant l'option C le temps de traitement sur un groupe témoin non volontaire [D1-§3]."}],
                "position_revisee": "favorable_sous_conditions",
                "option_revisee": "C",
                "resume": "Option C maintenue, avec un protocole de mesure objectif comme condition de passage à A."},
    "strategie": {"reponses": [{"objection_id": "O2", "attitude": "ajuste",
                                "texte": "Le dossier ne contient pas de retour client. Je retire l'argument comme fait établi et propose de sonder les trois grands comptes [D3-§1]."}],
                  "position_revisee": "favorable_sous_conditions",
                "option_revisee": "C",
                  "resume": "A reste la cible stratégique, mais C est la bonne décision pour décembre."},
    "operations": {"reponses": [{"objection_id": "O3", "attitude": "maintient",
                                 "texte": "C n'est acceptable que si le test de charge précède l'extension : passage à 600 par paliers de 150, avec le renforcement prévu au budget C [D2-§3]."}],
                   "position_revisee": "favorable_sous_conditions",
                "option_revisee": "C",
                   "resume": "Favorable à C à condition d'une montée en charge par paliers après test."},
    "clients": {"reponses": [{"objection_id": "O4", "attitude": "ajuste",
                              "texte": "La relecture peut être ciblée : obligatoire pour les grands comptes, par échantillonnage ailleurs [D1-§4]."}],
                "position_revisee": "favorable_sous_conditions",
                "option_revisee": "C",
                "resume": "Je me rallie à C : la relecture ciblée doit être mesurée avant de viser A."},
    "rh": {"reponses": [{"objection_id": "O5", "attitude": "concede",
                         "texte": "Oui, une extension significative justifie la consultation : il faut saisir le CSE dès janvier, ce qui reste compatible avec C mais pas avec une généralisation en 2027 [D4-§2]."}],
           "position_revisee": "favorable_sous_conditions",
                "option_revisee": "C",
           "resume": "C avec saisine du CSE en janvier 2027."},
}

SYNTHESE = {
    "recommandation": "Retenir l'option C conçue comme une étape probante vers l'option A : étendre à 600 utilisateurs incluant des non-volontaires, avec mesure objective et test de charge, puis statuer sur la généralisation en juin 2027. Écarter l'option B tant que l'éditeur ne garantit pas un traitement et un support intégralement dans l'UE.",
    "conditions": [
        "Test de charge avant de dépasser 250 utilisateurs, puis extension par paliers.",
        "Groupe témoin non volontaire et mesure objective (temps de traitement, réclamations).",
        "Saisine du CSE en janvier 2027.",
        "Filtrage des données sensibles et formation obligatoire avant tout élargissement.",
        "Prestataire ou recrutement pour sécuriser l'exploitation avant février 2027.",
    ],
    "options": [
        {"nom": "A - Généraliser", "pour": ["Conforme aux engagements contractuels", "Forte demande interne", "Actif potentiellement différenciant"],
         "contre": ["Valeur non démontrée", "Capacité jamais testée", "95 % de l'enveloppe", "Calendrier social incompatible avec 2027"]},
        {"nom": "B - SaaS", "pour": ["Pas d'infrastructure à exploiter", "Coût prévisible"],
         "contre": ["Incompatible avec les engagements couvrant 38 % du CA", "Dépendance à l'éditeur"]},
        {"nom": "C - Étendre le pilote", "pour": ["Coût faible et en partie réutilisable", "Produit les données manquantes", "Compatible avec le calendrier social"],
         "contre": ["Report d'un an de la valeur", "Reproduit le biais des volontaires si mal conçu"]},
    ],
    "accords": ["L'option B est exclue en l'état [D3-§2].", "La valeur de 8,6 M€ ne peut pas fonder la décision.",
                "Aucun élargissement sans filtrage des données sensibles."],
    "desaccords": [
        {"sujet": "Rythme de généralisation", "positions": "Stratégie veut afficher A comme cible 2027 ; Opérations et RH jugent cette date irréaliste."},
        {"sujet": "Valeur commerciale de l'hébergement interne", "positions": "Stratégie y voit un argument client ; le contradicteur relève qu'aucune donnée client ne l'étaye."},
    ],
    "hypotheses_fragiles": ["Gain déclaratif de 2,1 h par semaine", "Suffisance de deux serveurs GPU", "Acceptation par le CSE"],
    "informations_a_obtenir": ["Résultat du test de charge", "Avis des trois grands comptes sur l'hébergement interne",
                               "Réponse de l'éditeur sur un support intégralement UE", "Coût d'un prestataire d'exploitation"],
}

_COMPARAISON = [
    {
        "criteres": [
            {"critere": "Valeur démontrée", "evaluation": "inconnu", "justification": "Les gains du pilote sont déclaratifs, sans mesure objective.", "sources": ["D1-§3"]},
            {"critere": "Conformité contractuelle et réglementaire", "evaluation": "mitige", "justification": "L'hébergement interne respecte les engagements, mais la prévention des saisies sensibles doit être renforcée.", "sources": ["D3-§3"]},
            {"critere": "Capacité d'exécution en 2027", "evaluation": "mitige", "justification": "Le départ d'un exploitant fragilise la capacité d'exécution.", "sources": ["D4-§3"]},
            {"critere": "Coût complet sur 4 ans", "evaluation": "mitige", "justification": "Le coût annuel complet estimé est de 1,03 M€ ; la valeur correspondante reste à établir.", "sources": ["D2-§1"]},
            {"critere": "Acceptabilité sociale", "evaluation": "inconnu", "justification": "La consultation et la formation restent à organiser.", "sources": ["D4-§2", "D4-§4"]},
        ],
        "preuves": [_arg("L'investissement prévu est de 1,9 M€.", "D2-§1"), _arg("Les gains mesurés par enquête sont déclaratifs.", "D1-§3")],
        "objections": ["O2 : l'avantage commercial allégué n'est pas documenté ; Stratégie retire cet argument comme fait établi.", "O4 : la qualité exige une relecture ciblée, à mesurer avant généralisation."],
        "incertitudes": ["Adoption hors volontaires et gains objectifs.", "Résultat de la consultation et disponibilité des compétences."],
    },
    {
        "criteres": [
            {"critere": "Valeur démontrée", "evaluation": "inconnu", "justification": "Aucun gain propre à l'offre SaaS n'est documenté.", "sources": []},
            {"critere": "Conformité contractuelle et réglementaire", "evaluation": "defavorable", "justification": "Le support hors UE rend l'offre incompatible avec les engagements en l'état.", "sources": ["D3-§1", "D3-§2"]},
            {"critere": "Capacité d'exécution en 2027", "evaluation": "inconnu", "justification": "Le calendrier de migration n'est pas établi dans le dossier.", "sources": []},
            {"critere": "Coût complet sur 4 ans", "evaluation": "inconnu", "justification": "Le prix annuel et les frais initiaux sont connus, mais pas le coût complet sur quatre ans.", "sources": ["D2-§2"]},
            {"critere": "Acceptabilité sociale", "evaluation": "inconnu", "justification": "La consultation reste nécessaire avant généralisation.", "sources": ["D4-§2"]},
        ],
        "preuves": [_arg("Le prix catalogue annuel est de 864 k€ pour 2 400 utilisateurs.", "D2-§2"), _arg("Le support de niveau 2 peut accéder aux données depuis un pays tiers.", "D3-§2")],
        "objections": ["La conformité bloque cette option en l'état ; aucune garantie contractuelle nouvelle n'est documentée."],
        "incertitudes": ["Issue d'une renégociation du support et des garanties contractuelles.", "Coût et calendrier de migration."],
    },
    {
        "criteres": [
            {"critere": "Valeur démontrée", "evaluation": "inconnu", "justification": "Le pilote étendu vise à mesurer la valeur ; son résultat n'est pas encore connu.", "sources": ["D1-§3", "D2-§3"]},
            {"critere": "Conformité contractuelle et réglementaire", "evaluation": "mitige", "justification": "L'hébergement reste interne, avec des mesures de prévention à mettre en place.", "sources": ["D3-§3"]},
            {"critere": "Capacité d'exécution en 2027", "evaluation": "mitige", "justification": "Le renforcement partiel est prévu, mais la disponibilité des exploitants reste fragile.", "sources": ["D2-§3", "D4-§3"]},
            {"critere": "Coût complet sur 4 ans", "evaluation": "inconnu", "justification": "Le coût 2027 est de 0,35 M€ ; les décisions et coûts ultérieurs restent ouverts.", "sources": ["D2-§3"]},
            {"critere": "Acceptabilité sociale", "evaluation": "inconnu", "justification": "L'extension doit intégrer le dialogue social et la formation.", "sources": ["D4-§2", "D4-§4"]},
        ],
        "preuves": [_arg("L'extension prévoit environ 600 utilisateurs et un coût 2027 de 0,35 M€.", "D0-§4", "D2-§3")],
        "objections": ["O1 : le gain de productivité doit être mesuré objectivement, comme Finance le concède.", "O3 : Opérations maintient la nécessité d'un test de charge avant extension.", "O5 : RH reconnaît la nécessité de consulter le CSE."],
        "incertitudes": ["Résultats du test de charge et du groupe témoin.", "Capacité du pilote à réduire les inconnues avant le prochain arbitrage."],
    },
]
for _option, _details in zip(SYNTHESE["options"], _COMPARAISON):
    _option.update(_details)

REFLEXIONS = {
    "cadrage": "Je relis la question posée et la décision attendue. Trois options sont décrites dans le dossier. Les contraintes non négociables portent sur la localisation des données, l'enveloppe et le CSE. Plusieurs inconnues sont listées par le dossier lui-même…",
    "avis": "Je reprends le cadrage validé et les sections qui concernent mon mandat. Je vérifie les chiffres cités, je sépare ce que le dossier établit de ce qu'il suppose, puis je construis ma position…",
    "contre_analyse": "Je cherche les hypothèses sur lesquelles reposent toutes les options. Le pilote porte sur des volontaires, les gains sont déclaratifs, la capacité n'a pas été testée…",
    "objections": "Je compare chaque avis à ma contre-analyse. Je retiens les arguments qui pèsent le plus sur la décision et qui reposent sur une hypothèse non démontrée…",
    "reponse": "Je relis l'objection qui m'est adressée. Est-elle fondée au regard du dossier ? Je décide de maintenir, d'ajuster ou de concéder…",
    "synthese": "Je rassemble les avis, la contre-analyse et les réponses. Je repère les points d'accord, les désaccords qui persistent après contradiction, et les conditions qui reviennent chez plusieurs membres…",
}


def reflexion(agent_id: str, etape: str) -> str:
    return REFLEXIONS.get(etape, "Analyse en cours…")


def reponse(agent_id: str, etape: str) -> dict:
    table = {
        "cadrage": CADRAGE,
        "avis": AVIS.get(agent_id),
        "contre_analyse": CONTRE_ANALYSE,
        "objections": OBJECTIONS,
        "reponse": REPONSES.get(agent_id),
        "synthese": SYNTHESE,
    }
    return copy.deepcopy(table[etape] or _generique(etape))


def _generique(etape: str) -> dict:
    if etape == "avis":
        return {"position": "reserve", "option_preferee": "A", "resume": "[Simulation] Avis générique.",
                "arguments": [], "hypotheses": [], "risques": [], "inconnues": [], "conditions_changement": [], "confiance": 2}
    if etape == "reponse":
        return {"reponses": [], "position_revisee": "reserve", "option_revisee": "A", "resume": "[Simulation] Réponse générique."}
    return {}
