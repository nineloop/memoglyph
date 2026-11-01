"""Seam d'intégration du CR LOCAL riche dans l'app.

Couvre le routage `report.generate_report` (UNIFIÉ : local ET cloud → riche) et
l'assemblage UI-compatible du CR local (jetons `[source HH:MM:SS]` cliquables,
marqueurs evidence/notices, strates MAIN/SECONDARY/CLOSING, aucune perte).
Aucun LLM : on teste le routage et l'assemblage déterministe.
"""
from __future__ import annotations

import re

from core import docid, report
from core.experimental import app_report


# ---------- routage du seam ----------
def test_generate_report_route_le_local_vers_le_riche(monkeypatch):
    appels = {}

    def _riche(name, **kw):
        appels["riche"] = name
        return {"name": "cr.md"}

    def _herite(*a, **k):
        appels["herite"] = True
        return {"name": "herite.md"}

    monkeypatch.setattr("core.llm.est_local", lambda p: True)
    monkeypatch.setattr(app_report, "make_local_report", _riche)
    monkeypatch.setattr(report, "make_report", _herite)
    out = report.generate_report("m.md", provider="mlx")
    assert appels.get("riche") == "m.md" and "herite" not in appels
    assert out == {"name": "cr.md"}


def test_generate_report_route_le_cloud_vers_le_riche(monkeypatch):
    # Bascule production (unification) : Mistral/cloud passe DÉSORMAIS par le CR
    # riche, comme le local — un seul contrat produit. Le legacy `make_report`
    # reste présent (repli) mais n'est plus le chemin normal.
    appels = {}

    def _riche(name, **kw):
        appels["riche"] = name
        appels["provider"] = kw.get("provider")
        return {"name": "cr.md"}

    def _herite(*a, **k):
        appels["herite"] = True
        return {"name": "herite.md"}

    monkeypatch.setattr("core.llm.est_local", lambda p: False)   # cloud
    monkeypatch.setattr(app_report, "make_local_report", _riche)
    monkeypatch.setattr(report, "make_report", _herite)
    out = report.generate_report("m.md", provider="mistral")
    assert appels.get("riche") == "m.md" and "herite" not in appels  # cloud → riche
    assert appels.get("provider") == "mistral"                        # provider transmis
    assert out == {"name": "cr.md"}


def test_make_report_herite_reste_disponible_en_repli():
    # Le legacy n'est PAS supprimé : il reste appelable (repli/référence).
    assert callable(report.make_report)


# ---------- assemblage UI-compatible ----------
def _u(gid, start, text, kind="fact"):
    return {"gid": gid, "start": start, "text": text, "kind": kind, "speaker": "S1"}


def _synthetic_out():
    units = [
        _u("g1", "00:00:05", "Le climat se réchauffe globalement."),
        _u("g2", "00:01:10", "Un point secondaire hors thème principal."),
        _u("g3", "00:07:53", "La foudre s'abat sur un immeuble."),  # clôture
        _u("g4", "00:08:20", "Voilà, merci."),                      # bruit
    ]
    final = {"g1": "T1", "g2": "other", "g3": "closing", "g4": "dialogic_noise"}
    struct = {"source_shape": "thematic",
              "thesis": {"text": "Le réchauffement s'accélère.", "support": ["g1"]},
              "tensions": [], "sections": [{"label": "Climat", "support": ["g1"]}]}
    rendered = {
        "sections": [{"theme": "T1", "label": "Climat",
                      "sentences": [{"text": "Le climat se réchauffe globalement.",
                                     "support": ["g1"]}], "unit_count": 1}],
        "secondary": [{"gid": "g2", "kind": "fact", "start": "00:01:10",
                       "text": "Un point secondaire hors thème principal."}],
        "closing": [{"gid": "g3", "kind": "fact", "start": "00:07:53",
                     "text": "La foudre s'abat sur un immeuble."}],
        "noise": [{"gid": "g4", "kind": "fact", "start": "00:08:20", "text": "Voilà, merci."}],
    }
    diag = {"provider": "mlx", "model": "qwen", "stages": {
        "stage1_extract": {"failed_chunks": []},
        "stage5_structure": {"b4_rejected": []},
        "stage6_render": {"rejected_sentences": []}},
        "totals": {"validated_units": 4, "main_units": 1, "secondary_units": 1,
                   "closing_units": 1, "noise_units_omitted": 1}}
    return {"struct": struct, "rendered": rendered, "units": units, "final": final,
            "themes": ["Climat"], "diag": diag, "title": "Réunion", "language": "fr"}


def _meta():
    ident = docid.nouveau()
    return {"id": ident, "meeting_id": docid.nouveau(), "project_id": docid.nouveau(),
            "pipeline": None}


def test_assemblage_jetons_source_cliquables_et_marqueurs():
    content = app_report._assemble_app_report("m.md", _meta(), "generique", _synthetic_out())
    # front-matter compatible bibliothèque/UI
    assert "type: report" in content
    assert re.search(r"^report_engine: local_hybrid_v1", content, re.M)
    # jetons INLINE cliquables (le SEUL format que l'UI reconnaît)
    assert "[source 00:00:05]" in content  # MAIN
    assert "[source 00:01:10]" in content  # SECONDARY
    assert "[source 00:07:53]" in content  # CLOSING
    # marqueurs de section attendus par la vue rapport
    assert "<!-- memoglyph:evidence -->" in content
    assert "<!-- memoglyph:generated-notices -->" in content
    # strates visibles
    assert "## Autres points" in content
    assert "## Clôture / autres séquences" in content
    # interprétation visiblement distinguée
    assert "(interprétation)" in content
    # éditorial (jetons cliquables) AVANT le marqueur evidence
    editorial = content.split("<!-- memoglyph:evidence -->")[0]
    assert "[source 00:00:05]" in editorial and "[source 00:07:53]" in editorial


def test_assemblage_aucune_perte_de_contenu_valide():
    out = _synthetic_out()
    content = app_report._assemble_app_report("m.md", _meta(), "generique", out)
    body_before_notices = content.split("<!-- memoglyph:generated-notices -->")[0]
    # g1/g2/g3 (MAIN/SECONDARY/CLOSING) rendus dans le corps ; g4 (bruit) déclaré
    for g, ts in [("g1", "00:00:05"), ("g2", "00:01:10"), ("g3", "00:07:53")]:
        assert f"[source {ts}]" in body_before_notices, f"{g} perdu du corps"
    # g4 = bruit : hors corps MAIS tracé dans les notices (jamais perdu en silence)
    notices = content.split("<!-- memoglyph:generated-notices -->")[1]
    assert "00:08:20" in notices and "bruit dialogique" in notices.lower()


def test_assemblage_declare_les_tranches_en_echec():
    out = _synthetic_out()
    out["diag"]["stages"]["stage1_extract"]["failed_chunks"] = [3]
    content = app_report._assemble_app_report("m.md", _meta(), "generique", out)
    assert "map_chunks_failed:" in content         # réserve surfacée par l'aside qualité
    assert "en échec : [3]" in content             # déclaré en transparence


def test_assemblage_herite_la_sensibilite_du_transcript():
    # PRÉSERVATION à travers les versions : le CR reprend la sensibilité de son
    # transcript source dans son front-matter. MÉTADONNÉE, sans effet génération.
    meta = {**_meta(), "sensitivity": "confidential"}
    content = app_report._assemble_app_report("m.md", meta, "generique", _synthetic_out())
    assert re.search(r"^sensitivity: confidential$", content, re.M)


def test_assemblage_sensibilite_absente_retombe_sur_standard():
    # Un transcript SANS le champ (créé avant) → le CR déclare `standard`.
    content = app_report._assemble_app_report("m.md", _meta(), "generique", _synthetic_out())
    assert re.search(r"^sensitivity: standard$", content, re.M)


def test_l_audit_lit_les_marqueurs_memoglyph_du_cr():
    """L'annexe des preuves et les réserves se trouvent derrière les marqueurs
    `memoglyph:` : un marqueur inconnu change l'audit."""
    from core import report_citations
    actuel = app_report._assemble_app_report("m.md", _meta(), "generique", _synthetic_out())
    assert report_citations.EVIDENCE_MARKER in actuel
    inconnu = actuel.replace("<!-- memoglyph:", "<!-- autre:")
    transcript = "[00:00:05] Le climat se réchauffe.\n[00:01:10] Un point secondaire.\n"
    assert report_citations.audit(inconnu, transcript) != report_citations.audit(actuel, transcript)
