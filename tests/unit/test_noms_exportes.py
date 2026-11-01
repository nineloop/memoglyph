"""Ce qui part avec le code se nomme sans vocabulaire de recherche ni ancien nom du produit.

Aucun fichier exporté ne porte dans son nom le vocabulaire de la recherche (`VOCABULAIRE`) :
le test ML à réponse connue s'appelle par ce qu'il vérifie, et tout ce qui le nomme suit.
Aucun fichier exporté ne porte, dans son chemin ou son texte, un ancien nom du produit.
Vaut dans les deux dépôts : dans l'export, la liste des exclus est vide.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tests.hors_export import exclus_de_l_export

pytestmark = pytest.mark.unit

RACINE = Path(__file__).resolve().parents[2]
VOCABULAIRE = re.compile(r"verite[_-]?terrain|ground[_-]?truth", re.IGNORECASE)


def exportes() -> list[str]:
    try:
        suivis = subprocess.run(["git", "ls-files", "-z"], cwd=RACINE, capture_output=True, text=True,
                                check=True).stdout.split("\0")
    except (OSError, subprocess.CalledProcessError):
        suivis = [str(p.relative_to(RACINE)) for p in RACINE.rglob("*") if p.is_file()
                  and not {".git", "node_modules", ".venv"} & set(p.parts)]
    exclus = exclus_de_l_export(RACINE)
    return sorted(c for c in suivis if c and c not in exclus)


def test_aucun_nom_de_fichier_exporte_ne_porte_le_vocabulaire_de_recherche() -> None:
    fichiers = exportes()
    assert "core/config.py" in fichiers and len(fichiers) > 200, "ancre : le balayage voit le dépôt"
    assert [c for c in fichiers if VOCABULAIRE.search(c)] == []


def _texte(chemin: str) -> str | None:
    """Le texte d'un fichier exporté lu sur le disque ; None pour un binaire ou un lien."""
    p = RACINE / chemin
    if p.is_symlink() or not p.is_file():
        return None
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


# Les deux anciens noms du produit, construits en morceaux : ce fichier part avec le code.
ANCIENS_NOMS = re.compile("glyph" + "bird|scri" + "bird", re.IGNORECASE)


def test_aucun_fichier_exporte_ne_porte_un_ancien_nom_du_produit() -> None:
    """Ni dans son chemin ni dans son texte : seul le nom actuel reste, sans repli."""
    fichiers = exportes()
    assert "core/config.py" in fichiers and len(fichiers) > 200, "ancre : le balayage voit le dépôt"
    trouves = [f"{c} (chemin)" for c in fichiers if ANCIENS_NOMS.search(c)]
    lus = 0
    for c in fichiers:
        texte = _texte(c)
        if texte is None:
            continue
        lus += 1
        trouves += [f"{c}:{n}" for n, ligne in enumerate(texte.splitlines(), 1) if ANCIENS_NOMS.search(ligne)]
    assert lus > 200, "ancre : le balayage lit le texte des fichiers"
    assert trouves == [], f"{len(trouves)} occurrence(s) :\n" + "\n".join(trouves)


# L'illustration du gabarit de l'interface, sans usage ; son nom en morceaux, pour que ce
# fichier ne la cite pas lui-même.
ILLUSTRATION = "hero" + ".png"


def test_l_illustration_du_gabarit_n_est_ni_livree_ni_citee() -> None:
    """Retirée de `ui/src/assets/`, et rien d'exporté ne la cite."""
    fichiers = exportes()
    assert "ui/src/App.tsx" in fichiers, "ancre : le balayage voit l'interface"
    assert f"ui/src/assets/{ILLUSTRATION}" not in fichiers
    assert not (RACINE / "ui" / "src" / "assets" / ILLUSTRATION).exists()
    citations = [f"{c}:{n}" for c in fichiers if (texte := _texte(c)) is not None
                 for n, ligne in enumerate(texte.splitlines(), 1) if ILLUSTRATION in ligne]
    assert citations == [], citations


# Les deux logos du gabarit Vite + React, sans usage non plus ; noms en morceaux.
LOGOS_DU_GABARIT = ("react" + ".svg", "vite" + ".svg")


@pytest.mark.parametrize("logo", LOGOS_DU_GABARIT)
def test_les_logos_du_gabarit_ne_sont_ni_livres_ni_cites(logo: str) -> None:
    """Retirés de `ui/src/assets/`, et rien d'exporté ne les cite."""
    fichiers = exportes()
    assert "ui/src/App.tsx" in fichiers, "ancre : le balayage voit l'interface"
    assert f"ui/src/assets/{logo}" not in fichiers
    assert not (RACINE / "ui" / "src" / "assets" / logo).exists()
    citations = [f"{c}:{n}" for c in fichiers if (texte := _texte(c)) is not None
                 for n, ligne in enumerate(texte.splitlines(), 1) if logo in ligne]
    assert citations == [], citations


def test_le_test_ml_a_reponse_connue_est_nomme_partout_par_son_nouveau_nom() -> None:
    """`run_ml_tests.py`, `ml-gate.json` et ce qui les vérifie nomment le même fichier, qui existe."""
    nom = "tests/ml/test_ami_reponse_connue.py"
    assert (RACINE / nom).is_file()
    for porteur in ("run_ml_tests.py", "ml-gate.json", "tests/unit/test_ml_runner.py"):
        texte = (RACINE / porteur).read_text(encoding="utf-8")
        assert nom in texte and not VOCABULAIRE.search(texte), porteur
