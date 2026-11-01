"""Le H1 d'un compte rendu porte le titre lisible de la réunion, pas le slug.

Les deux générateurs écrivaient « # Compte-rendu — <slug> » : le titre lu dans le
front-matter du transcript est `slugify(source.stem)`. Ils passent maintenant par
`library.titre_lisible` (titre saisi, sinon nom de la source). Aucun appel modèle :
les étapes sont simulées.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core import docid, library, llm, projects
from core.experimental import app_report, pipeline, stages

pytestmark = pytest.mark.unit

SOURCE = "AMI IS1009a, réunion de lancement d'une télécommande.wav"
SLUG = "ami-is1009a-réunion-de-lancement-dune-télécommande"
LISIBLE = "AMI IS1009a, réunion de lancement d'une télécommande"
CORPS = ("## Transcript\n\n**Speaker 1** [00:00:01] On fixe le prix de vente.\n"
         "**Speaker 2** [00:00:05] D'accord pour vingt-cinq euros.\n")


def _h1(contenu: str) -> str:
    _meta, corps = library._split_frontmatter(contenu)
    return next(ligne for ligne in corps.splitlines() if ligne.startswith("# "))


@pytest.fixture
def transcript_importe(isolated_stores, monkeypatch) -> str:
    from core import semantic

    monkeypatch.setattr(semantic, "index_name", lambda *a, **k: None)
    sortie = Path(isolated_stores["output_dir"])
    projects.ensure_inbox(sortie)
    nom = f"{SLUG}.md"
    (sortie / nom).write_text(
        f"---\nid: {docid.nouveau()}\nmeeting_id: {docid.nouveau()}\n"
        f"project_id: {projects.load(sortie).id}\ntitle: {SLUG}\nsource: \"{SOURCE}\"\n"
        f"language: fr\n---\n\n{CORPS}", encoding="utf-8")
    library.reindex()
    return nom


def test_le_cr_herite_ecrit_le_titre_lisible_dans_son_h1(monkeypatch, transcript_importe) -> None:
    from core import report

    monkeypatch.setattr(report.llm, "is_available", lambda **k: True)
    monkeypatch.setattr(report.llm, "chat_json", lambda *a, **k: {"sections": []})
    monkeypatch.setattr(report.llm, "chat", lambda *a, **k: "generique")
    monkeypatch.setattr(report.report_citations, "render_structured",
                        lambda *a, **k: "## Décisions\n\n- Prix fixé [source 00:00:05]\n")

    resultat = report.make_report(transcript_importe)

    assert _h1(resultat["content"]) == f"# Compte-rendu — {LISIBLE}"
    meta, _ = library._split_frontmatter(resultat["content"])
    assert meta["title"] == f"{LISIBLE} — compte-rendu"


def _etapes_simulees(monkeypatch) -> None:
    """Le strict nécessaire pour que `run_stages` aille au bout sans modèle."""
    monkeypatch.setattr(llm, "is_available", lambda **k: True)
    monkeypatch.setattr(llm, "modele_servi", lambda p: "modele-test")

    def extraire(chunks, *, diag, **_k):
        diag["stage1_extract"] = {"calls": [], "in_tok_est": 0, "out_tok_est": 0,
                                  "failed_chunks": []}
        return [{"gid": "g1", "start": "00:00:05", "text": "Prix fixé.", "kind": "fact",
                 "speaker": "S1"}]

    def classer(units, themes, *, diag, **_k):
        diag["stage3_classify"] = {"calls": []}
        return {"g1": "other"}

    def rendre(units, final, themes, *, diag, **_k):
        diag["stage6_render"] = {"secondary_units": 1, "closing_units": 0,
                                 "noise_units_omitted": 0, "kept_sentences": 0,
                                 "rejected_sentences": [], "out_tok_est": 0}
        return {"sections": [], "closing": [], "noise": [],
                "secondary": [{"gid": "g1", "kind": "fact", "start": "00:00:05",
                               "text": "Prix fixé."}]}

    def structurer(*_a, diag, **_k):
        diag["stage5_structure"] = {"b4_rejected": []}
        return {"source_shape": "thematic", "thesis": {}, "tensions": [], "sections": []}

    monkeypatch.setattr(stages, "extract_atomic", extraire)
    monkeypatch.setattr(stages, "validate_units", lambda raw, *_a, **_k: raw)
    monkeypatch.setattr(stages, "classify_units", classer)
    monkeypatch.setattr(stages, "apply_veto", lambda classified, *_a, **_k: (classified, None))
    monkeypatch.setattr(stages, "structure", structurer)
    monkeypatch.setattr(stages, "render_and_guard", rendre)


def test_le_cr_de_l_application_ecrit_le_titre_lisible_dans_son_h1(monkeypatch) -> None:
    _etapes_simulees(monkeypatch)
    meta = {"id": docid.nouveau(), "meeting_id": docid.nouveau(), "project_id": docid.nouveau(),
            "title": SLUG, "source": SOURCE, "language": "fr"}

    sortie = pipeline.run_stages(f"{SLUG}.md", {"meta": meta, "body": CORPS},
                                 provider="mistral", seed=42)
    contenu = app_report._assemble_app_report(f"{SLUG}.md", meta, "generique", sortie)

    assert _h1(contenu) == f"# Compte-rendu — {LISIBLE}"
    front, _ = library._split_frontmatter(contenu)
    assert front["title"] == f"{LISIBLE} — compte-rendu"
