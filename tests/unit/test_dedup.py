from __future__ import annotations

import pytest

from core import dedup


pytestmark = pytest.mark.unit


def _record(name: str, body: str, **overrides) -> dict:
    base = {
        "name": name,
        "title": name,
        "source": None,
        "language": "fr",
        "duration": "00:01:00",
        "diarization": 0,
        "profiled": 0,
        "n_speakers": None,
        "generated_at": None,
        "mtime": 1.0,
        "body": body,
    }
    base.update(overrides)
    return base


def test_norm_removes_markdown_speakers_and_timestamps() -> None:
    assert dedup._norm("**Speaker 1** [00:01:02] Bonjour_le-monde") == (
        "bonjour le monde"
    )


def test_score_prefers_richer_transcript() -> None:
    plain = _record("plain.md", "court")
    rich = _record(
        "rich.md",
        "court",
        diarization=1,
        profiled=1,
        n_speakers=2,
    )

    assert dedup._score(rich) > dedup._score(plain)


def test_find_groups_matches_same_source_and_recommends_richer(monkeypatch) -> None:
    records = [
        _record("plain.md", "version différente", source="audio.mp3"),
        _record(
            "rich.md",
            "autre transcription",
            source="audio.mp3",
            diarization=1,
            profiled=1,
        ),
    ]
    monkeypatch.setattr(dedup, "_records", lambda project=None: records)

    groups = dedup.find_groups()

    assert len(groups) == 1
    assert groups[0]["keep"] == "rich.md"
    assert groups[0]["reason"] == "le plus complet — diarisé + profilé"
    assert groups[0]["members"][0]["keep"] is True


def test_find_groups_matches_similar_normalized_content(monkeypatch) -> None:
    records = [
        _record("a.md", "[00:00:01] Le climat change rapidement."),
        _record("b.md", "**Speaker 1** Le climat change rapidement !"),
        _record("other.md", "Un sujet sans aucun rapport."),
    ]
    monkeypatch.setattr(dedup, "_records", lambda project=None: records)

    groups = dedup.find_groups(threshold=0.8)

    assert len(groups) == 1
    assert {m["name"] for m in groups[0]["members"]} == {"a.md", "b.md"}


def test_find_groups_returns_empty_for_fewer_than_two_records(monkeypatch) -> None:
    monkeypatch.setattr(dedup, "_records", lambda project=None: [_record("only.md", "texte")])

    assert dedup.find_groups() == []

