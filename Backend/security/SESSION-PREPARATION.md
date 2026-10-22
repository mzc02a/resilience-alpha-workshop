# Preparation de la demo avec l'authentification faciale actuelle

Les scripts n'inscrivent plus de compte et ne se connectent plus avec le seul
mot de passe. Ils reutilisent une session obtenue via le navigateur apres
identifiant, visage, mot de passe et attente de 30 secondes. Les API verifient
toujours les JWT et les droits. Aucun changement de l'authentification applicative.

## Une fois apres mise a jour, sur le PC hote

Recuperer les nouveaux fichiers puis reconstruire les composants de test :

```powershell
Backend\security\dos-stack.cmd build remediator calibration
Backend\security\dos-stack.cmd up -d --no-deps remediator
```

Cela ne redemarre pas ml-api. La calibration sera recreee explicitement plus bas.

## Preparer une session valide pour les sondes

1. Utiliser un compte fictif deja inscrit et enrole dans l'application.
2. Se connecter normalement sur le navigateur du PC hote puis attendre les 30s.
3. Choisir un AUTRE compte fictif existant comme destinataire des virements de 1 EUR.
   Le compte emetteur doit avoir assez de fonds fictifs (jusqu'a un virement toutes
   les quatre secondes pendant la sonde). Ne pas recrediter automatiquement les comptes.
4. Dans la console developpeur du navigateur (F12), copier localement la session :

```javascript
copy(JSON.parse(sessionStorage.getItem("resilience-alpha.session")).token)
```

Cette commande ne cree pas de JWT, elle copie celui de la connexion deja effectuee.
Ne pas publier cette valeur, la mettre dans une commande shell ou l'envoyer dans le chat.
La saisie du lanceur ci-dessous est masquee. Effacer ensuite le presse-papiers.

5. Dans PowerShell, a la racine du depot :

```powershell
.\cyber\prepare-lab.ps1 -ForCalibration
```

Coller le JWT dans la saisie masquee, puis saisir l'IBAN du destinataire fictif.
Le script controle la session sur les API et le beneficiaire, puis enregistre
`Backend/security/runtime/probe-session.json`. Ce fichier contient un secret :
il est ignore par Git et ne doit jamais entrer dans les rendus publics.
Les conteneurs lisent ce fichier via un montage readonly /lab.

## Calibrer si le modele n'est pas pret

```powershell
Backend\security\dos-stack.cmd up -d --no-deps --force-recreate calibration
```

Le rapport commence par preparing, puis collecting, validating et ready.
Toute erreur de preparation remplace l'ancien ready par failed.
Une session avec au moins 21 minutes restantes est exigee avant cette collecte.
Le script ne prolonge pas les sessions et ne contourne pas leur expiration.
Ne pas attaquer pendant les 20 minutes de calibration.

## Avant le passage

Si la session est trop ancienne, refaire la connexion complete et :

```powershell
.\cyber\prepare-lab.ps1
```

Cela actualise seulement la session des sondes, pas le modele ML. Il n'est pas
necessaire de recalibrer si le modele est reste pret sur le meme profil.
Apres une mise a jour du fichier, laisser quelques secondes au controleur pour
qu'il quitte waiting_for_probe_session. Lancer ensuite :

```powershell
.\cyber\jury-hote.ps1
```

La sonde utilise un IBAN et une cle Idempotency-Key nouvelle par virement voulu,
sans repetition automatique d'un POST ambigu. Elle verifie aussi la relecture.

## PC 2

Recuperer lab_traffic.py, bounded_load.py et lab_session.py dans Backend/security.
Utiliser de preference une session d'un autre compte fictif obtenue par le meme
parcours complet sur le PC hote (une fenetre privee permet de separer les comptes).
Le script demande maintenant un JWT masque, plus le mot de passe :

```powershell
.\cyber\jury-pc2.ps1
```

ou directement :

```powershell
python .\Backend\security\lab_traffic.py --url http://192.168.137.1:8080 --rps 4 --seconds 120 --output dos-traffic.json
```

Une session pas encore active, trop courte ou refusee par /auth/me bloque le
lancement. Aucun JWT n'est ecrit dans les rapports de trafic.

## Verification hors reseau

```powershell
python -B -m unittest discover -s Backend/security -p test_lab_session.py -v
```

Ces tests utilisent des sessions synthetiques et des API simulees. Ils ne
remplacent pas la recette reelle a deux PC ni une verification du materiel.
