# Changelog

All notable changes to Memoglyph are documented in this file. Versions are listed from the most recent to the oldest; dates use the ISO 8601 format.

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
