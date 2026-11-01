"""Garde du compte rendu : un chiffre d'une unité doit figurer dans la parole citée.

Les cas viennent de comptes rendus du corpus AMI : des unités non ancrées ajoutaient
un chiffre absent de la parole (notes « sur sept » reportées d'une échelle annoncée plus
tôt, tailles de boutons, « 8.82 points »), et ES2002a dit « twelve fifty ».
"""
from __future__ import annotations

import pytest

from core import factcheck
from core.experimental import stages

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("enonce,parole,manquants", [
    ("Buttons should be 8 mm wide.", ["the buttons should be quite small"], ["8"]),        # ajouté
    ("The design scores 5 out of 7.", ["I'd give it a five"], ["7"]),                      # échelle d'ailleurs
    ("Seven people attended.", ["there were seven of us"], []),                            # en lettres
    ("Le coût est de 12,50 €.", ["ça coûte douze cinquante"], []),                         # lettres, décimale
    ("The cost is 12.50 euros.", ["it costs twelve fifty euro"], []),
    ("We need 3 buttons.", ["we need 3 buttons"], []),                                     # correct
])
def test_chiffres_non_ancres(enonce, parole, manquants) -> None:
    assert factcheck.chiffres_non_ancres(enonce, parole) == manquants


LIGNES = [
    "**Speaker 1** [00:01:00] I'd give it a five on that scale.",
    "**Speaker 2** [00:01:25] We need three buttons.",
    "**Speaker 1** [00:02:30] On a scale of one to seven, then.",
]


def _unite(texte: str, start: str) -> dict:
    return {"kind": "fact", "text": texte, "start": start}


def test_une_unite_dont_un_chiffre_manque_a_la_parole_proche_est_ecartee_et_comptee() -> None:
    diag: dict = {}
    gardees = stages.validate_units([
        _unite("Speaker 1 rates it 5.", "00:01:00"),                 # cinq, à l'horodatage
        _unite("Three buttons are needed.", "00:01:00"),             # trois, 25 s plus tard
        _unite("Speaker 1 rates it 5 out of 7.", "00:01:00"),        # sept : 90 s plus tard
        _unite("Buttons should be 8 mm wide.", "00:01:25"),          # huit : jamais dit
    ], {"00:01:00", "00:01:25", "00:02:30"}, diag=diag, lines=LIGNES)
    assert [u["text"] for u in gardees] == ["Speaker 1 rates it 5.", "Three buttons are needed."]
    v = diag["stage2_validate"]
    assert v["rejected_unsupported_numbers"] == 2
    assert [u["missing_numbers"] for u in v["unsupported_numbers_units"]] == [["7"], ["8"]]


def test_sans_les_repliques_la_validation_reste_celle_d_avant() -> None:
    diag: dict = {}
    gardees = stages.validate_units([_unite("Buttons should be 8 mm wide.", "00:01:25")],
                                    {"00:01:25"}, diag=diag)
    assert len(gardees) == 1 and diag["stage2_validate"]["rejected_unsupported_numbers"] == 0
