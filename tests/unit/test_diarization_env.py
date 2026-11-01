"""`core/` utilisé comme bibliothèque doit trouver le HF_TOKEN du `.env`.

`config.HF_TOKEN` est lu à l'import, avant tout chargement du `.env` : seuls
`api/main.py` et `core/pipeline.py` appellent `load_env()`. Un script important
`core.diarization` directement se voyait donc répondre « HF_TOKEN manquant.
Renseigne-le dans .env » alors que le token y était — le message envoyait
corriger ce qui n'était pas cassé.
"""

import pytest

from core import config, diarization

pytestmark = pytest.mark.unit


def test_le_token_du_env_est_charge_a_la_demande(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(config, "HF_TOKEN", None)
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setenv("MEMOGLYPH_ENV_FILE", str(tmp_path / ".env"))  # le `.env` de la racine d'essai
    (tmp_path / ".env").write_text("HF_TOKEN=hf_depuis_le_fichier\n", encoding="utf-8")

    vu = {}

    def faux_pipeline(token):
        vu["token"] = token
        return "pipeline"

    monkeypatch.setattr(diarization, "_build_pipeline", faux_pipeline, raising=False)
    try:
        diarization.load_pipeline(None)
    except Exception:
        pass  # le chargement pyannote lui-même n'est pas testé ici
    assert config.HF_TOKEN == "hf_depuis_le_fichier", (
        "load_pipeline doit tenter load_env() avant de renoncer"
    )


def test_sans_token_nulle_part_l_erreur_reste_explicite(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(config, "HF_TOKEN", None)
    monkeypatch.setattr(config, "ROOT", tmp_path)  # pas de .env du tout
    with pytest.raises(diarization.DiarizationError) as exc:
        diarization.load_pipeline(None)
    assert "HF_TOKEN" in str(exc.value)
    assert "huggingface.co" in str(exc.value), "le message doit pointer vers les conditions du modèle"
