"""Variables d'environnement : les réglages se lisent sous le préfixe `MEMOGLYPH_`.

La règle ne tient que si TOUTES les lectures passent par `config.getenv_compat` :
une lecture directe ailleurs échapperait au point unique, en silence. D'où un test
qui lit le source.
"""

import re
from pathlib import Path

import pytest

from core import config
from tests.hors_export import exclus_de_l_export

pytestmark = pytest.mark.unit

RACINE = Path(__file__).resolve().parents[2]


def test_le_prefixe() -> None:
    assert config.ENV_PREFIX == "MEMOGLYPH_"


@pytest.mark.parametrize("environ,attendu", [
    ({"MEMOGLYPH_X": "m", "X": "nu"}, "m"),
    ({}, "défaut"),
    # Une valeur vide est une valeur posée : le défaut ne la remplace pas.
    ({"MEMOGLYPH_X": ""}, ""),
])
def test_la_variable_prefixee_posee_l_emporte_sur_le_defaut(environ: dict, attendu: str) -> None:
    assert config.getenv_compat("X", "défaut", environ=environ) == attendu


def test_sans_environ_explicite_on_lit_l_environnement(monkeypatch) -> None:
    monkeypatch.delenv("MEMOGLYPH_TEST_PREFIXE", raising=False)
    assert config.getenv_compat("TEST_PREFIXE", "défaut") == "défaut"
    monkeypatch.setenv("MEMOGLYPH_TEST_PREFIXE", "actuel")
    assert config.getenv_compat("TEST_PREFIXE") == "actuel"


_PREFIXE = r"MEMOGLYPH_"
_LECTURE_PY = re.compile(
    r"(?:getenv|environ\.get|env\.get)\(\s*[\"']" + _PREFIXE
    + r"|environ\[\s*[\"']" + _PREFIXE)
_LECTURE_TS = re.compile(r"process\.env\." + _PREFIXE + r"|process\.env\[\s*'" + _PREFIXE)


def _sources(*motifs: str) -> list[Path]:
    fichiers: list[Path] = []
    for motif in motifs:
        fichiers += [p for p in RACINE.glob(motif)
                     if "node_modules" not in p.parts and ".venv" not in p.parts]
    return fichiers


def test_aucune_lecture_directe_hors_du_helper() -> None:
    """Toute lecture de variable préfixée passe par `config.getenv_compat`.

    Les ÉCRITURES (`setenv`, `delenv`, `env.update`) restent libres : elles ne
    choisissent pas de préfixe à la place du code. Les fichiers exclus de l'export
    (`tests/hors_export.py`) ont leur propre contrôle.
    """
    fautifs = []
    exclus = exclus_de_l_export(RACINE)
    pythons = [p for p in _sources("core/**/*.py", "api/**/*.py", "tools/**/*.py",
                                   "tests/**/*.py", "*.py")
               if p.relative_to(RACINE).as_posix() not in exclus]
    scripts_ui = _sources("ui/*.ts", "ui/src/**/*.ts", "ui/src/**/*.tsx")
    # Ancre : le balayage voit bien les fichiers qui lisaient ces variables.
    assert RACINE / "core" / "usage_store.py" in pythons
    assert RACINE / "run_ml_tests.py" in pythons
    assert RACINE / "ui" / "vite.config.ts" in scripts_ui
    for p in pythons:
        if p == RACINE / "core" / "config.py" or p == Path(__file__).resolve():
            continue
        for n, ligne in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if _LECTURE_PY.search(ligne):
                fautifs.append(f"{p.relative_to(RACINE)}:{n}")
    for p in scripts_ui:
        for n, ligne in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if _LECTURE_TS.search(ligne):
                fautifs.append(f"{p.relative_to(RACINE)}:{n}")
    assert not fautifs, f"lectures directes d'une variable préfixée : {fautifs}"


def test_les_scripts_shell_lisent_le_prefixe_avec_un_defaut() -> None:
    """Dans un script, une variable `MEMOGLYPH_` se lit avec son défaut explicite."""
    lecture = re.compile(r"\$\{MEMOGLYPH_\w+:-")
    scripts = _sources("*.sh")
    # Ancre : `start.sh` et `stop.sh` lisent sept variables préfixées.
    assert RACINE / "start.sh" in scripts
    lectures = sum(len(lecture.findall(p.read_text(encoding="utf-8"))) for p in scripts)
    assert lectures >= 7, lectures
