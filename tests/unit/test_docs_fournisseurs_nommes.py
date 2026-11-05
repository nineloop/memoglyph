"""La documentation d'OpenRouter et de Groq : variables, phrase de redémarrage, politiques de données.

Les liens de politique de données sont ceux des pages LUES pendant la recherche, cités sans jugement ; pour ce que les pages lues
ne précisent pas, la documentation le dit et ne l'affirme pas.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

RACINE = Path(__file__).resolve().parents[2]
VARIABLES = ["OPENROUTER_API_KEY", "MEMOGLYPH_OPENROUTER_MODEL", "GROQ_API_KEY", "MEMOGLYPH_GROQ_MODEL"]
PHRASE = "après avoir ajouté ou changé une clé dans `.env`, redémarrer l'instance"


def _lire(chemin: str) -> str:
    return (RACINE / chemin).read_text(encoding="utf-8")


@pytest.mark.parametrize("variable", VARIABLES)
# `SPEC.md` est exporté mais hors des chemins qui relancent la CI (`test_ci_workflow`) : un test ne le lit donc pas.
@pytest.mark.parametrize("fichier", [".env.example", "docs/configuration.md"])
def test_chaque_variable_est_documentee_la_ou_les_autres_le_sont(fichier, variable) -> None:
    assert variable in _lire(fichier)


@pytest.mark.parametrize("fichier", ["docs/configuration.md", "docs/local-et-en-ligne.md"])
def test_la_phrase_de_redemarrage_est_ecrite_telle_quelle(fichier) -> None:
    # Seule la casse de la première lettre peut changer (début de phrase) : le reste est écrit tel quel.
    assert PHRASE in _lire(fichier).replace("Après avoir", "après avoir")


def test_les_pages_de_politique_de_donnees_sont_citees_sans_jugement() -> None:
    texte = _lire("docs/local-et-en-ligne.md")
    for lien in ("https://openrouter.ai/docs/guides/privacy/data-collection",
                 "https://openrouter.ai/docs/guides/privacy/provider-logging",
                 "https://console.groq.com/docs/your-data"):
        assert lien in texte
    # Sans jugement : aucun qualificatif d'appréciation sur les services.
    for mot in ("meilleur", "pire", "fiable", "sûr ", "dangereux", "recommandons"):
        assert mot not in texte.lower().split("## openrouter et groq")[1].split("## ce qui ne part pas")[0], mot


def test_ce_que_les_pages_lues_ne_precisent_pas_est_dit_et_non_affirme() -> None:
    section = _lire("docs/local-et-en-ligne.md").split("## OpenRouter et Groq")[1].split("## Ce qui ne part pas")[0]
    assert "ne précise pas" in section and "ne donnent pas le libellé" in section
    assert "groq.com/privacy-policy" in section and "openrouter.ai/privacy" in section


def test_les_modeles_gratuits_sont_dits_fermes_a_l_ultra_confidentiel_meme_autorise() -> None:
    """Un modèle gratuit reçoit « public · normal » et « confidentiel », jamais l'ultra-confidentiel,
    même autorisé au nuage ; la règle est dite changeable, avec la page qui en donne la raison."""
    section = _lire("docs/local-et-en-ligne.md").split("## OpenRouter et Groq")[1].split("## Ce qui ne part pas")[0]
    assert "public · normal" in section and "confidentiel" in section
    assert "ultra-confidentiel" in section and "même si son propriétaire a autorisé le cloud" in section
    assert "confidentiel ou ultra-confidentiel" not in section, "l'ancienne règle (le confidentiel refusé aussi) n'est plus dite"
    assert "par défaut" in section and "réglable" in section
    assert "réglages d'entraînement" in section        # la page citée (collecte de données) en donne la raison
