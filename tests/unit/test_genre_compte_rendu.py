"""Le genre du compte rendu : le type du profil d'abord, des définitions, un garde-fou, la réponse brute gardée.

Le constat : le compte rendu d'IS1009c (réunion de conception d'une télécommande ; corpus AMI, CC BY 4.0) a été classé
`analyse_sportive` alors que son profil disait « Type: meeting » : il a été rédigé avec les questions d'un gabarit sportif.
Un petit modèle local, des clés nues sans définition, `analyse_sportive` en tête de liste, le type du profil ignoré, aucune
vérification après coup : quatre causes, quatre correctifs. Ces tests nomment chacun.
"""
from __future__ import annotations

import inspect

import pytest

from core import llm, provenance, report, sensitivity, templates

pytestmark = pytest.mark.unit

# Le profil d'IS1009c (partie 1), tel que le pipeline l'écrit (extrait minimal), puis le début du transcript.
PROFIL_IS1009C = """\
## 🗣️ Conversation profile

**Type:** meeting
*duration 07:31 · 2 speakers · 10 turns*

The speakers review the previous meeting's discussion on the design and features of a new remote control device, focusing on marketing, usability, and interface design. They agree on core functionalities and additional features to enhance user experience and appeal.

**Topics:** remote control interface design · user-friendly features and learning curve · channel navigation and volume control · visual and interactive elements for appeal · specific features for children and accessibility

## 👥 Speaker profiles

**Speaker 1 — facilitator** · 04:56 · 74% · 5 turns
- *Tone:* calm and structured

## Transcript

**Speaker 1** [00:00:01] Okay. So this is the second meeting about the remote control.
**Speaker 2** [00:00:09] Yeah, I think we should keep the interface simple.
"""

PROFIL_FR = PROFIL_IS1009C.replace("**Type:** meeting", "**Type :** réunion").replace(
    "Conversation profile", "Profil de la conversation")


def _profil(type_: str | None, corps: str = "") -> str:
    """Un profil minimal : un type (ou aucun), un résumé, des thèmes, puis le transcript."""
    ligne_type = f"**Type:** {type_}\n" if type_ is not None else ""
    return ("## 🗣️ Conversation profile\n\n" + ligne_type
            + f"*duration 07:31 · 2 speakers · 10 turns*\n\n{corps or 'Two people talk.'}\n\n**Topics:** various\n\n"
            "## Transcript\n\n**Speaker 1** [00:00:01] Hello.\n")


SPORT = ("Le match de samedi : le coach et les joueurs analysent la saison, le championnat et la défense de l'équipe "
         "après le penalty de la mi-temps.")


@pytest.fixture
def modele(monkeypatch):
    """Le classifieur remplacé par un appel qui note le message et rend la réponse voulue."""
    etat = {"appels": [], "reponse": "generique"}

    def chat(systeme, message, **options):
        etat["appels"].append(message)
        return etat["reponse"]
    monkeypatch.setattr(llm, "chat", chat)
    return etat


# ---------- 1. le type du profil d'abord ----------

@pytest.mark.parametrize("corps,langue", [(PROFIL_IS1009C, "en"), (PROFIL_FR, "fr")], ids=["en", "fr"])
def test_un_profil_de_type_reunion_donne_projet_sans_interroger_le_modele(corps, langue, modele) -> None:
    """LE cas d'IS1009c : même si le modèle répondrait « analyse_sportive », le type « meeting » décide, sans appel."""
    modele["reponse"] = "analyse_sportive"
    detail: dict = {}
    assert report.classify_genre(corps, langue, provider="mlx", envoi=None, detail=detail) == "projet"
    assert modele["appels"] == [], "le modèle n'a pas été interrogé"
    assert detail["source"] == "profil" and detail["brut"] is None


@pytest.mark.parametrize("type_", ["meeting", "Meeting", "réunion", "Réunion", "project meeting", "  meeting. ",
                                    "workshop", "Workshop", "team meeting", "réunion de travail", "atelier"])
def test_les_types_de_reunion_viennent_des_alias_de_projet(type_) -> None:
    assert templates.est_type_reunion(type_) is True


@pytest.mark.parametrize("type_", ["interview", "podcast", "sports commentary", "discussion", "", None, "analyse sportive", "sports meeting", "monologue", "debate", "presentation", "workshop sportif"])
def test_les_autres_types_ne_decident_pas(type_) -> None:
    assert templates.est_type_reunion(type_) is False


@pytest.mark.parametrize("corps,attendu", [
    (PROFIL_IS1009C, "meeting"), (PROFIL_FR, "réunion"), (_profil("interview"), "interview"), (_profil(None), None),
    ("Pas de profil du tout.\n\n## Transcript\n\n[00:00:01] Salut.\n", None)])
def test_le_type_se_lit_dans_le_bloc_de_profil(corps, attendu) -> None:
    assert report.type_du_profil(corps) == attendu


def test_un_type_absent_ou_autre_interroge_le_modele(modele) -> None:
    modele["reponse"] = "interview"
    assert report.classify_genre(_profil("interview"), "en", provider="mlx", envoi=None) == "interview"
    assert len(modele["appels"]) == 1
    modele["reponse"] = "projet"
    assert report.classify_genre(_profil(None), "en", provider="mlx", envoi=None) == "projet"
    assert len(modele["appels"]) == 2


def test_un_type_de_reunion_hors_du_bloc_de_profil_n_est_pas_lu(modele) -> None:
    """Un « **Type:** meeting » dans le transcript (une parole) ne décide rien : seul le bloc de profil compte."""
    corps = ("## Transcript\n\n**Speaker 1** [00:00:01] voici la ligne\n**Type:** meeting\n")
    assert report.type_du_profil(corps) is None


# ---------- 2. des définitions dans le message ----------

@pytest.mark.parametrize("langue,mot", [("fr", "réunion"), ("en", "meeting")])
def test_le_message_porte_une_definition_par_categorie_et_le_sport_en_dernier(langue, mot, modele) -> None:
    modele["reponse"] = "interview"
    report.classify_genre(_profil("interview"), langue, provider="mlx", envoi=None)
    message = modele["appels"][0]
    for genre in templates.GENRES:
        assert f"- {genre} :" in message, f"définition absente pour {genre}"
    assert mot in message.lower()
    assert message.index("- analyse_sportive :") > max(message.index(f"- {g} :") for g in templates.GENRES
                                                        if g != "analyse_sportive"), "le sport n'est plus en tête"


def test_sans_profil_le_message_ne_propose_ni_projet_ni_steerco(modele) -> None:
    """Le garde existant (une preuve faible n'engage pas un gabarit d'engagements) tient avec les définitions."""
    modele["reponse"] = "interview"
    report.classify_genre("## Transcript\n\n**S1** [00:00:01] Salut tout le monde.\n", "fr", provider="mlx", envoi=None)
    message = modele["appels"][0]
    assert "- projet :" not in message and "- steerco :" not in message and "- interview :" in message


# ---------- 3. le garde-fou : le sport sans mots du sport est refusé ----------

def test_analyse_sportive_sans_mot_du_domaine_est_refusee(modele) -> None:
    modele["reponse"] = "analyse_sportive"
    detail: dict = {}
    corps = _profil("discussion", "People talk about the interface of a device and its buttons.")
    assert report.classify_genre(corps, "en", provider="mlx", envoi=None, detail=detail) == templates.GENERIC
    assert detail["source"] == "garde" and detail["brut"] == "analyse_sportive"


def test_analyse_sportive_avec_des_mots_du_sport_est_gardee(modele) -> None:
    modele["reponse"] = "analyse_sportive"
    detail: dict = {}
    assert report.classify_genre(_profil("debate", SPORT), "fr", provider="mlx", envoi=None, detail=detail) == "analyse_sportive"
    assert detail["source"] == "modele"


def test_un_seul_mot_du_sport_ne_suffit_pas(modele) -> None:
    """Le garde trop laxiste : « match » seul (un match de couleurs, d'allumettes) ne fait pas une analyse sportive."""
    modele["reponse"] = "analyse_sportive"
    assert report.classify_genre(_profil("debate", "The colours match the design."), "en", provider="mlx",
                                 envoi=None) == templates.GENERIC


def test_les_mots_de_toute_reunion_ne_comptent_pas_comme_du_sport() -> None:
    """« team », « score », « game », « goal », « player », « équipe » : ensemble, ils ne font pas une analyse sportive (le garde
    exige deux mots DISTINCTS du domaine ; ces mots-là ne sont pas du domaine)."""
    texte = "Our team set a goal: the score of the usability game, the player and the équipe agree on the plan."
    assert templates.domaine_present("analyse_sportive", texte) is False


def test_un_autre_genre_n_est_pas_soumis_au_garde(modele) -> None:
    modele["reponse"] = "interview"
    assert report.classify_genre(_profil("podcast", "A host asks questions."), "en", provider="mlx", envoi=None) == "interview"


def test_le_garde_ne_se_declenche_pas_sur_le_vrai_profil_d_is1009c() -> None:
    """La non-régression : les mots du sport ne sont dans ni le profil ni le transcript d'IS1009c."""
    assert templates.domaine_present("analyse_sportive", PROFIL_IS1009C) is False


# ---------- 4. la réponse brute est gardée (bornée) ----------

def test_la_reponse_brute_est_gardee_et_bornee(modele) -> None:
    modele["reponse"] = "projet " + "x" * 500
    detail: dict = {}
    report.classify_genre(_profil("interview"), "en", provider="mlx", envoi=None, detail=detail)
    assert detail["brut"].startswith("projet x") and len(detail["brut"]) <= 200


def test_une_panne_du_classifieur_replie_sur_le_generique_et_le_dit(monkeypatch) -> None:
    def casse(*a, **k):
        raise llm.LLMError("panne")
    monkeypatch.setattr(llm, "chat", casse)
    detail: dict = {}
    assert report.classify_genre(_profil("interview"), "en", provider="mlx", envoi=None, detail=detail) == templates.GENERIC
    assert detail["source"] == "repli" and detail["brut"] is None


# ---------- 5. la compatibilité ----------

def test_la_signature_reste_compatible() -> None:
    params = inspect.signature(report.classify_genre).parameters
    assert list(params)[:6] == ["body", "language", "num_ctx", "provider", "mistral_api_key", "envoi"]
    assert params["detail"].kind is inspect.Parameter.KEYWORD_ONLY and params["detail"].default is None


def test_un_refus_de_la_regle_d_envoi_traverse_toujours(monkeypatch) -> None:
    def refuse(*a, **k):
        raise sensitivity.EnvoiRefuse()
    monkeypatch.setattr(llm, "chat", refuse)
    with pytest.raises(sensitivity.EnvoiRefuse):
        report.classify_genre(_profil("interview"), "en", provider="mlx", envoi=None)


def test_la_version_du_classifieur_change_avec_son_comportement() -> None:
    assert provenance.GENRE_CLASSIFIER_VERSION == 2


# ---------- 6. le compte rendu expose le détail, jamais dans le front matter ----------

def test_le_compte_rendu_garde_le_detail_dans_son_diagnostic_et_pas_dans_le_document(monkeypatch, tmp_path) -> None:
    from core import report as report_mod
    from core.experimental import app_report
    from tests.unit.test_cr_rubriques import _meta, _sortie
    item = {"meta": {**_meta(), "language": "fr"}, "body": "## Transcript\n\n**Alice** [00:00:10] bonjour\n"}
    monkeypatch.setattr(app_report.library, "get_transcript", lambda name: item)
    monkeypatch.setattr(app_report.library, "envoi_du_document", lambda name, meta: None)
    monkeypatch.setattr(app_report.library, "transcript_path", lambda name: None)
    monkeypatch.setattr(app_report.library, "sync_file", lambda chemin: None)
    monkeypatch.setattr(app_report.sensitivity, "controler", lambda envoi, local: False)
    monkeypatch.setattr(app_report.writer_md, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(app_report.semantic, "index_name", lambda name: 0)
    monkeypatch.setattr(app_report._pipeline, "run_stages", lambda *a, **k: _sortie())

    def classer(corps, langue, **k):
        k["detail"].update({"source": "modele", "brut": "MARQUE-BRUTE-DU-MODELE"})
        return "projet"
    monkeypatch.setattr(report_mod, "classify_genre", classer)
    resultat = app_report.make_local_report("m.md", provider="mlx")
    assert resultat["generation"]["classifieur"] == {"source": "modele", "brut": "MARQUE-BRUTE-DU-MODELE"}
    assert "MARQUE-BRUTE-DU-MODELE" not in resultat["content"]


def test_un_profil_de_type_workshop_donne_projet_sans_interroger_le_modele(modele) -> None:
    """24 des 44 profils AMI mesurés disent « workshop » : ce sont des réunions de conception."""
    modele["reponse"] = "analyse_sportive"
    corps = PROFIL_IS1009C.replace("**Type:** meeting", "**Type:** workshop")
    assert report.classify_genre(corps, "en", provider="mlx", envoi=None) == "projet"
    assert modele["appels"] == []
