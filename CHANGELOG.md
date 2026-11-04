# Changelog

All notable changes to Memoglyph are documented in this file. Versions are listed from the most recent to the oldest; dates use the ISO 8601 format.

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
