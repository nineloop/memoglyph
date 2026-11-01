"""SECURITY.md adosse chaque affirmation à un test ou à un fichier : qu'ils existent.

Une politique de sécurité qui cite un test renommé ou supprimé promet quelque chose
que plus rien ne vérifie. Ce test échoue dès qu'une référence ne pointe plus nulle part.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

RACINE = Path(__file__).resolve().parents[2]
SECURITE = RACINE / "SECURITY.md"


def _source_des_tests() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in (RACINE / "tests").rglob("*.py"))


def test_chaque_test_cite_existe() -> None:
    cites = set(re.findall(r"`(test_[A-Za-z0-9_]+)`", SECURITE.read_text(encoding="utf-8")))
    assert len(cites) >= 20, "SECURITY.md doit nommer les tests qui portent ses affirmations"
    source = _source_des_tests()
    absents = sorted(nom for nom in cites if f"def {nom}(" not in source)
    assert absents == []


def test_chaque_fichier_cite_existe() -> None:
    texte = SECURITE.read_text(encoding="utf-8")
    chemins = set(re.findall(r"`((?:tests|core|api|compagnon|tools|docs)/[^`\s]+)`", texte))
    chemins |= set(re.findall(r"\]\(((?:docs|tools)/[^)\s]+)\)", texte))
    assert chemins, "SECURITY.md doit citer les fichiers qui portent ses affirmations"
    absents = sorted(c for c in chemins if not (RACINE / c).exists())
    assert absents == []
