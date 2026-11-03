# Comptes et connexion

Connexion, comptes, attribution de la bibliothèque et protections de l'API.

## Connexion obligatoire et comptes

**Connexion obligatoire partout, local compris.** Au premier lancement,
`./start.sh` affiche un code d'installation à usage unique ; l'interface ne montre
alors que l'écran « Créer le compte administrateur ». Pas d'inscription libre :
l'administrateur crée les autres comptes. Mot de passe oublié, sur la machine :
`./memoglyph reset-password <identifiant>`.

**Chaque compte ne voit que ses projets**, et son propre « Non classé ». Les
projets d'une bibliothèque d'avant les comptes n'ont pas de propriétaire : seuls
les administrateurs les voient. Pour les attribuer à un compte, simuler d'abord,
puis appliquer : `./memoglyph attribuer-bibliotheque <identifiant>`, puis la même
commande avec `--appliquer`. Seuls les manifestes de projet changent ; les Markdown
ne sont pas réécrits. Faites-le **avant de créer le moindre document** avec ce
compte : sinon il aura deux « Non classé » (le sien, créé avec le compte,
et celui de la bibliothèque) et une même personne pourra apparaître en deux voix, à
fusionner à la main. Un « Non classé » en trop, resté vide, se supprime à la main.

## Protections de l'API

En mode LAN comme en local, l'API refuse toute requête qui modifie l'état si son
en-tête `Origin` n'est ni l'hôte ni l'interface elle-même : une page d'un autre site
ne peut rien déclencher. Après cinq échecs de connexion en 15 minutes, pour un même
identifiant ou une même adresse, chaque essai attend un délai croissant, plafonné à
15 minutes ; `./memoglyph reset-password <identifiant>` lève le blocage du compte.

---

[Retour au README](../README.fr.md)
