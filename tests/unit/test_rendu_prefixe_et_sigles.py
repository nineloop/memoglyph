"""Rendu du CR : préfixe d'identifiant d'unité retiré avant la garde, sigles AMI à tirets bas.

Deux défauts observés sur des comptes rendus de réunions AMI, rejoués sans modèle :

- dans IS1009c, le modèle de rendu a écrit « Unit g87: … » en tête de phrase ; la garde
  refusait le nombre 87, absent de l'unité, et la phrase était perdue ;
- le transcript AMI écrit les sigles lettre par lettre (« L_C_D_ ») ; une phrase rendue
  « LCD » était refusée pour une entité absente de l'unité.

Les phrases et unités viennent d'un compte rendu de la réunion publique IS1009c
(corpus AMI, CC BY 4.0).
"""
from __future__ import annotations

import pytest

from core.experimental import guard, stages

pytestmark = pytest.mark.unit

# (gid, texte de l'unité, phrase rendue par le modèle), IS1009c.
IS1009C = [
    ("g87", "try to come back to yours, and limit yours a bit.",
     "Unit g87: Attempt to return to the original and slightly limit the current one."),
    ("g96", "The User Interface Designer wants to see the news in the television at around eight o'clock.",
     "Unit g96: The User Interface Designer desires to view the news on television at approximately "
     "eight o'clock."),
    ("g97", "The T_V_ will switch on when the User Interface Designer says good morning.",
     "Unit g97: The T_V_ will turn on when the User Interface Designer says good morning."),
    ("g105", "The User Interface Designer is to come up with the user interface design.",
     "Unit g105: The User Interface Designer is responsible for developing the user interface design."),
    ("g108", "Specific instructions will be sent to the User Interface Designer and the Industrial Designer "
             "by their coaches.",
     "Unit g108: Specific instructions will be transmitted to both the User Interface Designer and the "
     "Industrial Designer by their coaches."),
]


def _rendre(monkeypatch, paires: list[tuple[str, str, str]]) -> tuple[dict, dict]:
    unites = [{"gid": g, "kind": "fact", "text": t, "start": f"00:26:{i:02d}", "speaker": None}
              for i, (g, t, _p) in enumerate(paires)]
    reponse = {"sentences": [{"text": p, "support": [g]} for g, _t, p in paires]}
    monkeypatch.setattr(stages.llm, "chat_json", lambda *a, **k: reponse)
    diag: dict = {}
    rendu = stages.render_and_guard(unites, {g: "T1" for g, _t, _p in paires}, ["Voix"],
                                    provider="mlx", seed=1, diag=diag)
    return rendu, diag["stage6_render"]


def test_les_phrases_d_is1009c_ne_sont_plus_refusees_pour_leur_identifiant(monkeypatch) -> None:
    rendu, diag = _rendre(monkeypatch, IS1009C)
    phrases = [s["text"] for s in rendu["sections"][0]["sentences"]]
    assert diag["rejected_sentences"] == []
    assert phrases[0] == "Attempt to return to the original and slightly limit the current one."
    assert phrases[2] == "The T_V_ will turn on when the User Interface Designer says good morning."
    assert not any(p.lower().startswith("unit") for p in phrases)
    assert diag["unit_prefixes_removed"] == 5


@pytest.mark.parametrize("brute, attendue", [
    ("Unit g87: Attempt to return.", "Attempt to return."),
    ("unit g87 : Attempt to return.", "Attempt to return."),
    ("Unité g87 : Attempt to return.", "Attempt to return."),
    ("UNIT G87 - Attempt to return.", "Attempt to return."),
    ("Unit g87 — Attempt to return.", "Attempt to return."),
    ("Unit g87. Attempt to return.", "Attempt to return."),
    ("Unit ID g87: Attempt to return.", "Attempt to return."),
    # Rien d'autre : un identifiant au milieu, une phrase sans séparateur, un mot qui commence par « unit ».
    ("The proposal from g87: attempt to return.", "The proposal from g87: attempt to return."),
    ("Unit g89 asks whether speech recognition matters.", "Unit g89 asks whether speech recognition matters."),
    ("United teams: attempt to return.", "United teams: attempt to return."),
])
def test_le_prefixe_n_est_retire_qu_en_tete(brute, attendue) -> None:
    assert stages.sans_prefixe_d_unite(brute) == attendue


def test_sans_prefixe_rien_n_est_compte(monkeypatch) -> None:
    _rendu, diag = _rendre(monkeypatch, [("g1", "The T_V_ will switch on.", "The T_V_ will switch on.")])
    assert diag["unit_prefixes_removed"] == 0 and diag["kept_sentences"] == 1


def test_un_chiffre_ajoute_reste_refuse_apres_le_retrait(monkeypatch) -> None:
    _rendu, diag = _rendre(monkeypatch, [("g1", "The T_V_ will switch on.", "Unit g1: The T_V_ will switch on at 8.")])
    assert [r["missing_numbers"] for r in diag["rejected_sentences"]] == [["8"]]


# ---------- Sigles à tirets bas ----------

# ES2002d, 00:09:43 : l'unité garde la graphie AMI.
UNITE_LCD = "you could take away the L_C_D_ panel and the advanced chip together"


@pytest.mark.parametrize("phrase, appui", [
    ("Removing the LCD panel and the advanced chip together could reduce costs.", UNITE_LCD),
    # dans l'autre sens : la phrase garde la graphie AMI, l'unité écrit le sigle
    ("Removing the L_C_D_ panel could reduce costs.", "you could take away the LCD panel"),
    # tiret bas final facultatif
    ("Removing the LCD panel could reduce costs.", "you could take away the L_C_D panel"),
])
def test_un_sigle_a_tirets_bas_vaut_le_sigle_ecrit_d_un_mot(phrase, appui) -> None:
    assert guard.check_sentence(phrase, appui)["ok"]


def test_un_chiffre_invente_reste_refuse() -> None:
    verdict = guard.check_sentence("Removing the LCD panel saves 12 euros.", UNITE_LCD)
    assert not verdict["ok"] and verdict["missing_numbers"] == ["12"]


def test_une_entite_inventee_reste_refusee() -> None:
    verdict = guard.check_sentence("Removing the LCD panel pleases Philips.", UNITE_LCD)
    assert not verdict["ok"] and verdict["missing_entities"] == ["Philips"]


def test_rien_d_autre_n_est_assoupli() -> None:
    # Un sigle absent de l'unité, sous l'une ou l'autre graphie, reste refusé.
    assert guard.check_sentence("Removing the DVD panel helps.", UNITE_LCD)["missing_entities"] == ["DVD"]
    assert not guard.check_sentence("Removing the D_V_D_ panel helps.", UNITE_LCD)["ok"]
    # « V_CR_ » n'est pas une suite de lettres isolées : il n'est pas lu « VCR ».
    assert guard.check_sentence("The remote skips the VCR.", "not the V_CR_")["missing_entities"] == ["VCR"]
    # Un sigle n'est pas confondu avec un autre mot qui contient ses lettres.
    assert not guard.check_sentence("Removing the LCDX panel helps.", UNITE_LCD)["ok"]


# ---------- Repli : identifiant d'unité employé comme sujet ----------

# (gid, texte de l'unité, phrase rendue par le modèle), IS1009c : des phrases refusées
# par la garde pour leur identifiant (« Unit g89 asks… »).
IS1009C_SUJET = [
    ("g89", "speech recognition is most important as far as you're concerned?",
     "Unit g89 asks whether speech recognition is most important from the user's perspective."),
    ("g90", "This could be uh one feature which could be sellable.",
     "Unit g90 argues that speech recognition could be a sellable feature."),
    ("g91", "that that be really good, yeah, I agree with that.",
     "Unit g91 agrees that speech recognition could be very effective."),
    ("g92", "I'm uh very very much doubtful how how uh far it will work, because a speech recogniser like i "
            "it it has its own uh uh problems, issues.",
     "Unit g92 expresses strong doubt about how well speech recognition will function due to inherent "
     "issues and problems with voice recognition systems."),
    ("g93", "it's not distance problem it it's recognising a person's voice, like maybe different people will "
            "be having different voices, so it like it's uh everything so i",
     "Unit g93 states that the issue is not distance but voice recognition, as different individuals have "
     "different voices, making the system complex."),
    ("g94", "to get a good recogni recognising system, it's a costly thing, I think.",
     "Unit g94 asserts that developing a reliable speech recognition system is a costly endeavor."),
    ("g95", "For example, you have a T_V_ system, I'm the user and my family members are the user, I will "
            "already record uh a question like, uh good morning.",
     "Unit g95 provides an example of a TV system where the user records a greeting like 'good morning' "
     "for family members to use."),
    ("g98", "The T_V_ will recognise the User Interface Designer's voice and switch on.",
     "Unit g98 notes that the TV system will recognize the User Interface Designer's voice and activate "
     "accordingly."),
    ("g103", "Speech recognition is considered a supplement to the user interface.",
     "Unit g103 argues that speech recognition is seen as a supplement to the user interface."),
    ("g104", "Speech recognition is proposed as a supplement, pending further research on cost by the "
             "industrial engineer.",
     "Unit g104 decides that speech recognition is proposed as a supplementary feature, pending further "
     "cost analysis by an industrial engineer."),
]


def test_les_dix_phrases_a_sujet_d_is1009c_retombent_sur_le_texte_de_leur_unite(monkeypatch) -> None:
    rendu, diag = _rendre(monkeypatch, IS1009C_SUJET)
    assert diag["rejected_sentences"] == []
    assert [(s["text"], s["support"]) for s in rendu["sections"][0]["sentences"]] == [
        (texte, [g]) for g, texte, _p in IS1009C_SUJET]
    assert diag["unit_subject_fallbacks"] == 10 and diag["unit_prefixes_removed"] == 0


@pytest.mark.parametrize("phrase", [
    "Unité g90 décide que la reconnaissance vocale est vendable.",
    "Unit ID g90 argues that speech recognition sells.",
    "UNIT G90 argues that speech recognition sells.",
    "unit g90 argues that speech recognition sells.",
])
def test_les_variantes_du_sujet_retombent_aussi_sur_l_unite(monkeypatch, phrase) -> None:
    rendu, diag = _rendre(monkeypatch, [("g90", "This could be uh one feature which could be sellable.", phrase)])
    assert rendu["sections"][0]["sentences"] == [
        {"text": "This could be uh one feature which could be sellable.", "support": ["g90"]}]
    assert diag["unit_subject_fallbacks"] == 1


def test_un_identifiant_qui_n_est_pas_celui_de_l_unite_retombe_sur_l_unite_rendue(monkeypatch) -> None:
    # La phrase nomme g12, mais c'est l'unité g89 qu'elle rend (son support) : le repli prend
    # le texte de l'unité rendue, jamais celui d'une autre unité nommée par le modèle.
    rendu, diag = _rendre(monkeypatch, [
        ("g12", "the code is printed on the box", "The code is printed on the box."),
        ("g89", "speech recognition is most important as far as you're concerned?",
         "Unit g12 asks whether speech recognition is most important.")])
    assert rendu["sections"][0]["sentences"] == [
        {"text": "The code is printed on the box.", "support": ["g12"]},
        {"text": "speech recognition is most important as far as you're concerned?", "support": ["g89"]}]
    assert diag["unit_subject_fallbacks"] == 1


def test_un_identifiant_au_milieu_n_est_pas_touche(monkeypatch) -> None:
    # Phrases gardées telles quelles si la garde les admet : ici les unités portent « g12 ».
    rendu, diag = _rendre(monkeypatch, [
        ("g1", "the code g12 is printed on the box", "The code g12 is printed on the box."),
        ("g2", "the marketing unit g12 handles the report", "The marketing unit g12 handles the report.")])
    assert rendu["sections"][0]["sentences"] == [
        {"text": "The code g12 is printed on the box.", "support": ["g1"]},
        {"text": "The marketing unit g12 handles the report.", "support": ["g2"]}]
    assert diag["unit_subject_fallbacks"] == 0 and diag["unit_prefixes_removed"] == 0


def test_un_identifiant_suivi_d_un_autre_signe_retombe_aussi_sur_l_unite(monkeypatch) -> None:
    # Après le retrait du préfixe à séparateur, ce qui reste en tête est un sujet.
    rendu, diag = _rendre(monkeypatch, [
        ("g90", "This could be uh one feature which could be sellable.", "Unit g90 (marketing) argues it sells.")])
    assert rendu["sections"][0]["sentences"][0]["text"] == "This could be uh one feature which could be sellable."
    assert diag["unit_subject_fallbacks"] == 1


def test_un_prefixe_a_separateur_reste_traite_par_le_retrait(monkeypatch) -> None:
    rendu, diag = _rendre(monkeypatch, [IS1009C[0]])
    assert rendu["sections"][0]["sentences"][0]["text"] == (
        "Attempt to return to the original and slightly limit the current one.")
    assert (diag["unit_prefixes_removed"], diag["unit_subject_fallbacks"]) == (1, 0)
