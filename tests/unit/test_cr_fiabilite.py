"""Fiabilité du compte-rendu (audit externe, points A1 et A2).

A1 : le CR lit tout transcript que `writer_md.render_markdown` écrit vraiment, et
refuse avant tout appel au modèle celui qui n'a pas d'horodatage.
A2 : aucune unité du corps ne disparaît en silence du rendu, et un CR qui échoue
pendant une transcription laisse une réserve durable dans le front-matter.
"""
from __future__ import annotations

import pytest

from core import docid, library, writer_md
from core.experimental import app_report
from core.experimental.chunking import TranscriptNonCitable, transcript_lines, verifier_citable
from core.stt_base import Segment, Transcript

pytestmark = pytest.mark.unit

TEXTES = ("On fixe le prix à vingt-cinq euros.", "Le budget reste à douze cinquante.",
          "Prochaine réunion jeudi.")


def _rendu(*, diarise: bool, horodatage: bool, locuteurs=("Speaker 1", "Speaker 2", None)) -> str:
    segments = [Segment(start=10.0 * i, end=10.0 * i + 5, text=texte,
                        speaker=locuteurs[i] if diarise else None)
                for i, texte in enumerate(TEXTES)]
    transcript = Transcript(segments=segments, full_text=" ".join(TEXTES), language="fr")
    contenu = writer_md.render_markdown(transcript, {"title": "Essai"},
                                        timestamps=horodatage, diarized=diarise)
    return contenu.split("\n---\n", 1)[1]


# ---------- A1 : tous les formats réellement écrits ----------

@pytest.mark.parametrize("diarise, locuteurs, attendus", [
    (True, ("Speaker 1", "Speaker 2", None), ["**Speaker 1**", "**Speaker 2**", "**Speaker ?**"]),
    (True, ("Alice", "Bruno Martin", "Speaker 3"), ["**Alice**", "**Bruno Martin**", "**Speaker 3**"]),
    (False, ("Speaker 1", "Speaker 2", None), ["", "", ""]),
])
def test_chaque_replique_horodatee_est_lue(diarise, locuteurs, attendus) -> None:
    corps = _rendu(diarise=diarise, horodatage=True, locuteurs=locuteurs)

    lignes, horodatages = transcript_lines(corps)

    assert horodatages == {"00:00:00", "00:00:10", "00:00:20"}
    assert len(lignes) == len(TEXTES)
    for ligne, prefixe, texte, ts in zip(lignes, attendus, TEXTES, sorted(horodatages)):
        assert ligne == (f"{prefixe} [{ts}] {texte}" if prefixe else f"[{ts}] {texte}")
    verifier_citable(corps)


@pytest.mark.parametrize("diarise", [True, False])
def test_un_transcript_sans_horodatage_est_refuse(diarise) -> None:
    corps = _rendu(diarise=diarise, horodatage=False)

    assert transcript_lines(corps) == ([], set())
    with pytest.raises(TranscriptNonCitable, match="horodatage"):
        verifier_citable(corps)


def test_le_bloc_de_profil_n_est_pas_pris_pour_une_replique() -> None:
    corps = ("# Essai\n\n## Profil\n\n**Thèmes :** prix · budget\n- [00:00:05] repère\n\n"
             "## Transcript\n\n[00:00:10] Seule réplique.\n")

    assert transcript_lines(corps)[0] == ["[00:00:10] Seule réplique."]


def test_le_cr_n_appelle_jamais_le_modele_sur_un_transcript_sans_horodatage(monkeypatch) -> None:
    corps = _rendu(diarise=False, horodatage=False)
    appels = []
    monkeypatch.setattr(app_report.library, "get_transcript",
                        lambda name: {"meta": {"id": docid.nouveau()}, "body": corps})
    monkeypatch.setattr(app_report._pipeline, "run_stages",
                        lambda *a, **k: appels.append("run_stages"))

    with pytest.raises(TranscriptNonCitable):
        app_report.make_local_report("essai.md")
    assert appels == []


# ---------- A2 : unités non rendues ----------

def _u(gid, start, kind="fact"):
    return {"gid": gid, "start": start, "text": f"texte réécrit de {gid}", "kind": kind, "speaker": None}


def _sortie(final, sections):
    units = [_u("g1", "00:00:05"), _u("g2", "00:01:00", "argument"), _u("g3", "00:02:30", "event")]
    diag = {"provider": "mlx", "stages": {
        "stage1_extract": {"failed_chunks": []}, "stage5_structure": {"b4_rejected": []},
        "stage6_render": {"rejected_sentences": []}},
        "totals": {"validated_units": 3, "main_units": 3, "secondary_units": 0,
                   "closing_units": 0, "noise_units_omitted": 0}}
    return {"struct": {"sections": [{"label": "Prix"}]},
            "rendered": {"sections": sections, "secondary": [], "closing": [], "noise": []},
            "units": units, "final": final, "themes": ["Prix"], "diag": diag,
            "title": "Essai", "language": "fr"}


def _meta():
    return {"id": docid.nouveau(), "meeting_id": docid.nouveau(), "project_id": docid.nouveau()}


def test_une_unite_du_corps_non_rendue_est_declaree_avec_son_horodatage() -> None:
    # g2 : phrase refusée par le garde (aucune phrase ne la cite) ; g3 : classée
    # dans un thème T4 qui n'a aucune section rendue.
    sortie = _sortie({"g1": "T1", "g2": "T1", "g3": "T4"},
                     [{"theme": "T1", "label": "Prix", "unit_count": 2,
                       "sentences": [{"text": "Le prix est fixé.", "support": ["g1"]}]}])

    contenu = app_report._assemble_app_report("essai.md", _meta(), "projet", sortie)

    notices = contenu.split("<!-- memoglyph:generated-notices -->")[1]
    assert "## Unités non rendues" in notices
    assert "- 00:01:00 · argument" in notices and "- 00:02:30 · event" in notices
    assert "00:00:05 · fact" not in notices
    assert "2 unité(s) du corps non rendue(s)" in notices
    assert "units_not_rendered: 2" in contenu
    # Le texte réécrit par le modèle n'a pas passé le garde : il n'est pas repris.
    assert "texte réécrit de g2" not in contenu and "texte réécrit de g3" not in contenu


def test_rien_a_declarer_quand_tout_est_rendu() -> None:
    sortie = _sortie({"g1": "T1", "g2": "T1", "g3": "T1"},
                     [{"theme": "T1", "label": "Prix", "unit_count": 3,
                       "sentences": [{"text": "Tout est dit.", "support": ["g1", "g2", "g3"]}]}])

    contenu = app_report._assemble_app_report("essai.md", _meta(), "projet", sortie)

    assert "Unités non rendues" not in contenu and "units_not_rendered" not in contenu


# ---------- A2 : réserve durable dans le front-matter ----------

def test_un_champ_du_front_matter_se_pose_et_se_retire_corps_intact(tmp_path) -> None:
    cible = tmp_path / "t.md"
    cible.write_text("---\ntitle: Essai\nsensitivity: standard\n---\n\n## Transcript\n\n[00:00:01] Bonjour.\n",
                     encoding="utf-8")

    assert library.ecrire_champ_meta(cible, "report_error", "Le modèle ne répond pas.")
    assert "report_error: Le modèle ne répond pas." in cible.read_text(encoding="utf-8")
    assert not library.ecrire_champ_meta(cible, "report_error", "Le modèle ne répond pas.")

    assert library.ecrire_champ_meta(cible, "report_error", None)
    texte = cible.read_text(encoding="utf-8")
    assert "report_error" not in texte and "sensitivity: standard" in texte
    assert texte.endswith("## Transcript\n\n[00:00:01] Bonjour.\n")
    assert [p.name for p in tmp_path.iterdir()] == ["t.md"]


def test_un_cr_reussi_leve_la_reserve_du_transcript(monkeypatch, tmp_path, isolated_stores) -> None:
    # Le CR réussi lance aussi l'extraction ECL : sortie et index temporaires.
    source = tmp_path / "essai.md"
    source.write_text("---\ntitle: Essai\nreport_error: Le modèle ne répond pas.\n---\n\n"
                      + _rendu(diarise=False, horodatage=True), encoding="utf-8")
    item = {"meta": _meta(), "body": source.read_text(encoding="utf-8")}
    sortie = _sortie({"g1": "T1"}, [{"theme": "T1", "label": "Prix", "unit_count": 1,
                                      "sentences": [{"text": "Prix fixé.", "support": ["g1"]}]}])
    sortie["diag"]["totals"].update(kept_sentences=1)
    from core import report as report_mod
    monkeypatch.setattr(app_report.library, "get_transcript", lambda name: item)
    monkeypatch.setattr(app_report.library, "transcript_path", lambda name: source)
    monkeypatch.setattr(app_report.library, "sync_file", lambda path: None)
    monkeypatch.setattr(app_report.semantic, "index_name", lambda name: 0)
    monkeypatch.setattr(app_report._pipeline, "run_stages", lambda *a, **k: sortie)
    monkeypatch.setattr(report_mod, "classify_genre", lambda *a, **k: "projet")

    resultat = app_report.make_local_report("essai.md")

    assert (tmp_path / resultat["name"]).exists()
    assert "report_error" not in source.read_text(encoding="utf-8")
