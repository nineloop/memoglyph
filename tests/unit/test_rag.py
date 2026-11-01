from __future__ import annotations

from contextlib import nullcontext

import pytest

from core import rag


pytestmark = pytest.mark.unit


def test_sources_carry_stable_identities_and_project_origin(monkeypatch) -> None:
    monkeypatch.setattr(
        rag.semantic,
        "retrieve",
        lambda *args, **kwargs: [{
            "name": "meeting.md", "title": "Meeting", "ts": "00:12",
            "text": "Un extrait", "rang": 1, "document_id": "doc-id",
            "meeting_id": "meeting-id", "project_id": "project-id",
            "project": "Client",
        }],
    )
    monkeypatch.setattr(rag.resources, "heavy", lambda **kwargs: nullcontext())
    # Sans ce mock, rag.ask exécute la vraie garde `is_available()` (GET httpx vers
    # Ollama) : un test marqué `unit` échouait sur arbre propre sans serveur.
    #
    # Les bouchons portent sur la FAÇADE, seule porte depuis que le chemin local
    # a cessé de doubler l'interface. Bouchonner une implémentation ici ferait
    # ce que faisaient mes premiers bouchons de `test_cablage_garde_fous` :
    # rater le vrai chemin, sans le dire.
    monkeypatch.setattr(rag.llm, "is_available", lambda *args, **kwargs: True)
    monkeypatch.setattr(rag.llm, "chat", lambda *args, **kwargs: "Réponse [1]")

    result = rag.ask("Question", project_id=None)

    assert result["sources"] == [{
        "n": 1, "name": "meeting.md", "title": "Meeting", "ts": "00:12",
        "rang": 1, "origine": "transcript", "excerpt": "Un extrait", "document_id": "doc-id",
        "meeting_id": "meeting-id", "project_id": "project-id",
        "project": "Client",
    }]


def test_l_extrait_cite_montre_le_chiffre_de_la_reponse_meme_au_dela_du_220e_caractere() -> None:
    # Constat 7 du parcours : l'extrait gardait les 220 premiers caractères, et le prix
    # que la réponse cite tombait après.
    passage = ("Okay so we talked about the design and the buttons, and the marketing people said that "
               "the target group is young people who like trendy things and who want a simple remote. "
               "Then the finance part: after a long discussion of every component we settled on the "
               "selling price, which is 25 euro for the remote.")
    assert passage.index("25 euro") > rag.EXTRAIT_MAX
    extrait = rag.extrait(passage, "The selling price decided for the remote control was 25 euro [1].")
    assert "25 euro" in extrait
    assert len(extrait) <= rag.EXTRAIT_MAX + 2  # plus les deux points de suspension
    assert extrait.startswith("…")


def test_un_passage_court_s_affiche_en_entier() -> None:
    assert rag.extrait("Un extrait", "Réponse [1]") == "Un extrait"
