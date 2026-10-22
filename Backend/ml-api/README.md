# Détection d'anomalies — référence persistante et covariance régularisée

Le detecteur courant apprend une covariance OAS avec regularisation de mesure,
sur une reference explicitement choisie par l'operateur. Isolation Forest reste
uniquement dans l'outil de diagnostic pour reconstruire les anciens scores.
Pas d'apprentissage sur le trafic d'incident : une reference deja acceptee est
persistee dans ml_model_data puis restauree au redemarrage si le profil correspond.
Aucun acces Docker, secret DB ou action de remediation depuis le ML.

Pour reprendre l'essai sans refaire 20 minutes : voir
[REPRISE-SANS-20-MIN.md](../../cyber/REPRISE-SANS-20-MIN.md).

## Contrat avec le responsable Prometheus / Grafana

Prometheus doit collecter payment-api sous `job="payment-api"` toutes les 15 secondes.
L'API métier expose désormais `/metrics` avec :

* `http_requests_total{status="200"}` et les autres codes HTTP.
  Initialiser aussi une série 5xx à zéro pour mesurer l'absence d'erreurs.
* `http_request_duration_seconds_sum` et `_count` (histogramme en secondes).
* `up{job="payment-api"}` produit par Prometheus.

Les noms/labels doivent correspondre aux requêtes dans `main.py`. Produire du trafic
normal varié et représentatif pendant au moins 16 minutes, idéalement 30 minutes.
Ne pas entraîner pendant une attaque. Des fenêtres chevauchantes ne constituent pas
des observations indépendantes ; évaluer séparément sur une période ultérieure.

### Préparer les données normales

Reconstruire payment-api après ajout de l'instrumentation :

```powershell
docker compose -f ../docker-compose.yml up -d --build payment-api
Invoke-WebRequest http://localhost:8000/metrics
python ml-api/normal_traffic.py --minutes 25
```

Le script demande un compte de test existant et son mot de passe masqué. Il crée
de vrais enregistrements de paiements fictifs dans la base configurée : utiliser
un compte dédié et coordonner la session avec le propriétaire de la base partagée.
Il s'arrête à la première erreur, sans entraîner automatiquement le modèle.
Prometheus doit collecter pendant toute la session. Les requêtes `/health` et
`/metrics` sont exclues des métriques métier pour ne pas fausser le trafic observé.
Pour une fenêtre indépendante d'évaluation, relancer après l'entraînement et
comparer les anomalies aux incidents effectivement provoqués et horodatés.

Configuration à transmettre au collègue (si Prometheus est dans le même Compose) :

```yaml
scrape_configs:
  - job_name: payment-api
    scrape_interval: 15s
    static_configs:
      - targets: ["payment-api:8000"]
```

Si Prometheus tourne sur un autre PC, `payment-api:8000` ne sera pas résolu :
convenir d'une cible accessible sur le réseau de laboratoire. Aucun changement
d'exposition réseau n'est effectué par ce composant.

## Lancement depuis Backend

```powershell
docker compose -f ../docker-compose.yml -f ml-api/compose.yml up -d --build ml-api
Invoke-RestMethod http://localhost:8002/health
Invoke-RestMethod -Method Post http://localhost:8002/train
Invoke-RestMethod http://localhost:8002/status
```

Le service Prometheus doit être sur le même réseau Compose. Sinon, définir
`PROMETHEUS_URL` vers le Prometheus accessible depuis le conteneur, par exemple
`http://host.docker.internal:9090` pour un port publié sur le PC hôte.
Ne pas publier le port d'entraînement sur le réseau. Garder un seul worker Uvicorn.

Ajouter ce job dans la configuration Prometheus de votre collègue :

```yaml
scrape_configs:
  # Conserver les autres jobs !
  - job_name: ml-api
    scrape_interval: 15s
    static_configs:
      - targets: ["ml-api:8002"]
```

Grafana utilise la source Prometheus existante. Panneaux :

* `ml_model_ready{service="payment-api"}` : modèle entraîné.
* `ml_telemetry_available{service="payment-api"}` : collecte utilisable.
* `ml_anomaly_score{service="payment-api"}` : positif = anomalie ; ce n'est pas une probabilité.
* `ml_anomaly_detected{service="payment-api"}` : 0 ou 1.
* `ml_last_analysis_timestamp_seconds{service="payment-api"}` : fraîcheur de l'analyse.

Ne jamais afficher « normal » si le modèle n'est pas prêt ou si la collecte échoue.
Une panne complète est signalée comme télémétrie indisponible, pas comme une
classification ML normale. Prévoir une alerte de disponibilité distincte.

## Recette à effectuer sur votre environnement

Tests automatisés (depuis Backend, après construction de l'image) :

```powershell
docker compose -f ../docker-compose.yml -f ml-api/compose.yml run --rm --no-deps -v "${PWD}/ml-api:/tests:ro" ml-api python -m unittest discover -s /tests -v
```

Ces tests utilisent des données synthétiques et une réponse Prometheus simulée ;
ils ne constituent pas une validation du modèle sur votre plateforme.

1. Sans Prometheus : `/train` doit refuser, sans modèle factice ni données inventées.
2. Apprendre sur du trafic normal ; garder les dates et le nombre d'échantillons.
3. Observer une autre période normale : compter les faux positifs.
4. Provoquer une dégradation autorisée dans le laboratoire : relever scores,
   détections, incidents manqués et délai de détection.
5. Couper la collecte : vérifier l'état `telemetry_unavailable` dans `/status`.
6. Après restauration : vérifier télémétrie et transaction métier.

Limites : un modèle ne garantit pas la détection de chaque attaque, surtout sur
une référence constante ou trop courte. Pas encore de persistance, d'évaluation
automatisée, d'historique d'incidents ou de raccordement à la remédiation.

Références : https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html
et https://prometheus.io/docs/prometheus/latest/querying/api/
