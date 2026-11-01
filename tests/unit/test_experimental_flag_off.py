"""Le pipeline expérimental est HORS défaut : drapeau bas ⇒ aucun effet.

Vérifie que le drapeau est faux par défaut (env non posé), que le pilote refuse
de tourner drapeau bas, et que le chemin de rapport de production n'importe rien
du paquet expérimental.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def recharger():
    """Recharge un module, puis lui rend son état d'avant à la fin du test.

    Recharger `core.config` recalcule TOUT le module, chemins des magasins
    compris : sans restauration, les tests suivants retrouveraient les chemins
    réels au lieu de ceux que `tests/conftest.py` a redirigés.
    """
    etats = []

    def _recharger(module):
        etats.append((module, dict(vars(module))))
        return importlib.reload(module)

    yield _recharger
    for module, etat in reversed(etats):
        vars(module).update(etat)


def test_flag_off_par_defaut(monkeypatch, recharger):
    monkeypatch.delenv("MEMOGLYPH_EXPERIMENTAL_REPORT", raising=False)
    import core.config as config
    recharger(config)
    assert config.EXPERIMENTAL_REPORT is False


def test_pilote_refuse_si_drapeau_bas(monkeypatch, recharger):
    monkeypatch.delenv("MEMOGLYPH_EXPERIMENTAL_REPORT", raising=False)
    import core.config as config
    recharger(config)
    from core.experimental import pipeline
    recharger(pipeline)
    with pytest.raises(RuntimeError):
        pipeline.make_experimental_report("peu-importe")


def test_report_n_importe_pas_l_experimental_au_chargement():
    """`core.report` ne doit PAS importer `core.experimental` au niveau MODULE.

    Migration (intégration du CR local riche) : la route LOCALE de l'app passe
    désormais par l'architecture riche via `report.generate_report`, mais par un
    import PARESSEUX (dans le corps de la fonction). Importer `core.report` ne
    doit donc jamais tirer `core.experimental` — pas de coût à l'import, pas de
    cycle. On vérifie l'ABSENCE d'import expérimental au niveau module (AST), ce
    qui autorise l'import paresseux tout en gardant le graphe d'import propre.
    """
    import ast

    import core.report as report

    tree = ast.parse(open(report.__file__, encoding="utf-8").read())
    top_level_imports: list[str] = []
    for node in tree.body:  # UNIQUEMENT le niveau module (pas les corps de fonction)
        if isinstance(node, ast.Import):
            top_level_imports += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            top_level_imports.append(node.module or "")
    assert not any("experimental" in m for m in top_level_imports), (
        f"import expérimental au niveau module : {top_level_imports}"
    )
