"""Un compte-rendu dont tous les appels sont refusés doit nommer la panne.

Une suite de 429 de Mistral, une par tranche, finissait en « Aucune unité
validée » : un `RuntimeError` qui devenait un 500 muet. Le refus du service,
seule information utile, restait enfoui dans le diagnostic.
"""

from __future__ import annotations

import pytest

from core import llm
from core.experimental import pipeline

CORPS = ("## Transcript\n\n"
         "**Speaker 1** [00:00:01] On fixe le prix de vente.\n"
         "**Speaker 2** [00:00:05] D'accord pour vingt-cinq euros.\n")


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(llm, "is_available", lambda **k: True)
    monkeypatch.setattr(llm, "modele_servi", lambda p: "modele-test")


def _lancer():
    return pipeline.run_stages("reunion.md", {"meta": {}, "body": CORPS},
                               provider="mistral", seed=42)


def test_tous_les_appels_refuses_nomment_la_panne_du_service(monkeypatch, service) -> None:
    def refus(*a, **k):
        raise llm.LLMError("Erreur API Mistral : {'status': 429, 'type': 'rate_limited'}")

    monkeypatch.setattr(llm, "chat_json", refus)

    with pytest.raises(llm.LLMError) as erreur:
        _lancer()

    assert "refusé" in str(erreur.value)
    assert "rate_limited" in str(erreur.value)


def test_une_extraction_vide_sans_refus_reste_une_autre_erreur(monkeypatch, service) -> None:
    """Le service a répondu, rien n'a été extrait : ce n'est pas une panne de
    service, et ce n'est pas un 503."""
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {"units": []})

    from core.experimental.pipeline import ExtractionVide

    with pytest.raises(ExtractionVide, match="Extraction vide : aucune unité retenue") as erreur:
        _lancer()

    assert not isinstance(erreur.value, llm.LLMError)
    assert erreur.value.refuses == 0 and erreur.value.appels >= 1
    assert f"sur {erreur.value.appels} appel(s) au modèle, dont 0 refusé(s)" in str(erreur.value)


def test_une_extraction_vide_avec_des_refus_partiels_les_compte(monkeypatch, service) -> None:
    """Une partie des appels refusés, les autres vides : extraction vide, pas panne."""
    from core.experimental.pipeline import ExtractionVide
    # Assez long pour plusieurs tranches, donc plusieurs appels d'extraction.
    long = "## Transcript\n\n" + "\n\n".join(
        f"[00:{i // 60:02d}:{i % 60:02d}] Réplique numéro {i} sur le budget et le calendrier du projet."
        for i in range(600))
    appels = {"n": 0}

    def alterne(*a, **k):
        appels["n"] += 1
        if appels["n"] % 2:
            raise llm.LLMError("quota")
        return {"units": []}

    monkeypatch.setattr(llm, "chat_json", alterne)

    with pytest.raises(ExtractionVide) as erreur:
        pipeline.run_stages("reunion.md", {"meta": {}, "body": long}, provider="mistral", seed=42)

    assert 1 <= erreur.value.refuses < erreur.value.appels
    assert "premier refus : LLMError: quota" in str(erreur.value)
