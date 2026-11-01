"""La clé Mistral saisie à l'écran ne part que chez Mistral.

Chaque moteur prend la clé qu'on lui passe : relayée vers Anthropic ou OpenAI, elle
partait chez eux dans l'en-tête d'authentification.
"""
from __future__ import annotations

from contextlib import nullcontext

import pytest

from core import ecl, llm, rag

pytestmark = pytest.mark.unit

CLE = "cle-mistral-" + "de-l-utilisateur"


@pytest.mark.parametrize("fournisseur, attendu", [
    ("mistral", CLE), ("cloud", CLE), ("anthropic", None), ("openai", None), ("mlx", None),
])
def test_la_cle_mistral_ne_vaut_que_pour_mistral(fournisseur, attendu) -> None:
    assert llm.cle_mistral(fournisseur, CLE) == attendu


@pytest.mark.parametrize("fournisseur", ["anthropic", "openai"])
def test_l_assistant_ne_relaie_pas_la_cle_mistral_a_un_autre_fournisseur(monkeypatch, fournisseur) -> None:
    vues: list = []
    monkeypatch.setattr(rag.semantic, "retrieve", lambda *a, **k: [{
        "name": "m.md", "title": "M", "ts": "00:00:01", "text": "Le budget est validé", "rang": 1, "project": "P"}])
    monkeypatch.setattr(rag.resources, "heavy", lambda **k: nullcontext())
    monkeypatch.setattr(rag.llm, "is_available", lambda **k: vues.append(("dispo", k.get("api_key"))) or True)
    monkeypatch.setattr(rag.llm, "chat", lambda *a, **k: vues.append(("chat", k.get("api_key"))) or "ok [1]")
    rag.ask("Le budget ?", provider=fournisseur, mistral_api_key=CLE)
    assert vues == [("dispo", None), ("chat", None)]


def test_l_assistant_relaie_la_cle_a_mistral(monkeypatch) -> None:
    vues: list = []
    monkeypatch.setattr(rag.semantic, "retrieve", lambda *a, **k: [{
        "name": "m.md", "title": "M", "ts": "00:00:01", "text": "Le budget est validé", "rang": 1, "project": "P"}])
    monkeypatch.setattr(rag.resources, "heavy", lambda **k: nullcontext())
    monkeypatch.setattr(rag.llm, "is_available", lambda **k: True)
    monkeypatch.setattr(rag.llm, "chat", lambda *a, **k: vues.append(k.get("api_key")) or "ok [1]")
    rag.ask("Le budget ?", provider="mistral", mistral_api_key=CLE)
    assert vues == [CLE]


def test_les_faits_ne_relaient_pas_la_cle_mistral_a_anthropic(monkeypatch, tmp_path) -> None:
    from core import library
    monkeypatch.setattr(library, "get_transcript", lambda name: {
        "meta": {"id": "DOC", "meeting_id": "M", "language": "fr"},
        "body": "## Transcript\n\n**Alice** [00:00:10] on fixe le prix de vente à 25 euros\n"})
    monkeypatch.setattr(ecl, "ecrire", lambda contenu: tmp_path / "f.json")
    monkeypatch.setattr(ecl, "indexer_contenu", lambda contenu: 0)
    monkeypatch.setattr(ecl.llm, "modele_servi", lambda provider, *a, **k: "faux")
    cles: list = []
    monkeypatch.setattr(ecl, "_modele", lambda system, user, **options: cles.append(options.get("api_key"))
                        or {"faits": []})
    ecl.extraire("reunion.md", provider="anthropic", mistral_api_key=CLE)
    assert cles and all(cle is None for cle in cles)
