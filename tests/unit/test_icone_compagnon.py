"""L'icône du compagnon : dessinée à la compilation depuis le symbole de la marque.

Rien de binaire n'est versionné (le contrôle de publication refuse une image hors manifeste) : un script Swift dessine
le `.iconset` avec les seuls outils de macOS, `make companion` le convertit en `.icns` et le range dans le paquet,
AVANT la signature (une ressource ajoutée après invaliderait `codesign --verify --strict`).
"""
from __future__ import annotations

import plistlib
import re
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
MAKEFILE = (RACINE / "Makefile").read_text(encoding="utf-8")
SCRIPT = RACINE / "compagnon/Outils/generer-icone.swift"
PLIST = RACINE / "compagnon/Sources/memoglyph-compagnon/Info.plist"
SYMBOLE = RACINE / "ui/public/brand/memoglyph-symbole-sombre.svg"


def _cible_companion() -> str:
    lignes = MAKEFILE.splitlines()
    debut = lignes.index("companion:")
    recette = []
    for ligne in lignes[debut + 1:]:
        if ligne and not ligne.startswith("\t"):
            break
        recette.append(ligne)
    return "\n".join(recette)


def test_le_plist_nomme_le_fichier_d_icone() -> None:
    info = plistlib.loads(PLIST.read_bytes())
    assert info["CFBundleIconFile"] == "Memoglyph"
    assert info["LSUIElement"] is True, "l'icône du Dock n'apparaît pas : l'application reste dans la barre de menus"


def test_le_symbole_et_le_script_existent_et_aucune_image_n_est_versionnee() -> None:
    assert SYMBOLE.is_file() and SCRIPT.is_file()
    balayes = [p for p in (RACINE / "compagnon").rglob("*") if p.is_file() and ".build" not in p.parts]
    assert len(balayes) > 20, "ancre : le balayage voit bien les sources du compagnon"
    versionnes = [p for p in balayes if p.suffix in (".icns", ".png")]
    assert not versionnes, f"produit à la compilation, jamais versionné : {versionnes}"


def test_le_script_dessine_la_piste_retenue() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "0x1B4A4D" in source and "0x0E2A2E" in source          # le dégradé sombre, teinte du dock
    assert "824 / 1024" in source                                  # la grille des icônes macOS
    assert "* 0.72" in source                                      # le symbole à environ 72 % de la largeur
    for nom in ("icon_16x16", "icon_512x512@2x"):
        assert nom in source


def test_make_companion_range_l_icone_avant_de_signer_et_la_verifie() -> None:
    cible = _cible_companion()
    ordre = [cible.index(m) for m in ("generer-icone.swift", "iconutil", "test -s", "codesign --sign")]
    assert ordre == sorted(ordre), "l'icône est produite, convertie et vérifiée AVANT codesign"
    assert re.search(r"Contents/Resources/Memoglyph\.icns", cible)
    assert "memoglyph-symbole-sombre.svg" in cible


def test_la_cible_ne_supprime_rien() -> None:
    assert not re.search(r"\brm\b", _cible_companion())
