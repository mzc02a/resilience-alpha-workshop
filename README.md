## Comptes et virements

IBAN de demonstration, 500 EUR initiaux et virements internes : [fonctionnement et tests](docs/transfers.md).

## Verification faciale du back-office

Mode developpement avec camera navigateur : [installation et essais](docs/face-auth.md).
Inscription avec capture camera obligatoire.
Connexion user et admin : identifiant, camera, puis mot de passe.
Les routes admin exigent la validation des deux facteurs ; activer explicitement le
fichier docker-compose.face-dev.yml pour les essais de comparaison faciale.

## Centralisation des logs avec Loki

```powershell
docker compose up -d loki alloy grafana
```

Alloy lit les sorties stdout/stderr des conteneurs du projet via l'API Docker
et les envoie a Loki. Grafana charge automatiquement la source Loki et le
dashboard http://localhost:3000/d/resilience-alpha-logs (filtre par service).
Les logs du navigateur ne sont pas collectes. Les logs HTTP montrent la route
et le statut HTTP, pas le montant ni le resultat metier du paiement.

Dans Grafana > Explore, selectionner Loki et essayer :

```logql
{service_name="payment-api"}
```

Pour retrouver les appels de creation de paiement :

```logql
{service_name="payment-api"} |= "POST /payments"
```

Faire une transaction depuis le frontend, attendre quelques secondes et choisir
les 15 dernieres minutes. Les logs sont conserves 7 jours dans `loki_data` ;
`alloy_data` conserve la position de lecture lors des redemarrages.
Loki est accessible uniquement sur localhost:3100, sans authentification.
Alloy dispose d'un acces au socket Docker (le montage `ro` ne limite pas les
operations de l'API Docker) : configuration destinee au workshop local.
Si le nom du projet Compose est personnalise, definir `COMPOSE_PROJECT_NAME`
dans `.env` pour que la collecte cible le bon projet.

```powershell
docker compose logs --tail=50 loki alloy
```

## Monitoring Prometheus / Grafana

Les API exposent `/metrics` sans JWT pour la collecte Prometheus. Les routes
sont regroupees par modele (les identifiants de paiement ne deviennent pas des
labels). `/metrics` est exclu des statistiques HTTP.

Dans `.env`, definir `DATABASE_URL`, `JWT_SECRET`, `GRAFANA_ADMIN_USER` (defaut :
`admin`) et `GRAFANA_ADMIN_PASSWORD`. Un mot de passe Grafana aleatoire a ete
ajoute au fichier local lors de l'installation. Ne pas committer ce fichier.

```powershell
docker compose up -d --build
```

- Grafana : http://localhost:3000 ; identifiants dans `.env`.
- Dashboard : http://localhost:3000/d/resilience-alpha
- Prometheus : http://localhost:9090 ; cibles dans `/targets`.
- Metriques : http://localhost:8001/metrics et http://localhost:8000/metrics.

La source Prometheus et le dashboard `RESILIENCE-ALPHA - APIs` sont configures
automatiquement. Le dashboard affiche l'etat de collecte des API, le debit HTTP,
les erreurs 5xx, la latence p95, le trafic par route/statut et la memoire des
processus. `up = 1` signifie que la collecte fonctionne, pas que toutes les
dependances metier sont disponibles. PostgreSQL et l'hote ne sont pas instrumentes.

Generer du trafic depuis le frontend ou les docs des API (`/docs`), puis attendre
au moins deux collectes (30 secondes). Les courbes utilisent une fenetre de
5 minutes ; sans trafic, certaines series restent absentes.

Prometheus collecte toutes les 15 secondes et conserve 15 jours de donnees.
Les volumes `prometheus_data` et `grafana_data` persistent apres un arret.
Le mot de passe administrateur configure dans `.env` s'applique a la premiere
initialisation du volume Grafana ; le modifier ensuite depuis Grafana.
Les interfaces de monitoring sont publiees sur la boucle locale uniquement.

```powershell
docker compose ps
docker compose logs --tail=50 prometheus grafana
```

Configuration : `monitoring/prometheus/prometheus.yml` et `monitoring/grafana/`.
Modifier le JSON versionne pour conserver les changements du dashboard.
