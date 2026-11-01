"""Connexion obligatoire sur toute l'API, éprouvée par HTTP.

Ces clients ne portent PAS la session de `tests/conftest.py` : ils partent d'un
magasin de comptes vide, comme une installation neuve.
"""
from __future__ import annotations

import re

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from api import auth
from api import main as api_main

pytestmark = [pytest.mark.integration, pytest.mark.sans_session]

MOT_DE_PASSE = "un mot de passe solide"


@pytest.fixture(autouse=True)
def magasin_vide(tmp_path, monkeypatch, isolated_stores):
    # `isolated_stores` : un `output/` neuf. L'`output/` partagé de la session
    # peut garder ce qu'un test précédent y a laissé, et `ensure_inbox` le prend
    # alors pour un corpus à migrer (409). Révélé en lançant la suite à l'envers.
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(auth, "CODE_PATH", tmp_path / ".memoglyph-installation")


@pytest.fixture
def client() -> TestClient:
    return TestClient(api_main.app, raise_server_exceptions=False)


def _routes_api() -> list[tuple[str, str]]:
    return sorted({(methode, route.path) for route in api_main.app.routes
                   if isinstance(route, APIRoute) and route.path.startswith("/api/")
                   for methode in route.methods if methode != "HEAD"})


def test_toute_route_api_exige_une_session_sauf_les_publiques(client) -> None:
    """Le test structurel : chaque route déclarée, sans session, rend 401."""
    ouvertes = []
    for methode, chemin in _routes_api():
        if (methode, chemin) in api_main.ROUTES_PUBLIQUES:
            continue
        url = re.sub(r"\{[^}]+\}", "x", chemin)
        if client.request(methode, url).status_code != 401:
            ouvertes.append(f"{methode} {chemin}")
    assert ouvertes == []
    assert len(_routes_api()) > 40
    # La documentation interactive ne décrit plus l'API à qui n'a pas de session.
    for chemin in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(chemin).status_code == 404
    assert {"/api/admin/utilisateurs/{identifiant}/desactivation",
            "/api/admin/utilisateurs/{identifiant}/reactivation"} <= {c for _, c in _routes_api()}


def test_les_routes_publiques_existent_et_restent_peu_nombreuses() -> None:
    assert api_main.ROUTES_PUBLIQUES <= set(_routes_api())
    assert {chemin for _, chemin in api_main.ROUTES_PUBLIQUES} == {
        "/api/health", "/api/auth/etat", "/api/auth/installation",
        "/api/auth/connexion", "/api/auth/deconnexion"}


def test_un_cookie_inconnu_ne_vaut_pas_une_session(client) -> None:
    client.cookies.set(auth.COOKIE, "jeton-invente")

    assert client.get("/api/config").status_code == 401


def test_installation_neuve_puis_connexion_complete(client) -> None:
    etat = client.get("/api/auth/etat").json()
    assert etat == {"installation_requise": True, "connecte": False, "utilisateur": None}

    code = auth.preparer_installation()
    refus = client.post("/api/auth/installation",
                        json={"code": "AAAA-BBBB-CCCC", "identifiant": "admin",
                              "mot_de_passe": MOT_DE_PASSE})
    assert refus.status_code == 400

    reponse = client.post("/api/auth/installation",
                          json={"code": code, "identifiant": "admin",
                                "mot_de_passe": MOT_DE_PASSE})
    assert reponse.status_code == 200
    cookie = reponse.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Secure" not in cookie
    assert client.get("/api/auth/etat").json()["connecte"] is True
    assert client.get("/api/config").status_code == 200

    # L'installation ne se rejoue pas, même avec l'ancien code.
    assert client.post("/api/auth/installation",
                       json={"code": code, "identifiant": "autre",
                             "mot_de_passe": MOT_DE_PASSE}).status_code == 400

    assert client.post("/api/auth/deconnexion").status_code == 200
    assert client.get("/api/config").status_code == 401
    assert client.get("/api/auth/etat").json()["connecte"] is False


def test_le_cookie_est_secure_en_https() -> None:
    https = TestClient(api_main.app, base_url="https://testserver")
    code = auth.preparer_installation()

    reponse = https.post("/api/auth/installation",
                         json={"code": code, "identifiant": "admin",
                               "mot_de_passe": MOT_DE_PASSE})

    assert "Secure" in reponse.headers["set-cookie"]


def test_la_connexion_ne_dit_pas_lequel_est_faux(client) -> None:
    auth.creer_compte("alice", MOT_DE_PASSE)

    mauvais_mot = client.post("/api/auth/connexion",
                              json={"identifiant": "alice", "mot_de_passe": "mauvais"})
    inconnu = client.post("/api/auth/connexion",
                          json={"identifiant": "bob", "mot_de_passe": MOT_DE_PASSE})
    bon = client.post("/api/auth/connexion",
                      json={"identifiant": "alice", "mot_de_passe": MOT_DE_PASSE})

    assert mauvais_mot.status_code == inconnu.status_code == 401
    assert mauvais_mot.json() == inconnu.json()
    assert bon.status_code == 200
    assert bon.json() == {"utilisateur": {"identifiant": "alice", "admin": False}}


def test_seul_un_administrateur_cree_un_compte(client) -> None:
    auth.creer_compte("admin", MOT_DE_PASSE, admin=True)
    auth.creer_compte("alice", MOT_DE_PASSE)
    nouveau = {"identifiant": "bob", "mot_de_passe": MOT_DE_PASSE}

    assert client.post("/api/admin/utilisateurs", json=nouveau).status_code == 401
    client.post("/api/auth/connexion", json={"identifiant": "alice", "mot_de_passe": MOT_DE_PASSE})
    # 404 et non 403 : la route n'existe pas pour un compte ordinaire.
    assert client.post("/api/admin/utilisateurs", json=nouveau).status_code == 404
    assert client.get("/api/admin/utilisateurs").status_code == 404

    client.post("/api/auth/connexion", json={"identifiant": "admin", "mot_de_passe": MOT_DE_PASSE})
    cree = client.post("/api/admin/utilisateurs", json=nouveau)
    assert cree.status_code == 201
    assert cree.json() == {"utilisateur": {"identifiant": "bob", "admin": False}}
    assert client.post("/api/admin/utilisateurs", json=nouveau).status_code == 400
    assert [c["identifiant"] for c in client.get("/api/admin/utilisateurs").json()] == [
        "admin", "alice", "bob"]


def test_aucune_route_d_inscription_libre() -> None:
    chemins = {chemin for _, chemin in _routes_api()}
    assert not [c for c in chemins if re.search(r"inscri|register|signup", c)]


def test_le_demarrage_tire_le_code_seulement_sans_compte(capsys, monkeypatch) -> None:
    # Seul le code d'installation est éprouvé ici. La réindexation du démarrage
    # lance une reprise sémantique en tâche de fond qui survivait au test : finie
    # après la restauration des magasins, elle laissait `/api/health` « degraded »
    # pour les tests suivants (révélé en lançant la suite à l'envers).
    monkeypatch.setattr(api_main, "_startup_reindex", lambda: None)
    monkeypatch.setattr(api_main, "_startup_finops", lambda: None)
    with TestClient(api_main.app):
        pass
    assert "code d'installation prêt" in capsys.readouterr().out
    code = auth.CODE_PATH.read_text().strip()
    assert code not in capsys.readouterr().out


# ---------- Auth 2 : comptes, tentatives, documentation, origine ----------

def _admin_connecte(client) -> None:
    auth.creer_compte("admin", MOT_DE_PASSE, admin=True)
    client.post("/api/auth/connexion", json={"identifiant": "admin", "mot_de_passe": MOT_DE_PASSE})


def test_l_administrateur_desactive_et_reactive_un_compte(client) -> None:
    _admin_connecte(client)
    client.post("/api/admin/utilisateurs", json={"identifiant": "alice", "mot_de_passe": MOT_DE_PASSE})
    alice = TestClient(api_main.app)
    alice.post("/api/auth/connexion", json={"identifiant": "alice", "mot_de_passe": MOT_DE_PASSE})
    assert alice.get("/api/config").status_code == 200

    assert client.post("/api/admin/utilisateurs/alice/desactivation").status_code == 200

    assert alice.get("/api/config").status_code == 401
    assert alice.post("/api/auth/connexion", json={
        "identifiant": "alice", "mot_de_passe": MOT_DE_PASSE}).status_code == 401
    assert client.post("/api/admin/utilisateurs/admin/desactivation").status_code == 400
    assert client.post("/api/admin/utilisateurs/alice/reactivation").status_code == 200
    assert alice.post("/api/auth/connexion", json={
        "identifiant": "alice", "mot_de_passe": MOT_DE_PASSE}).status_code == 200


def test_seul_l_administrateur_desactive(client) -> None:
    auth.creer_compte("admin", MOT_DE_PASSE, admin=True)
    auth.creer_compte("alice", MOT_DE_PASSE)
    client.post("/api/auth/connexion", json={"identifiant": "alice", "mot_de_passe": MOT_DE_PASSE})

    assert client.post("/api/admin/utilisateurs/admin/desactivation").status_code == 404
    assert client.post("/api/admin/utilisateurs/admin/reactivation").status_code == 404


def test_cinq_echecs_bloquent_sans_reveler_si_le_compte_existe(client) -> None:
    auth.creer_compte("alice", MOT_DE_PASSE)
    reponses = {}
    for identifiant in ("alice", "fantome"):
        voisin = TestClient(api_main.app, client=(f"10.0.0.{len(reponses) + 1}", 5000))
        for _ in range(5):
            assert voisin.post("/api/auth/connexion", json={
                "identifiant": identifiant, "mot_de_passe": "mauvais"}).status_code == 401
        reponses[identifiant] = voisin.post("/api/auth/connexion", json={
            "identifiant": identifiant, "mot_de_passe": MOT_DE_PASSE})

    assert reponses["alice"].status_code == reponses["fantome"].status_code == 429
    assert int(reponses["alice"].headers["retry-after"]) > 0
    assert reponses["alice"].json().keys() == reponses["fantome"].json().keys()


def test_une_adresse_qui_echoue_est_bloquee_sur_tous_les_identifiants(client) -> None:
    attaquant = TestClient(api_main.app, client=("10.0.0.9", 5000))
    for i in range(5):
        attaquant.post("/api/auth/connexion", json={"identifiant": f"essai{i}", "mot_de_passe": "x"})

    assert attaquant.post("/api/auth/connexion", json={
        "identifiant": "autre", "mot_de_passe": "x"}).status_code == 429


def test_le_code_d_installation_est_limite_aussi(client) -> None:
    code = auth.preparer_installation()
    for _ in range(5):
        assert client.post("/api/auth/installation", json={
            "code": "AAAA-BBBB-CCCC", "identifiant": "admin",
            "mot_de_passe": MOT_DE_PASSE}).status_code == 400

    bloque = client.post("/api/auth/installation", json={
        "code": code, "identifiant": "admin", "mot_de_passe": MOT_DE_PASSE})
    assert bloque.status_code == 429


def test_reset_password_debloque_la_connexion(client) -> None:
    auth.creer_compte("alice", MOT_DE_PASSE)
    for i in range(5):
        TestClient(api_main.app, client=(f"10.1.0.{i}", 5000)).post(
            "/api/auth/connexion", json={"identifiant": "alice", "mot_de_passe": "mauvais"})
    nouveau = TestClient(api_main.app, client=("10.2.0.1", 5000))
    assert nouveau.post("/api/auth/connexion", json={
        "identifiant": "alice", "mot_de_passe": MOT_DE_PASSE}).status_code == 429

    auth.reinitialiser_mot_de_passe("alice", "un autre mot de passe")

    assert nouveau.post("/api/auth/connexion", json={
        "identifiant": "alice", "mot_de_passe": "un autre mot de passe"}).status_code == 200


@pytest.mark.parametrize("chemin", ["/docs", "/redoc", "/openapi.json"])
def test_la_documentation_interactive_est_fermee(client, chemin) -> None:
    auth.creer_compte("admin", MOT_DE_PASSE, admin=True)
    client.post("/api/auth/connexion", json={"identifiant": "admin", "mot_de_passe": MOT_DE_PASSE})

    assert client.get(chemin).status_code == 404


def test_une_origine_etrangere_ne_modifie_rien(client) -> None:
    auth.creer_compte("alice", MOT_DE_PASSE)
    corps = {"identifiant": "alice", "mot_de_passe": MOT_DE_PASSE}

    assert client.post("/api/auth/connexion", json=corps,
                       headers={"Origin": "https://pirate.example"}).status_code == 403
    assert client.post("/api/auth/connexion", json=corps,
                       headers={"Origin": "http://testserver"}).status_code == 200
    port_ui = api_main._port_interface()
    assert client.post("/api/auth/connexion", json=corps,
                       headers={"Origin": f"http://localhost:{port_ui}"}).status_code == 200
    # Sans Origin (curl, script) : accepté. En lecture : jamais bloqué.
    assert client.post("/api/auth/connexion", json=corps).status_code == 200
    assert client.get("/api/auth/etat", headers={"Origin": "https://pirate.example"}).status_code == 200


def test_derriere_le_proxy_l_origine_et_l_adresse_viennent_des_en_tetes() -> None:
    auth.creer_compte("alice", MOT_DE_PASSE)
    proxy = TestClient(api_main.app, client=("127.0.0.1", 5000))
    corps = {"identifiant": "alice", "mot_de_passe": MOT_DE_PASSE}
    lan = {"Origin": "http://macbook.local:8770", "X-Forwarded-Host": "macbook.local:8770",
           "X-Forwarded-Proto": "http", "X-Forwarded-For": "192.168.1.20"}

    assert proxy.post("/api/auth/connexion", json=corps, headers=lan).status_code == 200
    assert proxy.post("/api/auth/connexion", json=corps, headers={
        **lan, "Origin": "http://pirate.local:8770"}).status_code == 403
    # Un client distant ne peut pas se prétendre derrière le proxy.
    distant = TestClient(api_main.app, client=("192.168.1.50", 5000))
    assert distant.post("/api/auth/connexion", json=corps, headers=lan).status_code == 403


def test_l_adresse_transmise_n_est_crue_que_depuis_la_machine() -> None:
    for i in range(5):
        TestClient(api_main.app, client=("127.0.0.1", 5000)).post(
            "/api/auth/connexion", json={"identifiant": f"x{i}", "mot_de_passe": "x"},
            headers={"X-Forwarded-For": "192.168.1.66"})

    bloque = TestClient(api_main.app, client=("127.0.0.1", 5000)).post(
        "/api/auth/connexion", json={"identifiant": "autre", "mot_de_passe": "x"},
        headers={"X-Forwarded-For": "192.168.1.66"})
    voisin = TestClient(api_main.app, client=("127.0.0.1", 5000)).post(
        "/api/auth/connexion", json={"identifiant": "autre2", "mot_de_passe": "x"},
        headers={"X-Forwarded-For": "192.168.1.67"})
    assert bloque.status_code == 429
    assert voisin.status_code == 401
