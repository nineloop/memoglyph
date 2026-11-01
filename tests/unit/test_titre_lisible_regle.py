"""Titre lisible d'une réunion : le titre saisi, sinon le nom de la source, jamais
le slug du fichier (`library.titre_lisible`, `library.h1_lisible`).

Le pipeline écrit `title: slugify(source.stem)` dans le front-matter : c'est ce
slug qui paraissait dans l'index, la recherche, le MCP, l'Assistant et le H1 du
compte rendu.
"""
from __future__ import annotations

import pytest

from core import library

pytestmark = pytest.mark.unit

SOURCE = "AMI IS1009a, réunion de lancement d'une télécommande.wav"
SLUG = "ami-is1009a-réunion-de-lancement-dune-télécommande"
LISIBLE = "AMI IS1009a, réunion de lancement d'une télécommande"


def test_un_titre_reste_slug_devient_le_nom_de_la_source_sans_extension() -> None:
    assert library.titre_lisible(SLUG, SOURCE) == LISIBLE


def test_un_titre_de_compte_rendu_garde_son_suffixe_autour_de_la_base_lisible() -> None:
    assert library.titre_lisible(f"{SLUG} — compte-rendu", SOURCE) == f"{LISIBLE} — compte-rendu"


@pytest.mark.parametrize("titre", ["Comité de pilotage", "Q3 — revue", "Budget"])
def test_un_titre_saisi_est_garde(titre) -> None:
    assert library.titre_lisible(titre, SOURCE) == titre


def test_le_slug_exact_d_une_source_sans_tiret_est_reconnu() -> None:
    # `slugify("Kickoff")` ne porte aucun tiret : seule la comparaison exacte le voit.
    assert library.titre_lisible("kickoff", "Kickoff.m4a") == "Kickoff"
    assert library.titre_lisible("kickoff", "autre.m4a") == "kickoff"


def test_sans_titre_ni_source_le_nom_du_fichier_reste_le_seul_repli() -> None:
    assert library.titre_lisible(None, SOURCE, nom=f"{SLUG}.md") == LISIBLE
    assert library.titre_lisible(None, None, nom="reunion.md") == "reunion"
    assert library.titre_lisible(SLUG, None) == SLUG


def test_le_premier_h1_slug_d_un_cr_porte_le_titre_lisible_a_la_lecture() -> None:
    corps = f"\n# Compte-rendu — {SLUG}\n\n## Décisions\n\n# Report — {SLUG}\n"
    lu = library.h1_lisible(corps, LISIBLE)
    assert lu.splitlines()[1] == f"# Compte-rendu — {LISIBLE}"
    assert lu.splitlines()[-1] == f"# Report — {SLUG}"  # seul le premier bloc


def test_un_h1_saisi_n_est_pas_remplace() -> None:
    corps = "# Compte-rendu — Comité de pilotage\n\nTexte."
    assert library.h1_lisible(corps, LISIBLE) == corps
