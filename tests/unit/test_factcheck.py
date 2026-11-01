"""Vérification déterministe des nombres d'un résumé contre sa source.

Cas de référence : un compte-rendu réel de débrief sportif qui a inventé
« Mondial 2028 » et qui a mal attribué un score bien présent dans la source.
"""

import pytest

from core import factcheck

pytestmark = pytest.mark.unit

SOURCE = [
    "On le rappelle, un but à zéro, puis deux buts à zéro, 3-0, 4-0.",
    "4-0 à la mi-temps, il a manqué de coordination.",
    "Barcola marque le but du 4-2, et là ça devient complètement fou.",
    "Le triplé, ça fait 5-3 à ce moment-là.",
]


def test_nombre_invente_signale():
    """Le cas réel : « Mondial 2028 » n'est prononcé nulle part."""
    resume = "Préparation pour le Mondial 2028."
    assert factcheck.unsupported_numbers(resume, SOURCE) == ["2028"]


def test_nombre_present_non_signale():
    resume = "À la mi-temps le score était de 4-0."
    assert factcheck.unsupported_numbers(resume, SOURCE) == []


def test_composite_de_la_source_soutient_ses_composants():
    """La source dit « 5-3 », donc « 5 » et « 3 » sont soutenus."""
    assert factcheck.unsupported_numbers("Il en a marqué 3.", SOURCE) == []


def test_score_absent_signale_meme_si_ses_chiffres_existent():
    """« 6-4 » est une affirmation à part entière, pas la somme de 6 et 4."""
    assert "6-4" in factcheck.unsupported_numbers("Score final 6-4.", SOURCE)


def test_nombres_en_toutes_lettres_normalises():
    """La source dit « un but à zéro », le résumé écrit « 1-0 » : pas d'alerte."""
    assert factcheck.unsupported_numbers("Ils menaient 1-0.", SOURCE) == []


def test_libelles_de_locuteurs_ignores():
    """« Speaker 7 » n'est pas une donnée chiffrée — sinon le bruit noie le signal."""
    resume = "**Speaker 7** et **Speaker 12** interviennent longuement."
    assert factcheck.unsupported_numbers(resume, SOURCE) == []


def test_puces_numerotees_ignorees():
    resume = "1. Premier point\n2. Deuxième point\n"
    assert factcheck.unsupported_numbers(resume, SOURCE) == []


def test_titre_du_gabarit_ignore():
    """Le titre reprend le nom du fichier (« france-4-6-angleterre ») : pas une affirmation."""
    resume = "# Compte-rendu — france-4-6-angleterre\n\nLe match fut serré."
    assert factcheck.unsupported_numbers(resume, SOURCE) == []


def test_score_partout_invente_signale():
    """Cas réel : le CR affirmait « finir à 6 buts partout ». Jamais dit.

    C'est la normalisation des scores oraux qui rend cette prise possible : sans
    elle, « 6 » existe isolément dans la source et rien n'était signalé.
    """
    resume = "L'équipe a remonté le score pour finir à 6 buts partout."
    assert "6-6" in factcheck.unsupported_numbers(resume, SOURCE)


def test_LIMITE_la_mauvaise_attribution_n_est_PAS_detectee():
    """Limite structurelle, documentée exprès pour qu'elle ne soit pas oubliée.

    Le CR réel affirmait « la France a marqué plus de buts (5-3) » alors que 5-3
    était l'avance de l'ADVERSAIRE. Le nombre existe dans la source, donc un test
    d'appartenance ne peut rien voir : il faudrait vérifier le couple
    (nombre, entité). Ce test échouera le jour où quelqu'un implémente ça — et
    ce sera une bonne nouvelle.
    """
    resume = "La France a marqué plus de buts en temps réglementaire (5-3)."
    assert factcheck.unsupported_numbers(resume, SOURCE) == []


def test_resultat_trie_donc_deterministe():
    resume = "En 2028, sur 90 minutes, avec 7 joueurs."
    assert factcheck.unsupported_numbers(resume, SOURCE) == ["7", "90", "2028"]


# --- Horodatages cités : une référence n'est pas une affirmation --------------
# Défaut réel (CR « when-an-audition-changed-tv-forever ») : le CR
# citait « (Speaker 2 at 00:15:46) », citation EXACTE et vérifiable. Le
# vérificateur la décomposait en 00/15/46 et signalait « 00 » et « 46 » comme
# inventés. Pire, le verdict était arbitraire : « 15 » échappait à l'alerte
# uniquement parce qu'il apparaissait par hasard ailleurs dans la parole.

def test_un_horodatage_cite_n_est_pas_signale():
    source = ["Je m'appelle Gabriel et je joue Harvey dans Suits."]
    resume = 'La série est annoncée (Speaker 2 at 00:15:46).'
    assert factcheck.unsupported_numbers(resume, source) == []


@pytest.mark.parametrize("forme", [
    "[00:15:46]", "00:15:46", "at 15:46", "à 01:23", "[9:05]",
])
def test_toutes_les_formes_d_horodatage_sont_neutralisees(forme):
    assert factcheck.numbers(f"Passage cité {forme} dans le CR.") == set()


def test_le_texte_source_n_a_JAMAIS_les_horodatages():
    """Racine du défaut : la source ne porte que la parole. Un composant
    d'horodatage ne pouvait donc passer que par coïncidence."""
    from core import quality
    md = (
        "---\ntitle: x\n---\n\n## Transcript\n\n"
        "**Speaker 1** [00:15:46] On a gagné trois à zéro.\n"
    )
    source = " ".join(quality.transcript_texts(md))
    assert "00:15:46" not in source
    assert "gagné" in source


def test_un_vrai_nombre_reste_signale_malgre_la_correction():
    """La correction ne doit pas désarmer le vérificateur."""
    source = ["Le budget est de 400 euros."]
    assert factcheck.unsupported_numbers("Le budget est de 900 euros.", source) == ["900"]
    # …y compris dans une phrase qui contient AUSSI un horodatage légitime
    resume = "À 00:15:46, il annonce le budget de 900 euros."
    assert factcheck.unsupported_numbers(resume, source) == ["900"]


def test_LIMITE_l_article_un_est_compte_comme_le_chiffre_1():
    """Trouvé en écrivant le test précédent, et laissé en l'état sciemment.

    `_normalize` convertit « un » et « une » en « 1 » — utile pour « un but à
    zéro » → « 1-0 », mais l'article y passe aussi. La limite ne peut PAS se
    manifester : toute source française contient « un » ou « une », donc « 1 »
    est toujours dans l'ensemble source et l'alerte ne part jamais.

    L'anglais est hors de cause pour une autre raison — la table contient
    désormais ses mots-nombres, mais ses articles (« a », « an ») n'en sont pas.

    Documenté plutôt que corrigé : retirer « un » de la table ferait perdre les
    scores énoncés à l'oral, pour supprimer un risque qui ne peut pas survenir.
    """
    assert factcheck.numbers("un budget") == {"1"}
    # Le masque, vérifié : une source française fournit « 1 » d'elle-même.
    source = ["Il y avait un public formidable."]
    assert "1" in factcheck._source_set(source)
    assert factcheck.unsupported_numbers("un budget", source) == []


# --- Composition des nombres en toutes lettres -------------------------------
# L'ancienne table PLATE mot → chiffre violait l'invariant revendiqué par le
# module (« jamais inventer une alerte ») : 7 cas sur 7 en faux positif. Deux
# causes — aucune composition (« soixante » + « quinze » ne faisaient jamais 75)
# et AUCUN mot anglais, alors que la moitié du corpus l'est.

@pytest.mark.parametrize("lettres,valeur", [
    # Français : les pièges sont les compositions
    ("soixante-quinze", 75), ("soixante-dix", 70), ("soixante-douze", 72),
    ("quatre-vingts", 80), ("quatre-vingt-dix", 90), ("quatre-vingt-douze", 92),
    ("vingt et un", 21), ("dix-sept", 17),
    ("deux cents", 200), ("vingt-cinq mille", 25_000), ("trois millions", 3_000_000),
    # Anglais : entièrement absent avant
    ("fifteen", 15), ("twenty-five", 25), ("ninety-nine", 99),
    ("three hundred", 300), ("two thousand", 2000), ("one million", 1_000_000),
])
def test_les_nombres_composes_sont_pliés(lettres, valeur):
    assert factcheck._plier(lettres.replace("-", " ").split()) == valeur


def test_une_suite_qui_n_est_pas_un_nombre_rend_None():
    assert factcheck._plier(["chat", "chien"]) is None
    assert factcheck._plier([]) is None


@pytest.mark.parametrize("source,resume", [
    ("Il y avait soixante-quinze personnes.", "75 personnes présentes."),
    ("Nous avons reçu quatre-vingt-dix candidats.", "90 candidats."),
    ("Environ vingt-cinq mille tickets vendus.", "25 000 tickets vendus."),
    ("Le délai est de vingt et un jours.", "Le délai est de 21 jours."),
    ("There were fifteen people in the room.", "15 people attended."),
    ("We looked at three options.", "3 options were considered."),
    ("That was back in twenty twenty.", "That was in 2020."),
])
def test_les_sept_faux_positifs_mesures_ne_se_produisent_plus(source, resume):
    assert factcheck.unsupported_numbers(resume, [source]) == []


def test_le_separateur_de_milliers_ne_coupe_plus_le_nombre():
    """« 25 000 » ressortait en « 25 » ET « 000 »."""
    assert factcheck.numbers("25 000 tickets") == {"25000"}


@pytest.mark.parametrize("source,resume,attendu", [
    ("Le budget est de 400 euros.", "Le budget est de 900 euros.", ["900"]),
    ("Il y avait soixante-quinze personnes.", "82 personnes.", ["82"]),
    ("We had fifteen people.", "We had 50 people.", ["50"]),
    ("Coupe du monde 2026.", "Mondial 2028.", ["2028"]),
])
def test_le_verificateur_reste_ARME(source, resume, attendu):
    """La correction ne doit pas transformer le garde-fou en passoire."""
    assert factcheck.unsupported_numbers(resume, [source]) == attendu


def test_l_annee_par_paires_n_est_ajoutee_qu_a_la_SOURCE():
    """« twenty twenty » vaut 40 (addition) ou 2020 (année). Hors contexte les
    deux lectures sont valides : on ne tranche pas, on rend la source permissive.
    Le biais est celui que le module assume — masquer une alerte, jamais en
    inventer une."""
    src = ["That was back in twenty twenty."]
    assert factcheck._source_set(src) >= {"40", "2020"}
    # Les deux lectures passent, aucune n'est signalée à tort.
    assert factcheck.unsupported_numbers("in 2020", src) == []
    assert factcheck.unsupported_numbers("in 40", src) == []
    # Mais le RÉSUMÉ ne bénéficie pas de la lecture alternative.
    assert "2020" not in factcheck.numbers("twenty twenty")


# --- Désarmement --------------------------------------------------------------

def test_le_verificateur_est_DESARME_par_defaut():
    """Ses alertes étaient des faux positifs, pour trois causes distinctes —
    horodatage cité, « two hours and a half » converti en 2.5, et le CR citant
    n_speakers. Un garde-fou dont les alertes sont fausses apprend à ignorer les
    avertissements.

    Le module reste testé et réarmable ; il n'écrit simplement plus dans le CR.
    """
    from core import config
    assert config.FACTCHECK_NUMBERS is False


def test_le_module_reste_FONCTIONNEL_une_fois_reamorce():
    """Désarmer n'est pas supprimer : le code doit continuer de marcher."""
    assert factcheck.unsupported_numbers(
        "Mondial 2028.", ["Coupe du monde 2026."]
    ) == ["2028"]
