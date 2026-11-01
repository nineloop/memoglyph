from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from core import docid, faithful_backfill, media, writer_md

pytestmark = pytest.mark.unit


def _document(root, name: str, **meta):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        writer_md._yaml_frontmatter(meta) + "\n\n# Transcript\n\nDu texte.\n",
        encoding="utf-8",
    )
    return path


def test_prepare_est_une_simulation_et_n_utilise_jamais_l_archive_d_ecoute(tmp_path):
    root = tmp_path / "output"
    sources = tmp_path / "sources"
    root.mkdir()
    sources.mkdir()
    ready_id, missing_id, existing_id = docid.nouveau(), docid.nouveau(), docid.nouveau()
    (sources / "ready.wav").write_bytes(b"original")
    _document(root, "ready.md", id=ready_id, project_id="p1", source="ready.wav")
    _document(root, "missing.md", id=missing_id, project_id="p1", source="missing.wav")
    _document(root, "existing.md", id=existing_id, project_id="p1", source="old.wav")
    _document(root, "report.md", type="report", source_transcript="ready.md")
    listening = media.audio_path(missing_id, root=root)
    listening.parent.mkdir(parents=True)
    listening.write_bytes(b"lossy")
    faithful = media.faithful_path(existing_id, root=root)
    faithful.parent.mkdir(parents=True)
    faithful.write_bytes(b"already")
    before = sorted(str(path.relative_to(root)) for path in root.rglob("*"))

    plan = faithful_backfill.prepare(root, source_dirs=[sources])

    assert [candidate.document.name for candidate in plan.candidates] == ["ready.md"]
    assert plan.missing_originals == ("missing.md",)
    assert plan.existing == ("existing.md",)
    assert sorted(str(path.relative_to(root)) for path in root.rglob("*")) == before
    assert listening.read_bytes() == b"lossy"


def test_prepare_est_borne_par_project_id(tmp_path):
    root = tmp_path / "output"
    sources = tmp_path / "sources"
    root.mkdir()
    sources.mkdir()
    (sources / "a.wav").write_bytes(b"a")
    (sources / "b.wav").write_bytes(b"b")
    _document(root, "a.md", id=docid.nouveau(), project_id="p1", source="a.wav")
    _document(root, "b.md", id=docid.nouveau(), project_id="p2", source="b.wav")

    plan = faithful_backfill.prepare(root, project_id="p1", source_dirs=[sources])

    assert [candidate.document.name for candidate in plan.candidates] == ["a.md"]
    assert [path.name for path, _digest in plan.document_hashes] == ["a.md"]


@pytest.fixture
def original_audio(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg absent")
    source = tmp_path / "original.wav"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=0.2", "-ar", "44100", "-ac", "2",
            str(source), "-y",
        ],
        check=True,
    )
    return source


def test_apply_publie_un_flac_identique_sans_toucher_au_markdown(
    tmp_path, original_audio,
):
    root = tmp_path / "output"
    root.mkdir()
    document_id = docid.nouveau()
    document = _document(
        root, "ready.md", id=document_id, project_id="p1", source=original_audio.name,
    )
    before = document.read_bytes()
    plan = faithful_backfill.prepare(root, source_dirs=[original_audio.parent])

    result = faithful_backfill.apply(plan)

    destination = media.faithful_path(document_id, root=root)
    assert result["done"] == 1 and result["errors"] == 0
    assert destination.exists()
    assert media.pcm_sha256(destination) == media.pcm_sha256(original_audio)
    assert document.read_bytes() == before
    assert not list(destination.parent.glob("*.tmp.flac"))


def test_create_faithful_refuse_une_verification_pcm_en_echec(
    tmp_path, original_audio, monkeypatch,
):
    destination = tmp_path / "faithful.flac"
    digests = iter(("source", "archive-differente"))
    monkeypatch.setattr(media, "pcm_sha256", lambda _path: next(digests))

    with pytest.raises(media.MediaError, match="non identique"):
        media.create_faithful(original_audio, destination, verify_pcm=True)

    assert not destination.exists()
    assert not list(tmp_path.glob("*.tmp.flac"))


def test_apply_exige_verification_pcm_et_interdit_l_ecrasement(
    tmp_path, monkeypatch,
):
    root = tmp_path / "output"
    sources = tmp_path / "sources"
    root.mkdir()
    sources.mkdir()
    source = sources / "ready.wav"
    source.write_bytes(b"original")
    document_id = docid.nouveau()
    _document(root, "ready.md", id=document_id, project_id="p1", source=source.name)
    plan = faithful_backfill.prepare(root, source_dirs=[sources])
    calls = []

    def create(src, destination, **options):
        calls.append((src, destination, options))
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"flac")
        return destination

    monkeypatch.setattr(media, "create_faithful", create)

    assert faithful_backfill.apply(plan)["done"] == 1
    assert calls[0][2] == {"verify_pcm": True, "overwrite": False}


def test_apply_refuse_un_original_modifie_depuis_la_simulation(tmp_path):
    root = tmp_path / "output"
    sources = tmp_path / "sources"
    root.mkdir()
    sources.mkdir()
    source = sources / "ready.wav"
    source.write_bytes(b"avant")
    document_id = docid.nouveau()
    _document(root, "ready.md", id=document_id, project_id="p1", source=source.name)
    plan = faithful_backfill.prepare(root, source_dirs=[sources])
    source.write_bytes("après — taille différente".encode())

    result = faithful_backfill.apply(plan)

    assert result["done"] == 0 and result["errors"] == 1
    assert "modifié depuis la simulation" in result["results"][0]["reason"]
    assert not media.faithful_path(document_id, root=root).exists()


def test_apply_retire_sa_publication_si_l_original_change_pendant_le_calcul(
    tmp_path, monkeypatch,
):
    root = tmp_path / "output"
    sources = tmp_path / "sources"
    root.mkdir()
    sources.mkdir()
    source = sources / "ready.wav"
    source.write_bytes(b"avant")
    document_id = docid.nouveau()
    _document(root, "ready.md", id=document_id, project_id="p1", source=source.name)
    plan = faithful_backfill.prepare(root, source_dirs=[sources])

    def create(src, destination, **_options):
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"flac")
        src.write_bytes("après — taille différente".encode())
        return destination

    monkeypatch.setattr(media, "create_faithful", create)

    result = faithful_backfill.apply(plan)

    assert result["done"] == 0 and result["errors"] == 1
    assert "modifié pendant le traitement" in result["results"][0]["reason"]
    assert not media.faithful_path(document_id, root=root).exists()


def test_apply_n_ecrase_pas_une_archive_apparue_apres_le_plan(tmp_path):
    root = tmp_path / "output"
    sources = tmp_path / "sources"
    root.mkdir()
    sources.mkdir()
    source = sources / "ready.wav"
    source.write_bytes(b"original")
    document_id = docid.nouveau()
    _document(root, "ready.md", id=document_id, project_id="p1", source=source.name)
    plan = faithful_backfill.prepare(root, source_dirs=[sources])
    destination = media.faithful_path(document_id, root=root)
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"concurrent")

    result = faithful_backfill.apply(plan)

    assert result["done"] == 0 and result["errors"] == 1
    assert destination.read_bytes() == b"concurrent"


def test_apply_refuse_un_markdown_modifie_depuis_la_simulation(tmp_path):
    root = tmp_path / "output"
    sources = tmp_path / "sources"
    root.mkdir()
    sources.mkdir()
    source = sources / "ready.wav"
    source.write_bytes(b"original")
    document_id = docid.nouveau()
    document = _document(
        root, "ready.md", id=document_id, project_id="p1", source=source.name,
    )
    plan = faithful_backfill.prepare(root, source_dirs=[sources])
    document.write_text(document.read_text() + "changement", encoding="utf-8")

    with pytest.raises(faithful_backfill.FaithfulBackfillError, match="Corpus modifié"):
        faithful_backfill.apply(plan)
    assert not media.faithful_path(document_id, root=root).exists()


def test_cli_simule_par_defaut(tmp_path, capsys):
    root = tmp_path / "output"
    root.mkdir()
    document_id = docid.nouveau()
    original = root / ".audio" / "originals" / f"{document_id}.wav"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"original")
    _document(root, "ready.md", id=document_id, project_id="p1", source="ready.wav")

    assert faithful_backfill.main(["--root", str(root)]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["simulation"] is True
    assert [item["document"] for item in result["eligible"]] == ["ready.md"]
    assert not media.faithful_path(document_id, root=root).exists()
