# Note du RSSI

## Classification et cloisonnement

La politique de sécurité du groupe interdit de traiter des données « Confidentiel RH » sur une infrastructure partagée avec d'autres équipes : une instance physiquement isolée, avec ses propres journaux et ses propres administrateurs, est requise. Les données « Interne » et « Confidentiel » peuvent être traitées sur une infrastructure partagée si chaque équipe dispose d'une authentification propre et que les échanges sont cloisonnés.

## Incident constaté

En août 2026, l'un des déploiements installés par une équipe a été accessible sans authentification sur le réseau interne pendant trois semaines. Aucun journal ne permet de savoir s'il a été utilisé par d'autres personnes. Les trois déploiements existants appliquent chacun leurs propres réglages ; aucun n'a fait l'objet d'une revue de sécurité.

## Exigences pour une plateforme partagée

- Authentification par équipe et par application, avec des clés révocables.
- Quotas par équipe pour éviter qu'un usage n'en prive un autre.
- Pas de conservation du contenu des requêtes par défaut ; conservation possible sur demande d'une équipe, à durée limitée.
- Revue de sécurité avant toute mise en service.

## Journalisation

Les journaux d'accès (qui, quand, quel modèle, combien de tokens) doivent être conservés un an et cloisonnés par équipe. Aucun déploiement actuel ne les produit.
