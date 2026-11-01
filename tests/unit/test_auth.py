"""Magasin de comptes : hachage, sessions, code d'installation, réinitialisation.

Chaque test part d'un magasin vide, dans un dossier jetable : le magasin isolé
de la session porte le compte de `tests/conftest.py`.
"""
from __future__ import annotations

import sqlite3
import stat
from datetime import timedelta

import pytest

from api import auth, cli

pytestmark = [pytest.mark.unit, pytest.mark.sans_session]

MOT_DE_PASSE = "un mot de passe solide"


@pytest.fixture(autouse=True)
def magasin_vide(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(auth, "CODE_PATH", tmp_path / ".memoglyph-installation")
    return tmp_path


def test_le_mot_de_passe_n_est_stocke_qu_en_argon2id(magasin_vide) -> None:
    auth.creer_compte("alice", MOT_DE_PASSE)

    with sqlite3.connect(auth.DB_PATH) as con:
        [hachage] = con.execute("SELECT hachage FROM utilisateurs").fetchone()
    assert hachage.startswith("$argon2id$")
    assert MOT_DE_PASSE not in hachage
    assert MOT_DE_PASSE.encode() not in auth.DB_PATH.read_bytes()


def test_la_verification_rend_l_utilisateur_ou_rien() -> None:
    auth.creer_compte("alice", MOT_DE_PASSE, admin=True)

    assert auth.verifier_identifiants("alice", MOT_DE_PASSE) == auth.Utilisateur(
        auth.verifier_identifiants("alice", MOT_DE_PASSE).id, "alice", True)
    assert auth.verifier_identifiants("ALICE", MOT_DE_PASSE) is not None
    assert auth.verifier_identifiants("alice", "mauvais mot de passe") is None
    assert auth.verifier_identifiants("bob", MOT_DE_PASSE) is None


@pytest.mark.parametrize("identifiant,mot_de_passe", [
    ("ab", MOT_DE_PASSE), ("a b c", MOT_DE_PASSE), ("alice", "court"), ("", MOT_DE_PASSE),
])
def test_les_identifiants_invalides_sont_refuses(identifiant, mot_de_passe) -> None:
    with pytest.raises(auth.ErreurAuth):
        auth.creer_compte(identifiant, mot_de_passe)


def test_un_identifiant_ne_sert_qu_une_fois_casse_comprise() -> None:
    auth.creer_compte("alice", MOT_DE_PASSE)

    with pytest.raises(auth.ErreurAuth, match="déjà pris"):
        auth.creer_compte("Alice", MOT_DE_PASSE)


def test_la_session_ne_stocke_que_l_empreinte_du_jeton() -> None:
    utilisateur = auth.creer_compte("alice", MOT_DE_PASSE)
    jeton = auth.ouvrir_session(utilisateur)

    with sqlite3.connect(auth.DB_PATH) as con:
        [empreinte] = con.execute("SELECT empreinte FROM sessions").fetchone()
    assert empreinte != jeton
    assert jeton.encode() not in auth.DB_PATH.read_bytes()
    assert auth.utilisateur_de_session(jeton) == utilisateur


def test_une_session_revoquee_ou_expiree_ne_vaut_plus(monkeypatch) -> None:
    utilisateur = auth.creer_compte("alice", MOT_DE_PASSE)
    revoquee, expiree = auth.ouvrir_session(utilisateur), None
    monkeypatch.setattr(auth, "DUREE_SESSION", timedelta(seconds=-1))
    expiree = auth.ouvrir_session(utilisateur)

    auth.revoquer_session(revoquee)

    assert auth.utilisateur_de_session(revoquee) is None
    assert auth.utilisateur_de_session(expiree) is None
    assert auth.utilisateur_de_session("jeton-inconnu") is None
    assert auth.utilisateur_de_session(None) is None


def test_le_code_d_installation_n_existe_que_sans_compte(magasin_vide) -> None:
    code = auth.preparer_installation()

    assert code and len(code) == 14
    assert auth.CODE_PATH.read_text().strip() == code
    assert stat.S_IMODE(auth.CODE_PATH.stat().st_mode) == 0o600
    with sqlite3.connect(auth.DB_PATH) as con:
        [empreinte] = con.execute("SELECT empreinte FROM installation").fetchone()
    assert empreinte != code
    # Redémarrer l'API ne change pas le code que l'utilisateur recopie.
    assert auth.preparer_installation() == code


def test_l_installation_cree_l_administrateur_une_seule_fois() -> None:
    code = auth.preparer_installation()

    with pytest.raises(auth.ErreurAuth, match="Code d'installation invalide"):
        auth.installer("AAAA-BBBB-CCCC", "admin", MOT_DE_PASSE)
    administrateur = auth.installer(code.lower(), "admin", MOT_DE_PASSE)

    assert administrateur.admin
    assert not auth.CODE_PATH.exists()
    assert not auth.installation_requise()
    with pytest.raises(auth.ErreurAuth, match="déjà faite"):
        auth.installer(code, "autre", MOT_DE_PASSE)
    # Un compte existe : plus aucun code n'est tiré au démarrage.
    assert auth.preparer_installation() is None


def test_la_reinitialisation_remplace_le_mot_de_passe_et_revoque_les_sessions() -> None:
    utilisateur = auth.creer_compte("alice", MOT_DE_PASSE)
    jeton = auth.ouvrir_session(utilisateur)

    auth.reinitialiser_mot_de_passe("alice", "un autre mot de passe")

    assert auth.utilisateur_de_session(jeton) is None
    assert auth.verifier_identifiants("alice", MOT_DE_PASSE) is None
    assert auth.verifier_identifiants("alice", "un autre mot de passe") is not None
    with pytest.raises(auth.ErreurAuth, match="Aucun compte"):
        auth.reinitialiser_mot_de_passe("bob", "un autre mot de passe")
    with pytest.raises(auth.ErreurAuth, match="trop court"):
        auth.reinitialiser_mot_de_passe("alice", "court")


def test_la_commande_reset_password(monkeypatch, capsys) -> None:
    auth.creer_compte("alice", MOT_DE_PASSE)
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("nouveau mot de passe\n"))

    assert cli.main(["reset-password", "alice", "--stdin"]) == 0
    assert "remplacé" in capsys.readouterr().out
    assert auth.verifier_identifiants("alice", "nouveau mot de passe") is not None


def test_la_commande_reset_password_refuse_deux_saisies_differentes(monkeypatch, capsys) -> None:
    auth.creer_compte("alice", MOT_DE_PASSE)
    saisies = iter(["premier mot de passe", "second mot de passe"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda invite: next(saisies))

    assert cli.main(["reset-password", "alice"]) == 1
    assert "diffèrent" in capsys.readouterr().err
    assert auth.verifier_identifiants("alice", MOT_DE_PASSE) is not None


def test_la_commande_signale_un_compte_inconnu(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("nouveau mot de passe\n"))

    assert cli.main(["reset-password", "inconnu", "--stdin"]) == 1
    assert "Aucun compte" in capsys.readouterr().err


# ---------- Désactivation (auth 2) ----------

def test_un_compte_desactive_ne_se_connecte_plus_et_perd_ses_sessions() -> None:
    auth.creer_compte("admin", MOT_DE_PASSE, admin=True)
    alice = auth.creer_compte("alice", MOT_DE_PASSE)
    jeton = auth.ouvrir_session(alice)

    auth.desactiver_compte("alice")

    assert auth.utilisateur_de_session(jeton) is None
    assert auth.verifier_identifiants("alice", MOT_DE_PASSE) is None
    assert {c["identifiant"]: c["actif"] for c in auth.lister_comptes()} == {
        "admin": True, "alice": False}
    auth.reactiver_compte("alice")
    assert auth.verifier_identifiants("alice", MOT_DE_PASSE) is not None
    # Les sessions révoquées le restent : réactiver n'en rouvre aucune.
    assert auth.utilisateur_de_session(jeton) is None


def test_le_dernier_administrateur_actif_ne_peut_pas_etre_desactive() -> None:
    auth.creer_compte("admin", MOT_DE_PASSE, admin=True)

    with pytest.raises(auth.ErreurAuth, match="dernier administrateur"):
        auth.desactiver_compte("admin")
    auth.creer_compte("second", MOT_DE_PASSE, admin=True)
    auth.desactiver_compte("admin")
    with pytest.raises(auth.ErreurAuth, match="dernier administrateur"):
        auth.desactiver_compte("second")
    with pytest.raises(auth.ErreurAuth, match="Aucun compte"):
        auth.desactiver_compte("inconnu")
    with pytest.raises(auth.ErreurAuth, match="Aucun compte"):
        auth.reactiver_compte("inconnu")


def test_un_magasin_d_avant_la_colonne_actif_est_migre(magasin_vide) -> None:
    with sqlite3.connect(auth.DB_PATH) as con:
        con.execute("CREATE TABLE utilisateurs (id TEXT PRIMARY KEY, identifiant TEXT NOT NULL "
                    "UNIQUE COLLATE NOCASE, hachage TEXT NOT NULL, admin INTEGER NOT NULL "
                    "DEFAULT 0, cree_le TEXT NOT NULL)")
        con.execute("INSERT INTO utilisateurs VALUES ('1', 'ancien', 'x', 1, '2026-01-01')")

    assert auth.lister_comptes() == [
        {"identifiant": "ancien", "admin": True, "actif": True, "cree_le": "2026-01-01"}]


# ---------- Limitation des tentatives (auth 2) ----------

def test_le_blocage_commence_au_cinquieme_echec_et_double(monkeypatch) -> None:
    cles = ["compte:alice"]
    for _ in range(4):
        auth.noter_echec(cles)
    assert auth.attente(cles) == 0

    auth.noter_echec(cles)
    premier = auth.attente(cles)
    auth.noter_echec(cles)
    second = auth.attente(cles)

    assert 0 < premier <= 31
    assert 31 < second <= 61
    with pytest.raises(auth.TropDeTentatives) as refus:
        auth.verifier_attente(cles)
    assert refus.value.secondes == second


def test_le_delai_est_plafonne_a_quinze_minutes() -> None:
    for _ in range(40):
        auth.noter_echec(["compte:admin"])

    assert auth.attente(["compte:admin"]) <= 15 * 60 + 1


def test_les_echecs_hors_fenetre_ne_comptent_plus(monkeypatch) -> None:
    for _ in range(6):
        auth.noter_echec(["adresse:1.2.3.4"])
    monkeypatch.setattr(auth, "FENETRE_ECHECS", timedelta(seconds=0))

    assert auth.attente(["adresse:1.2.3.4"]) == 0


def test_reset_password_leve_le_blocage_du_compte() -> None:
    auth.creer_compte("alice", MOT_DE_PASSE)
    for _ in range(6):
        auth.noter_echec([auth.cle_compte("Alice")])
    assert auth.attente([auth.cle_compte("alice")]) > 0

    auth.reinitialiser_mot_de_passe("alice", "un autre mot de passe")

    assert auth.attente([auth.cle_compte("alice")]) == 0
