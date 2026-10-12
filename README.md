# COMEX virtuel, version 1

Huit agents (président, six experts, un contradicteur) analysent une question stratégique
à partir d'un dossier documentaire, selon un protocole borné, sous le contrôle d'un décideur humain.
L'interface « salle du COMEX » montre chaque agent au travail en temps réel.

![Aperçu](docs/apercu.png)

COMEX **consultatif** : il prépare un arbitrage, il ne décide pas.

## Démarrage rapide (mode simulation, sans modèle)

```bash
pip install -r requirements.txt
python -m uvicorn app.server:app --port 8000
```

Ouvrez http://localhost:8000, choisissez `assistant-ia-2027`, cliquez **Ouvrir la séance**.
En mode simulation, les réponses sont préenregistrées pour le dossier d'exemple : c'est fait pour
vérifier l'interface et le protocole, pas pour juger la qualité des analyses.

## Brancher vos modèles Ollama (PowerShell)

Les deux modèles sont configurés dans `config.yaml` sur `http://localhost:11434/v1`,
avec une clé API vide :

- `gpt-oss:120b-cloud`
- `gemma4:31b-cloud`

1. Avec Ollama démarré, vérifiez les modèles exposés :

```powershell
(Invoke-RestMethod -Uri "http://localhost:11434/v1/models").data | Select-Object id
```

2. Activez les appels réels et lancez l'application dans la même session PowerShell :

```powershell
$env:COMEX_MODE = "reel"
python -m uvicorn app.server:app --port 8000
```

Les URL sont déjà renseignées dans `config.yaml` : aucune variable `QWEN27_URL`,
`QWEN14_URL`, `MISTRAL_URL` ni clé API n'est nécessaire. Si Ollama tourne sur une autre
machine ou un autre port, modifiez les champs `base_url` dans ce fichier.

La répartition des agents sur les modèles se change dans `config.yaml` (section `agents`), sans toucher au code.

Pour revenir à la simulation, définissez `$env:COMEX_MODE = "simulation"` puis relancez l'application.

## Le protocole d'une séance

1. **Cadrage** : le président reformule la question, les options, les critères et les inconnues.
2. **Validation humaine** : la séance s'arrête. Vous validez, ou apportez des précisions et demandez un nouveau cadrage.
3. **Analyses indépendantes** : les six experts et la contre-analyse du contradicteur tournent en parallèle,
   **sans voir les avis des autres**.
4. **Contradiction** : le contradicteur lit tous les avis et adresse des objections ciblées.
5. **Réponses** : chaque expert visé maintient, ajuste ou concède.
6. **Synthèse** : le président restitue options, accords, désaccords persistants et une recommandation conditionnelle.
7. **Arbitrage** : vous saisissez votre décision ; le compte rendu Markdown est téléchargeable.

Chaque contribution est attribuée, horodatée, liée au modèle qui l'a produite, et jamais réécrite.
Les références citées (`[D1-§2]`) sont vérifiées : une référence inventée apparaît barrée en rouge.
Les références dans le débat, le dossier et la synthèse ouvrent le passage cité avec son
document et son fichier d'origine. La fenêtre se ferme avec **Fermer**, Échap ou un clic à l'extérieur.

L'onglet **Décision** compare chaque option selon les critères du cadrage et distingue
les preuves documentées, les objections et les incertitudes. Les mêmes éléments figurent
dans le compte rendu Markdown. Une citation existante n'atteste pas à elle seule la validité
d'une affirmation : le passage reste à vérifier. `protocole.synthese_max_tokens` fixe le budget
de réponse de la synthèse détaillée (6 144 tokens par défaut).

## Carte des positions et vue d'ensemble des critères

L'onglet **Positions** place chaque expert sur une carte : la colonne indique l'option qu'il préfère,
la hauteur sa position (favorable, sous conditions, réservé, défavorable). Les points apparaissent au fil
des avis initiaux, puis glissent vers l'option et la position révisées après la contradiction.
Le bouton **Rejouer l'évolution** relance l'animation ; un court récit résume ce qui a bougé.
Le champ `option_revisee` de la réponse aux objections alimente ce déplacement.

En tête de l'onglet **Décision**, une carte de chaleur résume l'évaluation de chaque option par critère.
Les cases hachurées « À documenter » montrent ce que personne ne sait encore ; un clic sur une case
affiche la justification et ses sources.

## Rejouer une séance (démo sans risque)

Chaque séance terminée est enregistrée dans `seances/<dossier>/rejeux/<id>.json` : journal complet
des événements, configuration des agents au moment de la séance et compte rendu.
Pour la rejouer, choisissez-la dans la liste à côté du dossier, réglez la vitesse (×1, ×2, ×4)
et cliquez **Rejouer la séance**. Aucun modèle n'est appelé : le rejeu fonctionne sans réseau,
même après un redémarrage du serveur.

Usage conseillé pour une présentation : faire tourner la séance la veille avec les vrais modèles,
puis la rejouer le jour J, en gardant le mode direct pour une seconde démonstration.
Les silences sont raccourcis ; la validation du cadrage reste affichée quelques secondes.

Un rejeu contient le contenu des analyses : versionnez uniquement ceux des dossiers non confidentiels.

### Traçabilité et format des rejeux

Depuis la version 2 du format, un rejeu enregistre aussi :
- la **composition** complète (gabarit et version, rôle et version de chaque membre, modèle, mandat
  effectivement envoyé et mandat de la bibliothèque) et les **révisions** de l'équipe ;
- la **consigne complète** de chaque appel, avec le rôle, sa version et le modèle ; chaque contribution
  y renvoie par `meta.consigne.index` (`GET /api/dossiers/<nom>/rejeux/<id>/consignes/<index>`) ;
- l'**empreinte** du dossier et du préfixe commun : le dossier n'est pas recopié, mais on sait s'il a changé.

Les rejeux de version 1 restent lisibles tels quels : l'API les ramène au format courant sans modifier le fichier.

Le compte rendu comporte une section **Composition du comité** : membres, versions des rôles, modèles et
hébergement, mandats modifiés (différence mot à mot avec la bibliothèque), rôles obligatoires retirés avec
leur justification, révisions de l'équipe, couverture des critères, critique de la composition par le
contradicteur et compactages éventuels du contexte.

## Confidentialité des dossiers

Chaque modèle déclare son hébergement dans `config.yaml` : `hebergement: interne` (vLLM sur site, par exemple)
ou `externe` (modèles `-cloud` d'Ollama, API publiques). Sans déclaration, un modèle est considéré comme externe.

Chaque dossier est **confidentiel par défaut**. Seul un fichier `_dossier.yaml` contenant
`confidentiel: false` permet de l'envoyer à des modèles externes (c'est le cas du dossier fictif Altaris).
En mode réel, une séance sur un dossier confidentiel est refusée dès qu'un seul agent pointe vers un
modèle externe : le bouton est désactivé dans l'interface et l'API répond 403. L'en-tête affiche en
permanence le statut du dossier et l'hébergement des modèles. Le mode simulation n'envoie aucune donnée.

## Comités et dossiers d'exemple

| Gabarit | Pour quelles décisions | Experts présélectionnés |
|---|---|---|
| COMEX | Décisions stratégiques engageant plusieurs directions | Stratégie, Finance, Clients, Opérations, Risques, RH |
| Comité d'architecture | Choix techniques structurants | Architecture, Exploitation, RSSI, FinOps, Équipes produit, Data et IA |
| Comité de pilotage | Arbitrages en cours de projet | Sponsor, PMO, Finance, Risques, Opérations |
| Comité go/no-go | Mise en production, lancement, passage d'étape | Sponsor, PMO, Risques, Exploitation, RSSI |

La bibliothèque compte 15 rôles ; tout rôle peut siéger dans n'importe quel comité. Les rôles marqués
« obligatoire » dans l'écran de constitution ne se retirent qu'avec une justification écrite.

Deux dossiers fictifs, non confidentiels, permettent de comparer deux comités sur le même protocole :
- `assistant-ia-2027` (COMEX) : trajectoire 2027 d'un assistant d'IA générative interne ;
- `plateforme-inference-llm` (comité d'architecture) : plateforme vLLM centrale, hébergement par équipe, ou
  plateforme centrale avec déploiements dédiés. Le dossier contient des fragilités volontaires (taux
  d'utilisation non mesuré, coûts d'exploitation omis, latence non testée, date limite fournisseur).

En simulation, chaque dossier a son scénario préenregistré pour le comité présélectionné. Un rôle ajouté au
comité, ou un dossier sans scénario, reçoit une réponse générique signalée comme telle.

## Bibliothèque des comités et affectation des modèles

Les membres ne sont plus codés en dur : ils sont décrits dans `comites/`.

```
comites/fonctions/   président (ou animateur) et contradicteur, présents dans tous les comités
comites/roles/       une expertise par fichier : mandat, question clé, critères portés, version
comites/gabarits/    un type de comité : rôles obligatoires et optionnels, présélection, taille, critères
```

Chaque fichier porte une `version` : incrémentez-la quand vous modifiez un mandat, pour savoir
quelle consigne a produit quel avis. Le chargement vérifie la cohérence de la bibliothèque
(identifiants, rôles inconnus, présélection entre `experts.min` et `experts.max`, au plus 6 experts).

L'affectation des modèles se fait par règles dans `config.yaml`, section `affectation` :
modèle du président, du contradicteur, liste des modèles répartis à tour de rôle entre les experts,
exceptions par rôle (`par_role`) et membres en mode réflexion (`thinking`). Le champ `lignee`
de chaque modèle indique sa famille d'entraînement : avec `famille_differente_contradicteur: preference`,
le contradicteur reçoit un modèle d'une autre lignée que le président lorsqu'il en existe un.
L'ancien format `agents:` reste accepté.

Chaque séance porte sa propre composition, figée à son ouverture : membres, mandats effectivement
envoyés, modèles. Deux séances simultanées ne partagent ni comité ni affectation, et le garde-fou
de confidentialité s'applique aux modèles réellement retenus. Le comité présélectionné pour un dossier
se déclare dans son `_dossier.yaml` (`gabarit: comex`) ; à défaut, `gabarit_defaut` de `config.yaml`.
Dans l'interface, **Constituer le comité** ouvre l'écran de constitution : gabarit présélectionné par le
dossier, membres à cocher (les rôles du gabarit, puis les autres rôles de la bibliothèque), mandat et modèle
de chaque membre, justification écrite si un rôle obligatoire est retiré. Le panneau de droite évalue la
composition en continu : nombre d'experts, confidentialité, estimation du contexte et critères usuels couverts.
**Lancer la séance** n'est actif que pour une composition valide. À la validation du cadrage, le volet
**Équipe** permet de réviser le comité de la même façon, avec la couverture des critères du cadrage ; le motif
de la révision est consigné. Autour de la table, les sièges se répartissent selon le nombre de membres.

La composition peut aussi se choisir par l'API :

```bash
curl -X POST localhost:8000/api/seances -H 'content-type: application/json' -d '{
  "dossier": "assistant-ia-2027",
  "selection": ["finance", "risques", "operations"],
  "mandats": {"finance": "Concentre-toi sur la trésorerie 2027."},
  "affectations": {"risques": "gpt_oss"}
}'
```

`GET /api/comites` renvoie la bibliothèque (rôles, fonctions, gabarits).
`POST /api/dossiers/<nom>/composition` évalue une composition sans lancer de séance : erreurs,
blocage de confidentialité, estimation du contexte et couverture des critères usuels.

### Validation : critères, couverture et révision de l'équipe

Les critères découlent de la décision : l'animateur part des critères usuels du gabarit, écarte ceux qui
ne s'appliquent pas et ajoute ceux que la décision exige. Il désigne pour chaque critère les membres qui le
portent. À la validation, le décideur voit la **couverture** : les critères sans porteur sont signalés
(alerte, pas blocage).

L'équipe peut être révisée à la validation, avant les analyses ; la révision est tracée (ajouts, retraits,
modèles et mandats changés, motif) dans l'état de la séance et dans le journal. Ajouter ou retirer un expert
ne demande pas de nouveau cadrage ; modifier options, contraintes ou critères passe par « Demander un
nouveau cadrage ». Une fois les analyses lancées, l'équipe est définitive.

```bash
curl -X POST localhost:8000/api/seances/<id>/validation -H 'content-type: application/json' -d '{
  "action": "valider",
  "composition_revisee": {"selection": ["finance", "risques", "operations", "rh"],
                          "affectations": {"rh": "gpt_oss"}, "motif": "Décision budgétaire."}
}'
```

Le contradicteur reçoit la composition et la conteste aussi (`critique_composition` : angles absents,
voix surreprésentées).

### Contexte : estimation au lancement, contrôle réel avant les étapes lourdes

Au lancement, la longueur des contributions est inconnue : l'application estime prudemment
(`protocole.estimation_tokens`) les appels les plus exigeants et renvoie un niveau `ok`, `attention` ou
`depassement`. Avant les objections et la synthèse, le prompt réellement construit est mesuré, budget de
sortie compris, puis corrigé par l'écart observé entre l'estimation et le comptage réel des appels précédents.
S'il ne tient pas, les contributions sont compactées en deux niveaux ; s'il ne tient toujours pas, l'appel
n'a pas lieu et la séance s'arrête avec un message explicite.

## Créer un nouveau dossier

1. Créez un répertoire dans `seances/`, par exemple `seances/cloud-souverain/`.
2. Copiez `docs/gabarit_00_question.md` en `00_question.md` et remplissez-le.
3. Ajoutez vos documents en Markdown (`01_...md`, `02_...md`). Pour convertir des PDF ou des Word :
   `pip install markitdown` puis `markitdown rapport.pdf > 01_rapport.md`.
4. Chaque titre de niveau 2 (`## …`) devient une section citable. Les fichiers commençant par `_` sont ignorés.
5. Le dossier est confidentiel par défaut. S'il est fictif ou public, ajoutez un `_dossier.yaml`
   avec `confidentiel: false` et une `description`.

Budget : le compteur de l'en-tête estime la taille du dossier. Restez sous 15 000 tokens pour laisser
la place aux instructions et aux réponses avec un contexte de 32K.

## Structure

```
config.yaml            modèles, répartition des agents, protocole
app/agents.py          mandats des membres, schémas JSON des contributions, prompts
app/graph.py           graphe LangGraph : le protocole de séance
app/llm.py             appels vLLM en streaming (thinking, sortie structurée, reprise) et simulation
app/dossier.py         chargement et numérotation du dossier, vérification des sources
app/runtime.py         journal d'événements par séance (rejouable après déconnexion)
app/server.py          API FastAPI et flux SSE
app/rapport.py         compte rendu Markdown
app/simulation.py      réponses préenregistrées du dossier d'exemple
static/index.html      interface (sans étape de build)
seances/               dossiers de séance ; comptes rendus dans <dossier>/resultats/ (non versionnés),
                       rejeux dans <dossier>/rejeux/, métadonnées dans <dossier>/_dossier.yaml
tests/                 python -m unittest discover tests
```

## Dépannage

| Symptôme | Piste |
|---|---|
| Erreur 400 ou réponses vides avec la sortie structurée | Mettre `sortie_structuree: consigne` dans `config.yaml` |
| Un agent en erreur « non conforme au format JSON » | Augmenter `max_tokens` (le mode thinking consomme des tokens) |
| Agents du 14B tronqués | Dossier trop long pour `contexte_max` : le résumer |
| Rien ne s'affiche en direct derrière un proxy | Désactiver la mise en tampon pour `/api/seances/*/evenements` |

## Limites de cette version

- Séances conservées en mémoire : un redémarrage les efface (les comptes rendus restent sur disque).
  Pour reprendre une séance, remplacer `InMemorySaver` par `SqliteSaver` ou `PostgresSaver` dans `app/graph.py`.
- Un seul utilisateur, pas d'authentification : à placer derrière votre reverse proxy et votre SSO.
- Le dossier entier est injecté dans chaque prompt. Au-delà du budget de contexte, il faudra ajouter
  le résumé par document ou un outil `lire_section`.
- Avant d'adopter l'outil, comparer ses résultats à ceux d'un agent unique bien guidé sur les mêmes questions.
