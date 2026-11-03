# Changelog

All notable changes to Memoglyph are documented in this file. Versions are listed from the most recent to the oldest; dates use the ISO 8601 format.

## v1.1.0 (2026-11-03)

A redesigned library with project navigation, a full-width meeting transcript, a processing view available from every screen, a clearer menu-bar companion, and report regeneration with a choice of model.

### Changes
- Library: a side panel of projects (with filter, "New project", foldable), a clickable breadcrumb with previous/next meeting arrows, and no more native "scope" drop-down: browsing is chosen in the panel, the destination of a new transcription in the "Save to" field, and the screens that use a scope (Search, Assistant, Voices, Maintenance) show it on a read-only line with a link to change it in the Library.
- Top bar and navigation: a lighter top bar with a "Settings" menu, a dock that stands out from the page, a project drawer and a "More" entry on phones.
- Meeting transcript: full width next to the speakers panel, speaker name above each turn, bounded line length on very wide screens.
- Processing view: an indicator in the top bar and a panel, from every screen; failures are described in the interface language, with the raw detail kept apart.
- Menu-bar companion (macOS, beta): captures are sent again on their own when Memoglyph is busy or unreachable (never duplicated, never deleted), short messages in French or English, a clear link status, "Saved to <project>" with "Follow", "ready" lines that open their meeting, an application icon.
- Reports: regenerate a report choosing the model among those that are ready, tracked in the processing view; the new version is added next to the existing ones.
- API (all optional and compatible): `capture_id` on `POST /api/transcribe`, `GET /api/jobs`, `POST /api/transcript/{name}/report/jobs`, error codes on failed jobs.
- Accessibility: no missing translation, touch-sized targets, Escape closes menus, drawers and panels, and the focus goes back to the control that opened a menu, a drawer or the regeneration panel.

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
