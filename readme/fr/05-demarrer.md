## Démarrer en cinq commandes

Les prérequis sont un Mac Apple Silicon avec Homebrew, Python 3.11 avec [uv](https://docs.astral.sh/uv/), Node.js 20.19+ ou 22.12+, et `ffmpeg`. Ces commandes se lancent depuis un clone du dépôt.

```bash
brew install uv ffmpeg
uv sync --locked
npm --prefix ui ci
cp .env.example .env
./start.sh
```

L'application s'ouvre sur [http://127.0.0.1:8770](http://127.0.0.1:8770). Si ce port est pris, `./start.sh` choisit la paire libre suivante et affiche l'adresse. Les modèles se téléchargent au premier usage.

L'ensemble s'arrête avec `./stop.sh` ; pendant un traitement, l'arrêt est refusé et nomme le traitement, et `--forcer` l'arrête malgré tout.

Au premier lancement, `./start.sh` affiche un code d'installation à usage unique. L'application montre alors un seul écran, pour créer le compte administrateur, et l'administrateur crée les autres comptes.

Une bibliothèque créée avant les comptes s'attribue à un compte par `./memoglyph attribuer-bibliotheque <identifiant>`, à lancer avant de créer le moindre document avec ce compte. La marche à suivre est dans [docs/comptes-et-partage.md](docs/comptes-et-partage.md).
