"""Une seule porte d'entrée pour les répliques, plus tolérante, sans faux locuteur.

Le CR (`chunking.transcript_lines`, `verifier_citable`) et l'ECL (`ecl.repliques`)
lisent les répliques par la même porte. Elle lit aussi les transcripts qui ne
viennent pas de `writer_md` : horodatage court `[MM:SS]` (normalisé en `HH:MM:SS`
dès la lecture, `[75:10]` donnant `01:15:10`), locuteur hors gras avant
l'horodatage, et « [HH:MM:SS] Nom : texte ».

« Nom : » ne devient un locuteur que si le même nom ouvre au moins deux répliques
du document, et seulement s'il tient en quatre mots sans chiffre : sinon le texte
reste entier.

Ces tests passent par les fonctions qui existaient déjà (`transcript_lines`,
`ecl.repliques`) : sur l'ancien code, les garde-fous de non-régression passent
et les formats nouveaux tombent.
"""
from __future__ import annotations

import pytest

from core import ecl
from core.experimental.chunking import transcript_lines, verifier_citable

pytestmark = pytest.mark.unit


def _corps(*lignes: str) -> str:
    return "# Essai\n\n## Transcript\n\n" + "\n".join(lignes) + "\n"


def _lu(corps: str) -> list[tuple[str, str | None, str]]:
    """(horodatage, locuteur, texte) vus par l'ECL, la même porte que le CR."""
    return [(r["horodatage"], r["locuteur"], r["texte"]) for r in ecl.repliques(corps)]


# ---------- (1) horodatage court, normalisé dès la lecture ----------

def test_l_horodatage_court_est_lu_et_normalise() -> None:
    corps = _corps("[00:05] Bonjour à tous.", "[01:10] On fixe le prix à 25 euros.")

    lignes, horodatages = transcript_lines(corps)

    assert lignes == ["[00:00:05] Bonjour à tous.", "[00:01:10] On fixe le prix à 25 euros."]
    assert horodatages == {"00:00:05", "00:01:10"}
    assert _lu(corps) == [("00:00:05", None, "Bonjour à tous."),
                          ("00:01:10", None, "On fixe le prix à 25 euros.")]
    assert [r["secondes"] for r in ecl.repliques(corps)] == [5, 70]
    verifier_citable(corps)


def test_des_minutes_au_dela_de_l_heure_donnent_des_heures() -> None:
    # Ni repli sur 00:15:10, ni rejet : 75 minutes et 10 secondes.
    corps = _corps("[59:59] Juste avant.", "[75:10] Une heure et quart plus tard.")

    lignes, horodatages = transcript_lines(corps)

    assert horodatages == {"00:59:59", "01:15:10"}
    assert lignes[1] == "[01:15:10] Une heure et quart plus tard."
    assert [r["secondes"] for r in ecl.repliques(corps)] == [3599, 4510]


def test_l_horodatage_court_avec_locuteur_en_gras() -> None:
    corps = _corps("**Alice** [12:34] On commence.")

    assert transcript_lines(corps)[0] == ["**Alice** [00:12:34] On commence."]
    assert _lu(corps) == [("00:12:34", "Alice", "On commence.")]


def test_des_secondes_impossibles_ne_font_pas_une_replique() -> None:
    corps = _corps("[12:75] Horodatage impossible.")

    assert transcript_lines(corps) == ([], set())
    assert len(ecl.repliques(_corps("[00:05] Témoin."))) == 1


# ---------- (1) locuteur hors gras, avant l'horodatage ----------

@pytest.mark.parametrize("ligne, attendu", [
    ("Alice [00:01:02] On fixe le prix.", ("00:01:02", "Alice", "On fixe le prix.")),
    ("Bruno Martin [00:01:05] D'accord.", ("00:01:05", "Bruno Martin", "D'accord.")),
    ("Jean-Marie de La Tour [01:05] Quatre mots.", ("00:01:05", "Jean-Marie de La Tour", "Quatre mots.")),
    ("Élodie [75:10] Plus tard.", ("01:15:10", "Élodie", "Plus tard.")),
])
def test_le_locuteur_hors_gras_avant_l_horodatage(ligne, attendu) -> None:
    corps = _corps(ligne)
    horodatage, locuteur, texte = attendu

    assert _lu(corps) == [attendu]
    assert transcript_lines(corps)[0] == [f"**{locuteur}** [{horodatage}] {texte}"]


@pytest.mark.parametrize("ligne", [
    "Jean Pierre Marie Paul Louis [00:01:02] Cinq mots, pas un nom.",
    "Salle 4 [00:01:02] Un chiffre, pas un nom.",
    "- [00:01:02] Une puce n'est pas un nom.",
])
def test_un_prefixe_qui_n_est_pas_un_nom_ne_fait_pas_de_replique(ligne) -> None:
    assert _lu(_corps(ligne)) == []
    assert len(_lu(_corps("Alice [00:01:02] Témoin."))) == 1


# ---------- (1) + (2) « [HH:MM:SS] Nom : texte » ----------

def test_le_nom_qui_ouvre_plusieurs_repliques_devient_le_locuteur() -> None:
    corps = _corps("[00:01:02] Alice : On fixe le prix à 25 euros.",
                   "[00:01:09] Bruno Martin: D'accord pour 25.",
                   "[01:15] Alice : Et la livraison ?",
                   "[00:01:20] Bruno Martin: Jeudi.")

    assert _lu(corps) == [("00:01:02", "Alice", "On fixe le prix à 25 euros."),
                          ("00:01:09", "Bruno Martin", "D'accord pour 25."),
                          ("00:01:15", "Alice", "Et la livraison ?"),
                          ("00:01:20", "Bruno Martin", "Jeudi.")]
    assert transcript_lines(corps)[0][0] == "**Alice** [00:01:02] On fixe le prix à 25 euros."


def test_une_etiquette_isolee_n_est_pas_un_locuteur() -> None:
    # « Budget » n'ouvre qu'une réplique : le texte reste entier.
    corps = _corps("[00:01:02] Budget : 25 €",
                   "[00:01:05] Alice : On valide.",
                   "[00:01:09] Alice : Suivant.")

    assert _lu(corps)[0] == ("00:01:02", None, "Budget : 25 €")
    assert transcript_lines(corps)[0][0] == "[00:01:02] Budget : 25 €"
    assert _lu(corps)[1][1] == "Alice"


def test_seule_une_etiquette_isolee_reste_une_replique_sans_locuteur() -> None:
    assert _lu(_corps("[00:01:02] Budget : 25 €")) == [("00:01:02", None, "Budget : 25 €")]


@pytest.mark.parametrize("etiquette", ["Le point sur le budget", "Point 3"])
def test_une_etiquette_repetee_trop_longue_ou_chiffree_reste_du_texte(etiquette) -> None:
    corps = _corps(f"[00:00:01] {etiquette} : premier.", f"[00:00:02] {etiquette} : second.")

    assert _lu(corps) == [("00:00:01", None, f"{etiquette} : premier."),
                          ("00:00:02", None, f"{etiquette} : second.")]


def test_les_deux_points_d_une_heure_ou_d_une_adresse_ne_font_pas_de_nom() -> None:
    corps = _corps("[00:00:01] Rendez-vous à 10:30 demain.", "[00:00:02] Rendez-vous à 10:30 demain.",
                   "[00:00:03] Voir https://exemple.org ici.", "[00:00:04] Voir https://exemple.org ici.")

    assert [locuteur for _ts, locuteur, _t in _lu(corps)] == [None, None, None, None]


# ---------- garde-fous de non-régression : les formats déjà lus ----------

def test_les_formats_deja_lus_gardent_leur_sens() -> None:
    corps = _corps("**Speaker 1** [00:00:05] Bonjour.",
                   "**Speaker ?** [00:00:07] Segment sans locuteur.",
                   "**Alice** [00:01:02] Budget : 25 €",
                   "**Bruno Martin** [00:01:05] Budget : 30 €",
                   "[00:00:09] Seule réplique nue.")

    assert _lu(corps) == [("00:00:05", "Speaker 1", "Bonjour."),
                          ("00:00:07", "Speaker ?", "Segment sans locuteur."),
                          ("00:01:02", "Alice", "Budget : 25 €"),
                          ("00:01:05", "Bruno Martin", "Budget : 30 €"),
                          ("00:00:09", None, "Seule réplique nue.")]
    lignes, horodatages = transcript_lines(corps)
    assert lignes == ["**Speaker 1** [00:00:05] Bonjour.",
                      "**Speaker ?** [00:00:07] Segment sans locuteur.",
                      "**Alice** [00:01:02] Budget : 25 €",
                      "**Bruno Martin** [00:01:05] Budget : 30 €",
                      "[00:00:09] Seule réplique nue."]
    assert horodatages == {"00:00:05", "00:00:07", "00:01:02", "00:01:05", "00:00:09"}


def test_le_profil_reste_hors_des_repliques() -> None:
    corps = ("# Essai\n\n## Profil\n\n**Thèmes :** prix · budget\n- [00:00:05] repère\n"
             "Alice [00:00:06] avant le transcript\n\n## Transcript\n\n[00:00:10] Seule réplique.\n")

    assert transcript_lines(corps)[0] == ["[00:00:10] Seule réplique."]
    assert _lu(corps) == [("00:00:10", None, "Seule réplique.")]


def test_une_replique_sans_texte_reste_ignoree() -> None:
    corps = _corps("**Alice** [00:00:01]", "[00:00:02]   ", "[00:00:03] Texte.")

    assert _lu(corps) == [("00:00:03", None, "Texte.")]
