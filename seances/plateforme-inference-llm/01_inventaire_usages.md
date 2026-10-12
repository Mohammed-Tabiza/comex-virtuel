# Inventaire des usages (relevé de la DSI, octobre 2026)

## Équipes demandeuses

| Équipe | Usage | Situation actuelle |
|---|---|---|
| Service client | Suggestions de réponse en direct pendant les échanges | 2 GPU dédiés, installés en mars 2026 |
| Data | Extraction d'informations dans des documents, traitements de nuit | 2 GPU dédiés |
| Développement | Assistant de code dans l'environnement de développement | 1 GPU dédié |
| Juridique | Synthèse de contrats clients | En attente |
| RH | Réponses aux questions des collaborateurs sur la paie et les congés | En attente |
| Marketing | Rédaction de contenus | En attente |
| Finance | Aide aux rapprochements comptables | En attente |

## Volumes et profils de charge

- Pic cumulé estimé : 140 requêtes simultanées, dont 60 pour le Service client entre 9 h et 12 h.
- Data : environ 2 millions de pages par mois, traitées la nuit ; aucune contrainte de latence.
- Taux d'utilisation moyen mesuré sur les cinq GPU déjà installés : 18 % sur le dernier trimestre (supervision de la DSI). Les GPU du Service client sont saturés aux heures de pointe et inactifs la nuit ; ceux de Data, l'inverse.

## Exigences de latence

- Service client : première réponse en moins de 1,5 seconde pour 95 % des requêtes, mesurée côté utilisateur.
- Développement : première suggestion en moins de 2 secondes, sinon l'outil n'est pas utilisé.
- Autres équipes : quelques secondes acceptables ; Data : aucune exigence.
- Aucun test de charge n'a été réalisé pour mesurer la latence du Service client sur une infrastructure partagée.

## Classification des données

- Interne : Développement, Marketing, Data (documents fournisseurs).
- Confidentiel : Service client, Juridique (contrats clients), Finance.
- Confidentiel RH : RH (données personnelles des collaborateurs).

## Modèles demandés

Trois familles de modèles ouverts sont demandées, en deux tailles chacune. Les équipes souhaitent pouvoir changer de modèle au fil des nouvelles versions, environ deux fois par an.
