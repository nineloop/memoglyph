from __future__ import annotations

import pytest

from core.profiling import (
    _fmt_hms,
    _lang_directive,
    _transcript_for_llm,
    conversation_stats,
    speaker_stats,
)
from core.stt_base import Segment, Transcript


pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "00:00"), (65, "01:05"), (3661, "01:01:01")],
)
def test_fmt_hms(seconds: float, expected: str) -> None:
    assert _fmt_hms(seconds) == expected


def test_speaker_stats_counts_time_words_and_turns() -> None:
    transcript = Transcript(
        segments=[
            Segment(0, 2, "un deux", "Speaker 1"),
            Segment(2, 3, "trois", "Speaker 1"),
            Segment(3, 6, "quatre cinq", "Speaker 2"),
            Segment(6, 7, "six", "Speaker 1"),
        ]
    )

    stats = speaker_stats(transcript)

    assert stats["Speaker 1"] == {
        "talk_time_sec": 4.0,
        "turns": 2,
        "words": 4,
        "talk_time": "00:04",
        "share": "57%",
    }
    assert stats["Speaker 2"]["turns"] == 1
    assert stats["Speaker 2"]["share"] == "43%"


def test_speaker_stats_ignores_unlabelled_segments() -> None:
    transcript = Transcript(segments=[Segment(0, 3, "Sans speaker")])

    assert speaker_stats(transcript) == {}


def test_conversation_stats_uses_full_span_and_turns() -> None:
    transcript = Transcript(
        segments=[
            Segment(10, 12, "A", "Speaker 1"),
            Segment(15, 20, "B", "Speaker 2"),
        ]
    )
    stats = speaker_stats(transcript)

    assert conversation_stats(transcript, stats) == {
        "duration": "00:10",
        "n_speakers": 2,
        "n_turns": 2,
    }


def test_transcript_for_llm_supports_both_modes() -> None:
    transcript = Transcript(
        segments=[Segment(0, 1, "Bonjour", "Speaker 1")],
        full_text="Texte intégral",
    )

    assert _transcript_for_llm(transcript, False) == "Texte intégral"
    assert _transcript_for_llm(transcript, True) == "Speaker 1: Bonjour"


def test_language_directive_has_known_and_fallback_variants() -> None:
    assert "FRANÇAIS" in _lang_directive("fr")
    assert "'pl'" in _lang_directive("pl")
    assert "SAME language" in _lang_directive(None)

