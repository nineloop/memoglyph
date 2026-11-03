## Five commands to start

The requirements are a Mac with Apple Silicon and Homebrew, Python 3.11 with [uv](https://docs.astral.sh/uv/), Node.js 20.19+ or 22.12+, and `ffmpeg`. The commands run from a clone of the repository.

```bash
brew install uv ffmpeg
uv sync --locked
npm --prefix ui ci
cp .env.example .env
./start.sh
```

The app opens at [http://127.0.0.1:8770](http://127.0.0.1:8770). If that port is taken, `./start.sh` picks the next free pair and prints the address. Models are downloaded on first use.

The app stops with `./stop.sh`; a running job makes it refuse and name the job, and `--forcer` stops it anyway.

On first launch, `./start.sh` prints a one-time installation code. The app then shows a single screen to create the administrator account, and the administrator creates the other accounts.

A library created before accounts existed is handed to an account with `./memoglyph attribuer-bibliotheque <username>`, run before creating any new document with that account. The steps are in [docs/comptes-et-partage.md](docs/comptes-et-partage.md), in French.
