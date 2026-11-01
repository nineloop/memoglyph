"""Sauvegarde et restauration complètes (`tools/sauvegarder.py`, docs/sauvegarde.md).

Toujours sur une racine jetable dans `tmp_path` : jamais la vraie bibliothèque.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import auth
from api import main as api_main
from api.jobs import JobManager
from core import usage_store
from tools import sauvegarder as outil

pytestmark = [pytest.mark.integration, pytest.mark.sans_session]

MOT_DE_PASSE = "un mot de passe solide"
QUAND = datetime(2026, 10, 25, 7, 0, 0)


def _port_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _pile_arretee(racine: Path) -> None:
    """Des ports libres pour cette racine : la vraie pile, si elle tourne, n'y est pour rien."""
    (racine / ".memoglyph-ports").write_text(f"UI_PORT={_port_libre()}\nAPI_PORT={_port_libre()}\n",
                                             encoding="utf-8")


@pytest.fixture
def racine(isolated_stores, tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(auth, "CODE_PATH", tmp_path / ".memoglyph-installation")
    monkeypatch.setattr(usage_store, "_DEFAULT_USAGE_DB", tmp_path / "usage.db")
    monkeypatch.setattr(api_main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(api_main, "jobs", JobManager())
    _pile_arretee(tmp_path)
    return tmp_path


def _connecte(identifiant: str) -> TestClient:
    client = TestClient(api_main.app)
    assert client.post("/api/auth/connexion", json={
        "identifiant": identifiant, "mot_de_passe": MOT_DE_PASSE}).status_code == 200
    return client


def _peupler(racine: Path, monkeypatch) -> tuple[str, list[str]]:
    """Un compte, un jeton d'appareil, une réunion envoyée par ce jeton, une ligne d'usage."""
    from tests.integration.test_jetons_appareil import attendre, creer_jeton, faux_pipeline, porteur

    auth.creer_compte("alice", MOT_DE_PASSE, admin=True)
    alice = _connecte("alice")
    _jeton, secret = creer_jeton(alice)
    faux_pipeline(monkeypatch, [])
    job = porteur(secret).post("/api/transcribe", files={"file": ("a.wav", b"a")}).json()["job_id"]
    assert attendre(alice, job)["status"] == "done"
    with sqlite3.connect(racine / "usage.db") as c:
        c.execute("CREATE TABLE IF NOT EXISTS essai (n INTEGER)")
        c.execute("INSERT INTO essai VALUES (42)")
    reunions = sorted(m["id"] for m in alice.get("/api/meetings").json())
    assert reunions
    return secret, reunions


def _effacer(racine: Path) -> None:
    shutil.rmtree(racine / "output")
    for nom in outil.BASES:
        for c in (nom, f"{nom}-wal", f"{nom}-shm"):
            (racine / c).unlink(missing_ok=True)


def test_sauvegarder_effacer_restaurer_puis_tout_retrouver(racine, tmp_path_factory, monkeypatch) -> None:
    from tests.integration.test_jetons_appareil import porteur

    secret, reunions = _peupler(racine, monkeypatch)
    vers = tmp_path_factory.mktemp("sauvegardes")
    dossier = outil.sauvegarder(racine, vers, maintenant=QUAND)
    assert dossier == vers / "memoglyph-2026-10-25-07h0000"
    manifeste = json.loads((dossier / outil.MANIFESTE).read_text(encoding="utf-8"))
    assert manifeste["elements"] == ["output", "index.db", "auth.db", "usage.db"]

    _effacer(racine)
    assert not any((racine / nom).exists() for nom in outil.PERIMETRE), "tout est perdu"

    assert outil.restaurer(dossier, racine) == ["output", "index.db", "auth.db", "usage.db"]
    alice = _connecte("alice")
    assert sorted(m["id"] for m in alice.get("/api/meetings").json()) == reunions
    assert porteur(secret).get("/api/projects").status_code == 200, "le jeton d'appareil vaut toujours"
    with sqlite3.connect(racine / "usage.db") as c:
        assert c.execute("SELECT n FROM essai").fetchall() == [(42,)]


def test_refus_pendant_que_memoglyph_tourne(racine, tmp_path_factory) -> None:
    (racine / "index.db").write_bytes(b"")
    vers = tmp_path_factory.mktemp("sauvegardes")
    (racine / ".memoglyph-api.pid").write_text(str(os.getpid()), encoding="utf-8")
    with pytest.raises(outil.Refus, match="Memoglyph tourne"):
        outil.sauvegarder(racine, vers)
    assert list(vers.iterdir()) == [], "rien d'écrit"
    (racine / ".memoglyph-api.pid").unlink()

    with socket.socket() as ecoute:
        ecoute.bind(("127.0.0.1", 0))
        ecoute.listen()
        (racine / ".memoglyph-ports").write_text(f"API_PORT={ecoute.getsockname()[1]}\n", encoding="utf-8")
        assert "écoute" in (outil.memoglyph_tourne(racine) or "")
        with pytest.raises(outil.Refus, match="Memoglyph tourne"):
            outil.sauvegarder(racine, vers)
        assert outil.sauvegarder(racine, vers, forcer=True).is_dir(), "--forcer passe outre"


def test_la_restauration_refuse_une_racine_occupee_et_ne_supprime_rien(racine, tmp_path_factory) -> None:
    (racine / "auth.db").write_bytes(b"")
    sqlite3.connect(racine / "auth.db").close()
    dossier = outil.sauvegarder(racine, tmp_path_factory.mktemp("sauvegardes"), maintenant=QUAND)
    (racine / "auth.db").write_bytes(b"donnees actuelles")
    with pytest.raises(outil.Refus, match="contient déjà"):
        outil.restaurer(dossier, racine)
    assert (racine / "auth.db").read_bytes() == b"donnees actuelles"

    outil.restaurer(dossier, racine, forcer=True, maintenant=QUAND)
    assert (racine / ".avant-restauration-2026-10-25-07h0000" / "auth.db").read_bytes() == b"donnees actuelles"


def test_l_outil_n_ecrit_pas_hors_du_dossier_choisi(racine, tmp_path_factory) -> None:
    (racine / "output").mkdir(exist_ok=True)
    (racine / "output" / "r.md").write_text("réunion", encoding="utf-8")
    with pytest.raises(outil.Refus, match="output/"):
        outil.sauvegarder(racine, racine / "output" / "sauvegardes")
    assert not (racine / "output" / "sauvegardes").exists()

    parent = tmp_path_factory.mktemp("parent")
    vers = parent / "choisi"
    dossier = outil.sauvegarder(racine, vers, maintenant=QUAND)
    assert [p.name for p in parent.iterdir()] == ["choisi"], "rien à côté du dossier choisi"
    with pytest.raises(outil.Refus, match="existe déjà"):
        outil.sauvegarder(racine, vers, maintenant=QUAND)

    # Un manifeste qui pointerait hors de la sauvegarde est refusé avant toute écriture.
    manifeste = json.loads((dossier / outil.MANIFESTE).read_text(encoding="utf-8"))
    manifeste["empreintes"]["../dehors.md"] = "0" * 64
    (dossier / outil.MANIFESTE).write_text(json.dumps(manifeste), encoding="utf-8")
    neuve = tmp_path_factory.mktemp("neuve")
    with pytest.raises(outil.Refus, match="hors de la sauvegarde"):
        outil.restaurer(dossier, neuve)
    assert list(neuve.iterdir()) == [] and not (parent / "dehors.md").exists()


def test_une_sauvegarde_abimee_est_refusee(racine, tmp_path_factory) -> None:
    (racine / "output").mkdir(exist_ok=True)
    (racine / "output" / "r.md").write_text("réunion", encoding="utf-8")
    dossier = outil.sauvegarder(racine, tmp_path_factory.mktemp("sauvegardes"))
    (dossier / "output" / "r.md").write_text("altérée", encoding="utf-8")
    neuve = tmp_path_factory.mktemp("neuve")
    with pytest.raises(outil.Refus, match="abîmée"):
        outil.restaurer(dossier, neuve)
    assert len(list((dossier / "output").iterdir())) == 1, "la sauvegarde avait bien de quoi restaurer"
    assert list(neuve.iterdir()) == []


def test_la_commande(racine, tmp_path_factory, capsys) -> None:
    (racine / "output").mkdir(exist_ok=True)
    vers = tmp_path_factory.mktemp("sauvegardes")
    assert outil.main(["sauvegarder", "--vers", str(vers), "--racine", str(racine)]) == 0
    assert "✓ Sauvegarde" in capsys.readouterr().out
    assert outil.main(["restaurer", str(vers / "absente"), "--racine", str(racine)]) == 2
    assert "Refus" in capsys.readouterr().err
