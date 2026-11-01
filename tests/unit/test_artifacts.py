"""Artefacts d'hallucination de Whisper sur les plages non vocales.

Cas RÉELS :
  · boucles : un mot répété des centaines de fois, en dernière ligne ;
  · génériques : « Sous-titrage Société Radio-Canada », y compris en MILIEU de
    fichier — une règle « dernière ligne seulement » les aurait ratés.
"""

import pytest

from core import artifacts
from core.stt_base import Segment

pytestmark = pytest.mark.unit


# --- Boucles de répétition ---------------------------------------------------

@pytest.mark.parametrize("texte", [
    "hot " * 219,
    "Sn " * 221,
    "You know there are there there are there there there there there",
])
def test_les_boucles_reelles_sont_detectees(texte):
    assert artifacts.est_une_boucle(texte)


@pytest.mark.parametrize("texte", [
    # Du français réellement répétitif, loin du seuil
    "Merci pour les émotions, les buts, les joies, les cris et les larmes",
    "C'est rare de voir une équipe de France jouer de cette manière ce soir",
    "De l'histoire de la Coupe du Monde, il restera ce match et ce joueur",
    # Sous 8 mots, une répétition est trop probable pour conclure
    "oui oui oui",
])
def test_la_parole_reelle_n_est_pas_prise_pour_une_boucle(texte):
    assert not artifacts.est_une_boucle(texte)


def test_le_prefixe_reel_est_conserve():
    """« Let's start a » est de la VRAIE parole : la replier serait une perte."""
    replie = artifacts.replier_boucle("Let's start a " + "hot " * 219)
    assert replie.startswith("Let's start a hot hot hot ")
    assert "219×" in replie
    assert replie.count("hot") == artifacts.OCCURRENCES_GARDEES + 1  # +1 dans la marque


def test_la_marque_dit_ce_qui_a_ete_retire():
    """Rien ne disparaît en silence — le lecteur doit pouvoir auditer."""
    replie = artifacts.replier_boucle("Sn " * 221)
    assert "221×" in replie and "boucle" in replie.lower()


# --- Génériques de sous-titrage ----------------------------------------------

@pytest.mark.parametrize("texte", [
    "Sous-titrage Société Radio-Canada",
    "Sous-titres réalisés par la communauté d'Amara.org",
    "Sous-titrage ST' 501",
    "Subtitles by the Amara.org community",
])
def test_les_generiques_connus_sont_detectes(texte):
    assert artifacts.est_un_generique(texte)


@pytest.mark.parametrize("texte", [
    # AMBIGUS : ce sont aussi de vraies phrases. Filtrés, on perdrait de la parole.
    "Thanks for watching, see you next time on the show everyone.",
    "Merci d'avoir regardé cette vidéo jusqu'au bout, à bientôt.",
    # Une phrase RÉELLE qui parle de sous-titrage doit survivre : trop longue.
    "On a longuement discuté du sous-titrage réalisé par les équipes de la "
    "chaîne pour rendre le programme accessible à tous les publics concernés.",
])
def test_ce_qui_est_ambigu_ou_long_n_est_PAS_retire(texte):
    assert not artifacts.est_un_generique(texte)


# --- Le nettoyage complet ----------------------------------------------------

def test_scrub_replie_retire_et_rend_compte():
    segs = [
        Segment(963.0, 967.0, "Let's start a " + "hot " * 219),
        Segment(497.0, 499.0, "Sous-titrage Société Radio-Canada"),
        Segment(10.0, 14.0, "Merci pour les émotions, les buts et les joies partagées."),
    ]
    propres, rapport = artifacts.scrub(segs)

    assert len(propres) == 2, "seul le générique est retiré ; la boucle est repliée"
    assert propres[0].text.startswith("Let's start a")
    assert propres[1].text.startswith("Merci pour les émotions")
    assert rapport.boucles == ["00:16:03 « hot » ×219"]
    assert rapport.generiques == ["00:08:17 Sous-titrage Société Radio-Canada"]


def test_un_transcript_propre_est_rendu_INTACT():
    segs = [Segment(0.0, 2.0, "Bonjour à tous et bienvenue dans cette émission.")]
    propres, rapport = artifacts.scrub(segs)
    assert [s.text for s in propres] == [s.text for s in segs]
    assert not rapport


def test_les_horodatages_sont_preserves():
    """Le rapport doit pouvoir renvoyer au bon endroit de l'enregistrement."""
    segs = [Segment(963.0, 967.5, "Sn " * 221)]
    propres, _ = artifacts.scrub(segs)
    assert (propres[0].start, propres[0].end) == (963.0, 967.5)


# --- Boucles étalées sur PLUSIEURS segments -----------------------------------
# Trou du détecteur d'origine, trouvé en analysant les segments sans
# locuteur du corpus : `est_une_boucle` exige 8 mots DANS UN segment, donc
# il ne voyait pas « Let's go to London. » répété à l'identique sur 21 segments
# consécutifs, ni « Music. » sur 3, ni « mon » sur 4.

def _serie(texte, n, depart=0.0):
    return [Segment(depart + i * 2.0, depart + i * 2.0 + 1.5, texte) for i in range(n)]


def test_une_serie_repetee_est_reperee():
    series = artifacts.boucles_entre_segments(_serie("Let's go to London.", 21))
    assert len(series) == 1
    debut, n, texte = series[0]
    assert (debut, n) == (0, 21)
    assert texte == "Let's go to London."


def test_la_casse_et_la_ponctuation_finale_sont_ignorees():
    """Whisper varie sur les deux d'une occurrence à l'autre."""
    segs = [Segment(0, 1, "Music."), Segment(2, 3, "music"), Segment(4, 5, "Music !")]
    assert len(artifacts.boucles_entre_segments(segs)) == 1


def test_sous_le_seuil_rien_n_est_signale():
    assert artifacts.boucles_entre_segments(_serie("Merci.", 2)) == []


def test_seules_les_repetitions_CONSECUTIVES_comptent():
    """Une phrase qui revient trois fois dans un entretien n'est pas une boucle."""
    segs = [Segment(0, 1, "D'accord."), Segment(10, 11, "Une vraie phrase ici."),
            Segment(20, 21, "D'accord."), Segment(30, 31, "Autre chose encore."),
            Segment(40, 41, "D'accord.")]
    assert artifacts.boucles_entre_segments(segs) == []


def test_les_series_sont_SIGNALEES_mais_rien_n_est_retire():
    """Un refrain, un slogan scandé ou une relance sont des répétitions
    LÉGITIMES. On pointe, le lecteur tranche — on ne supprime pas."""
    segs = _serie("Let's go to London.", 21) + [Segment(100, 104, "Une phrase unique.")]
    propres, rapport = artifacts.scrub(segs)
    assert len(propres) == len(segs), "aucun segment ne doit disparaître"
    assert len(rapport.series) == 1
    assert "×21" in rapport.series[0]


def test_un_transcript_sans_repetition_ne_declenche_rien():
    segs = [Segment(i * 3.0, i * 3.0 + 2.0, f"Phrase numéro {i} du transcript.")
            for i in range(10)]
    _, rapport = artifacts.scrub(segs)
    assert rapport.series == []
    assert not rapport
