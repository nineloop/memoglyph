"""Le code exporté ne renvoie qu'à des documents qui partent avec lui.

Chaque document `docs/….md` cité par le code exporté (Python, TypeScript, Swift, scripts)
existe dans le dépôt et part avec l'export : un renvoi vers un document absent ou exclu ne
mène à rien chez qui lit le code public. Vaut dans les deux dépôts : dans l'export, la
liste des exclus est vide.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.unit.test_noms_exportes import RACINE, exportes

pytestmark = pytest.mark.unit

RENVOI_DOC = re.compile(r"(?<![\w/.-])docs/[\w./-]+?\.md")
SUFFIXES_DE_CODE = {".py", ".ts", ".tsx", ".js", ".swift", ".sh", ".mk"}


def test_chaque_document_cite_par_le_code_exporte_existe_et_part_avec_l_export() -> None:
    livres = set(exportes())
    sources = [c for c in livres if Path(c).suffix in SUFFIXES_DE_CODE or Path(c).name == "Makefile"]
    assert "core/stt_mistral.py" in sources and len(sources) > 150, "ancre : le balayage voit le code"
    cites = {}
    for chemin in sources:
        for n, ligne in enumerate((RACINE / chemin).read_text(encoding="utf-8").splitlines(), 1):
            for renvoi in RENVOI_DOC.findall(ligne):
                cites.setdefault(renvoi, []).append(f"{chemin}:{n}")
    assert "docs/ecl.md" in cites, "ancre : un renvoi connu est vu"
    rompus = {doc: ou for doc, ou in cites.items() if doc not in livres}
    assert rompus == {}, f"renvoi(s) vers un document absent ou exclu de l'export : {rompus}"
