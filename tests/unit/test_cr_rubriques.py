"""CR à rubriques : décisions, actions et questions ouvertes, tirées des unités.

Aucun CR n'avait de
rubrique décisions, actions ou problèmes, parce que le schéma d'extraction n'avait
aucun de ces types. Ici, sans modèle :

- l'extraction admet trois types de plus, `decision`, `action` (avec son porteur,
  gardé seulement s'il est dit) et `open_question`, et refuse toujours un type inconnu ;
- l'assemblage place la phrase rendue de chaque unité de ces types dans sa rubrique,
  avec sa citation, et l'enlève de sa section thématique (déplacée, pas dupliquée) ;
- les faits d'action de l'ECL complètent la rubrique Actions là où aucune unité
  d'action ne cite le même instant à ±15 s, marqués « à vérifier », sous la garde de
  contenance.
"""
from __future__ import annotations

import re

import pytest

from core import docid
from core.experimental import app_report, prompts, stages

pytestmark = pytest.mark.unit



# ---------- (1) Types d'unité ----------

def test_le_schema_et_la_consigne_admettent_les_trois_types() -> None:
    for kind in ("decision", "action", "open_question"):
        assert kind in prompts.ALLOWED_KINDS
        assert kind in prompts.EXTRACT_SYSTEM
    # Les six types d'avant restent.
    for kind in ("fact", "event", "argument", "nuance", "example", "formulation"):
        assert kind in prompts.ALLOWED_KINDS
    item = prompts.EXTRACT_SCHEMA["items"]["properties"]
    assert item["kind"]["enum"] == list(prompts.ALLOWED_KINDS)
    # `owner` n'est une clé que des unités `action` : ni dans les clés communes, ni
    # dans les propriétés de base du schéma ; une condition l'ajoute aux seules actions.
    assert "owner" not in item
    assert "Each unit has keys: id, kind, text, start, end, speaker." in prompts.EXTRACT_SYSTEM
    assert "An action unit also has the key 'owner'" in prompts.EXTRACT_SYSTEM
    condition = prompts.EXTRACT_SCHEMA["items"]["allOf"][0]
    assert condition["if"] == {"properties": {"kind": {"const": "action"}}}
    assert condition["then"]["properties"]["owner"] == {"type": ["string", "null"]}
    assert condition["else"] == {"not": {"required": ["owner"]}}


LIGNES = [
    "**Project Manager** [00:01:00] so we agree the remote is sold at twenty five euro",
    "**Project Manager** [00:01:20] the industrial designer will work on the working design",
    "**Marketing Expert** [00:02:00] I'll send the trend report tomorrow",
    "**Project Manager** [00:03:00] we still have to decide on the jog dial",
    "**User Interface Designer** [00:09:00] something unrelated much later",
]
HORODATAGES = {"00:01:00", "00:01:20", "00:02:00", "00:03:00", "00:09:00"}


def _brute(kind, text, start, owner=None, speaker=None) -> dict:
    return {"id": "u", "kind": kind, "text": text, "start": start, "owner": owner,
            "speaker": speaker}


def test_la_validation_garde_les_trois_types_et_refuse_un_type_inconnu() -> None:
    diag: dict = {}
    gardees = stages.validate_units([
        _brute("decision", "The team agrees on a selling price.", "00:01:00"),
        _brute("action", "The industrial designer works on the working design.", "00:01:20",
               owner="industrial designer"),
        _brute("open_question", "The jog dial is still to be decided.", "00:03:00"),
        _brute("todo", "Something unrelated is said much later.", "00:09:00"),
    ], HORODATAGES, diag=diag, lines=LIGNES)
    assert [u["kind"] for u in gardees] == ["decision", "action", "open_question", "fact"]
    assert diag["stage2_validate"]["bad_kind_coerced"] == 1


def test_le_porteur_d_une_action_n_est_garde_que_s_il_est_dit() -> None:
    diag: dict = {}
    gardees = stages.validate_units([
        # dit dans la parole proche : gardé tel quel
        _brute("action", "The industrial designer works on the working design.", "00:01:20",
               owner="industrial designer"),
        # personne ne le dit : None, rien n'est deviné
        _brute("action", "Someone prepares the costs.", "00:01:20", owner="Carole"),
        # engagement à la première personne : la voix qui le prend
        _brute("action", "The trend report is sent tomorrow.", "00:02:00", owner="Marketing Expert"),
        # un porteur sur un autre type n'a pas de sens : retiré, même dit 20 s plus loin
        _brute("decision", "The price is agreed.", "00:01:00", owner="industrial designer"),
        # nommé seulement à six minutes de là : hors de la fenêtre
        _brute("action", "Unrelated work is done.", "00:09:00", owner="industrial designer"),
    ], HORODATAGES, diag=diag, lines=LIGNES)
    assert [u.get("owner", "absente") for u in gardees] == [
        "industrial designer", None, "Marketing Expert", "absente", None]
    # La clé n'existe que sur les actions.
    assert ["owner" in u for u in gardees] == [True, True, True, False, True]
    assert diag["stage2_validate"]["owner_dropped"] == 3


def test_sans_les_repliques_aucun_porteur_n_est_garde() -> None:
    gardees = stages.validate_units(
        [_brute("action", "The industrial designer works.", "00:01:20", owner="industrial designer")],
        HORODATAGES, diag={})
    assert gardees[0]["kind"] == "action" and gardees[0]["owner"] is None


def test_les_strates_gardent_le_type_des_unites() -> None:
    unites = [{"gid": "g1", "kind": "decision", "start": "00:01:00", "text": "a"},
              {"gid": "g2", "kind": "open_question", "start": "00:02:00", "text": "b"}]
    tiers = stages.collect_tiers(unites, {"g1": "other", "g2": "closing"})
    assert [r["kind"] for r in tiers["secondary"]] == ["decision"]
    assert [r["kind"] for r in tiers["closing"]] == ["open_question"]


# ---------- (2) Rubriques ----------

def _u(gid, start, text, kind="fact", owner=None) -> dict:
    return {"gid": gid, "start": start, "text": text, "kind": kind, "speaker": "S1", "owner": owner}


def _sortie(language="fr") -> dict:
    units = [
        _u("g1", "00:00:30", "Le contexte du projet est présenté."),
        _u("g2", "00:01:00", "Le prix de vente est fixé à 25 euros.", "decision"),
        _u("g3", "00:01:20", "Le designer industriel prépare le design.", "action",
           owner="designer industriel"),
        _u("g4", "00:02:00", "Marie envoie le rapport.", "action", owner="Marie"),
        _u("g5", "00:03:00", "La molette reste à trancher.", "open_question"),
        _u("g6", "00:04:00", "Un point hors thème : le planning des congés.", "action"),
        _u("g7", "00:05:00", "Une remarque secondaire.", "fact"),
        _u("g8", "00:06:00", "Une décision refusée au rendu.", "decision"),
    ]
    final = {"g1": "T1", "g2": "T1", "g3": "T1", "g4": "T2", "g5": "T2", "g6": "other",
             "g7": "other", "g8": "T2"}
    rendered = {
        "sections": [
            {"theme": "T1", "label": "Prix", "unit_count": 3, "sentences": [
                {"text": "Le contexte du projet est présenté.", "support": ["g1"]},
                {"text": "Le prix de vente est arrêté à 25 euros.", "support": ["g2"]},
                {"text": "Le designer industriel préparera le design.", "support": ["g3"]}]},
            {"theme": "T2", "label": "Suite", "unit_count": 3, "sentences": [
                {"text": "Marie enverra le rapport.", "support": ["g4"]},
                {"text": "La molette reste à trancher.", "support": ["g5"]}]},
        ],
        "secondary": [{"gid": "g6", "kind": "action", "start": "00:04:00",
                       "text": "Un point hors thème : le planning des congés."},
                      {"gid": "g7", "kind": "fact", "start": "00:05:00",
                       "text": "Une remarque secondaire."}],
        "closing": [], "noise": [],
    }
    struct = {"thesis": {"text": "Le prix est le sujet.", "support": ["g2"]}, "tensions": [],
              "sections": [{"label": "Prix"}, {"label": "Suite"}]}
    diag = {"provider": "mlx", "stages": {
        "stage1_extract": {"failed_chunks": []}, "stage5_structure": {"b4_rejected": []},
        "stage6_render": {"rejected_sentences": [
            {"section": "Suite", "reason": "containment", "support": ["g8"]}]}},
        "totals": {"validated_units": 8, "main_units": 6, "secondary_units": 2,
                   "closing_units": 0, "noise_units_omitted": 0}}
    return {"struct": struct, "rendered": rendered, "units": units, "final": final,
            "themes": ["Prix", "Suite"], "diag": diag, "title": "Réunion", "language": language}


def _meta() -> dict:
    return {"id": docid.nouveau(), "meeting_id": docid.nouveau(), "project_id": docid.nouveau()}


def _sections(contenu: str) -> dict[str, str]:
    """Titre `## …` → texte de la section, dans la partie éditoriale."""
    editorial = contenu.split("<!-- memoglyph:evidence -->")[0]
    morceaux = re.split(r"^## (.+)$", editorial, flags=re.M)
    return {titre.strip(): corps for titre, corps in zip(morceaux[1::2], morceaux[2::2])}


def _titres(contenu: str) -> list[str]:
    return re.findall(r"^## (.+)$", contenu.split("<!-- memoglyph:evidence -->")[0], flags=re.M)


def test_trois_rubriques_apres_la_lecture_et_avant_les_sections() -> None:
    contenu = app_report._assemble_app_report("m.md", _meta(), "projet", _sortie())
    # « Suite » n'a plus de phrase : ses deux unités sont en rubrique.
    assert _titres(contenu) == ["Lecture (interprétation)", "Décisions", "Actions",
                                "Questions ouvertes", "Prix", "Autres points"]


def test_chaque_ligne_est_la_phrase_rendue_avec_sa_citation() -> None:
    s = _sections(app_report._assemble_app_report("m.md", _meta(), "projet", _sortie()))
    assert s["Décisions"].strip() == "- Le prix de vente est arrêté à 25 euros. [source 00:01:00]"
    assert s["Questions ouvertes"].strip() == "- La molette reste à trancher. [source 00:03:00]"
    actions = s["Actions"].strip().splitlines()
    # Le porteur figure déjà dans la phrase : il n'est pas répété.
    assert actions == ["- Le designer industriel préparera le design. [source 00:01:20]",
                       "- Marie enverra le rapport. [source 00:02:00]",
                       "- Un point hors thème : le planning des congés. [source 00:04:00]"]


def test_une_unite_en_rubrique_est_deplacee_pas_dupliquee() -> None:
    contenu = app_report._assemble_app_report("m.md", _meta(), "projet", _sortie())
    editorial = contenu.split("<!-- memoglyph:evidence -->")[0]
    for phrase in ("Le prix de vente est arrêté à 25 euros.", "Marie enverra le rapport.",
                   "La molette reste à trancher.", "le planning des congés"):
        assert editorial.count(phrase) == 1, phrase
    s = _sections(contenu)
    assert s["Prix"].strip() == "Le contexte du projet est présenté. [source 00:00:30]"
    assert "Suite" not in s  # toutes ses phrases sont en rubrique : la section vide est omise
    assert s["Autres points"].strip() == "- Une remarque secondaire. [source 00:05:00]"
    # La preuve liste toujours chaque unité citée, une fois.
    preuves = contenu.split("<!-- memoglyph:evidence -->")[1].split(
        "<!-- memoglyph:generated-notices -->")[0]
    for gid_start in ("00:01:00] decision", "00:01:20] action", "00:03:00] open_question",
                      "00:04:00] action"):
        assert preuves.count(gid_start) == 1, gid_start


def test_le_decompte_des_unites_non_rendues_ne_change_pas() -> None:
    contenu = app_report._assemble_app_report("m.md", _meta(), "projet", _sortie())
    # g8, décision refusée au rendu, reste déclarée ; aucune unité en rubrique ne l'est.
    assert "units_not_rendered: 1" in contenu
    notices = contenu.split("<!-- memoglyph:generated-notices -->")[1]
    assert notices.count(" · ") == 1 and "- 00:06:00 · decision" in notices
    assert "Une décision refusée au rendu." not in contenu


def test_une_rubrique_vide_est_omise() -> None:
    sortie = _sortie()
    for u in sortie["units"]:
        if u["kind"] == "open_question":
            u["kind"] = "fact"
    contenu = app_report._assemble_app_report("m.md", _meta(), "projet", sortie)
    assert "Questions ouvertes" not in _titres(contenu)
    assert "La molette reste à trancher. [source 00:03:00]" in _sections(contenu)["Suite"]


def test_sans_unite_de_ces_types_aucune_rubrique() -> None:
    sortie = _sortie()
    for u in sortie["units"]:
        u["kind"] = "fact"
    for r in sortie["rendered"]["secondary"]:
        r["kind"] = "fact"
    titres = _titres(app_report._assemble_app_report("m.md", _meta(), "projet", sortie))
    assert not {"Décisions", "Actions", "Questions ouvertes"} & set(titres)


def test_les_intitules_suivent_la_langue_du_cr() -> None:
    sortie = _sortie("en")
    titres = _titres(app_report._assemble_app_report("m.md", _meta(), "projet", sortie))
    assert titres[1:4] == ["Decisions", "Actions", "Open questions"]


def test_le_porteur_dit_est_ajoute_quand_la_phrase_ne_le_porte_pas() -> None:
    sortie = _sortie()
    sortie["rendered"]["sections"][1]["sentences"][0]["text"] = "Le rapport sera envoyé."
    s = _sections(app_report._assemble_app_report("m.md", _meta(), "projet", sortie))
    assert "- Le rapport sera envoyé. (porteur : Marie) [source 00:02:00]" in s["Actions"]
    sortie["language"] = "en"
    s = _sections(app_report._assemble_app_report("m.md", _meta(), "projet", sortie))
    assert "- Le rapport sera envoyé. (owner: Marie) [source 00:02:00]" in s["Actions"]


# ---------- Découplage ECL / CR ----------

def test_le_compte_rendu_n_appelle_plus_l_ecl(monkeypatch, tmp_path) -> None:
    """Les faits de la réunion se lancent à part (onglet « Faits ») ; le CR n'en dépend plus."""
    from core import ecl as ecl_mod
    from core import report as report_mod

    appels: list[str] = []
    item = {"meta": {**_meta(), "language": "fr"},
            "body": "## Transcript\n\n**Alice** [00:00:10] bonjour\n"}
    monkeypatch.setattr(app_report.library, "get_transcript", lambda name: item)
    monkeypatch.setattr(app_report.library, "envoi_du_document", lambda name, meta: None)
    monkeypatch.setattr(app_report.library, "transcript_path", lambda name: None)
    monkeypatch.setattr(app_report.library, "sync_file", lambda chemin: None)
    monkeypatch.setattr(app_report.sensitivity, "controler", lambda envoi, local: False)
    monkeypatch.setattr(app_report.writer_md, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(app_report.semantic, "index_name", lambda name: 0)
    monkeypatch.setattr(app_report._pipeline, "run_stages", lambda *a, **k: _sortie())
    monkeypatch.setattr(report_mod, "classify_genre", lambda *a, **k: "projet")
    monkeypatch.setattr(ecl_mod, "extraire", lambda *a, **k: appels.append("extraire") or {})
    monkeypatch.setattr(ecl_mod, "lire_fichier", lambda *a, **k: appels.append("lire_fichier"))

    resultat = app_report.make_local_report("m.md", provider="mlx")
    assert appels == []
    assert resultat["warnings"] is None
    s = _sections(resultat["content"])
    # Les trois rubriques viennent des seules unités.
    assert {"Décisions", "Actions", "Questions ouvertes"} <= set(s)
    assert "Fait de la réunion" not in resultat["content"]


def test_l_assemblage_n_a_plus_de_faits_de_l_ecl() -> None:
    import inspect

    assert "faits_ecl" not in inspect.signature(app_report._assemble_app_report).parameters
    for nom in ("_actions_de_l_ecl", "_marque_ecl", "ECL_MARGE_S"):
        assert not hasattr(app_report, nom), nom


# ---------- Reprise : fragments et doublons de l'extraction ----------

LIGNES_DOUBLONS = [
    "**Project Manager** [00:01:00] so we agree the remote is sold at twenty five euro",
    "**Project Manager** [00:01:10] yes we agree, twenty five euro it is",
    "**Marketing Expert** [00:01:40] decide now",
    "**Marketing Expert** [00:03:00] something else entirely",
]
TS_DOUBLONS = {"00:01:00", "00:01:10", "00:01:40", "00:03:00"}


def test_une_unite_typee_de_moins_de_trois_mots_redevient_un_fait() -> None:
    diag: dict = {}
    gardees = stages.validate_units([
        _brute("decision", "Decide now.", "00:01:40"),
        _brute("open_question", "Why?", "00:01:40"),
        _brute("action", "Mm-hmm. Mm-hmm.", "00:01:40", owner="Marketing Expert"),
        _brute("decision", "We decide that.", "00:01:40"),     # trois mots : gardée
        _brute("fact", "Ok.", "00:03:00"),                     # un fait court reste un fait, non compté
    ], TS_DOUBLONS, diag=diag, lines=LIGNES_DOUBLONS)
    assert [u["kind"] for u in gardees] == ["fact", "fact", "fact", "decision", "fact"]
    assert not any("owner" in u for u in gardees)               # plus une action : pas de porteur
    assert diag["stage2_validate"]["rubric_fragments_to_fact"] == 3


def test_le_fait_double_d_une_unite_typee_a_15_s_est_retire_et_compte() -> None:
    diag: dict = {}
    gardees = stages.validate_units([
        _brute("fact", "The remote price is agreed!", "00:01:00"),
        _brute("decision", "The remote price is agreed.", "00:01:10"),   # 10 s, même énoncé
        _brute("fact", "The remote price is agreed", "00:03:00"),        # 2 min : un autre moment
        _brute("fact", "Something else is said by all.", "00:01:10"),    # autre énoncé
    ], TS_DOUBLONS, diag=diag, lines=LIGNES_DOUBLONS)
    assert [(u["kind"], u["start"]) for u in gardees] == [
        ("decision", "00:01:10"), ("fact", "00:03:00"), ("fact", "00:01:10")]
    v = diag["stage2_validate"]
    assert v["fact_duplicates_removed"] == 1
    assert v["fact_duplicates_units"] == [{"start": "00:01:00", "text": "The remote price is agreed!",
                                           "kept_kind": "decision", "kept_start": "00:01:10"}]
    # Les gid restent contigus après le retrait.
    assert [u["gid"] for u in gardees] == ["g1", "g2", "g3"]


def test_au_meme_horodatage_l_unite_typee_l_emporte_sur_le_fait_venu_avant() -> None:
    diag: dict = {}
    gardees = stages.validate_units([
        _brute("fact", "The remote is sold at 25 euro.", "00:01:00"),
        _brute("decision", "The remote is sold at 25 euro.", "00:01:00"),
    ], TS_DOUBLONS, diag=diag, lines=LIGNES_DOUBLONS)
    assert [u["kind"] for u in gardees] == ["decision"]
    assert diag["stage2_validate"]["fact_duplicates_removed"] == 1
    assert diag["stage2_validate"]["exact_dups_removed"] == 0


def test_la_normalisation_des_doublons() -> None:
    assert stages.enonce_normalise("L'écran  LCD, est retiré !") == stages.enonce_normalise("l ecran lcd est retire")
    assert stages.enonce_normalise("Prix : 25 €") != stages.enonce_normalise("Prix : 26 €")


def test_les_faits_retires_comme_doublons_sont_declares_dans_le_cr() -> None:
    sortie = _sortie()
    sortie["diag"]["stages"]["stage2_validate"] = {"fact_duplicates_removed": 2,
                                                   "rubric_fragments_to_fact": 1}
    notices = app_report._assemble_app_report("m.md", _meta(), "projet", sortie).split(
        "<!-- memoglyph:generated-notices -->")[1]
    assert ("- 2 unité(s) de type fait retirée(s) : même énoncé qu'une décision, une action ou une "
            "question ouverte à 15 s près.") in notices
    sortie["language"] = "en"
    notices = app_report._assemble_app_report("m.md", _meta(), "projet", sortie).split(
        "<!-- memoglyph:generated-notices -->")[1]
    assert ("- 2 fact unit(s) removed: same statement as a decision, action or open question "
            "within 15 s.") in notices


# ---------- Reprise : bornes de sortie de l'extraction ----------

# Marge d'un profil = jetons de sortie permis par jeton d'entrée d'une tranche
# (`extract_max_tokens / chunk_tokens`). Trois
# tranches denses de ~2 040 jetons d'entrée ont dépassé 3 500 jetons de sortie avec les
# nouveaux types : une densité d'au moins 3 500 / 2 040 = 1,72. La marge locale est
# portée à 5 000 / 2 000 = 2,5, et aucun profil cloud ne doit en laisser moins.
DENSITE_TRONQUEE = 3500 / 2040


def test_les_bornes_de_sortie_de_l_extraction_laissent_la_meme_marge_partout() -> None:
    from core.experimental import profiles

    def marge(profil) -> float:
        return profil.extract_max_tokens / profil.chunk_tokens

    assert profiles.CONSERVATIVE.extract_max_tokens == 5000
    assert marge(profiles.CONSERVATIVE) == 2.5 > DENSITE_TRONQUEE
    for profil in (profiles.CONSERVATIVE, profiles.LARGE_CONTEXT):
        assert marge(profil) >= marge(profiles.CONSERVATIVE), profil.name
