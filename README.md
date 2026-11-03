<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/brand/memoglyph-logo-horizontal-sombre.svg">
    <img src="docs/assets/brand/memoglyph-logo-horizontal.svg" alt="Memoglyph" width="420">
  </picture>
</p>

# Memoglyph

*English · [Français](README.fr.md)*

**Local meeting transcripts and reports, with citations you can check against the audio.**

Record a call or import a file, and Memoglyph gives you a timestamped transcript with speakers, a meeting report with decisions, actions and open questions, and answers to your questions across meetings. Every quoted claim links back to the second it was said.

![The Memoglyph library: projects and recent meetings](docs/assets/screenshots/library.png)
*The library: the meetings of a project.*

![A meeting transcript with speakers, waveform and timestamps](docs/assets/screenshots/meeting-transcript.png)
*A meeting: transcript with speakers, waveform and timestamps.*

![A meeting report where each statement carries timestamped citations](docs/assets/screenshots/report-citations.png)
*The report: each statement carries timestamped citations; a click plays the audio at that moment.*

<sub>Screenshots of the current version, with demo data only: meeting IS1009a of the AMI Meeting Corpus (University of Edinburgh and the AMI project partners), licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Reference: J. Carletta et al., "The AMI Meeting Corpus: A Pre-announcement", *Machine Learning for Multimodal Interaction* (MLMI), 2005.</sub>

- **Verifiable**: each claim cites its timestamp, and a number that was never spoken is dropped.
- **Local by default**: Whisper and a local model on Apple Silicon. Mistral, OpenAI, Anthropic or a self-hosted OpenAI-compatible server, only if you pick them.
- **For you, or a small team, self-hosted**: accounts, one private space each, read-only sharing.
- **Confidentiality levels**: ultra-confidential meetings are never sent to a cloud model unless their owner allows it.
- **Agent-ready**: a read-only MCP server, scoped to one account.
- **No telemetry**: usage is recorded locally; nothing goes anywhere except to the providers you choose and, on first use, to model downloads from Hugging Face.

## What it does

Memoglyph turns an audio or video file into a Markdown workspace on your own Mac:
a timestamped transcript, speakers, a meeting report, search across meetings, and
questions answered from your own corpus. It is built for Apple Silicon and runs
**fully locally by default**: audio, transcripts and indexes stay on the machine.

- **Transcription** with Whisper `large-v3-turbo` (local), language detected or set.
- **Speaker diarization** with pyannote (local, optional), and voice identification
  across meetings.
- **Meeting reports** shaped by meeting type (project sync, steering committee,
  interview, briefing, town hall, webinar…). Every claim carries a `[source]`
  timestamp; a click plays the audio at that moment. The exact quotes are kept in an
  appendix whose integrity is checked deterministically.
- **Hybrid search** (full text with FTS5, plus semantic search with `sqlite-vec`) and
  a question-answering assistant, scoped to a project or to the whole library.
- **Projects, trash and verified purge**, duplicate detection, index maintenance.
- **Usage and cost tracking** per document and per project.
- **Read-only MCP server** for agents (Claude, Codex, Cursor…): projects, meetings,
  search, reports with their timestamped citations, and meeting facts when enabled. Local, scoped to one
  account; ultra-confidential content only if that account allows the cloud for it:
  `./memoglyph mcp <username>`.
- **Bilingual interface**, French and English.

Markdown files are the source of truth for meetings, and the search indexes rebuild from
them. Accounts and device tokens (`auth.db`), the voice registry (`index.db`) and usage
history (`usage.db`) do not: a full backup covers all four, see
[docs/sauvegarde.md](docs/sauvegarde.md) (French) and `tools/sauvegarder.py`.

## Local first, cloud only if you choose it

The default pipeline uses only local models: Whisper, pyannote, and an MLX language
model (`Qwen3-4B-Instruct`) served on your Mac. Three cloud providers are optional for
reports, the assistant and meeting facts, **Mistral**, **OpenAI** and **Anthropic** (Claude),
and Mistral can also transcribe. With Mistral, reports and the assistant use
`ministral-14b-2512`, which Mistral's default tier serves (`mistral-small` gets a zero
quota there). Voxtral transcribes a meeting longer than 50 minutes in overlapping chunks,
paced to the default tier's audio limit (3,600 seconds of audio per minute), with
timestamps realigned and speakers matched across chunks. With Anthropic, reports and the
assistant use Claude Haiku 5.5 and meeting facts use Claude Sonnet 5.5. You have to pick
them explicitly; they then send audio and/or text to their API. Semantic search always
runs on a local embedding model. A document labelled **ultra-confidential** goes to no
cloud provider by default: transcription, report, facts and assistant passages stay local.
Each account can allow the cloud for its own ultra-confidential documents in **Privacy**
(off by default, confirmed when turned on, effective at once when turned off); it opens
nothing for shared documents or other accounts, and each allowed send is logged without
content (see [SECURITY.md](SECURITY.md)).

A model server you host yourself (MLX, or an OpenAI-compatible server such as Ollama,
LM Studio or vLLM, set in `.env`) counts as local only on this machine's loopback address.
On any other host it is shown as **Private cloud** and follows the cloud rules,
ultra-confidential included. Each meeting carries one sensitivity, read again before every
cloud call. **Confidential** is a label for people: it does not route anything by itself,
and you decide what goes where.

Citations are landmarks for checking, not proof. Each one points to a passage at a
timestamp, and the check confirms that this passage is in the transcript as written. The
transcript itself can be wrong (a misheard word or figure, a voice given to the wrong
person), and a model can read into a passage something it does not say. Click the
citation, listen to the audio, and check what matters before you rely on it.

Known limits: an amount with decimals can lose its decimal point in transcription
(Whisper writes "1250" for "twelve fifty"), and no check on the text can see it, since the
figure is in the quote. Listen to the quote of an amount before relying on it. A Whisper
repetition loop is decoded a second time; if it persists, it is folded with a visible
mark and shown as a caveat on the meeting.

## Quick start

Requirements: macOS on Apple Silicon, Python 3.11 with [uv](https://docs.astral.sh/uv/),
Node.js 20.19+ or 22.12+, and `ffmpeg`.

```bash
brew install uv ffmpeg
uv sync --locked
npm --prefix ui ci
cp .env.example .env
./start.sh
```

The app prints its address, usually [http://127.0.0.1:8770](http://127.0.0.1:8770).
The first start, and the first one after an update that changes the index, can take
several minutes on a large library: the API rebuilds its index before it answers, and
`./start.sh` waits for it (up to 900 s, set `MEMOGLYPH_ATTENTE_S` to change that).
Stop it with `./stop.sh`. While a job runs (transcription, meeting facts, voices,
archives), `./stop.sh` and `./restart.sh` refuse and name it, since jobs live in memory and
would be lost; `--forcer` stops anyway. Models are downloaded on first use. Meeting facts (ECL, the « Faits » tab of a meeting) are
experimental and disabled by default: set `MEMOGLYPH_FAITS=1` to enable them. Only then do they
need `ibm-granite/granite-4.2-8b-q4-mlx` locally (4.9 GB, download it once
with `hf download ibm-granite/granite-4.2-8b-q4-mlx`); without it, they fall back to the local
report model, and the report says so.

Speaker diarization is an optional group. It needs a Hugging Face token in `.env` and
acceptance of the pyannote model terms (see [.env.example](.env.example)). The token is
required even once the model is cached; without it, transcripts have no speakers, and
the job and the meeting say why:

```bash
uv sync --locked --group diar
```

Sign-in is required everywhere, local use included. On first launch, `./start.sh`
prints a one-time installation code; the app then shows a single screen to create
the administrator account. There is no open sign-up: the administrator creates the
other accounts. Forgotten password, on the machine: `./memoglyph reset-password <username>`.

Each account sees only its own projects, plus its own « Non classé » (inbox). The
projects of a library created before accounts have no owner and are visible to
administrators only. To hand them over to an account, preview first, then apply:
`./memoglyph attribuer-bibliotheque <username>`, then the same with `--appliquer`.
Only the project manifests change; the Markdown files are not rewritten.
Do it **before creating any new document** with that account: otherwise it ends up
with two « Non classé » (its own, created with the account, and the library's) and
the same person may appear as two voices, to be merged by hand. An empty extra
« Non classé » folder can be deleted by hand.

`./start.sh --lan` opens the app to other devices on your local network. They see the
sign-in screen, but traffic is plain HTTP: do not use LAN mode on a public or
untrusted Wi-Fi. The API refuses any state-changing request whose `Origin` is neither
the host nor the app itself, and repeated failed sign-ins are slowed down (five
failures in 15 minutes, then a growing delay capped at 15 minutes).

## Recording a call (beta)

The menu bar companion records a call (the microphone and the call application's sound)
or a replay, and hands the file to Memoglyph. It is in **beta** and **macOS only**. Build
and install it with:

```bash
make companion
```

It installs into `/Applications`; if `/Applications` is not writable, it says so and
installs into `~/Applications` instead. `DEST=<folder>` installs it elsewhere. The Finder
then shows the app. The menu follows the system language. Its « Start in Visio mode »
entry lists calls in progress first, then one line per open application, and also offers
« All Mac audio ». A capture that cannot be sent right away (Memoglyph busy with another
job, or not reachable) is sent again on its own, a limited number of times, and is never
deleted from the Mac; the menu says in one short sentence where each capture stands.
Details (French):
[compagnon/README.md](compagnon/README.md) and
[docs/compagnon-capture.md](docs/compagnon-capture.md).

## Learn more

- [README.fr.md](README.fr.md): the full documentation (French), with configuration,
  provenance, trash and purge, project scoping, and the test gates.
- [SPEC.md](SPEC.md): design and architecture (French).
- [docs/sauvegarde.md](docs/sauvegarde.md): what a full backup covers, and how to back up
  and restore (French).
- [docs/mcp.md](docs/mcp.md): the read-only MCP server, its tools and its guarantees
  (French).
- [docs/corpus-demo.md](docs/corpus-demo.md): the public demo corpus (AMI Meeting
  Corpus, CC BY 4.0) used for screenshots and demos.
- [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

The codebase, its comments and most technical docs are written in French.

## Development

```bash
make hooks     # installs the pre-push hook
make check     # lock check, Ruff, Python and UI tests, lint and build
make test-ml   # real media and local models; a manual gate, not run in CI
```

## License

Apache-2.0: see [LICENSE](LICENSE) and [NOTICE](NOTICE). Models keep their own
licenses. The Memoglyph name and logo are not covered by the Apache-2.0 license of the
code; see [docs/assets/brand/](docs/assets/brand/README.md).
