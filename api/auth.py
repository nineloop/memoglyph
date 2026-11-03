"""Comptes, sessions et code d'installation.

Memoglyph gère lui-même ses comptes, sans fournisseur d'identité : la connexion
est obligatoire partout, en local comme sur un serveur, par un seul chemin de
code.

- **Magasin** : `auth.db`, une base SQLite à côté de `index.db`, sous la racine
  de données. Ce n'est pas un index dérivé : la perdre perd les comptes.
- **Mots de passe** : hachés en Argon2id (`argon2-cffi`), jamais écrits en clair
  ni journalisés.
- **Sessions** : un jeton aléatoire remis dans un cookie `HttpOnly` ; seule son
  empreinte SHA-256 est stockée, et une session se révoque en base.
- **Premier compte** : tant qu'aucun compte n'existe, un code d'installation à
  usage unique est tiré au démarrage. Son empreinte va en base, le code lui-même
  dans un fichier lisible par le seul propriétaire, que `./start.sh` affiche et
  que la création du compte administrateur supprime. Pas de compte par défaut,
  pas de création par variable d'environnement.
- **Jetons d'appareil** : ce qu'un compte remet au compagnon de capture, qui n'a
  pas de navigateur. Affiché une seule fois à la création, stocké en empreinte
  comme une session, retiré un par un. Il n'ouvre que les routes du compagnon,
  listées dans `api.main`.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from core import sensitivity, sqlite_db

# Chemins calculés à l'import depuis la racine de données ; `tests/conftest.py`
# les redirige comme les autres magasins.
DB_PATH = sqlite_db.ROOT / "auth.db"
CODE_PATH = sqlite_db.ROOT / ".memoglyph-installation"

COOKIE = "memoglyph_session"
DUREE_SESSION = timedelta(days=14)
# Limitation des tentatives : au-delà de SEUIL échecs dans la FENÊTRE, chaque
# nouvel essai attend un délai qui double, plafonné à DELAI_MAX. Le plafond
# compte : sans lui, cinq mauvais mots de passe tapés sur l'identifiant de
# l'administrateur le bloqueraient sans fin.
SEUIL_ECHECS = 5
FENETRE_ECHECS = timedelta(minutes=15)
DELAI_BASE = timedelta(seconds=30)
DELAI_MAX = timedelta(minutes=15)
LONGUEUR_MIN_MOT_DE_PASSE = 10
_IDENTIFIANT = re.compile(r"^[A-Za-z0-9._-]{3,64}$")
# Alphabet sans caractères ambigus (0/O, 1/I/L) : le code se recopie à la main.
_ALPHABET_CODE = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

_HACHEUR = PasswordHasher()


class ErreurAuth(ValueError):
    """Refus lisible : identifiant invalide, mot de passe trop court, code faux."""


class TropDeTentatives(ErreurAuth):
    """Refus temporaire après trop d'échecs ; `secondes` avant le prochain essai."""

    def __init__(self, secondes: int) -> None:
        super().__init__(f"Trop de tentatives. Réessayez dans {secondes} s.")
        self.secondes = secondes


@dataclass(frozen=True)
class Utilisateur:
    id: str
    identifiant: str
    admin: bool


def _maintenant() -> datetime:
    return datetime.now(timezone.utc)


def _empreinte(valeur: str) -> str:
    return hashlib.sha256(valeur.encode("utf-8")).hexdigest()


def _connexion() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS utilisateurs (
            id TEXT PRIMARY KEY,
            identifiant TEXT NOT NULL UNIQUE COLLATE NOCASE,
            hachage TEXT NOT NULL,
            admin INTEGER NOT NULL DEFAULT 0,
            actif INTEGER NOT NULL DEFAULT 1,
            cree_le TEXT NOT NULL,
            cloud_ultra_autorise INTEGER NOT NULL DEFAULT 0,
            cloud_ultra_modifie_le TEXT
        );
        CREATE TABLE IF NOT EXISTS sessions (
            empreinte TEXT PRIMARY KEY,
            utilisateur_id TEXT NOT NULL REFERENCES utilisateurs(id) ON DELETE CASCADE,
            cree_le TEXT NOT NULL,
            expire_le TEXT NOT NULL,
            revoquee INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS installation (
            cle INTEGER PRIMARY KEY CHECK (cle = 1),
            empreinte TEXT NOT NULL,
            cree_le TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS echecs (
            cle TEXT NOT NULL,
            instant TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS echecs_cle ON echecs (cle, instant);
        CREATE TABLE IF NOT EXISTS jetons_appareil (
            id TEXT PRIMARY KEY,
            empreinte TEXT NOT NULL UNIQUE,
            utilisateur_id TEXT NOT NULL REFERENCES utilisateurs(id) ON DELETE CASCADE,
            nom TEXT NOT NULL,
            cree_le TEXT NOT NULL,
            dernier_usage TEXT
        );
    """)
    # Magasin créé avant la colonne `actif` : on l'ajoute, tous actifs.
    colonnes = {ligne["name"] for ligne in con.execute("PRAGMA table_info(utilisateurs)")}
    if "actif" not in colonnes:
        con.execute("ALTER TABLE utilisateurs ADD COLUMN actif INTEGER NOT NULL DEFAULT 1")
        con.commit()
    # Magasin créé avant le réglage cloud de l'ultra-confidentiel : on l'ajoute, inactif
    # pour tous. Aucun compte, existant ou neuf, ne l'a sans l'avoir activé lui-même.
    if "cloud_ultra_autorise" not in colonnes:
        con.execute("ALTER TABLE utilisateurs ADD COLUMN cloud_ultra_autorise "
                    "INTEGER NOT NULL DEFAULT 0")
        con.commit()
    if "cloud_ultra_modifie_le" not in colonnes:
        con.execute("ALTER TABLE utilisateurs ADD COLUMN cloud_ultra_modifie_le TEXT")
        con.commit()
    return con


def nombre_de_comptes() -> int:
    with closing(_connexion()) as con:
        return con.execute("SELECT COUNT(*) FROM utilisateurs").fetchone()[0]


def installation_requise() -> bool:
    return nombre_de_comptes() == 0


def _valider(identifiant: str, mot_de_passe: str) -> str:
    identifiant = (identifiant or "").strip()
    if not _IDENTIFIANT.match(identifiant):
        raise ErreurAuth("Identifiant invalide : 3 à 64 caractères parmi lettres, "
                         "chiffres, point, tiret et soulignement.")
    if len(mot_de_passe or "") < LONGUEUR_MIN_MOT_DE_PASSE:
        raise ErreurAuth(f"Mot de passe trop court : {LONGUEUR_MIN_MOT_DE_PASSE} "
                         "caractères au minimum.")
    return identifiant


def _inserer(con: sqlite3.Connection, identifiant: str, mot_de_passe: str,
             admin: bool) -> Utilisateur:
    utilisateur = Utilisateur(secrets.token_hex(8), identifiant, admin)
    try:
        con.execute(
            "INSERT INTO utilisateurs (id, identifiant, hachage, admin, cree_le) "
            "VALUES (?, ?, ?, ?, ?)",
            (utilisateur.id, identifiant, _HACHEUR.hash(mot_de_passe), int(admin),
             _maintenant().isoformat()))
    except sqlite3.IntegrityError as exc:
        raise ErreurAuth("Cet identifiant est déjà pris.") from exc
    return utilisateur


def creer_compte(identifiant: str, mot_de_passe: str, *, admin: bool = False) -> Utilisateur:
    """Crée un compte. Réservé à un administrateur, vérifié par la route appelante."""
    identifiant = _valider(identifiant, mot_de_passe)
    with closing(_connexion()) as con, con:
        return _inserer(con, identifiant, mot_de_passe, admin)


def lister_comptes() -> list[dict]:
    with closing(_connexion()) as con:
        lignes = con.execute(
            "SELECT identifiant, admin, actif, cree_le FROM utilisateurs "
            "ORDER BY cree_le").fetchall()
    return [{"identifiant": ligne["identifiant"], "admin": bool(ligne["admin"]),
             "actif": bool(ligne["actif"]), "cree_le": ligne["cree_le"]}
            for ligne in lignes]


def desactiver_compte(identifiant: str) -> None:
    """Le compte ne se connecte plus et ses sessions sont révoquées.

    Pas de suppression en v1 : supprimer un compte toucherait à ses données.
    Le dernier administrateur actif ne peut pas être désactivé, sinon plus
    personne ne pourrait créer ni réactiver de compte depuis l'interface.
    """
    with closing(_connexion()) as con, con:
        ligne = con.execute("SELECT id, admin, actif FROM utilisateurs WHERE identifiant = ?",
                            ((identifiant or "").strip(),)).fetchone()
        if ligne is None:
            raise ErreurAuth(f"Aucun compte « {identifiant} ».")
        if ligne["admin"] and ligne["actif"]:
            autres = con.execute(
                "SELECT COUNT(*) FROM utilisateurs WHERE admin = 1 AND actif = 1 AND id != ?",
                (ligne["id"],)).fetchone()[0]
            if not autres:
                raise ErreurAuth("Impossible de désactiver le dernier administrateur actif.")
        con.execute("UPDATE utilisateurs SET actif = 0 WHERE id = ?", (ligne["id"],))
        con.execute("UPDATE sessions SET revoquee = 1 WHERE utilisateur_id = ?", (ligne["id"],))
        # Comme les sessions : une réactivation ne rend pas les jetons d'avant.
        con.execute("DELETE FROM jetons_appareil WHERE utilisateur_id = ?", (ligne["id"],))


def reactiver_compte(identifiant: str) -> None:
    with closing(_connexion()) as con, con:
        modifies = con.execute("UPDATE utilisateurs SET actif = 1 WHERE identifiant = ?",
                               ((identifiant or "").strip(),)).rowcount
    if not modifies:
        raise ErreurAuth(f"Aucun compte « {identifiant} ».")


# ---------- Limitation des tentatives ----------

def cle_compte(identifiant: str) -> str:
    return "compte:" + (identifiant or "").strip().lower()


def attente(cles: list[str]) -> int:
    """Secondes à attendre avant un nouvel essai pour ces clés ; 0 si libre."""
    maintenant = _maintenant()
    depuis = (maintenant - FENETRE_ECHECS).isoformat()
    pire = 0
    with closing(_connexion()) as con:
        for cle in cles:
            lignes = con.execute(
                "SELECT instant FROM echecs WHERE cle = ? AND instant > ? ORDER BY instant",
                (cle, depuis)).fetchall()
            if len(lignes) < SEUIL_ECHECS:
                continue
            delai = min(DELAI_MAX, DELAI_BASE * 2 ** (len(lignes) - SEUIL_ECHECS))
            fin = datetime.fromisoformat(lignes[-1]["instant"]) + delai
            pire = max(pire, int((fin - maintenant).total_seconds()) + 1)
    return max(pire, 0)


def verifier_attente(cles: list[str]) -> None:
    secondes = attente(cles)
    if secondes > 0:
        raise TropDeTentatives(secondes)


def noter_echec(cles: list[str]) -> None:
    maintenant = _maintenant()
    with closing(_connexion()) as con, con:
        con.executemany("INSERT INTO echecs (cle, instant) VALUES (?, ?)",
                        [(cle, maintenant.isoformat()) for cle in cles])
        con.execute("DELETE FROM echecs WHERE instant <= ?",
                    ((maintenant - FENETRE_ECHECS).isoformat(),))


def effacer_echecs(cle: str) -> None:
    with closing(_connexion()) as con, con:
        con.execute("DELETE FROM echecs WHERE cle = ?", (cle,))


# ---------- Code d'installation ----------

def preparer_installation() -> Optional[str]:
    """Tire un code à usage unique si aucun compte n'existe ; sinon ne fait rien.

    Appelé au démarrage de l'API. Un code déjà tiré et encore valide est gardé :
    redémarrer l'API ne doit pas invalider celui que l'utilisateur recopie.
    Rend le code tiré, ou `None` si aucun n'est nécessaire.
    """
    if not installation_requise():
        retirer_code_installation()
        return None
    with closing(_connexion()) as con, con:
        existant = con.execute("SELECT empreinte FROM installation").fetchone()
        if existant and CODE_PATH.is_file():
            code = CODE_PATH.read_text(encoding="utf-8").strip()
            if hmac.compare_digest(_empreinte(code), existant["empreinte"]):
                return code
        code = "-".join("".join(secrets.choice(_ALPHABET_CODE) for _ in range(4))
                        for _ in range(3))
        con.execute("INSERT OR REPLACE INTO installation (cle, empreinte, cree_le) "
                    "VALUES (1, ?, ?)", (_empreinte(code), _maintenant().isoformat()))
    CODE_PATH.parent.mkdir(parents=True, exist_ok=True)
    descripteur = os.open(CODE_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descripteur, "w", encoding="utf-8") as f:
        f.write(code + "\n")
    os.chmod(CODE_PATH, 0o600)
    return code


def retirer_code_installation() -> None:
    CODE_PATH.unlink(missing_ok=True)
    with closing(_connexion()) as con, con:
        con.execute("DELETE FROM installation")


def installer(code: str, identifiant: str, mot_de_passe: str) -> Utilisateur:
    """Crée le compte administrateur avec le code d'installation, une seule fois."""
    identifiant = _valider(identifiant, mot_de_passe)
    with closing(_connexion()) as con, con:
        if con.execute("SELECT COUNT(*) FROM utilisateurs").fetchone()[0]:
            raise ErreurAuth("L'installation est déjà faite.")
        attendu = con.execute("SELECT empreinte FROM installation").fetchone()
        if not attendu or not hmac.compare_digest(
                _empreinte((code or "").strip().upper()), attendu["empreinte"]):
            raise ErreurAuth("Code d'installation invalide.")
        utilisateur = _inserer(con, identifiant, mot_de_passe, admin=True)
        con.execute("DELETE FROM installation")
    CODE_PATH.unlink(missing_ok=True)
    return utilisateur


# ---------- Connexion et sessions ----------

def verifier_identifiants(identifiant: str, mot_de_passe: str) -> Optional[Utilisateur]:
    """L'utilisateur si le couple est bon, sinon `None`, sans dire lequel est faux."""
    with closing(_connexion()) as con:
        ligne = con.execute("SELECT * FROM utilisateurs WHERE identifiant = ?",
                            ((identifiant or "").strip(),)).fetchone()
        if ligne is None:
            # Même coût qu'une vérification réelle : la durée de la réponse ne
            # révèle pas qu'un identifiant existe.
            _HACHEUR.hash(mot_de_passe or "")
            return None
        try:
            _HACHEUR.verify(ligne["hachage"], mot_de_passe or "")
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return None
        if not ligne["actif"]:
            return None
        if _HACHEUR.check_needs_rehash(ligne["hachage"]):
            with con:
                con.execute("UPDATE utilisateurs SET hachage = ? WHERE id = ?",
                            (_HACHEUR.hash(mot_de_passe), ligne["id"]))
        return Utilisateur(ligne["id"], ligne["identifiant"], bool(ligne["admin"]))


def ouvrir_session(utilisateur: Utilisateur) -> str:
    """Rend le jeton à placer dans le cookie ; seule son empreinte est stockée."""
    jeton = secrets.token_urlsafe(32)
    maintenant = _maintenant()
    with closing(_connexion()) as con, con:
        con.execute(
            "INSERT INTO sessions (empreinte, utilisateur_id, cree_le, expire_le) "
            "VALUES (?, ?, ?, ?)",
            (_empreinte(jeton), utilisateur.id, maintenant.isoformat(),
             (maintenant + DUREE_SESSION).isoformat()))
    return jeton


def utilisateur_actif(identifiant: str) -> Optional[Utilisateur]:
    """Le compte actif de cet identifiant, sans mot de passe : pour une commande lancée
    sur la machine (`memoglyph mcp`), où l'accès au serveur fait foi. Rien n'est écrit."""
    with closing(_connexion()) as con:
        ligne = con.execute("SELECT id, identifiant, admin FROM utilisateurs "
                            "WHERE identifiant = ? AND actif = 1",
                            ((identifiant or "").strip(),)).fetchone()
    return None if ligne is None else Utilisateur(ligne["id"], ligne["identifiant"], bool(ligne["admin"]))


def utilisateur_de_session(jeton: Optional[str]) -> Optional[Utilisateur]:
    if not jeton:
        return None
    with closing(_connexion()) as con:
        ligne = con.execute(
            "SELECT u.id, u.identifiant, u.admin, s.expire_le, s.revoquee "
            "FROM sessions s JOIN utilisateurs u ON u.id = s.utilisateur_id "
            "WHERE s.empreinte = ? AND u.actif = 1", (_empreinte(jeton),)).fetchone()
    if ligne is None or ligne["revoquee"]:
        return None
    if datetime.fromisoformat(ligne["expire_le"]) <= _maintenant():
        return None
    return Utilisateur(ligne["id"], ligne["identifiant"], bool(ligne["admin"]))


def revoquer_session(jeton: Optional[str]) -> None:
    if not jeton:
        return
    with closing(_connexion()) as con, con:
        con.execute("UPDATE sessions SET revoquee = 1 WHERE empreinte = ?",
                    (_empreinte(jeton),))


# ---------- Jetons d'appareil ----------

LONGUEUR_MAX_NOM_JETON = 64
# Préfixe lisible : on reconnaît un jeton Memoglyph collé au mauvais endroit.
PREFIXE_JETON = "mgj_"
# `dernier_usage` n'est réécrit qu'au-delà de ce délai : le compagnon interroge
# l'état de son travail en boucle, une écriture par appel serait du bruit.
PAS_DERNIER_USAGE = timedelta(minutes=1)


def _jeton_public(ligne: sqlite3.Row) -> dict:
    return {"id": ligne["id"], "nom": ligne["nom"], "cree_le": ligne["cree_le"],
            "dernier_usage": ligne["dernier_usage"]}


def creer_jeton(utilisateur: Utilisateur, nom: str) -> tuple[dict, str]:
    """Crée un jeton d'appareil ; rend sa description et le jeton, à montrer une fois."""
    nom = " ".join((nom or "").split())
    if not nom or len(nom) > LONGUEUR_MAX_NOM_JETON:
        raise ErreurAuth(f"Nom d'appareil invalide : 1 à {LONGUEUR_MAX_NOM_JETON} caractères.")
    jeton = PREFIXE_JETON + secrets.token_urlsafe(32)
    with closing(_connexion()) as con, con:
        con.execute(
            "INSERT INTO jetons_appareil (id, empreinte, utilisateur_id, nom, cree_le) "
            "VALUES (?, ?, ?, ?, ?)",
            (secrets.token_hex(8), _empreinte(jeton), utilisateur.id, nom,
             _maintenant().isoformat(timespec="seconds")))
        ligne = con.execute("SELECT * FROM jetons_appareil WHERE empreinte = ?",
                            (_empreinte(jeton),)).fetchone()
    return _jeton_public(ligne), jeton


def lister_jetons(utilisateur: Utilisateur) -> list[dict]:
    with closing(_connexion()) as con:
        return [_jeton_public(ligne) for ligne in con.execute(
            "SELECT * FROM jetons_appareil WHERE utilisateur_id = ? ORDER BY cree_le, id",
            (utilisateur.id,))]


def retirer_jeton(utilisateur: Utilisateur, jeton_id: str) -> bool:
    """Retire un jeton du compte ; faux s'il n'existe pas ou n'est pas à lui."""
    with closing(_connexion()) as con, con:
        return con.execute("DELETE FROM jetons_appareil WHERE id = ? AND utilisateur_id = ?",
                           (jeton_id, utilisateur.id)).rowcount > 0


def utilisateur_de_jeton(jeton: Optional[str]) -> Optional[Utilisateur]:
    """Le compte d'un jeton d'appareil valide, d'un compte actif ; sinon `None`."""
    if not jeton:
        return None
    with closing(_connexion()) as con, con:
        ligne = con.execute(
            "SELECT j.id AS jeton_id, j.dernier_usage, u.id, u.identifiant, u.admin "
            "FROM jetons_appareil j JOIN utilisateurs u ON u.id = j.utilisateur_id "
            "WHERE j.empreinte = ? AND u.actif = 1", (_empreinte(jeton),)).fetchone()
        if ligne is None:
            return None
        maintenant = _maintenant()
        if (ligne["dernier_usage"] is None
                or datetime.fromisoformat(ligne["dernier_usage"]) <= maintenant - PAS_DERNIER_USAGE):
            con.execute("UPDATE jetons_appareil SET dernier_usage = ? WHERE id = ?",
                        (maintenant.isoformat(timespec="seconds"), ligne["jeton_id"]))
    return Utilisateur(ligne["id"], ligne["identifiant"], bool(ligne["admin"]))


def id_du_jeton(jeton: Optional[str]) -> Optional[str]:
    """L'identifiant PUBLIC d'un jeton d'appareil (jamais son secret), ou `None`.

    Il sert à relier un traitement au jeton qui l'a lancé : avec ce jeton, le compagnon ne voit
    que ses propres captures.
    """
    if not jeton:
        return None
    with closing(_connexion()) as con:
        ligne = con.execute("SELECT id FROM jetons_appareil WHERE empreinte = ?",
                            (_empreinte(jeton),)).fetchone()
    return None if ligne is None else ligne["id"]


def reinitialiser_mot_de_passe(identifiant: str, mot_de_passe: str) -> None:
    """Remplace le mot de passe, révoque les sessions et lève le blocage du compte."""
    if len(mot_de_passe or "") < LONGUEUR_MIN_MOT_DE_PASSE:
        raise ErreurAuth(f"Mot de passe trop court : {LONGUEUR_MIN_MOT_DE_PASSE} "
                         "caractères au minimum.")
    with closing(_connexion()) as con, con:
        ligne = con.execute("SELECT id FROM utilisateurs WHERE identifiant = ?",
                            ((identifiant or "").strip(),)).fetchone()
        if ligne is None:
            raise ErreurAuth(f"Aucun compte « {identifiant} ».")
        con.execute("UPDATE utilisateurs SET hachage = ? WHERE id = ?",
                    (_HACHEUR.hash(mot_de_passe), ligne["id"]))
        con.execute("UPDATE sessions SET revoquee = 1 WHERE utilisateur_id = ?",
                    (ligne["id"],))
    effacer_echecs(cle_compte(identifiant))


def chemin_code_installation() -> Path:
    return CODE_PATH


# ---------- Cloud pour l'ultra-confidentiel ----------
#
# Réglage du compte : autorise-t-il un fournisseur cloud pour SES documents
# ultra-confidentiels (`core.sensitivity`) ? Non par défaut. Il ne se lit et ne se
# change que pour le compte de la session (`api.main`), jamais pour un autre, ni
# par un jeton d'appareil.

def cloud_ultra(utilisateur: Utilisateur) -> dict:
    """Le réglage du compte : `autorise` et la date du dernier changement (ou None)."""
    with closing(_connexion()) as con:
        ligne = con.execute("SELECT cloud_ultra_autorise, cloud_ultra_modifie_le "
                            "FROM utilisateurs WHERE id = ?", (utilisateur.id,)).fetchone()
    if ligne is None:
        return {"autorise": False, "modifie_le": None}
    return {"autorise": bool(ligne["cloud_ultra_autorise"]),
            "modifie_le": ligne["cloud_ultra_modifie_le"]}


def regler_cloud_ultra(utilisateur: Utilisateur, autorise: bool) -> dict:
    """Change le réglage du compte et date le changement ; rend le réglage relu."""
    with closing(_connexion()) as con, con:
        con.execute("UPDATE utilisateurs SET cloud_ultra_autorise = ?, cloud_ultra_modifie_le = ? "
                    "WHERE id = ?",
                    (int(bool(autorise)), _maintenant().isoformat(timespec="seconds"),
                     utilisateur.id))
    return cloud_ultra(utilisateur)


def cloud_ultra_autorise(identifiant: str) -> bool:
    """Le propriétaire `identifiant` autorise-t-il le cloud pour son ultra-confidentiel ?

    Le résolveur de `core.sensitivity` : relu à chaque envoi, d'un compte ACTIF
    seulement. Un compte désactivé n'autorise plus rien.
    """
    with closing(_connexion()) as con:
        ligne = con.execute("SELECT cloud_ultra_autorise FROM utilisateurs "
                            "WHERE identifiant = ? AND actif = 1",
                            ((identifiant or "").strip(),)).fetchone()
    return ligne is not None and bool(ligne["cloud_ultra_autorise"])


# Branché à l'import : `api.main` et `api.mcp` importent ce module, et le cœur ne
# peut pas importer `api/`. Sans lui, `core.sensitivity` répond toujours non.
sensitivity.enregistrer_resolveur(cloud_ultra_autorise)
