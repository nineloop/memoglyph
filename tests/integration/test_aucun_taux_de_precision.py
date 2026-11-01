"""Aucune surface ne porte de taux de précision des faits : ni l'API, ni le MCP, ni l'interface.

Les faits restent « à vérifier » hors cloud reconnu : c'est une mise en garde, pas une
mesure. Le produit ne porte aucune table de précision par modèle (`core/ecl.py` n'en a
plus), et aucune surface n'en rend, quel que soit le modèle qui a extrait les faits,
y compris ceux qui avaient été mesurés.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import auth, mcp
from api import main as api_main
from core import config, docid, ecl, library, llm, projects
from tests.conftest import COMPTE_DE_TEST

pytestmark = pytest.mark.integration

RACINE = Path(__file__).resolve().parents[2]
CLE_DE_TAUX = re.compile(r"taux|precision|rate", re.IGNORECASE)
# Les mots d'un taux de précision à l'écran, dans les deux langues du catalogue.
TEXTE_DE_TAUX = re.compile(r"précision (non )?mesurée|précision affichée|measured precision|precision (not )?measured|"
                           r"\{rate\}", re.IGNORECASE)
FAIT = {"type": "decision", "horodatage": "00:00:10", "citation": "on fixe le prix de vente à 25 euros",
        "enonce": "Le prix de vente est fixé à 25 euros."}


class _Granite:
    """Le modèle local par défaut des faits, celui qui avait été mesuré."""

    def modele_servi(self, model=None) -> str:
        return "ibm-granite/granite-4.2-8b-q4-mlx"

    def unload(self) -> bool:
        return True


@pytest.fixture
def reunion(isolated_stores, monkeypatch) -> dict:
    monkeypatch.setattr(config, "FAITS", True)
    monkeypatch.setattr(ecl, "_modele", lambda *a, **k: {"faits": [dict(FAIT)]})
    monkeypatch.setattr(ecl, "moteur_local", lambda: (_Granite(), None))
    monkeypatch.setattr(llm, "unload", lambda *a, **k: True)
    sortie = isolated_stores["output_dir"]
    inbox = projects.create(sortie, name=projects.INBOX_NAME, kind="inbox", owner=COMPTE_DE_TEST)
    meeting_id = docid.nouveau()
    (sortie / "tarifs.md").write_text(
        f"---\nid: {docid.nouveau()}\nmeeting_id: {meeting_id}\nproject_id: {inbox.id}\n"
        "title: Tarifs\nlanguage: fr\nsensitivity: standard\n---\n\n## Transcript\n\n"
        "**Alice** [00:00:10] après discussion on fixe le prix de vente à 25 euros\n", encoding="utf-8")
    library.reindex()
    return {"meeting_id": meeting_id, "portee": {"view_scope": inbox.id}}


def _sans_taux(fait: dict) -> None:
    assert not [cle for cle in fait if CLE_DE_TAUX.search(cle)], f"clé de taux : {sorted(fait)}"
    nombres = {cle: v for cle, v in fait.items()
               if isinstance(v, float) and 0 < v < 1 and cle != "secondes"}
    assert nombres == {}, f"une proportion est rendue : {nombres}"


def test_le_produit_ne_porte_aucune_table_de_precision() -> None:
    assert not hasattr(ecl, "TAUX") and not hasattr(ecl, "taux")
    source = (RACINE / "core" / "ecl.py").read_text(encoding="utf-8")
    assert not re.search(r"0[.,](42|54|67)\b", source), "une précision mesurée reste dans core/ecl.py"


@pytest.mark.parametrize("fournisseur, a_verifier", [("anthropic", False), ("mlx", True)])
def test_ni_l_api_ni_le_mcp_ne_rendent_de_taux(reunion, fournisseur, a_verifier) -> None:
    ecl.extraire("tarifs.md", provider=fournisseur)
    corps = TestClient(api_main.app).get(f"/api/meetings/{reunion['meeting_id']}/ecl",
                                         params=reunion["portee"]).json()
    lecteur = mcp.Lecteur(auth.Utilisateur(id=COMPTE_DE_TEST, identifiant=COMPTE_DE_TEST, admin=False))
    par_mcp = lecteur.lire_faits({"reunion": reunion["meeting_id"]})["faits"]
    assert len(corps["faits"]) == 1 and len(par_mcp) == 1, "ancre : le fait est rendu"
    assert corps["faits"][0]["modele"] in ("claude-sonnet-5-5", "ibm-granite/granite-4.2-8b-q4-mlx")
    for fait in (*corps["faits"], *par_mcp):
        _sans_taux(fait)
        assert fait["a_verifier"] is a_verifier, "la mise en garde « à vérifier » reste"


def test_l_interface_et_la_doc_exportee_n_affichent_aucun_taux() -> None:
    sources = [RACINE / "ui" / "src" / "lib" / "messages.ts", RACINE / "ui" / "src" / "api.ts",
               *sorted((RACINE / "ui" / "src" / "components").glob("*.tsx")),
               RACINE / "docs" / "ecl.md", RACINE / "docs" / "mcp.md"]
    assert len(sources) > 20, "ancre : le balayage voit l'interface"
    fautifs = [f"{p.relative_to(RACINE)}:{n}" for p in sources
               for n, ligne in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
               if TEXTE_DE_TAUX.search(ligne) or re.search(r"\btaux\b|precision_mesuree|\bTAUX\b", ligne)]
    assert fautifs == [], f"un taux de précision reste affiché ou décrit : {fautifs}"
    messages = (RACINE / "ui" / "src" / "lib" / "messages.ts").read_text(encoding="utf-8")
    assert "'meeting.facts.toVerify': 'À vérifier'" in messages, "la mise en garde reste"
