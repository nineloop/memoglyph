"""Langue de l'Assistant : une consigne explicite seulement quand le français ou l'anglais est établi.

`rag.langue_de` ne lit que la question, sans modèle. Elle rend « fr » ou « en » quand la
langue est nettement établie, et s'abstient (None) sinon : langue inconnue, question
courte, noms propres et vocabulaire technique sans mots outils, mélange de langues.
L'abstention laisse la consigne générale seule (« dans la langue de la question ») : ni
la langue de l'interface, ni celle des extraits ne la remplacent.
"""
from __future__ import annotations

from contextlib import nullcontext

import pytest

from core import rag

pytestmark = pytest.mark.unit

CONSIGNES = ("Answer in English.", "Réponds en français.")


@pytest.mark.parametrize("question, attendue", [
    ("What did the team decide about the budget?", "en"),
    ("Who is responsible for the marketing plan?", "en"),
    ("Why did the CI pipeline fail on main?", "en"),
    ("What did Jean-Pierre say about Le Monde?", "en"),
    ("Qu'est-ce que l'équipe a décidé pour le budget ?", "fr"),
    ("Quelles sont les décisions sur le budget ?", "fr"),
    ("Quelle est la date de la prochaine réunion ?", "fr"),
    ("Pourquoi le build CI a échoué sur la branche main ?", "fr"),
    ("Qui a parlé du budget marketing ?", "fr"),
])
def test_francais_et_anglais_etablis_gardent_leur_consigne(question, attendue) -> None:
    assert rag.langue_de(question) == attendue


@pytest.mark.parametrize("question", [
    "Was ist die Entscheidung?",
    "Quali sono le decisioni sul budget?",
    "¿Cuál es la fecha de la próxima reunión?",
    "Qual é a data da próxima reunião?",
    "Wat is de beslissing over het budget?",
    "Каково решение по бюджету?",
    "予算の決定は何ですか？",
])
def test_une_autre_langue_ne_recoit_aucune_consigne_forcee(question) -> None:
    assert rag.langue_de(question) is None


@pytest.mark.parametrize("question", [
    "", "   ", "Budget ?", "Le budget ?", "Who spoke?", "Qui parle ?", "Pourquoi pas ?", "What about?",
    "Kubernetes Terraform Jenkins ?", "Alice, Bruno, Carole ?", "Q3 KPI OKR 2026",
])
def test_question_tres_courte_noms_propres_ou_technique_s_abstient(question) -> None:
    assert rag.langue_de(question) is None


@pytest.mark.parametrize("question", [
    "What est la décision sur le budget ?",
    "Quel est le planning for the launch ?",
    "Who a décidé du budget for the team ?",
])
def test_un_melange_de_langues_s_abstient(question) -> None:
    assert rag.langue_de(question) is None


def _invite_transmise(monkeypatch, question: str, passages: list[dict]) -> str:
    invites: list[str] = []
    monkeypatch.setattr(rag.semantic, "retrieve", lambda q, **k: [dict(p) for p in passages])
    monkeypatch.setattr(rag.resources, "heavy", lambda **kwargs: nullcontext())
    monkeypatch.setattr(rag.llm, "is_available", lambda *args, **kwargs: True)
    monkeypatch.setattr(rag.llm, "chat", lambda system, prompt, **k: invites.append((system, prompt)) or "ok [1]")
    rag.ask(question, provider="mlx")
    assert len(invites) == 1
    system, prompt = invites[0]
    assert "LANGUE DE LA QUESTION" in system, "la consigne générale reste"
    return prompt


def _passage(texte: str) -> dict:
    return {"name": "point.md", "title": "Point", "ts": "00:00:05", "text": texte,
            "sensitivity": "standard", "project": "Non classé"}


ALLEMAND = _passage("Die Entscheidung über das Budget ist gefallen, und der Plan wird umgesetzt.")
FRANCAIS = _passage("La décision sur le budget est prise et le plan sera mis en œuvre pour la rentrée.")


@pytest.mark.parametrize("question, passage, consigne", [
    # Des extraits dans une autre langue ne déterminent pas la langue demandée.
    ("Quelles sont les décisions sur le budget ?", ALLEMAND, "Réponds en français."),
    ("What did the team decide about the budget?", FRANCAIS, "Answer in English."),
])
def test_rag_ask_transmet_la_consigne_de_la_question_quels_que_soient_les_extraits(
        monkeypatch, question, passage, consigne) -> None:
    invite = _invite_transmise(monkeypatch, question, [passage])
    assert invite.count(consigne) == 1
    assert all(c not in invite for c in CONSIGNES if c != consigne)


@pytest.mark.parametrize("question, passage", [
    ("Was ist die Entscheidung?", FRANCAIS),
    ("Quali sono le decisioni sul budget?", FRANCAIS),
    ("¿Cuál es la fecha de la próxima reunión?", FRANCAIS),
    ("Was ist die Entscheidung?", ALLEMAND),
    ("Budget ?", FRANCAIS),
    ("Quel est le planning for the launch ?", FRANCAIS),
])
def test_rag_ask_sans_langue_etablie_ne_transmet_que_la_consigne_generale(monkeypatch, question, passage) -> None:
    invite = _invite_transmise(monkeypatch, question, [passage])
    assert "Réponds dans la langue de la question." in invite
    assert all(c not in invite for c in CONSIGNES), invite[-200:]
