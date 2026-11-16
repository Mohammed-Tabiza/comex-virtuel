# Architecture et migration (direction des systèmes d'information, décembre 2026)

## Plateforme SaaS de l'éditeur

- L'éditeur, fondé en 2016, compte 2 banques clientes en production de taille comparable (300 000 et 500 000 clients).
- Sa plateforme est hébergée chez un fournisseur de cloud américain, dans une région située en Union européenne.
- Une migration menée par cet éditeur dans un autre pays a provoqué une interruption de 2 jours lors de la bascule d'un lot ; l'éditeur indique avoir revu sa méthode depuis.
- Les évolutions de la plateforme sont livrées tous les mois à tous les clients ; la banque ne choisit pas leur calendrier.

## Plateforme mutualisée de la CIBR

- La CIBR exploite sa plateforme dans deux centres de données en France, qu'elle possède.
- Les 5 banques membres partagent les mêmes versions ; une demande d'évolution propre à une banque passe par un comité où chaque membre dispose d'une voix.
- Délai moyen de prise en compte d'une évolution demandée : 9 mois.
- La plateforme de la CIBR repose elle-même en partie sur un mainframe, modernisé en 2023.

## Modernisation du mainframe

- La méthode consiste à exposer les fonctions existantes par des API, puis à réécrire les modules un par un autour du système existant.
- Un premier module (épargne) a été réécrit en 2025 en 11 mois, pour 1,6 M€.
- La direction des systèmes d'information estime à 25 le nombre de modules à traiter.

## Reprise des données

- Les options A et C exigent une reprise complète de l'historique des comptes (20 ans).
- Aucun inventaire des données n'a été réalisé ; les éditeurs estiment cette phase entre 9 et 18 mois.
- Chaque bascule de lot doit tenir dans un week-end pour respecter la tolérance d'interruption de 4 heures en jours ouvrés.
