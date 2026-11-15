"""L'ordre de sévérité des sensibilités : du plus sensible au plus ouvert. « Plus stricte » est un ordre strict (jamais l'égalité)."""
from __future__ import annotations

import pytest

from core import sensitivity

pytestmark = pytest.mark.unit

CAS = [
    ("ultra_confidential", "confidential", True),
    ("ultra_confidential", "standard", True),
    ("confidential", "standard", True),
    ("confidential", "ultra_confidential", False),
    ("standard", "confidential", False),
    ("standard", "ultra_confidential", False),
    ("standard", "standard", False),
    ("confidential", "confidential", False),
    ("ultra_confidential", "ultra_confidential", False),
]


@pytest.mark.parametrize("candidate,reference,attendu", CAS)
def test_plus_stricte_est_un_ordre_strict(candidate, reference, attendu) -> None:
    assert sensitivity.plus_stricte(candidate, reference) is attendu


@pytest.mark.parametrize("a,b,attendu", [
    ("standard", "confidential", "confidential"),
    ("confidential", "standard", "confidential"),
    ("ultra_confidential", "confidential", "ultra_confidential"),
    ("confidential", "ultra_confidential", "ultra_confidential"),
    ("standard", "standard", "standard"),
])
def test_la_plus_stricte_des_deux(a, b, attendu) -> None:
    assert sensitivity.la_plus_stricte(a, b) == attendu


def test_une_valeur_inconnue_vaut_le_defaut_et_ne_bat_rien() -> None:
    assert sensitivity.plus_stricte("secret", "standard") is False
    assert sensitivity.plus_stricte("confidential", "secret") is True
    assert sensitivity.la_plus_stricte("secret", "confidential") == "confidential"
