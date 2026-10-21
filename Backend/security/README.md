# WAF et remédiation — laboratoire RESILIENCE-ALPHA

## Architecture

Client → ModSecurity + OWASP CRS → passerelle → auth-api / payment-api.
Prometheus → ML → contrôleur → activation temporaire du limiteur de la passerelle.
Le contrôle utilise un secret distinct ; aucun socket Docker ni commande système.

Le WAF applique CRS en mode bloquant (paranoïa 1). Le limiteur est un composant
distinct : il ne remplace pas le WAF. Les API et PostgreSQL ne publient plus de
ports avec cet override. Les ports locaux 8000/8001 passent tous deux par le WAF.
Les métriques Prometheus continuent à utiliser les noms internes des API.

## Lancement depuis la racine du dépôt

Les secrets ont déjà été générés sur le PC hôte. Sur une nouvelle machine, avec
Python disponible : `python Backend/security/setup.py`. Alternative sans Python
Windows, via un conteneur temporaire (depuis la racine) :

```powershell
docker run --rm -v "${PWD}/Backend/security:/work" -w /work python:3.11-slim python setup.py
```

Les fichiers de `runtime/` sont ignorés par Git et exclus de l'image Docker.
Ce sont des secrets Compose locaux, pas un coffre Vault/SOPS : cette exigence
du sujet reste à traiter. Ne pas inclure runtime dans l'archive de livraison.

```powershell
docker compose -f docker-compose.yml -f Backend/ml-api/compose.yml -f Backend/security/compose.yml up -d --build
```

ATTENTION : conserver les trois `-f` pour les futures commandes `up`. Relancer
seulement le Compose de base réexposerait les API et contournerait le WAF.
Raccourci depuis la racine : `Backend\security\stack.cmd ps`,
`Backend\security\stack.cmd logs --tail 30 remediator` et
`Backend\security\stack.cmd up -d --no-deps waf`.
Ne pas reconstruire/redémarrer ml-api pendant une recette : son modèle est en mémoire.

Interface locale inchangée : frontend localhost:5173, API localhost:8000/8001.
État de remédiation : http://localhost:8003/status ; métriques : /metrics.

## Politique automatique

* Trois analyses **distinctes**, anormales et consécutives de payment-api.
* Timestamp vieux de 60 secondes maximum, pas de date future ; interruption de
  plus de 35 secondes : séquence remise à zéro.
* Trafic au moins 1 requête/s (variable REMEDIATION_MIN_RPS du contrôleur).
  C'est une condition de choix de l'action, pas un remplacement du modèle ML.
  Une baisse de trafic n'est pas corrigée par un limiteur.
* Protection pendant 120 secondes : par source, 1 requête/s, rafale de 3.
  Les requêtes excédentaires reçoivent HTTP 429. Expiration automatique, sans reload.
* Au moins 180 secondes entre deux activations ; aucune extension infinie.
* Création/connexion d'un compte fictif de sonde dédié, paiement de 1 EUR à travers
  le WAF et relecture du paiement. Un échec est enregistré explicitement.

Le contrôleur ne déclare pas « reprise complète » parce qu'un paiement passe :
la disponibilité, les erreurs, le retour ML et le parcours utilisateur doivent
également être observés. Les redémarrages du contrôleur remettent son cooldown
à zéro ; ceux de la passerelle désactivent sa protection temporaire.
`/status` conserve `last_action` avec les dates d'activation/expiration et la
preuve du paiement, même si une collecte ultérieure échoue.

## Réseau du PC attaquant

Activer le hotspot Windows dédié au groupe. Dans `.env`, définir uniquement
sur le PC hôte : `LAB_BIND_IP=192.168.137.1`. Recréer seulement le WAF :

```powershell
docker compose -f docker-compose.yml -f Backend/ml-api/compose.yml -f Backend/security/compose.yml up -d --no-deps waf
```

Autoriser TCP 8080 dans le pare-feu Windows pour l'IP du PC attaquant sur le
réseau de laboratoire. Ne pas ouvrir 5432, 8002, 8003, 3000 ou 9090 à ce PC.
Il utilise http://192.168.137.1:8080/auth/login et /payments. Même origine pour
les deux API. Sans LAB_BIND_IP, le WAF reste volontairement local.
Le frontend reste local au PC hôte ; il sert à vérifier un paiement légitime.

Vérifier dans les logs de la passerelle l'adresse `source` des limitations.
Docker Desktop peut masquer des adresses derrière du NAT ; ne pas prétendre
isoler un attaquant si toutes les sources sont confondues. Les en-têtes
X-Real-IP/X-Forwarded-For fournis par le client sont remplacés par le WAF.

## Recette

Pour le **DoS HTTP avec dégradation métier mesurée**, suivre plutôt
[DOS-HTTP.md](DOS-HTTP.md) : profil de capacité explicite, nouvelle calibration,
sonde continue, charge concurrente plafonnée et preuve de reprise sous charge.
Le scénario ci-dessous reste un simple test de trafic inhabituel en profil normal.

1. Vérifier `/health`, puis un paiement normal via le frontend.
2. Vérifier que le modèle est entraîné, sa collecte fraîche, et lancer le trafic
   normal via le WAF (adapter les deux URL du script normal_traffic à http://waf:8080
   si exécuté dans un conteneur du projet). Les anciens appels directs à
   payment-api contournent la protection et ne constituent pas un test WAF.
3. À partir du PC attaquant autorisé, lancer le script de trafic borné avec des
   identifiants fictifs dédiés. Garder les horaires de début/fin.
4. Observer le ML puis les événements `remediation_activated`, `request_limited`,
   `payment_verified` ou `verification_failed` dans les logs/Loki.
5. Vérifier un nouveau paiement utilisateur, l'absence d'incohérence et l'expiration.
6. Conserver scores, délai de détection, 429, résultats métier, faux positifs et limites.

Une requête XSS de test doit être rejetée par CRS. Ce contrôle déterministe WAF
ne démontre pas une détection ML. Une hausse modérée de trafic ne prouve pas une
indisponibilité ; une démonstration de reprise nécessite un effet métier mesuré
avant correction. Ne pas augmenter arbitrairement la charge pour le provoquer.

## Vérifications automatisées

```powershell
docker compose -f docker-compose.yml -f Backend/ml-api/compose.yml -f Backend/security/compose.yml run --rm --no-deps -v "${PWD}/Backend/security:/tests:ro" remediator python -m unittest discover -s /tests -v
docker compose -f docker-compose.yml -f Backend/ml-api/compose.yml -f Backend/security/compose.yml run --rm --no-deps -v "${PWD}/Backend/security:/tests:ro" remediator python /tests/check_integration.py
```

La deuxième commande crée un paiement fictif, active un limiteur pendant 15s
et attend son expiration. Ne pas l'exécuter pendant la capture de démonstration.

## Observabilité

Le dashboard provisionné **RESILIENCE-ALPHA - WAF et remediation** est disponible
dans le dossier RESILIENCE-ALPHA de Grafana. Son JSON est également fourni dans
`Backend/security/dashboard.json` (source Prometheus UID `prometheus`).

Jobs Prometheus à ajouter aux jobs existants :

```yaml
- job_name: gateway
  metrics_path: /internal/metrics
  static_configs:
    - targets: ["gateway:8080"]
- job_name: remediator
  static_configs:
    - targets: ["remediator:8081"]
```

Métriques : gateway_protection_active, gateway_blocked_requests_total,
remediation_actions_total, remediation_payment_verified. Pour cette dernière,
une valeur 1 décrit la dernière sonde, pas une vérification permanente.
Loki : `{service_name=~"waf|gateway|remediator"}`. Vérifier qu'Alloy filtre
le bon projet Compose : `COMPOSE_PROJECT_NAME` doit correspondre au nom réel.

Limites : HTTP réservé au laboratoire, pas de TLS de confiance ou d'authentification
mutuelle entre services ; réseau Compose partagé à durcir davantage, limiteur
en mémoire à un seul worker, pas de protection contre une attaque distribuée.
Contrôles WAF et remédiation ne remplacent pas le SIEM ni l'analyse de risques.

## Script du PC attaquant autorisé

Copier `lab_traffic.py` et `bounded_load.py` dans le même dossier sur le PC
attaquant (Python requis), puis :

```powershell
python lab_traffic.py --url http://192.168.137.1:8080 --rps 3 --seconds 120
```

Entrer un compte de test existant. Le script ne crée pas de paiement : il lit
l'historique, plafonne à 5 requêtes/s et 180 secondes, et comptabilise les 429.
Il refuse les destinations hors localhost et du hotspot 192.168.137.0/24.
N'utiliser que les machines autorisées du groupe.

Référence WAF : https://github.com/coreruleset/modsecurity-crs-docker
