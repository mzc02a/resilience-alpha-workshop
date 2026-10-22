# Comptes et virements de demonstration

Chaque utilisateur et administrateur possede un compte interne :
- UUID technique conserve pour les liens, droits et references faciales.
- IBAN de demonstration unique, format francais de 27 caracteres et controle MOD 97.
- Solde initial de 500.00 EUR, attribue UNE fois.

Les numeros banque/guichet sont synthetiques. Le systeme ne se connecte a aucune
banque : seules les coordonnees presentes dans la table accounts sont acceptables
comme destinataires. Un IBAN avec une cle correcte n'est pas une preuve de compte reel.

## Initialisation et conservation des donnees

Auth-api cree la table accounts et attribue un compte aux anciens utilisateurs
qui n'en ont pas encore. Les nouvelles inscriptions creent utilisateur, reference
faciale et compte dans la meme transaction. L'IBAN est protege par une contrainte
UNIQUE ; une collision est reessayee. Les redemarrages ne recreditent pas les 500 EUR.

Payment-api ajoute les colonnes manquantes dans payments sans supprimer les
anciennes lignes. Les anciens paiements restent historiques (sans destinataire),
ne sont pas rejoues et ne modifient pas les nouveaux soldes initiaux.

Les deux services utilisent un verrou transactionnel PostgreSQL commun pendant
l'initialisation du schema. Le module bank_accounts.py est identique dans les deux
images et porte la definition de accounts, les limites et la generation des IBAN.

## Utilisation

Se connecter avec identifiant, visage et mot de passe. L'espace affiche le solde
et l'IBAN de demonstration. Choisir un destinataire dans la liste (son e-mail),
verifier l'IBAN affiche, puis saisir le montant. Le bouton Actualiser recharge le
solde et l'historique : le destinataire peut ainsi voir le virement recu.

Un solde insuffisant produit un virement DECLINED sans deplacer d'argent.
Les virements vers soi-meme et les destinataires inconnus sont refuses.
La limite historique de 1000 EUR n'est plus une regle : le solde disponible fait foi.

## API

Authentification JWT avec visage + mot de passe obligatoire pour tous les roles.

- GET /accounts/me : IBAN et solde du compte connecte.
- GET /beneficiaries : autres comptes du laboratoire, e-mail et IBAN.
- POST /payments : montant decimal, currency=EUR, recipient_iban.
  En-tete Idempotency-Key obligatoire (16 a 64 caracteres).
- GET /payments : envois et receptions reussies du compte connecte, plus ses refus.
- GET /payments/{id} : seulement emetteur ou destinataire d'un virement reussi.
- GET /admin/payments : vue globale reservee a un administrateur.

Les montants utilisent Decimal/Numeric(12,2), jamais des flottants pour le calcul
des soldes. Debit, credit et ligne de virement sont valides dans une transaction.
Les deux comptes sont verrouilles dans l'ordre de leurs UUID pour eviter les
interblocages ; les valeurs sont rechargees apres l'attente des verrous.

La cle d'idempotence est unique par emetteur. Rejouer la meme requete retourne le
meme virement ; changer montant ou destinataire avec cette cle retourne HTTP 409.
L'interface garde cette cle apres une erreur reseau et bloque les doubles clics.
Un refus pour solde insuffisant reste un refus pour la meme cle, meme apres un credit.

Les soldes ne sont jamais changes par les routes de lecture ou les connexions.
Il n'existe pas de route publique de credit ou de reinitialisation du solde.

## Tests

Tests rapides :
```powershell
docker compose -f docker-compose.yml -f docker-compose.face-dev.yml run --rm --no-deps --volume "${PWD}/Backend/payment-api:/tests:ro" payment-api sh -c 'pip install -q httpx==0.28.1 && python -m unittest discover -s /tests -v'
docker compose run --rm --no-deps frontend npm test -- --maxWorkers=1
```

Les tests de concurrence exigent TEST_DATABASE_URL pointant exclusivement sur
une base temporaire nommee transfer_test. Ils creent puis suppriment un schema
isole par test. Ne jamais utiliser la base payments pour ces tests.

Couverture : conservation de la somme des soldes, centimes exacts, decouvert,
double envoi, cle reutilisee, virements opposes, echec de stockage, confidentialite
des historiques, conservation des anciens paiements et initialisation repetable.

## Trafic ML

normal_traffic.py demande desormais un JWT obtenu apres reconnaissance faciale,
sans le journaliser. Par defaut il ne fait que lire /payments.
--recipient-iban permet de petits virements de 0.01 a 1 EUR toutes les dix requetes.
Le script s'arrete avant expiration de session ou au premier refus/erreur.

Reference de format : registre IBAN SWIFT
https://www.swift.com/fr/swift-resource/9606/download
