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

## Confidentialité des dossiers

Chaque modèle déclare son hébergement dans `config.yaml` : `hebergement: interne` (vLLM sur site, par exemple)
ou `externe` (modèles `-cloud` d'Ollama, API publiques). Sans déclaration, un modèle est considéré comme externe.

Chaque dossier est **confidentiel par défaut**. Seul un fichier `_dossier.yaml` contenant
`confidentiel: false` permet de l'envoyer à des modèles externes (c'est le cas du dossier fictif Altaris).
En mode réel, une séance sur un dossier confidentiel est refusée dès qu'un seul agent pointe vers un
modèle externe : le bouton est désactivé dans l'interface et l'API répond 403. L'en-tête affiche en
permanence le statut du dossier et l'hébergement des modèles. Le mode simulation n'envoie aucune donnée.

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
