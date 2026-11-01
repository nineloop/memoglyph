"""Premier usage : le téléchargement du modèle est ANNONCÉ.

La barre restait figée vers 11 % pendant le téléchargement (~1,5 Go), sans
signal, indiscernable d'un blocage. La transcription émet maintenant le code
`step.model.downloading` quand le modèle n'est PAS encore en cache — et rien de
tel quand il l'est déjà.

`mlx_whisper` est absent de la CI (profil sans `torch`/`mlx-whisper`) : on
l'injecte, minimal, pour éprouver le CÂBLAGE sans modèle réel.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from core import stt_whisper

pytestmark = pytest.mark.unit


def _fake_hf(monkeypatch, fn) -> None:
    """Injecte un faux `huggingface_hub` : le test ne dépend pas du paquet réel,
    absent de la CI sans stack ML (il est OPTIONNEL, avec repli gardé)."""
    fake = types.ModuleType("huggingface_hub")
    fake.try_to_load_from_cache = fn
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake)


def test_en_cache_vrai_quand_un_fichier_du_repo_est_trouve(monkeypatch) -> None:
    _fake_hf(monkeypatch, lambda repo_id, filename:
             "/c/config.json" if filename == "config.json" else None)
    assert stt_whisper._modele_en_cache("mlx-community/whisper-x") is True


def test_en_cache_faux_quand_aucun_fichier_n_est_en_cache(monkeypatch) -> None:
    _fake_hf(monkeypatch, lambda repo_id, filename: None)
    assert stt_whisper._modele_en_cache("mlx-community/whisper-x") is False


def test_en_cache_vrai_pour_un_chemin_local(tmp_path) -> None:
    """Un modèle déjà sur disque (chemin) n'est jamais « à télécharger »."""
    assert stt_whisper._modele_en_cache(str(tmp_path)) is True


def _fausse_mlx_whisper(monkeypatch) -> None:
    import numpy as np

    fake = types.ModuleType("mlx_whisper")
    fake.transcribe = lambda audio, **k: {
        "segments": [{"text": "salut", "start": 0.0, "end": 1.0}],
        "text": "salut", "language": "fr"}
    audio_mod = types.ModuleType("mlx_whisper.audio")
    audio_mod.load_audio = lambda p: np.zeros(16000, dtype=np.float32)  # 1 s
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake)
    monkeypatch.setitem(sys.modules, "mlx_whisper.audio", audio_mod)


def test_premier_usage_annonce_le_telechargement(monkeypatch) -> None:
    """Modèle NON en cache → le code `step.model.downloading` est émis."""
    _fausse_mlx_whisper(monkeypatch)
    monkeypatch.setattr(stt_whisper, "_modele_en_cache", lambda repo: False)

    codes: list[str] = []
    tr = stt_whisper.WhisperEngine("mlx-community/whisper-x").transcribe(
        Path("x.wav"),
        progress=lambda p, m: codes.append(getattr(m, "code", str(m))))

    assert "step.model.downloading" in codes, codes
    assert "salut" in tr.full_text, "la transcription doit quand même aboutir"


def test_modele_deja_en_cache_n_annonce_aucun_telechargement(monkeypatch) -> None:
    """Modèle en cache → aucun bruit « premier usage » à chaque run."""
    _fausse_mlx_whisper(monkeypatch)
    monkeypatch.setattr(stt_whisper, "_modele_en_cache", lambda repo: True)

    codes: list[str] = []
    stt_whisper.WhisperEngine("mlx-community/whisper-x").transcribe(
        Path("x.wav"),
        progress=lambda p, m: codes.append(getattr(m, "code", str(m))))

    assert "step.model.downloading" not in codes
