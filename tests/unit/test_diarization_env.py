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
    # Le chargement pyannote lui-même n'est pas testé ici, et ne doit JAMAIS joindre le réseau : sans ce faux module,
    # un venv où `pyannote.audio` est installé va jusqu'au vrai `Pipeline.from_pretrained` (garde réseau : refus).
    import sys
    import types

    class _PipelineFactice:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            raise RuntimeError("arrêt volontaire du faux chargement")

    faux = types.ModuleType("pyannote.audio")
    faux.Pipeline = _PipelineFactice
    monkeypatch.setitem(sys.modules, "pyannote.audio", faux)
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


def test_la_telemetrie_de_pyannote_est_coupee_aux_deux_chargements_meme_si_elle_etait_activee(monkeypatch):
    """`pyannote.audio` envoie par défaut des statistiques d'usage à un hôte tiers : la promesse « aucune télémétrie »
    exige que la variable soit « false » AU MOMENT où le paquet charge un modèle, même si l'utilisateur l'avait posée à « true »."""
    import os
    import sys
    import types

    monkeypatch.setenv("PYANNOTE_METRICS_ENABLED", "true")
    monkeypatch.setattr(diarization, "_MODELE_EMPREINTE", None)
    vu: dict[str, str | None] = {}

    class Pipeline:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            vu["pipeline"] = os.environ.get("PYANNOTE_METRICS_ENABLED")
            raise RuntimeError("arrêt volontaire du faux chargement")

    class Model:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            vu["empreinte"] = os.environ.get("PYANNOTE_METRICS_ENABLED")
            raise RuntimeError("arrêt volontaire du faux chargement")

    faux = types.ModuleType("pyannote.audio")
    faux.Pipeline, faux.Model, faux.Inference = Pipeline, Model, object
    monkeypatch.setitem(sys.modules, "pyannote.audio", faux)

    with pytest.raises(diarization.DiarizationError):
        diarization.load_pipeline("jeton")
    with pytest.raises(RuntimeError):
        diarization._modele_empreinte("jeton")

    assert vu == {"pipeline": "false", "empreinte": "false"}
