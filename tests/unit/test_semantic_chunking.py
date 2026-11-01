from __future__ import annotations

import pytest

from core.semantic import _clean, chunk_body


pytestmark = pytest.mark.unit


def test_clean_removes_speaker_markdown_and_timestamp() -> None:
    assert _clean("**Speaker 1** [00:01:02] Bonjour à tous") == "Bonjour à tous"


def test_chunk_body_ignores_headings_and_empty_paragraphs() -> None:
    body = "# Titre\n\n## Transcript\n\n[00:00:03] Premier passage.\n\n\n"

    assert chunk_body(body) == [("Premier passage.", "00:00:03")]


def test_chunk_body_groups_paragraphs_until_target() -> None:
    body = "[00:01] abcde\n\nfghij\n\n[00:03] klmno"

    chunks = chunk_body(body, target=10)

    assert chunks == [
        ("abcde fghij", "00:00:01"),
        ("klmno", "00:00:03"),
    ]


def test_chunk_timestamp_is_first_timestamp_in_chunk() -> None:
    body = "Sans horodatage\n\n[00:04] Ensuite\n\nFin"

    assert chunk_body(body, target=100) == [
        ("Sans horodatage Ensuite Fin", "00:00:04")
    ]


def test_l_horodatage_court_d_un_passage_est_normalise() -> None:
    # La forme du CR et de l'ECL : 75 minutes donnent une heure et quart.
    assert chunk_body("[75:10] Plus tard") == [("Plus tard", "01:15:10")]
    assert chunk_body("[00:12:34] Déjà canonique") == [("Déjà canonique", "00:12:34")]


def test_empty_body_has_no_chunks() -> None:
    assert chunk_body("") == []
    assert chunk_body("# Titre uniquement") == []

