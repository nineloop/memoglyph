# Sauvegarder et restaurer Memoglyph

Tout Memoglyph tient dans sa racine de données, le dossier du dépôt. Une sauvegarde
complète, c'est quatre éléments ; `tools/sauvegarder.py` les copie et les remet.

## Le périmètre

| Élément | Contenu | Se régénère ? |
|---|---|---|
| `output/` | les réunions en Markdown, qui font foi ; l'audio archivé (`.audio/`) ; les manifestes de projets ; les faits de l'ECL (`.ecl/`) et leurs versions ; la corbeille | non |
| `index.db` | l'index de recherche, l'index sémantique **et le registre des voix** (noms donnés aux voix, empreintes) | l'index se reconstruit depuis `output/`, **pas le registre des voix** |
| `auth.db` | les comptes, les sessions, les jetons d'appareil, les partages | non : la perdre perd tous les comptes et tous les jetons |
| `usage.db` | la télémétrie d'usage et de coût (onglet Usage) | non |

Ne se sauvegardent pas :

- `.env`, qui porte vos clés d'API : gardez-le à part, dans un gestionnaire de mots de
  passe plutôt que dans une sauvegarde de fichiers ;
- les caches de modèles (Hugging Face, MLX) : ils se téléchargent à nouveau ;
- `.cache/`, les journaux, les fichiers `.memoglyph-*` (ports, processus, code
  d'installation) : ils appartiennent à une exécution, pas aux données ;
- `inputs/`, si vous y gardez vos médias d'origine : l'audio utile à la relecture est déjà
  archivé dans `output/.audio/`. Sauvegardez-le à part si vous tenez aux originaux.

## Sauvegarder

Memoglyph arrêté :

```bash
./stop.sh
.venv/bin/python tools/sauvegarder.py sauvegarder --vers ~/Sauvegardes
```

L'outil crée un dossier neuf, `~/Sauvegardes/memoglyph-AAAA-MM-JJ-HHhMMSS/`, avec :

- une copie de `output/` ;
- une image cohérente de chaque base, prise par l'API de sauvegarde de SQLite ;
- un `MANIFESTE.json`, avec l'empreinte SHA-256 de chaque fichier.

Il **refuse** :

- de tourner pendant que Memoglyph tourne (un processus de `.memoglyph-*.pid` vivant, ou un
  port de la pile qui écoute) : une sauvegarde prise en pleine écriture capterait un compte
  rendu à moitié écrit. `--forcer` passe outre, en connaissance de cause ;
- d'écrire dans `output/` (la sauvegarde s'y copierait elle-même) ;
- d'écrire par-dessus une sauvegarde existante.

Il n'écrit rien en dehors du dossier choisi. Gardez une copie hors du Mac (disque externe,
stockage chiffré) : une sauvegarde sur le même disque ne protège pas d'une panne.

## Restaurer

Memoglyph arrêté, dans une racine **vide** (un clone neuf du dépôt, par exemple) :

```bash
.venv/bin/python tools/sauvegarder.py restaurer ~/Sauvegardes/memoglyph-AAAA-MM-JJ-HHhMMSS
./start.sh
```

Avant d'écrire quoi que ce soit, l'outil vérifie :

- le manifeste et l'empreinte de chaque fichier : une sauvegarde abîmée ou modifiée est
  refusée ;
- qu'aucun chemin ne sort de la sauvegarde ni de la racine.

Si la racine contient déjà des données, il refuse. `--forcer` les range d'abord dans
`.avant-restauration-<date>/`, sans rien supprimer, puis restaure.

Après la restauration, les comptes, les jetons d'appareil (le compagnon reste relié), les
partages, les voix et les réunions sont ceux du moment de la sauvegarde.

## Vérifier

`tests/integration/test_sauvegarde.py` refait le parcours sur une racine jetable :
- un compte, un jeton d'appareil, une réunion envoyée par ce jeton, une ligne d'usage ;
- sauvegarder, tout effacer, restaurer ;
- se connecter, retrouver la réunion, et le jeton qui vaut toujours.

Le test couvre aussi les refus : Memoglyph en marche, racine occupée, chemin hors du
dossier, sauvegarde abîmée.

Pour vous rassurer sur une vraie sauvegarde, restaurez-la de temps en temps dans un clone
jetable, lancé sur d'autres ports (`MEMOGLYPH_PORT_BASE=9200 ./start.sh`). Le clone doit
tourner seul : arrêtez-le avant de reprendre votre installation.
