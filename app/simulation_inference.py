"""Scénario de simulation du dossier « plateforme-inference-llm » (comité d'architecture).

Réponses préenregistrées, cohérentes avec le dossier et avec le gabarit « architecture ».
Elles servent à démontrer l'interface et le protocole sans modèle : ce ne sont PAS des sorties de modèle.
"""


def _arg(texte, *sources):
    return {"texte": texte, "sources": list(sources)}


CRITERES = ["Coût total", "Sécurité et isolation des données", "Performance", "Autonomie des équipes",
            "Exploitabilité", "Réversibilité", "Délai de mise à disposition"]

CADRAGE = {
    "question_reformulee": "Quelle architecture d'inférence retenir pour 2027 : une plateforme vLLM centrale, un "
                           "hébergement par équipe, ou une plateforme centrale complétée de déploiements dédiés ?",
    "decision_attendue": "Architecture cible 2027 et autorisation de commande des GPU au comité du 9 décembre 2026, "
                         "commande avant fin janvier 2027 [D0-§2] [D4-§3].",
    "options": [
        {"id": "A", "nom": "Plateforme centrale mutualisée", "description": "Cluster de 16 GPU opéré par la DSI, passerelle commune, 1,6 M€ [D2-§1]."},
        {"id": "B", "nom": "Hébergement par équipe", "description": "Chaque équipe achète et exploite ses serveurs, 2,4 M€ [D2-§2]."},
        {"id": "C", "nom": "Centrale et déploiements dédiés", "description": "Plateforme de 12 GPU, instance isolée RH, groupe réservé au Service client, 1,75 M€ [D2-§3]."},
    ],
    # Critères usuels du comité repris mot pour mot, plus un critère propre à cette décision
    "criteres": CRITERES,
    "porteurs_criteres": [
        {"critere": "Coût total", "membres": ["finops"]},
        {"critere": "Sécurité et isolation des données", "membres": ["rssi"]},
        {"critere": "Performance", "membres": ["exploitation", "equipes_produit", "referent_data_ia"]},
        {"critere": "Autonomie des équipes", "membres": ["equipes_produit"]},
        {"critere": "Exploitabilité", "membres": ["exploitation"]},
        {"critere": "Réversibilité", "membres": ["architecte_entreprise", "referent_data_ia"]},
        {"critere": "Délai de mise à disposition", "membres": ["exploitation"]},
    ],
    "contraintes": ["Données « Confidentiel RH » jamais sur une infrastructure partagée [D0-§5] [D3-§1]",
                    "Investissement 2027 plafonné à 1,8 M€ [D0-§5]",
                    "Service client : première réponse en moins de 1,5 s pour 95 % des requêtes [D0-§5]"],
    "informations_manquantes": ["Taux d'utilisation réel d'une plateforme mutualisée [D0-§6] [D2-§4]",
                                "Latence du Service client sur une infrastructure partagée [D1-§3]",
                                "Croissance de la demande en 2027 [D0-§6]",
                                "Coût d'une passerelle du marché [D2-§4]"],
    "questions_pour_humain": ["Le recrutement de deux ingénieurs d'exploitation est-il budgété pour 2027 ?",
                              "Les équipes qui ont déjà leurs serveurs acceptent-elles d'y renoncer ?"],
    "suffisant": True,
}

AVIS = {
    "architecte_entreprise": {
        "position": "favorable_sous_conditions", "option_preferee": "C",
        "resume": "Une passerelle commune est la vraie décision structurante : elle rend l'infrastructure réversible. "
                  "L'option C l'apporte sans ignorer les contraintes RH et de latence.",
        "arguments": [
            _arg("Les applications appellent déjà une interface compatible OpenAI : derrière une passerelle, changer de modèle ou d'infrastructure ne les touche pas [D4-§4].", "D4-§4"),
            _arg("Sans passerelle, chaque application porte l'adresse de son serveur : l'option B fige la dispersion actuelle [D4-§4] [D0-§3].", "D4-§4", "D0-§3"),
            _arg("Les données Confidentiel RH imposent de toute façon une instance isolée : une plateforme unique stricte (A) n'est pas possible [D3-§1].", "D3-§1"),
        ],
        "hypotheses": ["La passerelle sera prête avant le raccordement des premières équipes."],
        "risques": ["Une passerelle développée en interne devient elle-même une dette."],
        "inconnues": ["Coût et maturité des passerelles du marché [D2-§4]."],
        "conditions_changement": ["Une passerelle du marché couvrant les exigences du RSSI à moindre coût."],
        "confiance": 4,
    },
    "exploitation": {
        "position": "reserve", "option_preferee": "C",
        "resume": "Techniquement, C est la bonne cible. Mais avec un seul ingénieur formé et sans astreinte, aucune option n'est exploitable en production en 2027 sans recrutement.",
        "arguments": [
            _arg("Un seul ingénieur connaît vLLM ; une astreinte demande au moins trois personnes, et un recrutement prend quatre à six mois [D4-§1].", "D4-§1"),
            _arg("Les déploiements existants ont jusqu'à sept mois de retard de version : l'option B multiplierait ces retards [D4-§2].", "D4-§2"),
            _arg("Le groupe réservé au Service client isole la latence de la charge des autres équipes [D2-§3] [D1-§3].", "D2-§3", "D1-§3"),
        ],
        "hypotheses": ["Deux recrutements aboutissent avant la mise en service."],
        "risques": ["Mise en production du Service client sans astreinte."],
        "inconnues": ["Latence réelle sur une infrastructure partagée [D1-§3]."],
        "conditions_changement": ["Un plan de recrutement et de formation engagé avant la commande."],
        "confiance": 4,
    },
    "rssi": {
        "position": "favorable_sous_conditions", "option_preferee": "C",
        "resume": "L'option B a déjà produit un incident ; la A ne respecte pas la règle sur les données RH. La C est acceptable si la passerelle applique les exigences de sécurité dès le premier jour.",
        "arguments": [
            _arg("Un déploiement d'équipe est resté accessible sans authentification pendant trois semaines, sans journaux [D3-§2].", "D3-§2"),
            _arg("Les données Confidentiel RH exigent une instance physiquement isolée [D3-§1].", "D3-§1"),
            _arg("Une plateforme partagée est acceptable avec authentification par équipe, quotas et journaux cloisonnés [D3-§3] [D3-§4].", "D3-§3", "D3-§4"),
        ],
        "hypotheses": ["Les trois déploiements existants seront migrés puis arrêtés."],
        "risques": ["Maintien en parallèle des déploiements d'équipe non revus."],
        "inconnues": ["Calendrier de migration des déploiements existants."],
        "conditions_changement": ["Un refus des équipes d'arrêter leurs déploiements."],
        "confiance": 5,
    },
    "finops": {
        "position": "favorable_sous_conditions", "option_preferee": "A",
        "resume": "La mutualisation est économiquement évidente : 18 % d'utilisation sur des GPU dédiés, c'est 82 % de capacité payée pour rien. L'option A est la moins chère par GPU utile.",
        "arguments": [
            _arg("Les cinq GPU existants sont utilisés à 18 % en moyenne, avec des pics décalés entre équipes [D1-§2].", "D1-§2"),
            _arg("B coûte 2,4 M€ d'investissement, au-delà du plafond de 1,8 M€ [D2-§2] [D0-§5].", "D2-§2", "D0-§5"),
            _arg("A coûte 1,6 M€ pour 16 GPU, contre 1,75 M€ pour C [D2-§1] [D2-§3].", "D2-§1", "D2-§3"),
        ],
        "hypotheses": ["Un taux d'utilisation de 70 % de la plateforme centrale [D2-§4]."],
        "risques": ["Refacturation contestée par les équipes."],
        "inconnues": ["Coût du personnel caché de l'option B [D2-§2]."],
        "conditions_changement": ["Un taux d'utilisation mesuré nettement inférieur à 70 %."],
        "confiance": 3,
    },
    "equipes_produit": {
        "position": "favorable_sous_conditions", "option_preferee": "B",
        "resume": "Les équipes qui ont avancé seules ne doivent pas être pénalisées : le Service client tient sa latence aujourd'hui avec ses propres GPU. Un guichet central risque de ralentir tout le monde.",
        "arguments": [
            _arg("Le Service client exige 1,5 s en première réponse ; personne n'a testé cette latence sur une infrastructure partagée [D1-§3].", "D1-§3"),
            _arg("Les équipes veulent changer de modèle deux fois par an [D1-§5].", "D1-§5"),
            _arg("Les quotas centraux ne sont pas encore acceptés par les équipes [D0-§6].", "D0-§6"),
        ],
        "hypotheses": ["Les équipes savent exploiter leurs serveurs dans la durée."],
        "risques": ["Les équipes en attente patientent encore six mois."],
        "inconnues": ["Délai réel d'accès à une plateforme centrale."],
        "conditions_changement": ["Une garantie de latence et de délai de raccordement."],
        "confiance": 3,
    },
    "referent_data_ia": {
        "position": "favorable_sous_conditions", "option_preferee": "C",
        "resume": "Six modèles renouvelés deux fois par an : c'est intenable équipe par équipe. Une plateforme centrale qui teste et publie les modèles une fois pour tous est la condition de la qualité.",
        "arguments": [
            _arg("Trois familles en deux tailles, renouvelées environ deux fois par an [D1-§5].", "D1-§5"),
            _arg("Chaque nouvelle famille demande un test de compatibilité et de performance [D4-§2].", "D4-§2"),
            _arg("La passerelle permet de basculer une application d'un modèle à l'autre sans la modifier [D4-§4].", "D4-§4"),
        ],
        "hypotheses": ["Un catalogue de modèles commun suffit à la majorité des usages."],
        "risques": ["Un catalogue trop restreint pousse les équipes à contourner la plateforme."],
        "inconnues": ["Besoins de modèles spécialisés hors catalogue."],
        "conditions_changement": ["Des besoins de modèles très spécifiques à chaque équipe."],
        "confiance": 4,
    },
}

CONTRE_ANALYSE = {
    "resume": "Toute l'économie des options A et C repose sur un taux d'utilisation de 70 % qui n'a jamais été mesuré, "
              "et toute la faisabilité sur une équipe d'exploitation qui n'existe pas encore.",
    "hypotheses_fragiles": [
        _arg("Les 70 % d'utilisation viennent d'une étude du fournisseur de GPU, pas d'une mesure interne [D2-§4].", "D2-§4"),
        _arg("Le coût de B omet le temps passé par chaque équipe à exploiter ses serveurs [D2-§2].", "D2-§2"),
        _arg("La latence du Service client sur une infrastructure partagée n'a pas été testée [D1-§3].", "D1-§3"),
        _arg("La passerelle à 0,15 M€ est un développement interne jamais comparé au marché [D2-§4].", "D2-§4"),
    ],
    "scenarios_echec": [
        "A : la plateforme partagée ne tient pas la latence du Service client, qui garde ses GPU ; on finance deux infrastructures.",
        "B : un nouvel incident de sécurité sur un déploiement non revu, sans journaux pour en mesurer l'ampleur.",
        "C : la plateforme est livrée sans les trois ingénieurs d'astreinte ; la première panne du Service client discrédite le projet.",
    ],
    "angles_morts": [
        "La date limite de prix du fournisseur (31 janvier) pousse à décider avant d'avoir mesuré quoi que ce soit [D4-§3].",
        "Rien n'est prévu si la demande dépasse les sept équipes connues [D2-§4].",
    ],
    "critique_composition": {
        "angles_absents": [
            "Les Achats sont absents alors que la commande de GPU, sa date limite et ses conditions sont sur le chemin critique [D4-§3].",
            "Aucun utilisateur métier du Service client, première application en production, ne siège.",
        ],
        "voix_surrepresentees": ["Architecture, Exploitation et Data/IA partagent la même perspective d'infrastructure ; "
                                 "les équipes qui consomment la plateforme n'ont qu'une voix."],
        "commentaire": "Comité adapté au choix technique, mais faible sur l'approvisionnement et sur le point de vue des équipes utilisatrices.",
    },
}

OBJECTIONS = {"objections": [
    {"id": "O1", "cible": "finops", "texte": "Ton argument repose sur 70 % d'utilisation, un chiffre de fournisseur. Que vaut l'option A à 40 % ?", "sources": ["D2-§4", "D1-§2"]},
    {"id": "O2", "cible": "equipes_produit", "texte": "L'option B ne chiffre pas le temps d'exploitation des équipes et a déjà produit un incident de sécurité. Est-ce vraiment plus rapide pour elles ?", "sources": ["D2-§2", "D3-§2"]},
    {"id": "O3", "cible": "exploitation", "texte": "Tu soutiens C, qui demande 3,5 ETP, alors qu'un seul ingénieur est formé et qu'un recrutement prend six mois. Quelle est ta condition minimale ?", "sources": ["D4-§1", "D2-§3"]},
    {"id": "O4", "cible": "rssi", "texte": "L'instance RH est isolée, mais passera-t-elle par la passerelle commune et ses journaux partagés ?", "sources": ["D3-§1", "D3-§4"]},
    {"id": "O5", "cible": "architecte_entreprise", "texte": "Tu fais de la passerelle la décision structurante, mais son coût n'a jamais été comparé au marché. Faut-il la construire ?", "sources": ["D2-§4"]},
]}

REPONSES = {
    "finops": {"reponses": [{"objection_id": "O1", "attitude": "concede",
                             "texte": "Juste : à 40 %, l'avantage de A sur C se réduit fortement. Je me rallie à C, en commençant par 12 GPU centraux et en mesurant l'utilisation trois mois avant d'étendre [D2-§3]."}],
               "position_revisee": "favorable_sous_conditions", "option_revisee": "C",
               "resume": "C, avec une extension conditionnée à l'utilisation mesurée."},
    "equipes_produit": {"reponses": [{"objection_id": "O2", "attitude": "ajuste",
                                      "texte": "L'incident et le coût caché sont réels. Ce que demandent les équipes, c'est une latence garantie : le groupe réservé au Service client de l'option C y répond [D2-§3] [D1-§3]."}],
                        "position_revisee": "favorable_sous_conditions", "option_revisee": "C",
                        "resume": "Ralliement à C si le Service client garde des GPU réservés et si le raccordement prend moins d'un mois."},
    "exploitation": {"reponses": [{"objection_id": "O3", "attitude": "maintient",
                                   "texte": "Condition minimale : deux recrutements lancés dès la décision et une mise en production du Service client seulement quand l'astreinte compte trois personnes [D4-§1]."}],
                     "position_revisee": "favorable_sous_conditions", "option_revisee": "C",
                     "resume": "Favorable à C sous condition d'astreinte constituée avant la production."},
    "rssi": {"reponses": [{"objection_id": "O4", "attitude": "ajuste",
                           "texte": "L'instance RH doit avoir sa propre passerelle, ou au minimum ses propres journaux et administrateurs, conformément à la politique [D3-§1]."}],
             "position_revisee": "favorable_sous_conditions", "option_revisee": "C",
             "resume": "C, avec une instance RH isolée jusqu'à la journalisation."},
    "architecte_entreprise": {"reponses": [{"objection_id": "O5", "attitude": "maintient",
                                            "texte": "La passerelle reste structurante ; c'est sa fabrication qui est à arbitrer. Je propose une consultation du marché avant fin décembre [D2-§4]."}],
                              "position_revisee": "favorable_sous_conditions", "option_revisee": "C",
                              "resume": "C, passerelle achetée ou construite selon le résultat d'une consultation."},
}


def _crit(critere, evaluation, justification, *sources):
    return {"critere": critere, "evaluation": evaluation, "justification": justification, "sources": list(sources)}


SYNTHESE = {
    "recommandation": "Retenir l'option C par étapes : plateforme centrale de 12 GPU derrière une passerelle commune, "
                      "instance isolée pour les données RH et GPU réservés au Service client ; commander avant le 31 janvier, "
                      "mais n'étendre la plateforme qu'après trois mois d'utilisation mesurée. Écarter B, qui dépasse le budget "
                      "et prolonge les risques de sécurité constatés.",
    "conditions": [
        "Deux recrutements d'exploitation lancés dès la décision ; Service client en production seulement avec une astreinte de trois personnes.",
        "Consultation du marché pour la passerelle avant fin décembre.",
        "Instance RH avec ses propres journaux et administrateurs.",
        "Test de latence du Service client avant sa migration.",
        "Migration puis arrêt des trois déploiements existants dans les six mois.",
        "Taux d'utilisation mesuré trois mois avant toute extension.",
    ],
    "options": [
        {"nom": "A - Plateforme centrale",
         "pour": ["Coût par GPU le plus bas si l'utilisation est forte", "Une seule plateforme à sécuriser"],
         "contre": ["Incompatible avec la règle sur les données RH", "Latence du Service client non garantie", "Repose sur 70 % d'utilisation non mesurés"],
         "criteres": [
             _crit("Coût total", "mitige", "1,6 M€, mais sur une hypothèse d'utilisation non mesurée.", "D2-§1", "D2-§4"),
             _crit("Sécurité et isolation des données", "defavorable", "Une plateforme unique ne respecte pas l'isolation exigée pour les données RH.", "D3-§1"),
             _crit("Performance", "inconnu", "Latence du Service client sur infrastructure partagée jamais testée.", "D1-§3"),
             _crit("Autonomie des équipes", "mitige", "Accès commun, mais soumis à des quotas non encore acceptés.", "D0-§6"),
             _crit("Exploitabilité", "mitige", "Une seule plateforme, mais l'équipe d'astreinte n'existe pas.", "D4-§1"),
             _crit("Réversibilité", "favorable", "Passerelle compatible OpenAI : changement de modèle sans toucher aux applications.", "D4-§4"),
             _crit("Délai de mise à disposition", "mitige", "Cinq mois de livraison après commande.", "D4-§3"),
         ],
         "preuves": [_arg("Investissement de 1,6 M€ pour 16 GPU.", "D2-§1"), _arg("Données RH : instance isolée obligatoire.", "D3-§1")],
         "objections": ["O1 : l'utilisation de 70 % est une donnée fournisseur ; FinOps s'est rallié à C."],
         "incertitudes": ["Taux d'utilisation réel.", "Latence partagée."]},
        {"nom": "B - Hébergement par équipe",
         "pour": ["Autonomie immédiate des équipes déjà équipées", "Latence maîtrisée par chaque équipe"],
         "contre": ["2,4 M€, au-delà du plafond de 1,8 M€", "Incident de sécurité déjà constaté", "Versions en retard et compétences dispersées"],
         "criteres": [
             _crit("Coût total", "defavorable", "2,4 M€ hors temps d'exploitation des équipes, non chiffré.", "D2-§2", "D0-§5"),
             _crit("Sécurité et isolation des données", "defavorable", "Un déploiement exposé sans authentification pendant trois semaines.", "D3-§2"),
             _crit("Performance", "favorable", "Chaque équipe dimensionne pour son propre pic.", "D2-§2"),
             _crit("Autonomie des équipes", "favorable", "Chaque équipe choisit ses modèles et son rythme.", "D1-§5"),
             _crit("Exploitabilité", "defavorable", "Jusqu'à sept mois de retard de version sur les déploiements actuels.", "D4-§2"),
             _crit("Réversibilité", "defavorable", "Sans passerelle, chaque application porte l'adresse de son serveur.", "D4-§4"),
             _crit("Délai de mise à disposition", "inconnu", "Dépend de chaque équipe ; aucun planning consolidé.", ),
         ],
         "preuves": [_arg("Investissement de 2,4 M€.", "D2-§2"), _arg("Incident d'août 2026 sans journaux.", "D3-§2")],
         "objections": ["O2 : coût d'exploitation caché et incident ; Équipes produit s'est rallié à C."],
         "incertitudes": ["Temps réellement consacré par les équipes à l'exploitation."]},
        {"nom": "C - Centrale et déploiements dédiés",
         "pour": ["Respecte la règle sur les données RH", "Latence du Service client isolée", "Passerelle commune et réversibilité", "Dans le budget"],
         "contre": ["Exploitation la plus exigeante (3,5 ETP)", "Passerelle encore à choisir"],
         "criteres": [
             _crit("Coût total", "mitige", "1,75 M€, sous le plafond de 1,8 M€, mais 1,0 M€ par an de fonctionnement.", "D2-§3", "D0-§5"),
             _crit("Sécurité et isolation des données", "favorable", "Instance RH isolée, exigences du RSSI applicables à la passerelle.", "D2-§3", "D3-§3"),
             _crit("Performance", "mitige", "GPU réservés au Service client ; latence à confirmer par un test.", "D2-§3", "D1-§3"),
             _crit("Autonomie des équipes", "mitige", "Accès commun avec quotas ; dépend du délai de raccordement.", "D0-§6"),
             _crit("Exploitabilité", "mitige", "Faisable seulement avec deux recrutements et une astreinte.", "D4-§1"),
             _crit("Réversibilité", "favorable", "Passerelle commune sur interface compatible OpenAI.", "D4-§4"),
             _crit("Délai de mise à disposition", "mitige", "Cinq mois de livraison ; commande avant le 31 janvier.", "D4-§3"),
         ],
         "preuves": [_arg("Investissement de 1,75 M€ dont instance RH et groupe Service client.", "D2-§3"),
                     _arg("Exploitation : un seul ingénieur formé aujourd'hui.", "D4-§1")],
         "objections": ["O3 : astreinte à constituer avant la production.", "O4 : journaux propres à l'instance RH.",
                        "O5 : passerelle à acheter ou construire selon une consultation."],
         "incertitudes": ["Résultat de la consultation sur la passerelle.", "Aboutissement des recrutements."]},
    ],
    "accords": ["L'option B est écartée : hors budget et déjà source d'un incident [D2-§2] [D3-§2].",
                "Une passerelle commune est indispensable, quelle que soit l'option retenue.",
                "Les données RH exigent une instance isolée [D3-§1]."],
    "desaccords": [
        {"sujet": "Calendrier", "positions": "Exploitation refuse une production du Service client avant l'astreinte ; Équipes produit veut un raccordement en moins d'un mois."},
        {"sujet": "Passerelle", "positions": "Architecture la juge structurante ; le contradicteur rappelle que son coût n'a jamais été comparé au marché."},
    ],
    "hypotheses_fragiles": ["Taux d'utilisation de 70 %", "Recrutements aboutis en six mois", "Latence partagée du Service client"],
    "informations_a_obtenir": ["Offres du marché pour la passerelle", "Test de latence du Service client",
                               "Conditions des Achats au-delà du 31 janvier", "Prévision de la demande 2027"],
}
