# PC 2 : essai HTTP mesure

## Nouveau profil DoS (version commune a2ee453)

Pour le test coordonne avec le PC hote, utiliser le generateur concurrent de
votre collegue, avec bounded_load.py dans le meme dossier :

```powershell
python .\Backend\security\lab_traffic.py --url http://192.168.137.1:8080 --rps 4 --seconds 120 --output dos-traffic.json
```

Attendre le signal du PC hote : profil actif, calibration terminee et sonde
metier lancee. Procedure complete : `Backend/security/DOS-HTTP.md`.
Le nouveau script n'a pas d'option --ramp. Son rapport est dos-traffic.json.
Les tests PowerShell 8000/8001 ne remplacent pas cet essai via le WAF 8080.

## Ancienne variante locale sauvegardee

`cyber/lab_traffic_measured.py` conserve notre travail precedent (sequentiel,
CSV/JSON et --ramp). Il n'est pas utilise pour le nouveau test de capacite.

Depuis la racine du depot, avec Python et un compte fictif utilisateur existant :

```powershell
python .\cyber\lab_traffic_measured.py --url http://192.168.137.1:8080 --rps 3 --seconds 180 --ramp
```

Le script fait 20 secondes a 1 requete/s, puis 160 secondes avec un plafond de
3 requetes/s. Il lit uniquement `/payments`. Une seule requete est en cours :
si le serveur ralentit, le debit reel diminue. Aucun rattrapage ni nouvel essai
automatique. Limites conservees : 5 requetes/s maximum et 180 secondes d'envoi.
Une requete deja en cours peut finir apres cette duree. Ctrl+C interrompt le test.

Coordonner le debut avec le PC hote : pas d'apprentissage ML pendant l'essai.
Verifier que le modele est pret, la protection inactive et le cooldown ecoule.
Le PC hote doit tester un paiement avant le trafic, pendant la phase de charge
avant protection, pendant la protection, puis apres expiration. La phase a
1 requete/s n'est pas un jeu d'apprentissage ni une preuve de trafic normal.

Les fichiers CSV et JSON dans `cyber/results/` contiennent les horaires UTC,
statuts, durees, debit reel moyen et p95 des lectures. Le p95 des reponses 200
est separe du p95 global : les rejets rapides 429 ne prouvent pas une amelioration
des paiements. Aucun identifiant de compte, mot de passe ou JWT n'est enregistre.
Les rapports sont ignores par Git ; selectionner les preuves a partager apres revue.

La cible est limitee au port WAF 8080 sur localhost ou le hotspot
192.168.137.0/24. Les redirections HTTP et les proxies d'environnement sont
desactives pour eviter de transmettre les identifiants hors de la cible.

429 signifie limitation. Ce script ne prouve pas a lui seul un DoS ou une reprise.
Il ne corrige pas le limiteur qui bloque les clients legitimes sur le PC hote.
Ne pas augmenter arbitrairement les limites pour forcer une panne.

Tests hors reseau :

```powershell
python -B -m unittest discover -s cyber -p test_lab_traffic.py -v
```
