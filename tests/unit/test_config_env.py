"""Le `.env` est posé avant la lecture des réglages préfixés (`core/config.py`).

Les constantes de `core.config` sont lues à l'import ; `load_env()`, appelé ensuite par
`api/main.py`, posait le `.env` trop tard : `MEMOGLYPH_MISTRAL_STT_TRANCHE_S` (ou
`MEMOGLYPH_MLX_PORT`, `MEMOGLYPH_ECL_MLX_MODEL`) écrit dans `.env` était ignoré. Une
variable déjà exportée l'emporte toujours. Chaque test écrit son propre `.env`
temporaire : le `.env` réel de la racine n'est jamais lu (`tests/conftest.py`).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from core import config

pytestmark = pytest.mark.unit

RACINE = Path(__file__).resolve().parents[2]
SUFFIXE = "MISTRAL_STT_TRANCHE_S"


def _sans_le_reglage(environ: dict) -> dict:
    return {k: v for k, v in environ.items() if k != config.ENV_PREFIX + SUFFIXE}


def _env(tmp_path: Path, contenu: str) -> Path:
    fichier = tmp_path / ".env"
    fichier.write_text(contenu, encoding="utf-8")
    return fichier


def _tranche_lue_a_l_import(fichier: Path, **exportees: str) -> str:
    """`config.MISTRAL_STT_TRANCHE_S` d'un interpréteur neuf, sur ce `.env`."""
    environ = {**_sans_le_reglage(dict(os.environ)), "MEMOGLYPH_ENV_FILE": str(fichier), **exportees}
    sortie = subprocess.run(
        [sys.executable, "-c", "from core import config; print(config.MISTRAL_STT_TRANCHE_S)"],
        cwd=RACINE, env=environ, capture_output=True, text=True, check=True)
    return sortie.stdout.strip()


def test_un_reglage_prefixe_du_env_est_lu_a_l_import(tmp_path) -> None:
    fichier = _env(tmp_path, f"# réglage de la machine\nMEMOGLYPH_{SUFFIXE}=1200\n")
    assert _tranche_lue_a_l_import(fichier) == "1200.0"
    assert _tranche_lue_a_l_import(tmp_path / "absent") == "3000.0"   # sans fichier : le défaut


def test_une_variable_exportee_l_emporte_sur_le_env(tmp_path) -> None:
    fichier = _env(tmp_path, f"MEMOGLYPH_{SUFFIXE}=1200\n")
    assert _tranche_lue_a_l_import(fichier, **{f"MEMOGLYPH_{SUFFIXE}": "900"}) == "900.0"


def test_load_env_pose_le_env_temporaire_sans_ecraser_l_exporte(tmp_path, monkeypatch) -> None:
    fichier = _env(tmp_path, f"MEMOGLYPH_{SUFFIXE}='1200'\n")
    with mock.patch.dict(os.environ, _sans_le_reglage(dict(os.environ)), clear=True):
        monkeypatch.setenv("MEMOGLYPH_ENV_FILE", str(fichier))
        for nom in ("HF_TOKEN", "MISTRAL_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.setattr(config, nom, getattr(config, nom))   # rendus après le test
        config.load_env()
        assert config.getenv_compat(SUFFIXE) == "1200"
    with mock.patch.dict(os.environ, {**_sans_le_reglage(dict(os.environ)), f"MEMOGLYPH_{SUFFIXE}": "900"},
                         clear=True):
        monkeypatch.setenv("MEMOGLYPH_ENV_FILE", str(fichier))
        config.load_env()
        assert config.getenv_compat(SUFFIXE) == "900"
