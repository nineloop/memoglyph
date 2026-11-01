"""Indicateur de stabilité de la langue (core/quality.py).

Ce que l'indicateur doit garantir avant tout : ne jamais faire passer un
« je ne sais pas » pour un « c'est propre ».
"""

import pytest

from core import quality

FR = ["Le président est dans la salle et il ne veut pas parler.",
      "Nous avons tout fait pour que cette réunion soit utile.",
      "Il est dans le vrai mais il ne le sait pas encore."]
EN = ["The manager said that this was not what they had in mind.",
      "You know what I mean, but this is just the beginning for them.",
      "They have been there and they know what the plan was about."]


def _many(lines, n):
    return [lines[i % len(lines)] for i in range(n)]


def test_langue_homogene_donne_zero():
    assert quality.language_mix(_many(FR, 40), "fr") == 0.0


def test_melange_mesure():
    texts = _many(FR, 30) + _many(EN, 10)
    mix = quality.language_mix(texts, "fr")
    assert mix == pytest.approx(0.25, abs=0.01)


def test_paire_non_couverte_rend_none_pas_zero():
    """Le piège : annoncer « aucun mélange » sur une langue non examinée."""
    assert quality.language_mix(_many(FR, 40), "de") is None
    assert quality.language_mix(_many(FR, 40), None) is None


def test_trop_peu_de_segments_rend_none():
    """Le plancher protège l'AFFICHAGE : `language_mix` est montré à l'écran, et
    rendre 0,0 sur trois segments annoncerait « vérifié propre » sur presque
    rien. Il est passé de 20 à 5 — assez bas pour admettre un message vocal
    court (8 à 10 segments), assez haut pour écarter une poignée de fragments.
    """
    assert quality.language_mix(FR, "fr") is None, "3 segments : sous le plancher"
    assert quality.language_mix(_many(FR, 8), "fr") == 0.0, (
        "8 segments — un message vocal — doivent être MESURÉS, pas tus")


def test_is_unstable_ne_declenche_pas_sur_non_verifie():
    assert quality.is_unstable(None) is False
    assert quality.is_unstable(0.0) is False
    assert quality.is_unstable(0.5) is True


def test_extraction_format_recent_avec_section():
    body = (
        "# titre\n\n## 🗣️ Profil de la conversation\n\n"
        "Résumé rédigé par le modèle, potentiellement dans une autre langue.\n\n"
        "## Transcript\n\n"
        "**Speaker 1** [00:00:04] Bonjour à tous.\n"
        "**Speaker 2** [00:00:09] Merci de nous recevoir.\n"
    )
    assert quality.transcript_texts(body) == ["Bonjour à tous.", "Merci de nous recevoir."]


def test_extraction_format_ancien_sans_section():
    """Les .md d'avant le profilage n'ont pas de « ## Transcript ».

    Exiger la section renvoyait 0 segment, donc « non vérifié » — un faux
    silence impossible à distinguer d'une vraie impossibilité de mesurer.
    """
    body = "# titre\n\nPremière phrase du transcript.\nDeuxième phrase.\n"
    assert quality.transcript_texts(body) == [
        "Première phrase du transcript.", "Deuxième phrase."]


def test_le_bloc_de_profil_est_exclu_meme_sans_section_transcript():
    body = ("# titre\n\n## Profil\n\nTexte du modèle.\n\n"
            "# titre\n\nVraie phrase prononcée.\n")
    assert "Texte du modèle." not in quality.transcript_texts(body)


# ---------------------------------------------------------------------------
# Écritures — ce que les mots-outils ne pouvaient pas voir
# ---------------------------------------------------------------------------


def test_une_ecriture_n_est_pas_une_langue() -> None:
    """L'arabe, le persan et l'ourdou partagent une écriture. Le relevé rend
    donc une ÉCRITURE, et le nom le dit — s'il rendait « ar » il mentirait sur
    ce qu'il sait."""
    assert quality.repartition_ecritures("جلالة الملك") == {"arabe": 1.0}
    assert quality.ecriture_dominante("سلام علیکم") == "arabe"


def test_les_caracteres_neutres_sont_ecartes() -> None:
    """Chiffres, ponctuation et espaces appartiennent à toutes les écritures.
    Les compter diluerait la mesure sans rien lui apprendre."""
    assert quality.repartition_ecritures("2026 — 30/07 !!") == {}
    assert quality.ecriture_dominante("2026 !!") is None, (
        "un texte sans caractère classable doit rendre « je ne sais pas »")


def test_un_texte_reellement_mixte_n_a_pas_de_dominante() -> None:
    """Un message franco-darija n'a pas d'écriture majoritaire, et en nommer
    une reviendrait à tirer au sort. `None` est ici une RÉPONSE."""
    assert quality.ecriture_dominante("Bonjour سلام", seuil=0.7) is None
    parts = quality.repartition_ecritures("Bonjour سلام")
    assert set(parts) == {"latin", "arabe"}
    assert abs(sum(parts.values()) - 1.0) < 1e-9


def test_la_bascule_en_traduction_est_vue_hors_du_couple_fr_en() -> None:
    """LE cas qui motive tout ceci.

    Whisper bascule parfois en TRADUCTION au milieu d'un fichier sans le
    signaler. Sur un audio arabe rendu en anglais, la langue déclarée dit
    toujours « ar » — mais le texte est en écriture latine. Les mots-outils ne
    couvraient que français/anglais et rendaient donc `None`, un silence
    indiscernable d'un « tout va bien ».
    """
    arabe = ["جلالة الملك نصره الله"] * 20
    traduits = ["His Majesty the King renewed his commitment"] * 20

    assert quality.language_mix(arabe, "ar") == 0.0
    assert quality.language_mix(traduits, "ar") == 1.0, (
        "une traduction silencieuse doit se voir")
    assert quality.language_mix(arabe + traduits, "ar") == 0.5


def test_une_langue_hors_perimetre_reste_un_silence_honnete() -> None:
    """On n'ajoute que ce qu'on est prêt à défendre. Le japonais écrit en kana
    est couvert ; le vietnamien, latin, ne l'est pas et retombe sur `None`."""
    assert quality.language_mix(["これはテストです"] * 20, "ja") == 0.0
    assert quality.language_mix(["Xin chào các bạn"] * 20, "vi") is None


# ---------------------------------------------------------------------------
# Segmentation régulière — la panne que l'écriture ne voit pas
# ---------------------------------------------------------------------------


def test_une_grille_parfaite_se_voit() -> None:
    """MESURÉ : un message vocal franco-darija de 19,8 s a rendu dix segments de
    2,0 s exactement, bornes jointives, moitié du texte incohérent. Whisper
    retombe sur une fenêtre fixe quand il ne reconnaît pas."""
    grille = [(i * 2.0, i * 2.0 + 2.0) for i in range(10)]
    assert quality.regularite_segmentation(grille) == 1.0
    assert quality.regularite_segmentation(grille) >= quality.SEUIL_GRILLE


def test_une_parole_reelle_tombe_tres_bas() -> None:
    """Sur des transcriptions saines, la valeur reste dans une bande étroite,
    loin du seuil."""
    reelle = [(0.0, 3.4), (3.4, 7.9), (7.9, 9.1), (9.1, 14.6), (14.6, 15.2),
              (15.2, 21.8), (21.8, 23.0), (23.0, 29.7)]
    mesure = quality.regularite_segmentation(reelle)
    assert mesure is not None and mesure < quality.SEUIL_GRILLE


def test_trop_peu_de_segments_est_un_silence_et_non_un_zero() -> None:
    """Trois segments de même durée ne prouvent rien — la régularité n'a de sens
    qu'au-delà d'un volume. `None` et 0.0 ne doivent pas se confondre."""
    assert quality.regularite_segmentation([(0.0, 2.0), (2.0, 4.0)]) is None
    assert quality.regularite_segmentation([]) is None


def test_les_segments_vides_ne_faussent_pas_la_proportion() -> None:
    """Une borne de durée nulle ou négative n'est pas un segment : la compter
    diluerait la mesure."""
    grille = [(i * 2.0, i * 2.0 + 2.0) for i in range(8)]
    assert quality.regularite_segmentation(grille + [(9.0, 9.0)]) == 1.0


def test_les_deux_indicateurs_regardent_deux_pannes_differentes() -> None:
    """Le cas qui a motivé cet indicateur : texte intégralement en écriture
    arabe, cohérent avec le `ar` déclaré — donc `language_mix` ne voit RIEN,
    parce que le français avait été rendu EN caractères arabes."""
    textes = ["السلام عليكم", "لا يوجد مشكلة"] * 10
    assert quality.language_mix(textes, "ar") == 0.0, (
        "l'écriture est cohérente : cet indicateur-là n'a rien à signaler")

    grille = [(i * 2.0, i * 2.0 + 2.0) for i in range(10)]
    assert quality.regularite_segmentation(grille) == 1.0, (
        "c'est la SEGMENTATION qui trahit la panne, pas l'écriture")


# ---------------------------------------------------------------------------
# La langue déduite du texte — le repli quand le moteur se tait
# ---------------------------------------------------------------------------


def test_la_langue_est_deduite_quand_l_ecriture_la_designe() -> None:
    """Voxtral ne déclare JAMAIS la langue. Sans ce repli, la directive de
    profilage retombe sur une consigne générique — et MESURÉ : un
    entretien ANGLAIS transcrit par Voxtral a été profilé en FRANÇAIS."""
    assert quality.langue_probable(["جلالة الملك", "السلام عليكم"]) == "ar"
    assert quality.langue_probable(["これはテストです"]) == "ja"
    assert quality.langue_probable(["Это тестовое предложение"]) == "ru"


def test_le_latin_retombe_sur_les_mots_outils() -> None:
    """L'écriture latine ne désigne aucune langue : elle est partagée par des
    dizaines. Seuls les mots-outils peuvent départager, et ils ne couvrent que
    le couple français / anglais."""
    assert quality.langue_probable(
        ["The candidate said that this was not what they had in mind"]) == "en"
    assert quality.langue_probable(
        ["Le président est dans la salle et il ne veut pas parler"]) == "fr"


def test_elle_ne_DEVINE_jamais() -> None:
    """LA règle qui prime sur la couverture.

    Se tromper de langue est PIRE que ne pas en imposer : un mauvais ancrage
    produit un document entier dans la mauvaise langue, avec l'assurance d'une
    consigne explicite. Le repli générique, lui, laisse le modèle décider.
    """
    assert quality.langue_probable(["2026 — 30/07 !!"]) is None, "aucun caractère classable"
    assert quality.langue_probable(["xyzzy plugh frobnitz"]) is None, "latin non reconnu"
    assert quality.langue_probable(["Bonjour", "سلام"]) is None, "réellement mélangé"
    assert quality.langue_probable([]) is None
    assert quality.langue_probable(["", "   "]) is None


def test_une_ecriture_minoritaire_ne_decide_pas() -> None:
    """Un seul segment arabe dans dix segments latins ne fait pas un document
    arabe. Le seuil de domination est ce qui l'empêche."""
    textes = ["The meeting was about the new product line"] * 9 + ["السلام عليكم"]
    assert quality.langue_probable(textes) == "en"
