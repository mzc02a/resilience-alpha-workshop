# DoS HTTP contrôlé — profil pédagogique à capacité réduite

Le laboratoire limite volontairement payment-api à **deux opérations en parallèle**,
avec **une seconde de travail simulé par opération**, attente maximale 50 ms.
L'excès retourne un vrai HTTP 503 et empêche réellement certains paiements.
Ce n'est ni une mesure de la capacité normale de l'application ni un épuisement
réel du PC hôte. Décrire explicitement cette limitation artificielle au jury.
Le profil normal conserve LAB_DOS_ENABLED=0 et n'ajoute aucun délai.

## État et commandes du PC hôte

Le mode DoS utilise **dos-stack.cmd**, pas stack.cmd :

```powershell
Backend\security\dos-stack.cmd ps
Backend\security\dos-stack.cmd logs --tail 20 calibration
Get-Content Backend/security/runtime/dos-calibration.json | ConvertFrom-Json | Select-Object state,requests,false_alert_fraction
```

La calibration de référence démarre sans attaque : 17 minutes de requêtes normales,
entraînement sur les 16 dernières minutes seulement, puis 3 minutes de validation
distincte. Ne pas lancer une charge simultanée ni redémarrer les services. Le
modèle précédent ne représentait pas la latence de ce nouveau profil.

`state=ready` indique que la courte validation s'est terminée avec au plus 20%
d'alertes ; ce seuil est un garde-fou de recette, pas une preuve de précision.
Si `failed` ou `review_required`, inspecter le rapport avant de lancer le test.
Les observations de validation sont conservées ; le seuil ML n'est pas modifié.

Pour refaire explicitement une calibration sans Python Windows :

```powershell
Backend\security\dos-stack.cmd up -d --no-deps --force-recreate ml-api
Backend\security\dos-stack.cmd up -d --no-deps --force-recreate calibration
```

## Test à deux PC, après calibration

1. Toi : Grafana, dashboard WAF et remédiation ; vérifier modèle=1, collecte=1,
   protection=0 et sonde fraîche. Modèle prêt ne suffit pas : vérifier le rapport.
2. Ton collègue : connecté au hotspot Windows du groupe, vérifier
   http://192.168.137.1:8080/health. Autoriser seulement TCP 8080 depuis son IP
   dans le pare-feu de l'hôte si nécessaire.
3. Toi : démarrer la sonde utilisateur continue dans un terminal (création de
   paiements fictifs de 1 EUR, sans retries pouvant dupliquer un paiement) :

```powershell
Backend\security\dos-stack.cmd run --rm --no-deps -v "${PWD}/Backend/security/runtime:/reports" remediator python business_probe.py --seconds 240
```

4. Après 30 secondes de fonctionnement normal, ton collègue utilise les **deux
   fichiers** lab_traffic.py et bounded_load.py dans le même dossier :

```powershell
python lab_traffic.py --url http://192.168.137.1:8080 --rps 4 --seconds 120
```

Le script autorise au plus 5 départs/s, 4 requêtes simultanées et 180 secondes.
Il n'ajoute pas de machines ni de destinations Internet. Le mot de passe du
compte fictif est saisi masqué ; le rapport dos-traffic.json ne contient pas de token.
La concurrence est nécessaire pour dépasser la petite capacité simulée, sans
augmenter le débit jusqu'à saturer le PC.

5. Toi : observer HTTP 503 et paiements échoués avant la protection. Après trois
   analyses ML anormales de trafic élevé : activation automatique, 429 côté
   émetteur, paiements réussis pendant que sa charge continue. Suivre les logs :

```powershell
Backend\security\dos-stack.cmd logs -f remediator gateway
```

6. Conserver business-probe.json (dans runtime), dos-traffic.json chez le collègue,
   les logs et les courbes. Comparer les horaires : une transaction réussie **après
   arrêt du trafic** ne prouve pas une correction sous charge. Si aucune dégradation
   préalable n'est mesurée, ne pas déclarer une reprise DoS réussie.

La sonde dans un conteneur a une source distincte de l'émetteur. Vérifier les IP
réelles dans les logs : le NAT Windows peut regrouper le navigateur hôte et le
PC attaquant. Ne pas assimiler la réussite de la sonde à celle de tous les clients.

## Conditions d'arrêt et retour normal

Ctrl+C sur le script de charge interrompt les nouveaux départs ; les requêtes en
cours ont un timeout de 3 secondes. Ne pas augmenter les plafonds en cas d'échec.
Le reste de la machine et la base ne sont pas volontairement saturés.

Pour désactiver le profil et sa calibration, puis retrouver les performances normales :

```powershell
Backend\security\dos-stack.cmd stop calibration
Backend\security\stack.cmd up -d --no-deps payment-api
```

Il faudra ensuite réentraîner sur le profil normal avant d'interpréter ses scores.

## Test technique de capacité et protection

check_dos.py vérifie une transaction de référence, des échecs métier sous charge,
puis des paiements réussis **pendant la charge**, après activation du limiteur
par le test. Cette recette de composants **n'est pas** une preuve de déclenchement ML.
La détection automatique doit être testée séparément après la calibration.
