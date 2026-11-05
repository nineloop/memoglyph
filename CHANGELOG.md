# Changelog

All notable changes to Memoglyph are documented in this file. Versions are listed from the most recent to the oldest; dates use the ISO 8601 format.

## v1.2.1 (2026-11-05)

Documentation, comments and repository files only; the behavior of Memoglyph does not change. The README opens with a more precise line and gains an "At a glance" section, an English overview page answers common questions in short form, and several pages are corrected where they disagreed with the code.

### Changes
- README: a more precise opening line (open source, Apple Silicon Macs, local by default), a new "At a glance" section, and two more lines in the limits (Memoglyph works on a recording after the meeting, and runs from a clone of the repository).
- New English overview page, `docs/overview.md`, with short answers on privacy, citations, the menu-bar companion, speakers, teams, online providers and installation.
- Documentation corrected: the menu-bar companion is described as a beta for macOS, with its specification reduced to what is delivered; semantic search is stated to always use a local embedding model; a unit of a report whose figure is absent from the speech within 30 seconds of its timestamp is stated to be dropped.
- Wording: local usage tracking is no longer called telemetry in the French pages (nothing leaves the machine), and the English FinOps pages are reworded around what each mechanism does.
- `docs/REPORT_TEMPLATES.md` describes how the report type is chosen: the meeting type written in the profile first, one definition per type, and the sports type kept only when the text contains at least two distinct sports words.
- Comments and docstrings reworded; no change in behavior.

## v1.2.0 (2026-11-04)

Two named online providers, OpenRouter and Groq, can be added next to the existing ones. Each appears under its own name wherever the ready providers appear; the key and the model are set in `.env`, and nothing is sent without the rules that already protect your documents.

### Changes
- OpenRouter and Groq: set `OPENROUTER_API_KEY` and `MEMOGLYPH_OPENROUTER_MODEL` (or `GROQ_API_KEY` and `MEMOGLYPH_GROQ_MODEL`) in `.env`, then restart the instance. A provider is offered only when both its key and its model are set. No model list is built in, and the address of each service is fixed.
- Online rule: both providers count as online, whatever their address. An ultra-confidential document is not sent without its owner's authorization, and the rule is checked again before every call.
- Free models: a model that the service itself marks as free (at OpenRouter, an identifier ending in `:free` or the `openrouter/free` router) receives "public · normal" and "confidential" documents by default, never an ultra-confidential one: that document is refused with a clear message, even when the cloud is authorized for it. OpenRouter's data-collection page says that the providers' training settings are separate for free and paid models. Groq marks no model as free, so the rule does not apply to it. The rule lives in one place and can be relaxed. Daily quota: according to OpenRouter's documentation (https://openrouter.ai/docs/api_reference/limits, read on 2026-11-04), free models are limited to 20 requests per minute, and to 50 requests per day for an account that has purchased fewer than 10 credits (1000 per day from 10 credits). A single report makes many model calls (9 in one real test with a free model), so a full report can use up a day's free quota; the report then fails: the screen shows the general message that the language model did not respond, and only the technical detail names the reached quota. A successful report with a free model has not been verified yet.
- Quota reached: when a service answers that its quota is reached, the service's own answer is never shown. Where a message is shown, it says to wait a moment or choose another model. What you see depends on the screen: in the Assistant, the message is "The provider's quota has been reached: wait a moment or choose another model." in the interface language; when a transcription's profile or report cannot be made, the sentence is written in the document's warning and the transcript is kept; when every call of a report is refused, the failure message names the quota in its technical detail; when only part of a report is refused, the report lists the numbers of the chunks that could not be processed, not the sentence. Relaunching the facts reports a generic failure. The genre classifier stays silent and falls back, as it does for any outage.
- Reasoning: reasoning blocks written inside a model's answer are removed; an answer that is empty or cut by the token limit is reported as a failure instead of an empty report.
- Usage: a model with no price entered is shown as unpriced, never as zero.
- Router answers: when the provider picks a model for each call (as OpenRouter's free router does), the usage record and the report's front matter (`models_served`) now say which model actually answered; `model` still holds the requested name. OpenRouter requests also ask for a low reasoning effort (documented parameter), so that a reasoning model leaves room for the visible text; whether this helps a given model is not promised. An answer with no text cut by the token limit is now reported as such, not as an answer "without text content".
- Messages: new error messages in French and English for a reached quota and for a refused free model.
- Quota wait: when OpenRouter or Groq answers that a quota is reached while a report, a facts run or the profiling of a transcription is working, the application waits for the delay the service announces (60 seconds at most; 20 seconds when the service gives none) and tries again, up to twice per call. The pause is published in the run's status ("Provider quota: resuming in N s"); only the Transcribe screen and the maintenance panel show that text, while the other screens (including a report regeneration and the list of runs) show only "step N of M": a report regeneration can therefore stay up to 5 minutes on the same step without showing it. The total wait of one run is capped at 5 minutes; beyond it, refused calls fail as before: a chunk is lost (the document says so), and the run fails only if everything is refused. This does not make a quota go away: each retry of a refused call without an announced delay waits 20 seconds, that is up to 40 seconds per call and 5 minutes in all, before the chunk is lost; the Assistant never waits. A refused call now leaves a usage line with its code only (for example 429 or 404), never the service's answer, and the usage call count now includes refused calls.
- Assistant: a failure of the model service now shows the server's own sentence instead of a raw `HTTP 503: {…}` text (the server writes it in French); a reached quota is the exception: the server sends it with a code, and the interface shows it translated.
- Provider tiles: on the Transcribe screen, the name and the model of each cloud provider tile are on two lines.
- API (all optional and compatible): a refused free model answers with a flat 403 `{detail, code: "model.free.restricted"}` on `POST /api/transcribe`, on both report routes, on the facts re-run and on `POST /api/ask`; `POST /api/ask` answers a reached quota with a 503 that also carries `code: "step.error.rateLimited"` (any other model failure stays a 503 without a code); failed jobs can carry two new error codes, `step.error.rateLimited` and `step.error.freeModelRestricted`.
- Report type: a meeting whose profile says it is a meeting (for example "meeting", "workshop" or "team meeting") is now given the project-meeting questions straight away, without asking the model; a report is given the sports questions only when the excerpt contains at least two distinct sports words; the classifier's raw answer (200 characters at most) is kept in the generation diagnostic, never in the document. This is covered by unit tests; it has not been run against a real online model.
- Documentation: the data-policy pages of both services are linked, without judgment, in `docs/local-et-en-ligne.md`.

## v1.1.2 (2026-11-04)

Fixes and a small feature on the Transcribe screen and the processing view: "Transcribe another recording" now asks for another file instead of sending the same one again, a second upload of a file that is already waiting or running is refused with a clear message, a job that is still waiting for its turn can be cancelled, and a file that stayed on disk after a job waited too long is now removed.

### Changes
- Transcribe screen: after a file is accepted, "Transcribe another recording" opens the file picker instead of sending the same file a second time. Removing a chosen file, or a second upload that is refused, no longer interrupts the follow-up of the job already started. While that job waits for its turn, the Processing header says "Waiting: another job is running." instead of showing the upload message.
- Duplicate uploads: sending a file whose content is already waiting or running in your account is refused with a clear message that names the existing job, with a "Follow" link. A job that is done or failed never blocks a new upload of the same file. The rule does not apply to files sent by the menu-bar companion, which recognizes its own captures. When the maximum number of active jobs is reached, that limit is reported before the duplicate refusal.
- Cancel a waiting job: the processing panel shows a "Cancel" button on each job that is waiting for its turn (a transcription or a report regeneration), with one confirmation. The job moves to "Recently finished" with a neutral "Cancelled" badge and no link to a meeting, and nothing is written to the library. For a transcription, the file you uploaded is deleted. A job that has already started has no Cancel button; if it starts at the very moment you confirm, the panel says it can no longer be cancelled. In the menu-bar companion, a capture cancelled from the web shows as failed; its file stays on the Mac and can be sent again on purpose.
- Fix: when a job waited too long for the machine ("Waited too long"), the file it had received stayed on disk. It is now removed.

## v1.1.1 (2026-11-03)

A documentation release: the READMEs are rebuilt from short sections in English and in French, the technical detail moves to documents under docs/, and a few statements of the published text are corrected. The application's behavior does not change.

### Changes
- README: both READMEs are now short and structured (the problem, three steps with screenshots, local or online, start in five commands, further reading, limits, development), in English and in French, with a new screenshot of the Transcribe screen. They are generated from small sections kept in a `readme/` folder: `make readme` rebuilds them, and a test checks that they are up to date.
- Documentation: the technical detail of the former French README (features, installation, accounts, limits, architecture, configuration, provenance, maintenance, development, default models) moves to documents under `docs/`, listed in `docs/README.md`, with a new document on what stays on the Mac and what can go to an online provider (in French).
- Corrections to the published text: semantic search uses a local embedding model by default (it was described as always local; `MEMOGLYPH_EMBED_BACKEND=ollama` keeps the older behavior); speaker diarization uses pyannote 4 with the `community-1` model and is an optional, separate installation (it was described as pyannote 3.1 and as part of the default pipeline); a report keeps only the statements that carry a citation verified in the transcript (it was described as every statement carrying one).
- Changelog: the entry for v1.1.0 now repeats the v1.1.0 release note word for word.

## v1.1.0 (2026-11-03)

A redesigned library with project navigation, a full-width meeting transcript, a processing view available from every screen, a clearer menu-bar companion, and report regeneration with a choice of model.

### Changes
- Library: a side panel lists your projects (All my projects, Unclassified, your projects, projects shared with you) with a filter and a "New project" button; it folds into a column of icons and remembers its state. A breadcrumb (Library › project › meeting) with previous and next arrows moves between meetings of the same project in one click. The native "scope" drop-down is replaced by the project panel: what you browse is chosen there, and where a new transcription is filed is chosen in the "Save to" field of the Transcribe screen. Search, Assistant, Voices and Maintenance show the scope they use on a read-only line, with a link to change it in the Library, so no control changes meaning from one screen to another.
- Top bar and navigation: a lighter top bar with a "Settings" menu (devices, privacy, accounts, language, theme, sign out), a bottom dock that stands out from the page, and on phones a menu button that opens the project drawer plus a dock with a "More" entry.
- Meeting transcript: the transcript uses the full width next to the speakers panel, with the speaker's name above each turn, and keeps a comfortable line length on very wide screens. Report citations and transcript timestamps are touch targets on touch screens; on desktop they stay compact, which is a documented exception to the touch-target rule.
- Processing view: a discreet indicator in the top bar and a panel, available from every screen, list what is running or waiting (which meeting, which step, since when, with an "Open the meeting" link). A failure is described in your language with the raw detail kept apart under "Technical detail".
- Menu-bar companion (macOS, beta): captures that cannot be sent right away are sent again on their own, a limited number of times, without ever creating a second meeting and without ever being deleted from the Mac. Short sentences in French or English following the system language, a clear linked / offline / link refused status, "Saved to <project>" with a "Follow" link, "ready" lines that open their meeting, and an application icon. For a trial against another instance, an environment variable gives the companion its own keychain entry, captures folder and settings.
- Regenerate a report: choose the model among those that are ready (local first), follow it in the processing view, and get a new version added next to the existing ones, with the model that produced it. Meetings marked ultra-confidential show online providers as unavailable, with the reason.
- API: `POST /api/transcribe` accepts an optional `capture_id` that makes a re-sent capture idempotent; `GET /api/jobs` lists the account's processing; `POST /api/transcript/{name}/report/jobs` regenerates a report as a tracked job; failed jobs carry an error code. All additions are optional and compatible with existing clients.
- Accessibility: interface in French and English with no missing label, touch-sized targets, visible focus, Escape closes menus, drawers and panels, and the focus goes back to the control that opened a menu, a drawer or the regeneration panel.

## v1.0.2 (2026-11-02)

Screenshots of the current version at the top of the README, in English and in French, and a wider pre-push check.

### Changes
- Documentation: the README opens with three screenshots (library, meeting transcript, report with timestamped citations), taken on demo data only, with the attribution required by the AMI Meeting Corpus licence (CC BY 4.0).
- The pre-push hook and the contribution check now also scan the author and the message of annotated tags before they are published.

## v1.0.1 (2026-11-01)

First public release of Memoglyph: local meeting transcription and reports with verified citations.

### Changes
- Documentation: removed links to pages that are not part of the repository.
- The migration command test no longer depends on the ports of a Memoglyph instance running on the same machine.
- A CI check and a pre-push hook scan contributions for secrets and private data.
- Added this changelog.
