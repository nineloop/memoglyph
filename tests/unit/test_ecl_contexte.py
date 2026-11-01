"""ECL 2e : un seul appel sous `BORNE_CONTEXTE`, et la langue des énoncés."""
from __future__ import annotations

import pytest

from core import ecl

pytestmark = pytest.mark.unit


def _lignes(n: int) -> list[str]:
    return [f"**Speaker {i % 4 + 1}** [00:{i // 60:02d}:{i % 60:02d}] " + "word " * 40 for i in range(n)]


def test_sans_borne_le_transcript_part_par_tranches(monkeypatch) -> None:
    monkeypatch.setattr(ecl, "BORNE_CONTEXTE", None)
    tranches, plafond = ecl.decouper(_lignes(200), "local")
    assert len(tranches) > 1
    assert plafond == ecl.MAX_TOKENS_PAR_MODE["local"]


def test_sous_la_borne_un_seul_appel_garde_le_budget_des_tranches(monkeypatch) -> None:
    lignes = _lignes(60)
    monkeypatch.setattr(ecl, "BORNE_CONTEXTE", None)
    n = len(ecl.decouper(lignes, "local")[0])
    assert n > 1
    monkeypatch.setattr(ecl, "BORNE_CONTEXTE", 10**9)
    tranches, plafond = ecl.decouper(lignes, "local")
    assert tranches == ["\n".join(lignes)]
    assert plafond == min(ecl.MAX_TOKENS_PAR_MODE["local"] * n, ecl.MAX_TOKENS_CONTEXTE)


def test_au_dela_de_la_borne_les_tranches_restent(monkeypatch) -> None:
    monkeypatch.setattr(ecl, "BORNE_CONTEXTE", 100)
    tranches, plafond = ecl.decouper(_lignes(200), "local")
    assert len(tranches) > 1
    assert plafond == ecl.MAX_TOKENS_PAR_MODE["local"]


@pytest.mark.parametrize("texte, langue", [
    ("Le prix de vente cible est de 25 euros.", "fr"),
    ("Fonsina expliquera la partie boutons.", "fr"),
    ("The selling price of the remote is 25 euro.", "en"),
    ("Speaker 2 will send the design to the team.", "en"),
    ("Okay.", None),
    ("A flat design could save one euro.", None),
])
def test_langue_probable_par_les_mots_outils(texte, langue) -> None:
    assert ecl.langue_probable(texte) == langue


# ---------- Constat 6 : un énoncé dans la langue de la réunion ----------

def _reunion_anglaise(monkeypatch, tmp_path, faits):
    from core import library
    monkeypatch.setattr(library, "get_transcript", lambda name: {
        "meta": {"id": "DOC-EN", "meeting_id": "M-EN", "language": "en"},
        "body": "## Transcript\n\n**Speaker 1** [00:00:12] We fix the selling price at twenty five euro.\n"})
    monkeypatch.setattr(ecl, "moteur_local", lambda: (None, None))
    monkeypatch.setattr(ecl, "ecrire", lambda contenu: tmp_path / "faits.json")
    monkeypatch.setattr(ecl, "indexer_contenu", lambda contenu: 0)
    monkeypatch.setattr(ecl.llm, "moteur", lambda provider: type("M", (), {"name": "mlx"})())
    monkeypatch.setattr(ecl.llm, "est_local", lambda name: True)
    monkeypatch.setattr(ecl.llm, "modele_servi", lambda provider: "faux")
    appels = []

    def modele(system, user, **options):
        appels.append(user)
        return {"faits": faits}
    monkeypatch.setattr(ecl, "_modele", modele)
    return appels


def test_le_rappel_de_langue_suit_le_transcript_dans_la_langue_de_la_reunion(monkeypatch, tmp_path) -> None:
    appels = _reunion_anglaise(monkeypatch, tmp_path, [])
    ecl.extraire("reunion.md")
    assert appels and appels[0].endswith(ecl._RAPPEL_LANGUE["en"])


def test_un_enonce_en_francais_dans_une_reunion_anglaise_cede_la_place_a_sa_citation(monkeypatch, tmp_path) -> None:
    citation = "We fix the selling price at twenty five euro."
    _reunion_anglaise(monkeypatch, tmp_path, [
        {"type": "decision", "horodatage": "00:00:12", "citation": citation,
         "enonce": "Le groupe fixe le prix de vente à vingt-cinq euros."}])
    ecrits = []
    monkeypatch.setattr(ecl, "ecrire", lambda contenu: ecrits.append(contenu) or tmp_path / "f.json")
    bilan = ecl.extraire("reunion.md")
    (fait,) = ecrits[0]["faits"]
    assert fait["enonce"] == citation and fait["enonce_statut"] == "cité"
    assert bilan["enonces_cites"] == 1 and bilan["gardes"] == 1


def test_un_enonce_dans_la_langue_de_la_reunion_reste_reformule(monkeypatch, tmp_path) -> None:
    _reunion_anglaise(monkeypatch, tmp_path, [
        {"type": "decision", "horodatage": "00:00:12",
         "citation": "We fix the selling price at twenty five euro.",
         "enonce": "The selling price is fixed at twenty five euro."}])
    ecrits = []
    monkeypatch.setattr(ecl, "ecrire", lambda contenu: ecrits.append(contenu) or tmp_path / "f.json")
    bilan = ecl.extraire("reunion.md")
    (fait,) = ecrits[0]["faits"]
    assert fait["enonce_statut"] == "reformulé" and bilan["enonces_cites"] == 0
