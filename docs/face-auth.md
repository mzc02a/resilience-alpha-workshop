# Authentification faciale - administrateurs et utilisateurs

## Inscription et reference faciale

L'inscription exige email, mot de passe et capture camera du visage.
Le serveur detecte un seul visage et cree le compte et sa reference dans la meme
transaction PostgreSQL. Une image absente/invalide n'ouvre pas de compte.
Seul le vecteur est conserve dans la table face_references ; la photo n'est pas
conservee. Les inscriptions creent toujours un role user, jamais admin.
L'inscription ne peut ni remplacer un compte existant, ni modifier sa reference.

La reference administrateur deja enregistree dans private/face-references reste
lisible pour compatibilite. Une reference en base est prioritaire sur ce fichier.
Ne pas inclure les vecteurs ou les sauvegardes de base dans l'archive de rendu.

## Parcours actuel

1. Saisir l'identifiant (e-mail), puis Continuer avec la camera.
2. La camera du navigateur s'ouvre. Comparer la capture au visage de reference.
3. Si le visage correspond, le champ mot de passe apparait (signal vert clignotant).
4. Si le mot de passe est correct, signal vert fixe pendant 30 secondes.
5. L'espace correspondant au role devient accessible a l'issue des 30 secondes.

Le meme parcours est obligatoire pour les roles user et admin. Seul un admin
peut ensuite acceder aux fonctions de gestion. Les anciens tokens emis sans
verification faciale ne sont plus acceptes, meme pour les paiements personnels.

La camera integree du PC peut servir aux essais. Pour la demonstration finale,
utiliser obligatoirement la camera USB raccordee au PC Hote qui heberge Docker
et le point d'acces RESILIENCE-G<n>. Ouvrir le navigateur sur ce PC.
Le selecteur permet de choisir la camera USB quand plusieurs cameras existent.

L'indicateur rouge/vert affiche dans l'interface represente les etats demandes.
Il ne pilote pas encore de LED physique. Le raccordement ESP8266 au WiFi et sa
programmation avec CLion + PlatformIO restent a realiser.

## Echecs et delais

- Visage non reconnu (ou aucun/plusieurs visages) : identifiant efface, signal
  rouge fixe pendant 30 secondes, puis arret de la camera et nouvelle saisie.
- Une seconde tentative faciale est permise. Apres le deuxieme echec, le compte
  est bloque 5 minutes a compter de ce second echec ; le rouge reste fixe 30 s.
- Mot de passe incorrect : preuve faciale consommee, rouge clignotant dix fois
  (une fois par seconde), puis reprise complete identifiant -> visage -> mot de passe.
- Une panne de modele n'est pas comptee comme une erreur de reconnaissance.
- Les challenges expirent apres deux minutes, avec deux nouvelles minutes pour
  saisir le mot de passe une fois le visage reconnu.
- La session ouverte apres les deux facteurs dure 30 minutes. Il n'y a plus de
  nouvelle verification faciale imposee toutes les cinq minutes.

Les compteurs et echeances sont conserves dans PostgreSQL, table face_login_states,
creee au demarrage d'auth-api. Un changement de navigateur ou un redemarrage
n'efface pas le blocage. Les verrous de ligne PostgreSQL serialisent les tentatives.

## Demarrage

Depuis la racine :

```powershell
New-Item -ItemType Directory -Force private/face-references
docker compose -f docker-compose.yml -f docker-compose.face-dev.yml up -d --build auth-api payment-api frontend
```

Ouvrir http://localhost:5173. Pour une autre origine, utiliser HTTPS et adapter
les origines autorisees. Une adresse IP locale servie en HTTP ne suffit pas pour
l'acces camera du navigateur.

Le nom historique du mode serveur reste FACE_AUTH_MODE=dev-images : le serveur
compare une image recue, et ne certifie pas sa provenance materielle.
Sans ce mode explicite, l'acces au back-office est refuse.
Les anciens JWT administrateur doivent etre remplaces par une nouvelle connexion.

## Enrolement local pour les comptes existants

Un compte existant sans reference doit etre enrole par l'operateur du PC Hote.
Le role peut etre user ou admin. Deposer temporairement un portrait volontaire
dans private/enrollment/reference.jpg :

```powershell
docker compose -f docker-compose.yml -f docker-compose.face-dev.yml run --rm --no-deps --volume "${PWD}/private/enrollment:/enrollment:ro" auth-api python enroll_face.py --user-id UUID-DU-COMPTE --image /enrollment/reference.jpg
```

L'outil enregistre la reference en base, sans modifier le role du compte.
--replace permet un remplacement explicite par l'operateur apres verification.
Aucun endpoint public ne permet de remplacer la reference d'un compte existant.
Supprimer le portrait temporaire apres usage.
Une suppression ou un remplacement de reference ne revoque pas les sessions deja emises.

## Contrat API et protection des etapes

- POST /auth/register : multipart email, password, image ; cree role user et
  reference ensemble, retourne le profil sans token de session.

- POST /auth/backoffice/identify : JSON email ; retourne un challenge aleatoire
  lie au compte et a sa reference. Aucun JWT de session.
- POST /auth/face/verify : multipart image et challenge_token ; retourne seulement
  une preuve pour l'etape mot de passe, jamais un acces au SI.
- POST /auth/login : JSON email, password, face_challenge pour tous les roles.
  La preuve est liee au compte, temporaire et consommee meme si le mot de passe echoue.
- Le JWT final porte auth_flow=identifier_face_password et face_method=image_comparison.
  Le claim nbf impose les 30 secondes d'attente dans les deux API.
- Toutes les routes authentifiees d'auth-api et payment-api refusent les anciens jetons,
  les preuves prealables et les tokens sans les deux facteurs.
- La base stocke le hachage du challenge, pas sa valeur en clair.
- Les logs face_attempt ne contiennent ni photo, ni vecteur, ni JWT.

Limite image : JPEG/PNG, 4 Mo, 12 megapixels maximum. YuNet detecte un seul visage,
SFace compare a la reference du compte (1:1). Seuil cosinus initial 0.363,
a evaluer avec vos volontaires ; ce n'est pas une probabilite.
Les modeles sont telecharges a la construction avec verification SHA-256, puis
l'inference reste locale.

## Verification

```powershell
docker compose run --rm --no-deps frontend npm test -- --maxWorkers=1
docker compose run --rm --no-deps frontend npx vite build
docker compose -f docker-compose.yml -f docker-compose.face-dev.yml run --rm --no-deps --volume "${PWD}/Backend/auth-api:/tests:ro" auth-api sh -c 'pip install -q httpx==0.28.1 && python -m unittest discover -s /tests -p test_face_auth.py -v'
docker compose -f docker-compose.yml -f docker-compose.face-dev.yml run --rm --no-deps --volume "${PWD}/Backend/payment-api:/tests:ro" payment-api sh -c 'pip install -q httpx==0.28.1 && python -m unittest discover -s /tests -v'
```

Les tests serveur utilisent une base SQLite isolee et simulent la comparaison.
Les tests interface simulent la camera et les horloges pour verifier les delais.
Ils ne remplacent pas la recette avec la camera USB et la LED reelles.
Tester aussi la vraie reconnaissance, le refus de permission, le debranchement
de la camera et les faux rejets/fausses acceptations.

## Limites restantes

- ESP8266 / LED physique non raccordes ; seulement un indicateur dans l'interface.
- Aucune preuve de presence reelle ni protection contre une photo presentee a la
  camera. Une camera virtuelle ou un appel direct peut aussi fournir une image.
- Le JWT reste reutilisable jusqu'a expiration en cas de copie. payment-api ne
  reverifie pas encore une retrogradation de role en base.
- Pour exposer le laboratoire : protection du proxy, HTTPS, filtrage et
  limitation des requetes avant l'API sont encore necessaires.

Sources :
- https://docs.opencv.org/4.x/d0/dd4/tutorial_dnn_face.html
- https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet
- https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface
