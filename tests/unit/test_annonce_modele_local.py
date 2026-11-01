"""Premier usage du modèle LOCAL de compte-rendu : préparation ANNONCÉE.

Pendant, côté CR, du premier téléchargement STT : au premier compte-rendu, `mlx_lm.server`
télécharge (~2 Go) puis charge Qwen sans signal, et la barre paraît figée. Le
pipeline annonce désormais, AVANT le premier appel au modèle local (profilage ou
CR), un état :

- `step.model.downloading` si le cache HF prouve que le modèle n'est pas là ;
- `step.model.loading` si le cache prouve qu'il est là ;
- `step.model.preparing` (générique honnête) si on ne peut pas trancher.

Un fournisseur cloud n'a rien de local à télécharger : aucune annonce. On ne
touche jamais `core/llm_mlx.py` — c'est une lecture SEULE du cache.
"""
from __future__ import annotations

import pytest

from core import pipeline
from core.stt_base import Segment, Transcript

pytestmark = pytest.mark.unit


def _stub(monkeypatch, tmp_path):
    """Le pipeline sans ses dépendances lourdes — ni audio, ni modèle, ni LLM."""
    class FauxMoteur:
        name, model_name = "whisper", "mlx-community/whisper-test"

        def transcribe(self, wav_path, **kw):
            return Transcript(
                segments=[Segment(0.0, 2.0, "bonjour", None)],
                full_text="bonjour", language="fr")

    monkeypatch.setattr(pipeline, "get_engine", lambda *a, **k: FauxMoteur())
    monkeypatch.setattr(pipeline.media, "to_wav16k", lambda src, **k: src)
    monkeypatch.setattr(pipeline.media, "probe_duration", lambda src: 2.0)
    monkeypatch.setattr(pipeline.media, "pcm_sha256", lambda src: "f" * 64)
    monkeypatch.setattr(pipeline.media, "validate_input", lambda src: src)
    monkeypatch.setattr(pipeline.media, "archive_audio", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.media, "archive_faithful", lambda *a, **k: None)
    monkeypatch.setattr(pipeline.media, "keep_original", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "_project_id_for_output", lambda d: "proj")
    monkeypatch.setattr(pipeline.llm, "unload", lambda **k: False)
    from core import report as report_mod
    monkeypatch.setattr(report_mod, "generate_report", lambda *a, **k: None)

    src = tmp_path / "reunion.wav"
    src.write_bytes(b"RIFF")
    out = tmp_path / "out"
    out.mkdir()
    return src, out


def _codes(monkeypatch, tmp_path, *, etat, provider="mlx",
           profile_on=False, report_on=True) -> list[str]:
    src, out = _stub(monkeypatch, tmp_path)
    monkeypatch.setattr(pipeline, "_etat_cache_hf", lambda repo: etat)
    codes: list[str] = []
    pipeline.run(
        src, engine_name="whisper", llm_provider=provider,
        profile_on=profile_on, report_on=report_on, output_dir=out,
        timestamps=True,
        progress=lambda p, m: codes.append(getattr(m, "code", str(m))))
    return codes


def test_premier_CR_non_en_cache_annonce_le_TELECHARGEMENT(monkeypatch, tmp_path):
    assert "step.model.downloading" in _codes(monkeypatch, tmp_path, etat="download")


def test_CR_en_cache_annonce_le_CHARGEMENT_pas_un_telechargement(monkeypatch, tmp_path):
    codes = _codes(monkeypatch, tmp_path, etat="load")
    assert "step.model.loading" in codes
    assert "step.model.downloading" not in codes


def test_cache_indeterminable_annonce_la_PREPARATION_generique(monkeypatch, tmp_path):
    codes = _codes(monkeypatch, tmp_path, etat="unknown")
    assert "step.model.preparing" in codes
    assert "step.model.downloading" not in codes


def test_fournisseur_CLOUD_n_annonce_aucun_modele_local(monkeypatch, tmp_path):
    codes = _codes(monkeypatch, tmp_path, etat="download", provider="mistral")
    assert not any(c.startswith("step.model.") for c in codes), codes


def test_avec_profilage_l_annonce_n_est_faite_qu_UNE_fois(monkeypatch, tmp_path):
    """Profilage = premier usage : on annonce avant lui, pas de nouveau au CR."""
    from core import profiling

    monkeypatch.setattr(profiling, "profile", lambda *a, **k: {"speakers": {}})
    codes = _codes(monkeypatch, tmp_path, etat="download",
                   profile_on=True, report_on=True)
    assert codes.count("step.model.downloading") == 1, codes
