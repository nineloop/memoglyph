from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from core import llm

from core import diarization, docid, pipeline, profiling, stt, voiceprint
from core.stt_base import Segment, Transcript


pytestmark = pytest.mark.integration


@dataclass
class FakeEngine:
    transcript: Transcript
    error: Exception | None = None
    name: str = "fake-whisper"
    model_name: str = "fake-model"

    def transcribe(self, wav_path: Path, progress=None) -> Transcript:
        self.wav_path = wav_path
        if progress:
            progress(0.5, "Moitié STT")
        if self.error:
            raise self.error
        return self.transcript


def _base_setup(monkeypatch, tmp_path, transcript: Transcript, *, error=None):
    source = tmp_path / "source.mp3"
    source.write_bytes(b"source")
    wav = tmp_path / "temporary.wav"
    wav.write_bytes(b"wav")
    target = tmp_path / "result.md"
    engine = FakeEngine(transcript, error=error)

    monkeypatch.setattr(pipeline.config, "load_env", lambda: None)
    monkeypatch.setattr(pipeline.media, "validate_input", lambda path: path)
    monkeypatch.setattr(pipeline.media, "probe_duration", lambda path: 61.2)
    monkeypatch.setattr(pipeline.media, "to_wav16k", lambda path: wav)
    monkeypatch.setattr(pipeline.media, "pcm_sha256", lambda path: "f" * 64)
    monkeypatch.setattr(pipeline, "get_engine", lambda name, language=None, **_kw: engine)
    monkeypatch.setattr(pipeline.writer_md, "unique_path", lambda name, output_dir=None: target)
    project_id = docid.nouveau()
    monkeypatch.setattr(
        pipeline, "_project_id_for_output", lambda output_dir: project_id, raising=False,
    )

    return source, wav, target, engine, project_id


def test_pipeline_runs_basic_transcription_and_scales_progress(monkeypatch, tmp_path) -> None:
    transcript = Transcript(
        segments=[Segment(0, 1, "Bonjour")],
        full_text="Bonjour",
        language="fr",
    )
    source, wav, target, engine, project_id = _base_setup(monkeypatch, tmp_path, transcript)
    rendered = {}
    events = []
    fingerprint_args = {}
    fingerprint = {"kind": "transcript", "fingerprint": "a" * 64}

    def make_fingerprint(**kwargs):
        fingerprint_args.update(kwargs)
        return fingerprint

    monkeypatch.setattr(pipeline.provenance, "transcript_fingerprint", make_fingerprint)

    def render(value, meta, **options):
        rendered.update(transcript=value, meta=meta, options=options)
        return "markdown"

    monkeypatch.setattr(pipeline.writer_md, "render_markdown", render)

    out_path, content = pipeline.run(
        source,
        engine_name="whisper",
        language="fr",
        timestamps=False,
        progress=lambda fraction, message: events.append((fraction, message)),
    )

    assert out_path == target
    assert content == "markdown"
    assert target.read_text(encoding="utf-8") == "markdown"
    assert engine.wav_path == wav
    assert not wav.exists()
    assert rendered["meta"]["duration"] == "00:01:01"
    assert rendered["meta"]["source"] == "source.mp3"
    assert rendered["meta"]["engine"] == "fake-whisper"
    assert rendered["meta"]["pipeline"] == fingerprint
    assert fingerprint_args == {
        "engine": "fake-whisper",
        "model": "fake-model",
        "language_requested": "fr",
        "timestamps": False,
        "diarized": False,
        "profiled": False,
        "input_pcm_sha256": "f" * 64,
        "derived_from_document_id": None,
        # La provenance doit décrire ce qui a TOURNÉ.
        # `diarization_model=None` parce que rien n'a diarisé ici ;
        # `profiling_model` suit le fournisseur LLM réellement employé.
        # `artifact_status` remplace `artifacts_enabled`, un booléen qui valait
        # toujours faux : c'est l'état RÉEL rendu par la politique du moteur,
        # ici celle de Whisper, qui exécute `artifacts.scrub`.
        "artifact_status": "checked",
        "artifact_policy": "artifacts.scrub",
        "artifact_policy_version": 2,   # suites sans espace depuis la v2
        "diarization_model": None,
        # DÉRIVÉ du défaut, pas figé : ce test épinglait `qwen3.5:9b`, donc
        # le modèle Ollama, et il aurait fallu le retoucher à chaque
        # changement de fournisseur par défaut.
        "profiling_model": llm.modele_servi(llm.FOURNISSEUR_DEFAUT),
    }
    assert docid.est_valide(rendered["meta"]["id"])
    assert docid.est_valide(rendered["meta"]["meeting_id"])
    assert rendered["meta"]["project_id"] == project_id
    assert rendered["options"]["timestamps"] is False
    assert any(abs(frac - 0.3) < 1e-6 and msg == "Moitié STT" for frac, msg in events)
    assert events[-1][0] == 1.0


def test_pipeline_records_additive_lineage(monkeypatch, tmp_path) -> None:
    transcript = Transcript(
        segments=[Segment(0, 1, "Bonjour")],
        full_text="Bonjour",
        language="fr",
    )
    source, _, _, _, _ = _base_setup(monkeypatch, tmp_path, transcript)
    captured = {}
    parent_id = docid.nouveau()

    def render(value, meta, **options):
        captured.update(meta=meta)
        return "markdown"

    monkeypatch.setattr(pipeline.writer_md, "render_markdown", render)
    pipeline.run(source, derived_from_document_id=parent_id)

    assert captured["meta"]["derived_from_document_id"] == parent_id
    assert (
        captured["meta"]["pipeline"]["lineage"]["derived_from_document_id"]
        == parent_id
    )


def test_pipeline_adds_diarization_and_aligned_embeddings(monkeypatch, tmp_path) -> None:
    transcript = Transcript(
        segments=[Segment(0, 2, "Bonjour")],
        full_text="Bonjour",
        language="fr",
    )
    source, wav, target, _, _ = _base_setup(monkeypatch, tmp_path, transcript)
    captured = {}
    result = diarization.DiarizeResult(
        turns=[diarization.SpeakerTurn(0, 2, "RAW_B")],
        embeddings={"RAW_B": [0.1, 0.2]},
    )
    monkeypatch.setattr(diarization, "diarize", lambda *args, **kwargs: result)

    def render(value, meta, **options):
        captured.update(transcript=value, meta=meta, options=options)
        return "diarized"

    monkeypatch.setattr(pipeline.writer_md, "render_markdown", render)
    # Les empreintes ne vont plus dans le .md mais dans le stockage annexe.
    saved: dict = {}
    monkeypatch.setattr(
        voiceprint, "save_embeddings",
        lambda name, embeddings, **kw: saved.update(name=name, embeddings=embeddings),
    )

    pipeline.run(source, diarize_on=True)

    assert not wav.exists()
    assert target.read_text(encoding="utf-8") == "diarized"
    assert captured["transcript"].segments[0].speaker == "Speaker 1"
    assert captured["meta"]["diarization"] is True
    assert captured["meta"]["n_speakers"] == 1
    assert "embeddings" not in captured["options"]  # plus dans le Markdown
    # le label BRUT est bien réaligné sur le libellé lisible du .md
    assert saved["embeddings"] == {"Speaker 1": [0.1, 0.2]}
    assert saved["name"] == captured["meta"]["id"]


def test_pipeline_profiles_after_unloading_stt(monkeypatch, tmp_path) -> None:
    transcript = Transcript(
        segments=[Segment(0, 1, "Hello")],
        full_text="Hello",
        language="en",
    )
    source, _, _, _, _ = _base_setup(monkeypatch, tmp_path, transcript)
    calls = []
    profile_data = {"conversation": {"summary": "Summary"}}
    monkeypatch.setattr(stt, "unload", lambda: calls.append("unload"))
    monkeypatch.setattr(
        profiling,
        "profile",
        lambda value, *, diarized, progress=None, **_kw: calls.append("profile") or profile_data,
    )
    captured = {}

    def render(value, meta, **options):
        captured.update(meta=meta, options=options)
        return "profiled"

    monkeypatch.setattr(pipeline.writer_md, "render_markdown", render)

    pipeline.run(source, profile_on=True)

    assert calls == ["unload", "profile"]
    assert captured["meta"]["profiled"] is True
    # `config.LLM_MODEL` est le modèle OLLAMA : cette assertion ne tenait que
    # parce qu'Ollama était le défaut. Elle demande maintenant au registre ce
    # que le fournisseur par défaut sert réellement.
    assert captured["meta"]["llm_model"] == llm.modele_servi(llm.FOURNISSEUR_DEFAUT)
    assert captured["options"]["profile"] == profile_data


def test_pipeline_cleans_temporary_wav_after_engine_error(monkeypatch, tmp_path) -> None:
    transcript = Transcript()
    source, wav, _, _, _ = _base_setup(
        monkeypatch,
        tmp_path,
        transcript,
        error=RuntimeError("STT failed"),
    )

    with pytest.raises(RuntimeError, match="STT failed"):
        pipeline.run(source)

    assert not wav.exists()


def test_stt_execution_is_scoped_to_the_destination_project(monkeypatch, tmp_path) -> None:
    """RÉGRESSION (acceptation FinOps V1) : l'exécution FinOps `stt` porte le `project_id`
    de destination, comme le CR. Sans lui, l'exécution restait à `project_id=NULL` et le coût
    de transcription — dominant en local — n'apparaissait dans AUCUNE vue Usage bornée par
    projet (seulement en global) : la dépense de transcription d'un projet devenait invisible."""
    from core import usage_store

    monkeypatch.setenv("MEMOGLYPH_USAGE_DB", str(tmp_path / "usage.db"))
    transcript = Transcript(
        segments=[Segment(0, 1, "Bonjour")], full_text="Bonjour", language="fr",
    )
    source, wav, target, engine, project_id = _base_setup(monkeypatch, tmp_path, transcript)
    monkeypatch.setattr(
        pipeline.provenance, "transcript_fingerprint",
        lambda **_k: {"kind": "transcript", "fingerprint": "a" * 64},
    )
    monkeypatch.setattr(pipeline.writer_md, "render_markdown", lambda value, meta, **_o: "markdown")

    pipeline.run(source, engine_name="whisper", language="fr", timestamps=False)

    conn = usage_store.read_connect(tmp_path / "usage.db")
    try:
        row = conn.execute(
            "SELECT project_id, document_id FROM execution WHERE kind='stt'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "aucune exécution STT persistée"
    # Le coût STT DOIT être attribuable au projet, sinon il fuit hors de toute vue projet.
    assert row["project_id"] == project_id


def test_pipeline_writes_capture_provenance_only_when_given(monkeypatch, tmp_path) -> None:
    """Provenance du compagnon de capture : recopiée dans le front-matter, absente sinon."""
    transcript = Transcript(segments=[Segment(0, 1, "Bonjour")], full_text="Bonjour", language="fr")
    source, _, _, _, _ = _base_setup(monkeypatch, tmp_path, transcript)
    metas = []

    def render(value, meta, **options):
        metas.append(meta)
        return "markdown"

    monkeypatch.setattr(pipeline.writer_md, "render_markdown", render)
    pipeline.run(source, capture={"application": "Microsoft Teams", "mode": "visio"})
    pipeline.run(source)

    assert metas[0]["capture"] == {"application": "Microsoft Teams", "mode": "visio"}
    assert metas[-1]["capture"] is None


def test_a_failed_report_leaves_a_durable_reserve_in_the_transcript(monkeypatch, tmp_path) -> None:
    """Le message de progression disparaît avec la page : le front-matter reste."""
    from core import report as report_mod

    transcript = Transcript(segments=[Segment(0, 1, "Bonjour")], full_text="Bonjour", language="fr")
    source, _, target, _, _ = _base_setup(monkeypatch, tmp_path, transcript)
    monkeypatch.setattr(pipeline, "_annoncer_modele_local", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.llm, "unload", lambda **k: None)

    def echec(*a, **k):
        raise RuntimeError("Le service LLM (mlx) n'est pas disponible.\n  détail")

    monkeypatch.setattr(report_mod, "generate_report", echec)
    pipeline.run(source, report_on=True)

    assert "report_error: Le service LLM (mlx) n'est pas disponible. détail" in target.read_text(
        encoding="utf-8")


def test_without_report_no_reserve_is_written(monkeypatch, tmp_path) -> None:
    transcript = Transcript(segments=[Segment(0, 1, "Bonjour")], full_text="Bonjour", language="fr")
    source, _, target, _, _ = _base_setup(monkeypatch, tmp_path, transcript)

    pipeline.run(source)

    assert "report_error" not in target.read_text(encoding="utf-8")


def test_an_empty_extraction_is_named_in_the_transcript_reserve(monkeypatch, tmp_path) -> None:
    from core import report as report_mod
    from core.experimental.pipeline import ExtractionVide

    transcript = Transcript(segments=[Segment(0, 1, "Bonjour")], full_text="Bonjour", language="fr")
    source, _, target, _, _ = _base_setup(monkeypatch, tmp_path, transcript)
    monkeypatch.setattr(pipeline, "_annoncer_modele_local", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.llm, "unload", lambda **k: None)

    def vide(*a, **k):
        raise ExtractionVide(appels=2, refuses=0)

    monkeypatch.setattr(report_mod, "generate_report", vide)
    pipeline.run(source, report_on=True)

    import yaml
    meta = yaml.safe_load(target.read_text(encoding="utf-8").split("---")[1])
    assert meta["report_error"] == ("Extraction vide : aucune unité retenue sur 2 appel(s) au modèle, "
                                    "dont 0 refusé(s).")
