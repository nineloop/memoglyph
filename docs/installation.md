# Installation et démarrage

Prérequis, démarrage, arrêt et relance, mode réseau local, modèles et diarisation.

## Démarrage rapide

### Prérequis

- macOS sur Apple Silicon ;
- Python 3.11 et [uv](https://docs.astral.sh/uv/) ;
- Node.js 20.19+ ou 22.12+ ;
- `ffmpeg` ;
- `mlx-lm` pour les fonctions LLM locales (Apple Silicon), installé par `uv sync` ;
- `MISTRAL_API_KEY` (optionnel) pour les fonctions cloud Mistral.

```bash
brew install uv ffmpeg
uv sync --locked
npm --prefix ui ci
cp .env.example .env
./start.sh
```

L'application indique l'adresse retenue, normalement
[http://127.0.0.1:8770](http://127.0.0.1:8770). Si ces ports sont occupés, le
script choisit automatiquement la paire libre suivante.

## Premier démarrage, arrêt et relance

```bash
./stop.sh     # Arrêter la stack
./restart.sh  # Redémarrer la stack (stop + start)
```

Le premier démarrage, et le premier après une mise à jour qui change l'index, peut
prendre plusieurs minutes sur une grande bibliothèque : l'API reconstruit son index
avant de répondre, et `./start.sh` l'attend (900 s au plus, réglable par
`MEMOGLYPH_ATTENTE_S`). Une API qui s'arrête pendant son démarrage est signalée tout
de suite, avec la fin de son journal.

Pendant un traitement (transcription, faits, voix, archives), `./stop.sh` et
`./restart.sh` refusent d'arrêter et le nomment : les traitements ne vivent qu'en
mémoire, un arrêt les perdrait. `./stop.sh --forcer` (ou `./restart.sh --forcer`)
arrête quand même. `./stop.sh` n'arrête que les serveurs lancés depuis son propre
dossier : une autre racine Memoglyph sur la même machine continue de tourner.

## Mode réseau local

Pour ouvrir Memoglyph depuis un téléphone ou un autre ordinateur connecté au
même réseau local :

```bash
./start.sh --lan
```

Le script affiche l'adresse à ouvrir, par exemple `http://192.168.1.20:8770`,
et le nom Bonjour `http://<mac>.local:8770` — plus stable que l'IP, qui change
au bail DHCP. Seul Vite écoute sur le réseau ; FastAPI reste sur `127.0.0.1` et
les appels `/api` passent par le proxy du frontend. Le pare-feu macOS peut
demander d'autoriser Node à recevoir des connexions.

En mode LAN, Vite accepte les accès par IP et par nom `.local`. Pour un autre
nom d'hôte (DNS d'entreprise), l'ajouter via `MEMOGLYPH_ALLOWED_HOSTS`
(liste séparée par des virgules). Hors mode LAN, ce réglage n'a aucun effet.

> **Attention :** en mode LAN, les autres appareils voient l'écran de connexion,
> mais le trafic circule en HTTP, sans chiffrement. Ne pas l'activer sur un Wi-Fi
> public ou non maîtrisé, et arrêter la stack après usage avec `./stop.sh`.

## Premier usage : modèles

Le premier usage télécharge Whisper depuis Hugging Face. Pour activer les
comptes-rendus, profils et réponses RAG :

```bash
# Le modèle MLX se télécharge au premier usage : mlx-community/Qwen3-4B-Instruct-2507-4bit
# Faits de la réunion (ECL) : expérimentaux, désactivés par défaut ; MEMOGLYPH_FAITS=1 les active.
# Seulement alors, en local : ibm-granite/granite-4.2-8b-q4-mlx (4,9 Go), à télécharger
# une fois (hf download ibm-granite/granite-4.2-8b-q4-mlx) ; absent, l'ECL le signale et retombe
# sur le modèle ci-dessus
```

## Diarisation et signatures vocales

La diarisation est un groupe optionnel. Elle nécessite aussi un `HF_TOKEN` dans
`.env` et l'acceptation des conditions du modèle pyannote indiquées dans
[.env.example](../.env.example). Le jeton reste exigé même une fois le modèle en cache ;
sans lui, les transcriptions sont sans locuteurs, et le travail comme la réunion disent
pourquoi.

```bash
uv sync --locked --group diar
```

---

[Retour au README](../README.fr.md)
