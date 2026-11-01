"""Liaisons « Speaker N (Nom) » : le CR ne rattache pas une identité à une étiquette.

Cas réel. Un CR a écrit « Speaker 10 (Léa Marchand/Iris) ». Le
transcript le CONTREDIT, et sans aucune connaissance extérieure :

  · Speaker 10 dit avoir auditionné POUR LE RÔLE DE DONNA ;
  · « Léa » est citée par d'AUTRES locuteurs comme partant avec Paul ;
  · la source apparie « Rémi and Nora » (personnages) et « Léa and Paul »
    (acteurs) — Léa joue donc Nora, pas Iris.

Ni le vérificateur numérique (il ne voit que des chiffres) ni `sanitize_roles`
(il ne s'applique qu'au `.md`) ne pouvaient l'attraper.
"""

import pytest

from core import names, report

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("avant,apres", [
    ("Speaker 3 (Paul R. Bertin) raconte son renvoi.", "Speaker 3 raconte son renvoi."),
    ("Speaker 10 (Léa Marchand/Iris) a rencontré Victor.", "Speaker 10 a rencontré Victor."),
    ("Victor Garnier (Speaker 2) annonce la série.", "Victor Garnier annonce la série."),
    ("Locuteur 2 (Claire Morel) mène l'entretien.", "Locuteur 2 mène l'entretien."),
    ("speaker 7 (Anna) intervient.", "speaker 7 intervient."),
])
def test_la_liaison_est_retiree(avant, apres):
    nettoye, retirees = names.strip_speaker_bindings(avant)
    assert nettoye == apres
    assert retirees, "la liaison retirée doit être signalée"


@pytest.mark.parametrize("texte", [
    # Un nom CITÉ est légitime : il est presque toujours réellement prononcé.
    "Victor Garnier gave one of the worst auditions in history.",
    "Le tournage (sauf le pilote) a eu lieu à Toronto.",
    "Speaker 2 announces the premiere at 00:15:41.",
    "La série (2011-2019) a duré neuf saisons.",
])
def test_ce_qui_n_est_pas_une_liaison_reste_intact(texte):
    nettoye, retirees = names.strip_speaker_bindings(texte)
    assert nettoye == texte
    assert retirees == []


def test_les_noms_survivent_ailleurs_dans_le_texte():
    """On retire le RATTACHEMENT, pas le nom : le CR garde son information."""
    txt = ("Speaker 3 (Paul R. Bertin) raconte son renvoi. "
           "Paul R. Bertin a quitté la série pour rejoindre sa femme.")
    nettoye, _ = names.strip_speaker_bindings(txt)
    assert "Speaker 3 (" not in nettoye
    assert nettoye.count("Paul R. Bertin") == 1


def test_plusieurs_liaisons_toutes_signalees():
    txt = "Speaker 3 (Bertin) et Speaker 10 (Marchand) discutent."
    nettoye, retirees = names.strip_speaker_bindings(txt)
    assert nettoye == "Speaker 3 et Speaker 10 discutent."
    assert len(retirees) == 2


def test_l_avis_liste_les_identites_retirees():
    avis = report._bindings_notice(["Speaker 10 (Léa Marchand/Iris)"], "en")
    assert "Speaker 10 (Léa Marchand/Iris)" in avis
    assert "Identities removed" in avis
    assert report._bindings_notice([], "en") == ""


def test_l_avis_ne_repete_pas_un_doublon():
    avis = report._bindings_notice(["Speaker 3 (Bertin)", "Speaker 3 (Bertin)"], "fr")
    assert avis.count("Speaker 3 (Bertin)") == 1


# --- Deux défauts trouvés sur un CR réel -------------------------------------
# Première version : on cherchait à CAPTURER le nom. Erreur de conception.
#   · `re.IGNORECASE`, requis pour « Speaker »/« speaker », neutralisait aussi
#     `[A-ZÀ-Þ]` — l'exigence de majuscule était annulée ;
#   · un nom finissant par autre chose qu'un caractère de mot échappait au motif.
# L'opération voulue est de RETIRER la parenthèse, pas de reconnaître un nom.

def test_un_nom_finissant_par_un_guillemet_ne_survit_PLUS():
    """Faux négatif mesuré : cette liaison avait survécu dans un CR de production."""
    avant = "L'animateur du journal « C'est à vous » (Speaker 2) mène l'entretien."
    apres, retirees = names.strip_speaker_bindings(avant)
    assert "(Speaker 2)" not in apres
    assert apres == "L'animateur du journal « C'est à vous » mène l'entretien."
    assert retirees


@pytest.mark.parametrize("avant,apres", [
    # Ponctuations et casses variées juste avant la parenthèse
    ("Le résultat (Speaker 3) est net.", "Le résultat est net."),
    ("« Untel » (locuteur 2) intervient.", "« Untel » intervient."),
    ("Quelqu'un — anonyme — (Speaker 12) parle.", "Quelqu'un — anonyme — parle."),
])
def test_la_liaison_saute_quel_que_soit_le_voisinage(avant, apres):
    assert names.strip_speaker_bindings(avant)[0] == apres


def test_le_texte_avant_la_parenthese_est_INTACT():
    """La capture trop gourmande mangeait les mots précédents : « tandis que
    Ségolène Royal ». Le texte ne doit plus jamais être amputé."""
    avant = "…tandis que Ségolène Royal (Speaker 1), identifiée comme invitée, répond."
    apres, _ = names.strip_speaker_bindings(avant)
    assert apres == "…tandis que Ségolène Royal, identifiée comme invitée, répond."
    assert "tandis que" in apres and "Ségolène Royal" in apres


def test_l_avertissement_donne_du_contexte_sans_l_avoir_retire():
    """Le libellé de l'avis reprend quelques mots amont pour être lisible —
    c'est informatif, et sans effet sur ce qui est réellement supprimé."""
    avant = "Un très long préambule puis Ségolène Royal (Speaker 1) répond."
    apres, retirees = names.strip_speaker_bindings(avant)
    assert "Ségolène Royal" in apres, "le nom reste dans le texte"
    assert "(Speaker 1)" in retirees[0], "l'avis dit ce qui a sauté"
    assert len(retirees[0].split()) <= 8, "le contexte reste borné"
