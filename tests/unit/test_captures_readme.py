"""Les captures du README : elles existent, restent légères et portent l'attribution du corpus de démo."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[2]
LIMITE_OCTETS = 500 * 1024
SIGNATURE_PNG = b"\x89PNG\r\n\x1a\n"
DOSSIER = "docs/assets/screenshots/"


@pytest.mark.parametrize("readme", ["README.md", "README.fr.md"])
def test_les_captures_du_readme_existent_sont_des_png_de_moins_de_500_ko(readme):
    texte = (RACINE / readme).read_text(encoding="utf-8")
    images = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", texte)
    captures = [i for i in images if i.startswith(DOSSIER)]
    assert len(captures) == 4, f"{readme} : quatre captures attendues (bibliothèque, Transcrire, transcript, compte rendu)"
    nom = "transcribe.png" if readme == "README.md" else "transcribe-fr.png"
    assert DOSSIER + nom in captures, f"{readme} : la capture de l'écran Transcrire ({nom}) manque"
    for chemin in captures:
        fichier = RACINE / chemin
        assert fichier.is_file(), f"{readme} : {chemin} introuvable"
        octets = fichier.read_bytes()
        assert octets.startswith(SIGNATURE_PNG), f"{chemin} n'est pas un PNG"
        assert len(octets) < LIMITE_OCTETS, f"{chemin} : {len(octets)} octets, limite {LIMITE_OCTETS}"


@pytest.mark.parametrize("readme", ["README.md", "README.fr.md"])
def test_les_captures_portent_l_attribution_du_corpus_de_demo(readme):
    texte = (RACINE / readme).read_text(encoding="utf-8")
    assert "AMI Meeting Corpus" in texte and "CC BY 4.0" in texte
    assert "Carletta" in texte, f"{readme} : référence du corpus manquante (docs/corpus-demo.md)"


def test_les_captures_viennent_toutes_du_dossier_prevu():
    pngs = sorted(p.name for p in (RACINE / DOSSIER).glob("*"))
    assert pngs == sorted(["library.png", "transcribe.png", "meeting-transcript.png", "report-citations.png",
                           "library-fr.png", "transcribe-fr.png", "meeting-transcript-fr.png", "report-citations-fr.png"])
