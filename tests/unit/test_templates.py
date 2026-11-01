"""Gabarits de CR par genre, exprimés en questions.

Propriété centrale : **toute incertitude retombe sur le gabarit sobre**. Une
erreur de classification doit produire un compte-rendu moins riche, jamais un
compte-rendu inventif — l'inverse du comportement d'origine, où le défaut était
le gabarit le plus exigeant (décisions + actions + prochaines étapes).
"""

import pytest

from core import templates

pytestmark = pytest.mark.unit


def test_reponse_exacte_reconnue():
    assert templates.normalize("interview") == "interview"
    assert templates.normalize("analyse_sportive") == "analyse_sportive"


def test_variations_benignes_rattrapees():
    assert templates.normalize("  Interview.  ") == "interview"
    assert templates.normalize("analyse-sportive") == "analyse_sportive"
    assert templates.normalize("`analyse sportive`") == "analyse_sportive"


def test_phrase_au_lieu_dun_mot():
    """Le modèle bavarde parfois malgré la consigne."""
    assert templates.normalize(
        "Je dirais que c'est une interview."
    ) == "interview"


def test_genre_inconnu_retombe_sur_le_gabarit_sobre():
    for raw in ["cours magistral", "xyz", "", None, "   ", "consultation médicale"]:
        assert templates.normalize(raw) == templates.GENERIC


def test_synonymes_de_reunion_reconnus():
    """« réunion de crise » EST une réunion : la reconnaître n'est pas un défaut."""
    assert templates.normalize("réunion de crise") == "projet"
    assert templates.normalize("comité de pilotage") == "steerco"


def test_le_libelle_le_plus_long_gagne():
    """« comité de pilotage » ne doit pas être capté par un préfixe plus court."""
    assert templates.normalize("c'est un comité de pilotage trimestriel") == "steerco"


def test_town_hall_et_briefing_interne_sont_deux_genres_distincts():
    assert templates.normalize("Town Hall / Internal Town Meeting") == "town_hall"
    assert templates.normalize("briefing interne opérationnel") == "internal_briefing"

    town_hall = templates.questions("town_hall", "fr")
    briefing = templates.questions("internal_briefing", "fr")
    assert town_hall != briefing
    assert any("réunion collective" in question for question in town_hall)
    assert any("consignes opérationnelles" in question for question in briefing)


def test_seuls_les_gabarits_dedies_parlent_de_decisions():
    """Cause du bug d'origine : demander des décisions à un contenu qui n'en a pas.

    Les genres hors réunion ne doivent porter AUCUN mot induisant ces sections.
    """
    interdits = ("décision", "action", "responsable", "prochaine étape", "engag",
                 "decision", "action item", "owner", "next step", "commit")
    for genre in templates.GENRES:
        if genre in templates.COMMITMENT_GENRES:
            continue
        for lang in ("fr", "en"):
            joint = " ".join(templates.questions(genre, lang)).lower()
            for mot in interdits:
                assert mot not in joint, f"{genre}/{lang} contient « {mot} »"


def test_les_gabarits_de_reunion_exigent_une_decision_ADOPTEE():
    """Une proposition non retenue n'est pas une décision (typologie DDA).

    Sans cette contrainte, le modèle transforme une suggestion en décision — le
    biais est directionnel et documenté.
    """
    for genre in templates.COMMITMENT_GENRES:
        fr = " ".join(templates.questions(genre, "fr")).lower()
        assert "adopté" in fr or "rendu" in fr or "tranch" in fr
        assert "n'est pas une décision" in fr or "n'est pas un arbitrage" in fr
        en = " ".join(templates.questions(genre, "en")).lower()
        assert "adopted" in en or "settled" in en


def test_les_gabarits_de_reunion_conditionnent_le_responsable():
    """L'attribuer est peu fiable : on ne le demande que si le dialogue le dit."""
    for genre in templates.COMMITMENT_GENRES:
        fr = " ".join(templates.questions(genre, "fr")).lower()
        assert "si le dialogue le dit" in fr


def test_l_entretien_d_embauche_n_evalue_pas_la_personne():
    """Juger un candidat serait une inférence sur un individu, hors périmètre."""
    fr = " ".join(templates.questions("entretien_embauche", "fr")).lower()
    assert "aucune appréciation" in fr
    en = " ".join(templates.questions("entretien_embauche", "en")).lower()
    assert "do not assess" in en


def test_le_gabarit_sobre_est_le_plus_court():
    sobre = templates.questions(templates.GENERIC, "fr")
    for genre in templates.GENRES:
        assert len(sobre) <= len(templates.questions(genre, "fr"))


def test_les_questions_autorisent_l_absence_de_reponse():
    """Une question qui peut légitimement être vide doit le dire.

    C'est ce qui distingue une question d'une rubrique : un titre de section
    exige d'être rempli, une question autorise « aucun ».
    """
    for genre in ("analyse_sportive", "interview"):
        joint = " ".join(templates.questions(genre, "fr")).lower()
        assert "aucune" in joint or "aucun" in joint
        joint_en = " ".join(templates.questions(genre, "en")).lower()
        assert "none" in joint_en


def test_langue_non_couverte_retombe_sur_l_anglais():
    assert templates.questions("interview", "de") == templates.questions("interview", "en")
    assert templates.questions("interview", None)  # pas d'exception


def test_genre_inconnu_donne_les_questions_sobres():
    assert templates.questions("réunion", "fr") == templates.questions(templates.GENERIC, "fr")


def test_liste_des_genres_pour_le_classifieur():
    listing = templates.genre_list()
    for genre in templates.GENRES:
        assert genre in listing
