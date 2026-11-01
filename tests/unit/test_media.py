from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import media


pytestmark = pytest.mark.unit


def test_validate_input_rejects_missing_file(tmp_path) -> None:
    with pytest.raises(media.MediaError, match="Fichier introuvable"):
        media.validate_input(tmp_path / "missing.mp3")


def test_validate_input_rejects_unsupported_extension(tmp_path) -> None:
    source = tmp_path / "notes.txt"
    source.write_text("not media", encoding="utf-8")

    with pytest.raises(media.MediaError, match="Format non supporté"):
        media.validate_input(source)


def test_validate_input_accepts_extension_case_insensitively(tmp_path) -> None:
    source = tmp_path / "AUDIO.MP3"
    source.write_bytes(b"media")

    assert media.validate_input(source) == source


def test_ensure_ffmpeg_reports_missing_tool(monkeypatch) -> None:
    monkeypatch.setattr(media.shutil, "which", lambda tool: None if tool == "ffprobe" else tool)

    with pytest.raises(media.MediaError, match="ffprobe.*introuvable"):
        media.ensure_ffmpeg()


def test_probe_duration_parses_ffprobe_output(monkeypatch, tmp_path) -> None:
    source = tmp_path / "audio.mp3"
    monkeypatch.setattr(
        media.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="12.75\n"),
    )

    assert media.probe_duration(source) == 12.75


@pytest.mark.parametrize(
    "error",
    [subprocess.CalledProcessError(1, "ffprobe"), ValueError("bad duration")],
)
def test_probe_duration_returns_zero_for_unreadable_duration(monkeypatch, tmp_path, error) -> None:
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(media.subprocess, "run", fail)

    assert media.probe_duration(tmp_path / "audio.mp3") == 0.0


def test_to_wav16k_builds_expected_command(monkeypatch, tmp_path) -> None:
    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    cache = tmp_path / "cache"
    calls = []
    monkeypatch.setattr(media, "ensure_ffmpeg", lambda: None)

    def run(cmd, **kwargs):
        calls.append(cmd)
        Path(cmd[-2]).write_bytes(b"wav")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(media.subprocess, "run", run)

    result = media.to_wav16k(source, cache)

    assert result.exists()
    assert calls[0][:3] == ["ffmpeg", "-i", str(source)]
    assert ["-ac", "1"] == calls[0][3:5]
    assert "-vn" in calls[0]
    assert calls[0][-1] == "-y"


def test_to_wav16k_reuses_existing_cached_file(monkeypatch, tmp_path) -> None:
    source = tmp_path / "audio.mp3"
    source.write_bytes(b"audio")
    cache = tmp_path / "cache"
    monkeypatch.setattr(media, "ensure_ffmpeg", lambda: None)

    def first_run(cmd, **kwargs):
        Path(cmd[-2]).parent.mkdir(parents=True, exist_ok=True)
        Path(cmd[-2]).write_bytes(b"wav")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(media.subprocess, "run", first_run)
    first = media.to_wav16k(source, cache)

    def unexpected(*args, **kwargs):
        raise AssertionError("ffmpeg ne doit pas être relancé")

    monkeypatch.setattr(media.subprocess, "run", unexpected)
    second = media.to_wav16k(source, cache)

    assert second == first


def test_to_wav16k_surfaces_ffmpeg_error(monkeypatch, tmp_path) -> None:
    source = tmp_path / "broken.mp3"
    source.write_bytes(b"broken")
    monkeypatch.setattr(media, "ensure_ffmpeg", lambda: None)
    monkeypatch.setattr(
        media.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stderr="line 1\nline 2\nline 3\nline 4\ninvalid audio",
        ),
    )

    with pytest.raises(media.MediaError, match="invalid audio"):
        media.to_wav16k(source, tmp_path / "cache")



# --- Nos propres archives doivent être relisibles ----------------------------
# `AUDIO_EXT` propose « libopus » → « .opus », mais `.opus` manquait à
# `AUDIO_EXTS` : nos archives Opus étaient refusées par notre propre validation,
# donc illisibles pour le backfill. Trouvé en essayant les formats
# d'archive candidats — l'essai a planté sur notre propre sortie.

def test_tout_format_d_archive_produit_est_relisible():
    from core.config import SUPPORTED_EXTS
    produits = {"aac": ".m4a", "libopus": ".opus", "libmp3lame": ".mp3"}
    refuses = {c: e for c, e in produits.items() if e not in SUPPORTED_EXTS}
    assert not refuses, (
        f"archives produites mais illisibles par validate_input : {refuses}"
    )


def test_le_flac_est_accepte():
    """Format retenu pour l'archive fidèle : 16 kHz mono sans perte."""
    from core.config import SUPPORTED_EXTS
    assert ".flac" in SUPPORTED_EXTS


def test_audio_is_found_by_document_id_after_a_markdown_rename(
    tmp_path, monkeypatch,
) -> None:
    from core import config, docid, library

    output = tmp_path / "output"
    audio = output / ".audio"
    output.mkdir()
    audio.mkdir()
    document_id = docid.nouveau()
    (output / "renamed.md").write_text(
        f"---\nid: {document_id}\ntitle: Renamed\n---\n\nBody\n",
        encoding="utf-8",
    )
    canonical = audio / f"{document_id}.m4a"
    canonical.write_bytes(b"audio")
    monkeypatch.setattr(library, "OUTPUT_DIR", output)
    monkeypatch.setattr(config, "AUDIO_DIR", audio)

    assert media.audio_path("renamed.md") == canonical


# --- Capture à deux canaux (micro, système) ----------------------------------
# Le compagnon de capture enregistre le micro à gauche et le son de la visio à
# droite. Avant le STT, les deux voix doivent survivre au passage en mono : une
# seule piste gardée ferait perdre soit l'utilisateur, soit les autres.

def _wav_deux_canaux(chemin: Path, gauche: bool, droite: bool) -> None:
    import math
    import struct
    import wave

    with wave.open(str(chemin), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(48_000)
        trames = bytearray()
        for i in range(48_000):
            echantillon = int(12_000 * math.sin(2 * math.pi * 440 * i / 48_000))
            trames += struct.pack("<hh", echantillon if gauche else 0, echantillon if droite else 0)
        wav.writeframes(bytes(trames))


@pytest.mark.parametrize("gauche, droite", [(True, False), (False, True)])
def test_un_wav_a_deux_canaux_passe_en_mono_sans_perdre_un_canal(tmp_path, gauche, droite) -> None:
    import array
    import wave

    source = tmp_path / f"capture-{int(gauche)}{int(droite)}.wav"
    _wav_deux_canaux(source, gauche, droite)

    sortie = media.to_wav16k(source, cache_dir=tmp_path / "cache")

    with wave.open(str(sortie), "rb") as wav:
        assert (wav.getnchannels(), wav.getframerate()) == (1, media.SAMPLE_RATE)
        trames = wav.readframes(wav.getnframes())
    echantillons = array.array("h", trames)
    assert max(abs(e) for e in echantillons) > 3_000
