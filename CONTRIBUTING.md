# Contributing to Memoglyph

Thanks for your interest! A few things to know before diving in.

## Language

The product UI is bilingual (French/English), but the **codebase — comments,
tests, commit history conventions, and most technical docs — is written in
French**. Contributions are welcome in French or English; keeping code
comments consistent with the surrounding file is appreciated.

Comments say *what* the code does and *why*, technically. They carry the
technical reason itself, never a reference to an internal decision journal:
`tests/unit/test_sans_identifiant_de_decision.py` fails when a decision
identifier appears in a tracked file.

## Ground rules

- **Markdown files are the source of truth**; SQLite stores only derived,
  rebuildable indexes. Changes must preserve that invariant.
- **Providers are selected by capability, never by hardcoded name.** The three
  LLM providers (local MLX, Mistral, OpenAI) share one contract; execution
  profiles differ, the generation chain does not.
- Deterministic checks are preferred over LLM judges anywhere correctness is
  asserted (quote appendix verification, repair rules, etc.).
- No personal data in the repo: `output/`, `inputs/`, `tests/fixtures/audio/`
  and the runtime databases are gitignored on purpose. Never commit real
  meeting audio or transcripts.

## Development setup

macOS on Apple Silicon is required for the full local pipeline (MLX).

```bash
brew install uv ffmpeg
uv sync --locked
npm --prefix ui ci
cp .env.example .env
make hooks        # installs the pre-push hook (runs `make check`)
```

Speaker diarization (pyannote) is an optional group:
`uv sync --locked --group diar`, plus a Hugging Face token in `.env` (see
`.env.example`). It decodes audio through `torchcodec`, which loads the
Homebrew FFmpeg libraries; the pinned version supports FFmpeg 4 to 9.

## Checks

```bash
make check        # uv lock check, Ruff, Python tests, UI tests, lint, build
make test-cov     # coverage floor 80% over core + api
make test-ml      # optional: real-media ML tests (local models needed)
```

`make check` is what the pre-push hook and CI run; it must be green before a
PR. ML tests are a manual gate — they need real media and local models, and
skip scenarios whose prerequisites are missing (`make test-ml-strict` refuses
to skip).

The test suite never touches your own library. `tests/conftest.py` redirects the
data stores (`output/`, `index.db`, `usage.db`) to a temporary directory before any
import of `core`, and a witness fails the run if one of the real files at the
repository root changes. If Memoglyph itself is running while you run `make check`,
it writes to those files and the witness reports it: stop the app (`./stop.sh`)
and run again.

CI cannot run the ML tests, but it can tell when they are overdue.
`ml-gate.json` records the last successful `make test-ml` run with a SHA-256
fingerprint of every watched pipeline file (`core/` and `run_ml_tests.py`).
When more than 25 of those files have changed since, `make check` fails on
`tests/unit/test_porte_ml.py`. Fingerprints cover file content, comments
included. To clear it, run `make test-ml` and commit the updated
`ml-gate.json`; never edit it by hand.

## Pull requests

- Work on a dedicated branch, never directly on `main`.
- Keep PRs focused; explain the *why* in the description.
- New behavior comes with tests. Bug fixes come with a test that would have
  caught the bug.

## Developer Certificate of Origin (DCO)

Memoglyph is licensed under Apache-2.0 (see [LICENSE](LICENSE) and
[NOTICE](NOTICE)). Contributions are accepted under the same license, and every
contributor keeps the copyright on their own work.

Every commit must carry a `Signed-off-by` line certifying the
[Developer Certificate of Origin 1.1](https://developercertificate.org/):
that you wrote the change, or otherwise have the right to submit it under the
project's license. Add it with `git commit -s`:

```
Signed-off-by: Your Name <your.email@example.com>
```

A pull request whose commits are not all signed off will not be merged.

## Reporting issues

Use GitHub issues for bugs and feature discussions. For suspected security
issues, see [SECURITY.md](SECURITY.md) — please do not open a public issue.
