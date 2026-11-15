"""Chaque code d'erreur que l'API émet a sa phrase, en français et en anglais, dans le catalogue de l'interface.

Le test lit le code de l'API et repère les codes aux endroits où ils sont écrits : `code="…"`, `"code": "…"`, `["code"] = …`,
`code == "…"`, `code = "…"`. Un code neuf sans phrase fait rougir ce test ; un code qui passe par une autre écriture n'est pas
vu, et le test de l'extracteur montre ce qu'il voit.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

RACINE = Path(__file__).resolve().parents[2]
SOURCES = [*sorted((RACINE / "api").glob("*.py")), RACINE / "core" / "sensitivity.py"]
CATALOGUE = RACINE / "ui" / "src" / "lib" / "messages.ts"

FORME_D_UN_CODE = re.compile(r"^[a-z]+(?:\.[A-Za-z]+)+$")
LIGNE_DE_CODE = re.compile(r"\bcode[\"\]]?\s*(?:=|:)")


def codes_ecrits(source: str) -> list[str]:
    codes: list[str] = []
    for ligne in source.splitlines():
        if not LIGNE_DE_CODE.search(ligne):
            continue
        codes += [m for m in re.findall(r'"([^"]+)"', ligne) if FORME_D_UN_CODE.match(m)]
    return codes


def test_l_extracteur_voit_les_ecritures_d_un_code_et_seulement_elles() -> None:
    assert codes_ecrits('raise RefusAvecCode(400, "Phrase.", code="share.self")') == ["share.self"]
    assert codes_ecrits('return JSONResponse({"detail": "x", "code": "auth.required"}, status_code=401)') == ["auth.required"]
    assert codes_ecrits('corps["code"] = code if code == "step.error.rateLimited" else "ask.unavailable"') == [
        "step.error.rateLimited", "ask.unavailable"]
    assert codes_ecrits('    code = "model.free.restricted"') == ["model.free.restricted"]
    assert codes_ecrits('status_code=403, fichier = "config.py"') == []


def test_le_balayage_voit_bien_le_code_de_l_api() -> None:
    codes = {c for source in SOURCES for c in codes_ecrits(source.read_text(encoding="utf-8"))}
    assert len(SOURCES) > 3 and len(codes) >= 29
    assert {"auth.required", "model.free.restricted", "ask.unavailable", "share.self"} <= codes


def test_chaque_code_a_sa_phrase_en_francais_et_en_anglais() -> None:
    catalogue = CATALOGUE.read_text(encoding="utf-8")
    codes = sorted({c for source in SOURCES for c in codes_ecrits(source.read_text(encoding="utf-8"))})
    # La clé figure une fois dans le catalogue français et une fois dans l'anglais.
    sans_phrase = [c for c in codes if catalogue.count(f"'erreur.{c}':") != 2]
    assert sans_phrase == []
