"""L'aide sous le champ du mot de passe dit la longueur minimale réellement exigée par le serveur."""
from __future__ import annotations

import re
from pathlib import Path

from api import auth

MESSAGES = Path(__file__).resolve().parents[2] / "ui" / "src" / "lib" / "messages.ts"


def _valeurs(cle: str) -> list[str]:
    return re.findall(rf"^  '{re.escape(cle)}':\s*\n?\s*'((?:[^'\\]|\\.)*)'", MESSAGES.read_text(encoding="utf-8"), flags=re.M)


def test_l_aide_du_mot_de_passe_cite_la_longueur_minimale_du_serveur_en_francais_et_en_anglais() -> None:
    valeurs = _valeurs("auth.field.passwordHint")
    assert len(valeurs) == 2, "ancre : l'aide existe dans les deux catalogues"
    for valeur in valeurs:
        assert str(auth.LONGUEUR_MIN_MOT_DE_PASSE) in valeur, valeur
