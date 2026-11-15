"""Les refus de connexion, de comptes et d'appareils portent un code stable, en plus de leur phrase.

Le code s'ajoute : le statut, la phrase (`detail`) et les en-têtes sont ceux d'avant, aucune autre clé n'apparaît dans le corps.
L'interface traduit le code ; sans code connu elle garde une phrase générale, jamais le texte du serveur.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api import auth
from api import main as api_main

pytestmark = [pytest.mark.integration, pytest.mark.sans_session]

MOT_DE_PASSE = "un mot de passe solide"


@pytest.fixture(autouse=True)
def magasin_vide(tmp_path, monkeypatch, isolated_stores):
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(auth, "CODE_PATH", tmp_path / ".memoglyph-installation")


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_main.app, raise_server_exceptions=False)


def _connecte(client: TestClient, identifiant: str = "admin", *, admin: bool = True) -> TestClient:
    auth.creer_compte(identifiant, MOT_DE_PASSE, admin=admin)
    reponse = client.post("/api/auth/connexion", json={"identifiant": identifiant, "mot_de_passe": MOT_DE_PASSE})
    assert reponse.status_code == 200
    return client


def _refus(reponse, statut: int, code: str) -> dict:
    """Le corps d'un refus codé : exactement `detail` et `code`, au statut d'avant."""
    assert reponse.status_code == statut
    corps = reponse.json()
    assert corps["code"] == code
    assert set(corps) == {"detail", "code"}, "une phrase et un code, rien d'autre"
    assert isinstance(corps["detail"], str) and corps["detail"]
    return corps


# ---------- connexion ----------

def test_sans_session_le_refus_porte_le_code_de_connexion_requise(client) -> None:
    corps = _refus(client.get("/api/config"), 401, "auth.required")
    assert corps["detail"] == "Connexion requise."


def test_une_origine_etrangere_est_refusee_avec_son_code(client) -> None:
    _connecte(client)
    reponse = client.post("/api/jetons", json={"nom": "Mac"}, headers={"Origin": "http://autre.example"})
    assert _refus(reponse, 403, "request.origin.refused")["detail"] == "Origine refusée."


def test_un_identifiant_ou_un_mot_de_passe_faux_porte_son_code(client) -> None:
    auth.creer_compte("alice", MOT_DE_PASSE)
    reponse = client.post("/api/auth/connexion", json={"identifiant": "alice", "mot_de_passe": "faux faux faux"})
    assert _refus(reponse, 401, "auth.login.invalid")["detail"] == "Identifiant ou mot de passe incorrect."


def test_trop_de_tentatives_garde_son_delai_et_porte_son_code(client) -> None:
    auth.creer_compte("alice", MOT_DE_PASSE)
    for _ in range(auth.SEUIL_ECHECS):
        client.post("/api/auth/connexion", json={"identifiant": "alice", "mot_de_passe": "faux faux faux"})
    reponse = client.post("/api/auth/connexion", json={"identifiant": "alice", "mot_de_passe": "faux faux faux"})
    corps = _refus(reponse, 429, "auth.login.throttled")
    assert corps["detail"].startswith("Trop de tentatives.")
    assert int(reponse.headers["Retry-After"]) > 0, "l'en-tête d'attente est conservé"


# ---------- comptes ----------

def test_un_identifiant_invalide_porte_son_code(client) -> None:
    _connecte(client)
    reponse = client.post("/api/admin/utilisateurs", json={"identifiant": "a", "mot_de_passe": MOT_DE_PASSE})
    assert _refus(reponse, 400, "account.id.invalid")["detail"].startswith("Identifiant invalide")


def test_un_mot_de_passe_trop_court_porte_son_code(client) -> None:
    _connecte(client)
    reponse = client.post("/api/admin/utilisateurs", json={"identifiant": "bruno", "mot_de_passe": "court"})
    assert _refus(reponse, 400, "account.password.tooShort")["detail"].startswith("Mot de passe trop court")


def test_un_identifiant_deja_pris_porte_son_code(client) -> None:
    _connecte(client)
    reponse = client.post("/api/admin/utilisateurs", json={"identifiant": "admin", "mot_de_passe": MOT_DE_PASSE})
    assert _refus(reponse, 400, "account.id.taken")["detail"] == "Cet identifiant est déjà pris."


@pytest.mark.parametrize("action", ["desactivation", "reactivation"])
def test_un_compte_inconnu_porte_son_code(action, client) -> None:
    _connecte(client)
    reponse = client.post(f"/api/admin/utilisateurs/inconnu/{action}")
    assert _refus(reponse, 400, "account.unknown")["detail"].startswith("Aucun compte")


def test_le_dernier_administrateur_actif_porte_son_code(client) -> None:
    _connecte(client)
    reponse = client.post("/api/admin/utilisateurs/admin/desactivation")
    assert _refus(reponse, 400, "account.lastAdmin")["detail"] == "Impossible de désactiver le dernier administrateur actif."


def test_la_reinitialisation_d_un_mot_de_passe_porte_les_memes_codes() -> None:
    """Hors d'une route (outil d'administration) : les mêmes refus portent les mêmes codes."""
    with pytest.raises(auth.ErreurAuth) as court:
        auth.reinitialiser_mot_de_passe("admin", "court")
    assert court.value.code == "account.password.tooShort"
    with pytest.raises(auth.ErreurAuth) as inconnu:
        auth.reinitialiser_mot_de_passe("inconnu", MOT_DE_PASSE)
    assert inconnu.value.code == "account.unknown"


def test_un_refus_sans_code_reste_sans_code() -> None:
    assert auth.ErreurAuth("phrase seule").code is None
    assert auth.TropDeTentatives(30).code is None, "le code du délai est posé par la route, pas par l'exception"


# ---------- installation ----------

def test_un_code_d_installation_faux_porte_son_code(client) -> None:
    reponse = client.post("/api/auth/installation",
                          json={"code": "FAUX-FAUX", "identifiant": "admin", "mot_de_passe": MOT_DE_PASSE})
    assert _refus(reponse, 400, "setup.code.invalid")["detail"] == "Code d'installation invalide."


def test_une_installation_deja_faite_porte_son_code(client) -> None:
    auth.creer_compte("admin", MOT_DE_PASSE, admin=True)
    reponse = client.post("/api/auth/installation",
                          json={"code": "FAUX-FAUX", "identifiant": "autre", "mot_de_passe": MOT_DE_PASSE})
    assert _refus(reponse, 400, "setup.done")["detail"] == "L'installation est déjà faite."


# ---------- appareils ----------

def test_un_nom_d_appareil_invalide_porte_son_code(client) -> None:
    _connecte(client)
    reponse = client.post("/api/jetons", json={"nom": "   "})
    assert _refus(reponse, 400, "device.name.invalid")["detail"].startswith("Nom d'appareil invalide")


def test_un_jeton_inconnu_porte_son_code(client) -> None:
    _connecte(client)
    reponse = client.delete("/api/jetons/inconnu")
    assert _refus(reponse, 404, "device.unknown")["detail"] == "Jeton inconnu"
