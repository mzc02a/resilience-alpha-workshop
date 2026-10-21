# Recette locale — 21 octobre 2026

Tests effectués sur le démonstrateur du PC hôte, pas sur un réseau tiers.

* 4 tests automatisés réussis : secret de contrôle, limitation par source et
  expiration, séquence de trois analyses distinctes/cooldown, rejet des données
  périmées et remise à zéro après interruption.
* WAF : /health retourne 200 ; accès public /internal/metrics retourne 404 ;
  requête XSS de laboratoire bloquée en 403.
* Activation sans secret : 403. Avec secret interne : limitation temporaire active.
* Rafale de 8 requêtes non authentifiées, avec fausses adresses X-Real-IP :
  3 réponses 401 puis 5 réponses 429. L'en-tête fourni par le client ne contourne
  pas la limite. Expiration après 15 secondes vérifiée.
* Un paiement fictif créé puis relu à travers le WAF après expiration du limiteur.
* Recette complète réelle : 3 requêtes/s pendant 100 secondes, en lecture seule
  pour le trafic de test. Résultats : 232 réponses 200 et 68 réponses 429.
* Le modèle ML existant a déclenché le contrôleur automatiquement, lequel a
  activé le limiteur et créé/relu un paiement fictif de sonde avec succès.
  Identifiant : 7cf2a362-830b-49a2-8bc1-7a1f7437d87f.
* Prometheus : up=1 pour gateway et remediator ; configuration validée par promtool.

Ce test prouve le raccordement ML → action automatique → vérification métier.
Il ne démontre pas encore une panne métier initiale, une reprise après une attaque
depuis le deuxième PC, ni le taux de détection général du modèle. Ces recettes
restent à réaliser sur le hotspot de laboratoire et à filmer.
