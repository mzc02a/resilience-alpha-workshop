# Reprendre la demo sans refaire la collecte de 20 minutes

La reference deja collectee est conservee dans Prometheus. Le rapport initial
contient ses dates exactes : le correctif reutilise cette fenetre et garde les
anciennes validations hors apprentissage. Ne pas lancer une nouvelle calibration.

## Sur le PC hote

Recuperer les fichiers de cette version puis :

```powershell
.\cyber\reparer-calibration.ps1
```

Si l'image vient deja d'etre reconstruite et que le ML repond, reprendre sans
nouvelle reconstruction : `.\cyber\reparer-calibration.ps1 -SkipBuild`.

Le lanceur :

1. Verifie que la session de sonde a au moins dix minutes restantes.
2. Reconstruit/recree uniquement ml-api avec le detecteur corrige et son volume.
3. Recharge les vraies observations historiques depuis Prometheus.
4. Compare le modele avec l'ancienne validation, sans l'utiliser dans fit().
5. Stabilise le trafic pendant 90 secondes (fenetre rate de 1 minute + delais de collecte),
   puis realise 120 secondes de NOUVELLE validation normale independante.
6. Ecrit ready seulement si cette validation est complete et a au plus 20% d'alertes.

Le lanceur exclut d'abord /lab-profile des metriques metier, en reconstruisant
uniquement payment-api si necessaire. Les verifications de profil ne doivent
pas modifier elles-memes la latence que le ML observe.
Le temps de reconstruction Docker s'ajoute aux 3 min 30 de stabilisation/verification.
Aucune attaque pendant cette procedure. Elle ne cree aucun paiement (lectures).
Le rapport precedent est archive dans runtime/dos-calibration-before-replay-*.json.
Si des mesures historiques manquent ou si la validation echoue, le lanceur refuse
de declarer la demo prete. Aucun resultat ne doit etre force a ready.

Si la session est trop ancienne, refaire seulement la connexion normale, attendre
les 30 secondes, recopier la session et lancer `cyber/prepare-lab.ps1` sans
-ForCalibration. Cela ne relance pas les 20 minutes. Ne pas partager le JWT.

Apres succes :

```powershell
.\cyber\jury-hote.ps1
```

Sur le PC 2 : `cyber/jury-pc2.ps1`, puis attendre le signal du PC hote.
La sonde attend des paiements reussis ET un etat ML normal recent avant ce signal.
Une periode inactive peut etre anormale pour un modele appris sur du trafic ;
la sonde retablit le trafic normal avant de lancer la charge, sans forcer le score.

## Ce qui change dans le ML

Isolation Forest avec son seuil auto a classe 29% de la reference et 100% de la
validation comme anormales dans votre rapport. Le detecteur courant est maintenant
un modele multivarie de covariance OAS, appris uniquement sur les observations de
reference, avec une regularisation qui tient compte d'une marge de mesure :
0.10 requete/s, 0.05 erreur/s et 20 ms. Ce sont des parametres d'ingenierie explicites,
pas des donnees inventees. Le seuil utilise un quantile chi-carre de reference 0.99 ;
cela ne signifie pas 99% de precision reelle. Le score reste positif en anomalie.

Les variations conjointes sont apprises ; la decision ne se limite pas a une liste
de conditions fixes sur le debit et les erreurs. La detection reelle et le delai
de remediaton doivent etre revalides pendant la repetition a deux PC.

Le changement ayant ete guide par l'ancienne validation, celle-ci sert au controle
historique et ne constitue plus une evaluation independante du choix de modele.
C'est pourquoi la nouvelle fenetre de 120 secondes est necessaire.

La reference (mesures numeriques uniquement, pas de pickle ni de secret) est
stockee dans le volume ml_model_data. Au redemarrage, elle est rechargee uniquement
si le service, les variables, le profil de capacite et la version du modele
correspondent. Un changement de profil exige une reference adaptee. Ne pas supprimer
le volume avec docker compose down -v si vous souhaitez conserver cette reference.

References techniques :
- https://scikit-learn.org/stable/modules/generated/sklearn.covariance.OAS.html
- https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html
