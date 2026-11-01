"""Les faits de la réunion (ECL) en option, coupée par défaut : `MEMOGLYPH_FAITS=1` l'active.

Option coupée : ni onglet, ni routes, ni outil MCP `lire_faits`, ni `ecl-extraire`, ni
réindex des faits au démarrage, et aucun modèle des faits chargé ; rien n'est supprimé
sur disque. Option activée : le comportement d'avant, à l'identique. Chaque surface est
vérifiée dans les deux états ; aucun modèle, aucun réseau.
"""
from __future__ import annotations

import inspect
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import auth, cli, mcp
from api import main as api_main
from core import config, ecl

pytestmark = pytest.mark.integration

RACINE = Path(__file__).resolve().parents[2]


@pytest.fixture(params=[False, True], ids=["coupee", "activee"])
def active(request, monkeypatch, isolated_stores) -> bool:
    monkeypatch.setattr(config, "FAITS", request.param)
    return request.param


@pytest.mark.parametrize("valeur, attendu", [(None, "False"), ("0", "False"), ("1", "True")])
def test_l_option_est_coupee_par_defaut_et_lue_dans_l_environnement(valeur, attendu, tmp_path) -> None:
    env = {k: v for k, v in os.environ.items() if not k.endswith("_FAITS")}
    env["MEMOGLYPH_ENV_FILE"] = str(tmp_path / ".env")          # absent : rien d'autre n'est lu
    if valeur is not None:
        env["MEMOGLYPH_FAITS"] = valeur
    fini = subprocess.run([sys.executable, "-c", "from core import config; print(config.FAITS)"],
                          cwd=RACINE, env=env, capture_output=True, text=True)
    assert fini.stdout.strip() == attendu, fini.stderr


def test_l_option_est_documentee() -> None:
    exemple = (RACINE / ".env.example").read_text(encoding="utf-8")
    assert "MEMOGLYPH_FAITS=" in exemple


def test_la_configuration_dit_l_etat_de_l_option(active) -> None:
    assert TestClient(api_main.app).get("/api/config").json()["faits"] is active


def test_les_routes_des_faits_refusent_quand_l_option_est_coupee(active, monkeypatch) -> None:
    lances: list = []
    monkeypatch.setattr(ecl, "extraire", lambda *a, **k: lances.append(a))
    client = TestClient(api_main.app)
    lecture = client.get("/api/meetings/inconnue/ecl", params={"view_scope": "all"})
    relance = client.post("/api/meetings/inconnue/ecl", params={"view_scope": "all"}, json={"provider": "mlx"})
    for reponse in (lecture, relance):
        assert reponse.status_code == 404
        assert (reponse.json()["detail"] == ecl.DESACTIVES) is (not active)
    assert lances == []


def test_l_outil_mcp_lire_faits_n_est_propose_qu_avec_l_option(active) -> None:
    attendus = set(mcp.OUTILS) if active else set(mcp.OUTILS) - {"lire_faits"}
    assert set(mcp.outils_proposes()) == attendus
    lecteur = mcp.Lecteur(auth.Utilisateur(id="alice", identifiant="alice", admin=False))
    with pytest.raises(mcp.Introuvable) as refus:
        lecteur.lire_faits({"reunion": "inconnue"})
    assert (str(refus.value) == ecl.DESACTIVES) is (not active)


def test_ecl_extraire_refuse_quand_l_option_est_coupee(active, monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "_application_tourne", lambda: None)
    monkeypatch.setattr(ecl, "a_extraire", lambda: ([], []))
    monkeypatch.setattr(ecl, "aux_faits_perimes", lambda: set())
    code = cli.main(["ecl-extraire"])
    erreur = capsys.readouterr().err
    assert (code, ecl.DESACTIVES in erreur) == ((0, False) if active else (1, True))


def test_le_demarrage_ne_reindexe_les_faits_qu_avec_l_option(active, monkeypatch) -> None:
    appels: list = []
    monkeypatch.setattr(ecl, "reindexer", lambda: appels.append("reindexer") or {})
    api_main._reindex_faits()
    assert appels == (["reindexer"] if active else [])
    assert "_reindex_faits()" in inspect.getsource(api_main._startup_reindex)
