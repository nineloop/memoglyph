"""Les réglages du traitement retenus PAR COMPTE : `GET` et `PUT /api/compte/reglages-traitement`.

Session seulement (un jeton d'appareil n'ouvre pas cette route), étanches entre comptes, vocabulaire fermé refusé à l'écriture,
migration d'une ancienne `auth.db`. Les clés retenues vivent dans UNE constante (`auth.CLES_RETENUES`) : ni plus, ni moins. La sensibilité n'est retenue que si elle est
plus stricte que le défaut de l'instance.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from api import auth
from api import main as api_main
from api.jobs import JobManager

pytestmark = [pytest.mark.integration, pytest.mark.sans_session]

MOT_DE_PASSE = "un mot de passe solide"
URL = "/api/compte/reglages-traitement"
REGLAGES = {"fournisseur": "groq", "langue": "fr",
            "resultats": {"compte_rendu": True, "titre_resume": True, "locuteurs": False}}


@pytest.fixture
def comptes(isolated_stores, tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(auth, "CODE_PATH", tmp_path / ".memoglyph-installation")
    monkeypatch.setattr(api_main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(api_main, "jobs", JobManager())
    auth.creer_compte("alice", MOT_DE_PASSE)
    auth.creer_compte("bruno", MOT_DE_PASSE)
    return tmp_path


def connecte(identifiant: str) -> TestClient:
    client = TestClient(api_main.app)
    assert client.post("/api/auth/connexion", json={
        "identifiant": identifiant, "mot_de_passe": MOT_DE_PASSE}).status_code == 200
    return client


def test_sans_rien_retenu_la_lecture_rend_null(comptes) -> None:
    assert connecte("alice").get(URL).json() == {"reglages": None}


def test_ecrire_puis_lire_rend_les_memes_reglages_et_une_seconde_ecriture_remplace(comptes) -> None:
    alice = connecte("alice")
    reponse = alice.put(URL, json=REGLAGES)
    assert reponse.status_code == 200, reponse.text
    assert reponse.json() == {"reglages": REGLAGES}
    assert connecte("alice").get(URL).json() == {"reglages": REGLAGES}            # une autre session du même compte
    autres = {**REGLAGES, "fournisseur": "mlx", "langue": ""}
    assert alice.put(URL, json=autres).json() == {"reglages": autres}
    assert alice.get(URL).json() == {"reglages": autres}


def test_un_compte_ne_voit_ni_ne_change_les_reglages_d_un_autre(comptes) -> None:
    alice, bruno = connecte("alice"), connecte("bruno")
    alice.put(URL, json=REGLAGES)
    assert bruno.get(URL).json() == {"reglages": None}
    autres = {**REGLAGES, "fournisseur": "mlx"}
    assert bruno.put(URL, json=autres).json() == {"reglages": autres}
    assert alice.get(URL).json() == {"reglages": REGLAGES}


def test_aucun_champ_ne_designe_un_autre_compte(comptes) -> None:
    """Un `identifiant` dans le corps est REFUSÉ, pas ignoré (comme pour `cloud-ultra`)."""
    reponse = connecte("alice").put(URL, json={**REGLAGES, "identifiant": "bruno"})
    assert reponse.status_code == 422


def test_un_jeton_d_appareil_n_ouvre_pas_la_route(comptes) -> None:
    alice = connecte("alice")
    secret = alice.post("/api/jetons", json={"nom": "MacBook de test"}).json()["secret"]
    compagnon = TestClient(api_main.app, headers={"Authorization": f"Bearer {secret}"})
    assert compagnon.get(URL).status_code in (401, 403)
    assert compagnon.put(URL, json=REGLAGES).status_code in (401, 403)
    assert ("GET", URL) not in api_main.ROUTES_DU_COMPAGNON and ("PUT", URL) not in api_main.ROUTES_DU_COMPAGNON


def test_sans_session_la_route_est_refusee(comptes) -> None:
    anonyme = TestClient(api_main.app)
    assert anonyme.get(URL).status_code == 401
    assert anonyme.put(URL, json=REGLAGES).status_code == 401


@pytest.mark.parametrize("corps", [
    {**REGLAGES, "fournisseur": "cloud"},                                            # l'alias n'est pas un moteur
    {**REGLAGES, "fournisseur": "inconnu"},
    {**REGLAGES, "fournisseur": ""},
    {**REGLAGES, "langue": "français !!"},
    {**REGLAGES, "langue": "x" * 13},
    {**REGLAGES, "resultats": {"compte_rendu": True, "titre_resume": True}},          # un booléen manque
    {**REGLAGES, "resultats": {"compte_rendu": "oui", "titre_resume": True, "locuteurs": False}},
    {**REGLAGES, "resultats": {**REGLAGES["resultats"], "extra": True}},
    {**REGLAGES, "inconnue": 1},
    {"fournisseur": "groq"},                                                          # incomplet
], ids=["alias cloud", "fournisseur inconnu", "fournisseur vide", "langue mal formée", "langue trop longue",
        "booléen absent", "booléen texte", "résultat inconnu", "clé inconnue", "incomplet"])
def test_un_vocabulaire_hors_du_ferme_est_refuse_a_l_ecriture(comptes, corps) -> None:
    alice = connecte("alice")
    assert alice.put(URL, json=corps).status_code == 422
    assert alice.get(URL).json() == {"reglages": None}, "un refus n'écrit rien"


def test_les_cles_retenues_sont_une_seule_liste_qui_porte_la_sensibilite(comptes) -> None:
    """`auth.CLES_RETENUES` est LA liste : ce qui est stocké est exactement ces clés, ni plus ni moins."""
    assert auth.CLES_RETENUES == ("fournisseur", "langue", "resultats", "sensibilite")
    alice = connecte("alice")
    alice.put(URL, json={**REGLAGES, "sensibilite": "confidential"})
    with closing(sqlite3.connect(comptes / "auth.db")) as con:
        brut = con.execute("SELECT reglages_traitement FROM utilisateurs WHERE identifiant = 'alice'").fetchone()[0]
    assert set(json.loads(brut)) == set(auth.CLES_RETENUES)


@pytest.mark.parametrize("niveau", ["confidential", "ultra_confidential"])
def test_une_sensibilite_plus_stricte_que_le_defaut_est_retenue_et_rendue(comptes, niveau) -> None:
    alice = connecte("alice")
    reponse = alice.put(URL, json={**REGLAGES, "sensibilite": niveau})
    assert reponse.status_code == 200, reponse.text
    assert reponse.json()["reglages"]["sensibilite"] == niveau
    assert connecte("alice").get(URL).json()["reglages"]["sensibilite"] == niveau


def test_une_sensibilite_egale_au_defaut_est_refusee_avec_une_phrase_claire_et_rien_n_est_ecrit(comptes) -> None:
    """Le défaut de l'instance est « standard » : retenir « standard » n'est pas plus strict. `<=` au lieu de `<` serait ici."""
    alice = connecte("alice")
    reponse = alice.put(URL, json={**REGLAGES, "sensibilite": "standard"})
    assert reponse.status_code == 422
    assert "plus stricte" in reponse.text
    assert alice.get(URL).json() == {"reglages": None}, "un refus n'écrit rien"


@pytest.mark.parametrize("valeur", ["secret", "", "Confidential", 3])
def test_une_sensibilite_hors_du_vocabulaire_est_refusee(comptes, valeur) -> None:
    alice = connecte("alice")
    reponse = alice.put(URL, json={**REGLAGES, "sensibilite": valeur})
    assert reponse.status_code == 422
    if valeur == "secret":
        assert "inconnue" in reponse.text                  # le vocabulaire est fermé : on le dit, on ne le traite pas en « égale au défaut »
    assert alice.get(URL).json() == {"reglages": None}


def test_sans_sensibilite_dans_le_corps_la_sensibilite_retenue_est_effacee(comptes) -> None:
    """L'écriture REMPLACE : retenir sans sensibilité (l'écran n'en envoie pas quand elle n'est pas plus stricte) l'efface."""
    alice = connecte("alice")
    alice.put(URL, json={**REGLAGES, "sensibilite": "ultra_confidential"})
    reponse = alice.put(URL, json=REGLAGES)
    assert reponse.status_code == 200
    assert "sensibilite" not in reponse.json()["reglages"]
    assert "sensibilite" not in alice.get(URL).json()["reglages"]


def test_le_defaut_releve_au_dessus_du_retenu_refuse_une_nouvelle_ecriture_sans_reecrire_le_compte(comptes, monkeypatch) -> None:
    """Le défaut gagne : « confidentiel » retenu hier n'est plus plus strict qu'un défaut « confidentiel », et la lecture rend
    ce qui a été écrit (le compte n'est jamais réécrit ; c'est l'écran qui applique la plus stricte des deux)."""
    from core import sensitivity
    alice = connecte("alice")
    alice.put(URL, json={**REGLAGES, "sensibilite": "confidential"})
    monkeypatch.setattr(sensitivity, "SENSIBILITE_DEFAUT", "confidential")
    assert alice.get(URL).json()["reglages"]["sensibilite"] == "confidential"          # lu tel qu'écrit
    assert alice.put(URL, json={**REGLAGES, "sensibilite": "confidential"}).status_code == 422
    assert alice.put(URL, json={**REGLAGES, "sensibilite": "ultra_confidential"}).status_code == 200


def test_un_magasin_d_avant_la_colonne_est_migre_sans_perdre_de_compte(comptes) -> None:
    """Une `auth.db` créée avant le réglage : la colonne s'ajoute, les comptes restent, la valeur est nulle."""
    (comptes / "auth.db").unlink()
    with closing(sqlite3.connect(comptes / "auth.db")) as con, con:
        con.execute("CREATE TABLE utilisateurs (id TEXT PRIMARY KEY, identifiant TEXT NOT NULL UNIQUE COLLATE NOCASE, "
                    "hachage TEXT NOT NULL, admin INTEGER NOT NULL DEFAULT 0, actif INTEGER NOT NULL DEFAULT 1, "
                    "cree_le TEXT NOT NULL, cloud_ultra_autorise INTEGER NOT NULL DEFAULT 0, cloud_ultra_modifie_le TEXT)")
        con.execute("INSERT INTO utilisateurs (id, identifiant, hachage, admin, actif, cree_le, cloud_ultra_autorise) "
                    "VALUES ('1', 'ancien', 'x', 0, 1, '2026-01-01', 1)")
    assert auth.lister_comptes() == [{"identifiant": "ancien", "admin": False, "actif": True, "cree_le": "2026-01-01"}]
    ancien = auth.Utilisateur(id="1", identifiant="ancien", admin=False)
    assert auth.reglages_traitement(ancien) is None
    assert auth.cloud_ultra(ancien)["autorise"] is True, "le réglage existant du compte est intact"
    assert auth.regler_traitement(ancien, REGLAGES) == REGLAGES
    assert auth.reglages_traitement(ancien) == REGLAGES
