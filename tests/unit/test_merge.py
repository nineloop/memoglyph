from __future__ import annotations

import pytest

from core.diarization import SpeakerTurn
from core.merge import _overlap, assign_speakers, merge_speakers
from core.stt_base import Segment


pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ((0, 1), (2, 3), 0.0),
        ((0, 2), (1, 3), 1.0),
        ((0, 4), (1, 2), 1.0),
        ((0, 1), (1, 2), 0.0),
    ],
)
def test_overlap(a, b, expected: float) -> None:
    assert _overlap(*a, *b) == expected


def test_label_map_uses_first_appearance_not_raw_label() -> None:
    turns = [
        SpeakerTurn(0, 1, "SPEAKER_09"),
        SpeakerTurn(1, 2, "SPEAKER_02"),
        SpeakerTurn(2, 3, "SPEAKER_09"),
    ]
    segments = [Segment(0, 1, "a"), Segment(1, 2, "b"), Segment(2, 3, "c")]

    _, label_map = merge_speakers(segments, turns)
    assert label_map == {"SPEAKER_09": "Speaker 1", "SPEAKER_02": "Speaker 2"}


def test_numerotation_suit_le_transcript_pas_les_tours() -> None:
    """Cas réel : locuteurs affichés « 1, 2, 3, 4, 6, 7, 5 ».

    « B » ouvre la diarisation mais son tour perd le chevauchement face à « A »,
    donc il n'apparaît qu'en dernier dans le transcript. Numéroter d'après les
    tours lui donnait « Speaker 1 » alors qu'il parle après tout le monde.
    """
    turns = [
        SpeakerTurn(0, 2, "B"),    # tour le plus précoce, mais très court…
        SpeakerTurn(0, 10, "A"),   # …A couvre bien mieux le segment 1-9
        SpeakerTurn(10, 20, "C"),
        SpeakerTurn(20, 30, "B"),  # B n'apparaît réellement qu'ici
    ]
    segments = [
        Segment(1, 9, "d'abord A"),
        Segment(11, 19, "puis C"),
        Segment(21, 29, "enfin B"),
    ]

    labeled, label_map = merge_speakers(segments, turns)
    assert [s.speaker for s in labeled] == ["Speaker 1", "Speaker 2", "Speaker 3"]
    # B est le dernier à apparaître : il doit porter le dernier numéro.
    assert label_map == {"A": "Speaker 1", "C": "Speaker 2", "B": "Speaker 3"}


def test_locuteur_sans_segment_absent_du_mapping() -> None:
    """Son empreinte doit être écartée : un « Speaker N » sans texte n'existe pas."""
    turns = [SpeakerTurn(0, 5, "A"), SpeakerTurn(90, 95, "FANTOME")]
    segments = [Segment(1, 4, "seul A parle")]

    labeled, label_map = merge_speakers(segments, turns)
    assert [s.speaker for s in labeled] == ["Speaker 1"]
    assert "FANTOME" not in label_map


def test_assigns_speaker_with_largest_overlap() -> None:
    segments = [Segment(0.5, 2.5, "Bonjour")]
    turns = [
        SpeakerTurn(0, 1, "A"),
        SpeakerTurn(1, 3, "B"),
    ]

    labeled, label_map = merge_speakers(segments, turns)

    # B l'emporte (1.5 s de recouvrement contre 0.5 s pour A).
    assert label_map == {"B": "Speaker 1"}
    # …et il porte le numéro 1 : A n'apparaît nulle part dans le document, donc
    # aucun numéro ne lui est réservé. L'ancienne numérotation, calculée sur les
    # tours, produisait ici un « Speaker 2 » précédé d'un « Speaker 1 » absent.
    assert labeled[0].speaker == "Speaker 1"


def test_segment_without_overlap_has_no_speaker() -> None:
    result = assign_speakers(
        [Segment(5, 6, "Hors tour")],
        [SpeakerTurn(0, 1, "A")],
    )

    assert result[0].speaker is None


def test_equal_overlap_keeps_first_turn() -> None:
    result = assign_speakers(
        [Segment(0, 2, "Égalité")],
        [SpeakerTurn(0, 1, "A"), SpeakerTurn(1, 2, "B")],
    )

    assert result[0].speaker == "Speaker 1"


def test_assignment_does_not_mutate_input_segments() -> None:
    original = Segment(0, 1, "Texte", speaker="Ancien")

    result = assign_speakers([original], [SpeakerTurn(0, 1, "A")])

    assert result[0] is not original
    assert original.speaker == "Ancien"
    assert result[0].speaker == "Speaker 1"


def test_no_turns_returns_original_list() -> None:
    segments = [Segment(0, 1, "Texte")]

    assert assign_speakers(segments, []) is segments

