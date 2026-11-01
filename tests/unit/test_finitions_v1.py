"""Finitions : langue de l'Assistant, lancement MCP sur une autre racine, rubriques vides du CR."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

import pytest

from core import rag

RACINE = Path(__file__).resolve().parents[2]

PASSAGE = {"name": "point.md", "title": "Point", "ts": "00:00:05", "text": "Budget approved.",
           "sensitivity": "standard", "project": "Non classé"}


@pytest.mark.parametrize("question, consigne, absente", [
    ("What did the team decide about the budget?", "Answer in English.", "Réponds en français."),
    ("Qu'est-ce que l'équipe a décidé pour le budget ?", "Réponds en français.", "Answer in English."),
])
def test_l_assistant_repond_dans_la_langue_de_la_question(monkeypatch, question, consigne, absente) -> None:
    """Une consigne générique ne suffit pas à un petit modèle local sur des extraits
    d'une autre langue : la langue détectée est nommée dans l'invite."""
    invites: list[str] = []
    monkeypatch.setattr(rag.semantic, "retrieve", lambda q, **k: [dict(PASSAGE)])
    monkeypatch.setattr(rag.resources, "heavy", lambda **kwargs: nullcontext())
    monkeypatch.setattr(rag.llm, "is_available", lambda *args, **kwargs: True)
    monkeypatch.setattr(rag.llm, "chat", lambda system, prompt, **k: invites.append(prompt) or "ok [1]")
    rag.ask(question, provider="mlx")
    assert len(invites) == 1 and consigne in invites[0] and absente not in invites[0]


def test_la_doc_mcp_dit_comment_servir_une_autre_racine() -> None:
    texte = (RACINE / "docs" / "mcp.md").read_text(encoding="utf-8")
    assert "python -m api.cli mcp" in texte and "core.config.ROOT" in texte


# La doc de l'ECL et celle des gabarits du CR (qui décrit les rubriques) : deux docs
# exportées, que la CI relit.
@pytest.mark.parametrize("doc", ["docs/ecl.md", "docs/REPORT_TEMPLATES.md"])
def test_la_doc_dit_qu_une_rubrique_vide_du_cr_est_omise(doc) -> None:
    assert "rubrique vide est omise" in (RACINE / doc).read_text(encoding="utf-8")
