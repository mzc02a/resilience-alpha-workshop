# Suppression depuis l'administration

`DELETE /admin/users/{user_id}` requiert un JWT administrateur valide et la
vérification faciale habituelle. Le compte administrateur courant ne peut pas
se supprimer lui-même (400). Un utilisateur non administrateur reçoit 403,
un identifiant absent 404. Succès : 204, sans corps.

Une transaction supprime le compte utilisateur, le compte bancaire fictif,
la référence faciale en base et les challenges de connexion. Le solde fictif
n'est pas transféré ni remboursé. Les virements historiques restent inchangés.
Les éventuelles anciennes références en fichiers montés en lecture seule
restent à gérer par l'opérateur ; elles ne permettent pas de se connecter sans
utilisateur en base. Les nouveaux jetons et les jetons déjà émis d'un utilisateur
supprimé sont refusés par les deux API.

L'interface demande confirmation et ne propose pas de suppression sur son
propre compte. Aucun utilisateur réel n'est supprimé pendant les tests.

## Session après actualisation

Le frontend garde dans sessionStorage le jeton existant, son échéance et
l'onglet affiché. Aucun mot de passe, photo ou vecteur facial n'y est enregistré.
Après rechargement, `/auth/me` revalide la session avant d'afficher les données.
Une expiration/révocation retourne à la connexion ; une panne réseau conserve
la session et affiche une possibilité de réessayer. Déconnexion : stockage vidé.
Le rechargement ne prolonge pas la durée d'authentification faciale.
