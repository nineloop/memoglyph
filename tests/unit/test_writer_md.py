from __future__ import annotations

import math

import pytest
import yaml

from core.stt_base import Segment, Transcript
from core.writer_md import (
    decode_embedding,
    encode_embedding,
    fmt_duration,
    fmt_timestamp,
    render_markdown,
    slugify,
    unique_path,
    write_md,
)


pytestmark = pytest.mark.unit


def _frontmatter(content: str) -> dict:
    raw = content.split("---", 2)[1]
    return yaml.safe_load(raw)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("  Une Réunion_importante !! ", "une-réunion-importante"),
        ("a---b", "a-b"),
        ("***", "transcript"),
    ],
)
def test_slugify(source: str, expected: str) -> None:
    assert slugify(source) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "00:00:00"),
        (59.6, "00:01:00"),
        (3661.2, "01:01:01"),
    ],
)
def test_timestamp_formatting(seconds: float, expected: str) -> None:
    assert fmt_timestamp(seconds) == expected
    assert fmt_duration(seconds) == expected


def test_embedding_round_trip_uses_float32() -> None:
    source = [0.1, -2.5, math.pi]

    decoded = decode_embedding(encode_embedding(source))

    assert decoded == pytest.approx(source, rel=1e-6)


def test_render_plain_transcript_with_timestamps(transcript_factory) -> None:
    transcript = transcript_factory(("Bonjour.", "Au revoir."))

    content = render_markdown(
        transcript,
        {"title": "essai", "language": "fr", "diarization": False},
    )

    assert "# essai" in content
    assert "[00:00:00] Bonjour." in content
    assert "[00:00:01] Au revoir." in content
    assert _frontmatter(content)["diarization"] is False


def test_render_without_timestamps_produces_fluid_text(transcript_factory) -> None:
    transcript = transcript_factory(("Une phrase.", "Une autre."))

    content = render_markdown(
        transcript,
        {"title": "texte"},
        timestamps=False,
    )

    assert "Une phrase. Une autre." in content
    assert "[00:" not in content


def test_render_diarized_transcript_uses_speaker_labels() -> None:
    transcript = Transcript(
        segments=[
            Segment(0, 1, "Question", "Speaker 1"),
            Segment(1, 2, "Réponse", None),
        ],
        full_text="Question Réponse",
        language="fr",
    )

    content = render_markdown(
        transcript,
        {"title": "dialogue"},
        diarized=True,
    )

    assert "**Speaker 1** [00:00:00] Question" in content
    assert "**Speaker ?** [00:00:01] Réponse" in content


def test_render_profile_and_embeddings_are_valid_yaml(transcript_factory) -> None:
    transcript = transcript_factory(
        ("Hello",), language="en", speakers=("Speaker 1",)
    )
    profile = {
        "conversation": {
            "type": "interview",
            "summary": "A short summary.",
            "topics": ["testing"],
        },
        "conversation_stats": {
            "duration": "00:01",
            "n_speakers": 1,
            "n_turns": 1,
        },
        "speakers": {
            "Speaker 1": {
                "role": "host",
                "talk_time": "00:01",
                "share": "100%",
                "tone": "calm",
                "key_points": ["Tests matter"],
            }
        },
    }

    content = render_markdown(
        transcript,
        {"title": "profile", "language": "en", "profiled": True},
        diarized=True,
        profile=profile,
    )
    meta = _frontmatter(content)

    assert meta["speakers"]["Speaker 1"]["role"] == "host"
    # Les empreintes vocales ne doivent PAS polluer le .md (pavé base64) :
    # elles vivent dans output/.voiceprints.json.
    assert "speaker_embeddings" not in meta
    assert "speaker_embeddings" not in content
    assert "## 🗣️ Conversation profile" in content
    assert "## Transcript" in content


def test_embedding_encoding_roundtrips() -> None:
    """L'encodage base64 des empreintes reste réversible (stockage annexe)."""
    assert decode_embedding(encode_embedding([0.25, 0.5])) == pytest.approx([0.25, 0.5])


def test_unique_path_increments_existing_names(tmp_path) -> None:
    (tmp_path / "réunion.md").write_text("first", encoding="utf-8")
    (tmp_path / "réunion-1.md").write_text("second", encoding="utf-8")

    assert unique_path("Réunion", tmp_path) == tmp_path / "réunion-2.md"


def test_write_md_creates_parent_directories(tmp_path) -> None:
    target = tmp_path / "nested" / "transcript.md"

    result = write_md("contenu", target)

    assert result == target
    assert target.read_text(encoding="utf-8") == "contenu"



# --- En-tête de profil sans rôle ---------------------------------------------
# Le garde-fou de nommage retire TOUS les rôles au-delà de 8 locuteurs. Le
# gabarit écrivait alors « **Speaker 1 — —** » : un tiret orphelin sur chaque
# ligne. Constaté sur un vrai transcript à 14 locuteurs.

def _entetes(speaker_info):
    from core.writer_md import _render_profile_sections
    lignes = _render_profile_sections(
        {"speakers": {"Speaker 1": speaker_info}}, diarized=True,
    )
    return [line for line in lignes if line.startswith("**Speaker")]


def test_sans_role_aucun_tiret_orphelin():
    lignes = _entetes({"talk_time": "00:22", "share": "2%"})
    assert lignes == ["**Speaker 1** \u00b7 00:22 \u00b7 2%"]
    assert "\u2014" not in lignes[0]


def test_avec_role_le_tiret_est_conserve():
    lignes = _entetes({"role": "Interviewer", "talk_time": "05:13"})
    assert lignes == ["**Speaker 1 \u2014 Interviewer** \u00b7 05:13"]


def test_role_vide_traite_comme_absent():
    """`sanitize_roles` renvoie une cha\u00eene vide, pas None."""
    assert _entetes({"role": "", "share": "2%"}) == ["**Speaker 1** \u00b7 2%"]
