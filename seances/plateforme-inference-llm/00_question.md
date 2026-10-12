# Question soumise au comité d'architecture

> Cas fictif destiné à tester le comité virtuel. Le Groupe Altaris, ses équipes, ses chiffres et ses documents sont inventés.

## Question posée

Faut-il mutualiser l'inférence des grands modèles de langage du Groupe Altaris sur une plateforme vLLM centrale, laisser chaque équipe héberger ses propres modèles, ou combiner une plateforme centrale et des déploiements dédiés ?

## Décision attendue

Le comité d'architecture du 9 décembre 2026 doit retenir une architecture cible pour 2027 et autoriser la commande des serveurs GPU correspondants. Compte tenu des délais de livraison (voir D4), la commande doit partir avant fin janvier 2027 pour une mise en service avant l'été.

## Contexte

Depuis le pilote de l'assistant interne, sept équipes utilisent ou veulent utiliser des modèles de langage ouverts. Trois d'entre elles ont déjà installé leurs propres serveurs GPU avec vLLM, sans cadre commun : Service client, Data et Développement. Les autres attendent une solution. La DSI ne dispose aujourd'hui d'aucune plateforme partagée, d'aucune passerelle d'accès commune et d'aucune refacturation des coûts GPU.

## Options envisagées

- **Option A, plateforme centrale mutualisée** : un cluster vLLM unique opéré par la DSI, exposé par une passerelle commune (authentification, quotas, journalisation), pour toutes les équipes.
- **Option B, hébergement par équipe** : chaque équipe achète, héberge et exploite ses serveurs et ses modèles, dans le respect de règles communes de sécurité.
- **Option C, plateforme centrale et déploiements dédiés** : la plateforme centrale pour la majorité des usages, et des déploiements dédiés, opérés par la DSI, pour les besoins particuliers (données les plus sensibles, latence très faible).

## Contraintes non négociables

- Les données classées « Confidentiel RH » ne sont jamais traitées sur une infrastructure partagée avec d'autres équipes (politique de sécurité du groupe, voir D3).
- Le budget d'investissement 2027 pour l'inférence ne peut dépasser 1,8 M€.
- Le service client exige un délai de première réponse inférieur à 1,5 seconde pour 95 % des requêtes.

## Ce qu'on ne sait pas encore

- Le taux d'utilisation réel d'une plateforme mutualisée : il n'a jamais été mesuré.
- L'évolution de la demande en 2027, au-delà des sept équipes connues.
- L'acceptation par les équipes de quotas fixés centralement.
- Le coût d'une passerelle d'accès du marché, par rapport à un développement interne.
