"""Aucun test ne peut joindre un hôte hors de la machine.

Les fournisseurs en ligne nommés multiplient les endroits où un appel réel pourrait partir. Comme la garde de port
(`test_garde_port_des_tests.py`), la garde réseau est posée par la suite elle-même, avant tout import de `core`.

RÈGLE DE CE FICHIER : chaque test de comportement **commence par vérifier que la garde est posée**. Sans la garde, il
échoue sur cette première ligne, **avant** qu'un paquet ou une requête de noms ne parte : le rouge ne touche pas au
réseau. Les adresses et noms employés après cette ligne sont réservés à la documentation (`192.0.2.1`, RFC 5737 ;
`*.invalid`, RFC 2606) : aucun hôte réel.
"""
from __future__ import annotations

import socket
import subprocess
from contextlib import contextmanager

import pytest

pytestmark = pytest.mark.unit

pytest_plugins = ["pytester"]

DOC_IP = "192.0.2.1"
DOC_NOM = "exemple.invalid"


def _garde_posee() -> bool:
    """La garde est-elle posée ? Un marqueur sur chaque point d'entrée, SANS rien importer d'elle."""
    points = (socket.socket.connect, socket.socket.connect_ex, socket.socket.sendto, socket.getaddrinfo,
              socket.gethostbyname, socket.gethostbyname_ex, socket.gethostbyaddr, socket.getnameinfo)
    return all(getattr(p, "_garde_reseau", False) for p in points)


def _exiger_la_garde() -> None:
    """PREMIÈRE ligne de chaque test : échoue AVANT toute tentative réseau si la garde manque."""
    assert _garde_posee(), "la garde réseau n'est pas posée : rien n'est tenté, rien ne part"


@contextmanager
def refus_attendus():
    """Les refus provoqués par le test sont retirés du registre : le témoin ne les prend pas pour une fuite."""
    from tests import conftest as conf
    avant = len(conf.REFUS)
    try:
        yield conf.REFUS
    finally:
        del conf.REFUS[avant:]


def _tcp() -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.2)
    return s


def test_la_garde_est_posee_sur_chaque_point_d_entree() -> None:
    _exiger_la_garde()


def test_une_connexion_vers_une_adresse_non_locale_est_refusee_en_nommant_la_garde() -> None:
    _exiger_la_garde()
    with refus_attendus() as refus, _tcp() as s:
        with pytest.raises(OSError, match="garde réseau"):
            s.connect((DOC_IP, 80))
        assert refus and DOC_IP in str(refus[-1])


def test_une_connexion_IPv6_non_locale_est_refusee_aussi() -> None:
    _exiger_la_garde()
    with refus_attendus(), socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        with pytest.raises(OSError, match="garde réseau"):
            s.connect(("2001:db8::1", 80, 0, 0))       # préfixe de documentation (RFC 3849)


def test_connect_ex_est_gardee_aussi() -> None:
    _exiger_la_garde()
    with refus_attendus(), _tcp() as s:
        with pytest.raises(OSError, match="garde réseau"):
            s.connect_ex((DOC_IP, 80))


def test_une_connexion_par_NOM_est_refusee_sans_resolution() -> None:
    """`connect(("nom", port))` résout dans la couche C, sans passer par `getaddrinfo` : le nom lui-même est refusé."""
    _exiger_la_garde()
    with refus_attendus(), _tcp() as s:
        with pytest.raises(OSError, match="garde réseau"):
            s.connect((DOC_NOM, 80))


def test_create_connection_vers_un_nom_non_local_est_refusee() -> None:
    _exiger_la_garde()
    with refus_attendus():
        with pytest.raises(OSError, match="garde réseau"):
            socket.create_connection((DOC_NOM, 80), timeout=0.2)


@pytest.mark.parametrize("appel", [
    lambda: socket.getaddrinfo(DOC_NOM, 80),
    lambda: socket.gethostbyname(DOC_NOM),
    lambda: socket.gethostbyname_ex(DOC_NOM),
    lambda: socket.gethostbyaddr(DOC_IP),
    lambda: socket.getnameinfo((DOC_IP, 80), 0),
    lambda: socket.getfqdn(DOC_IP),
], ids=["getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr", "getnameinfo", "getfqdn"])
def test_chaque_point_d_entree_de_resolution_refuse_un_nom_ou_une_adresse_non_locale(appel) -> None:
    _exiger_la_garde()
    with refus_attendus() as refus:
        try:
            appel()
        except OSError:
            pass       # `getfqdn` avale l'erreur : le REGISTRE, lui, l'a notée
        assert refus, "le refus doit être enregistré même si l'appelant l'avale"


def test_getaddrinfo_appelee_avec_des_mots_cles_est_refusee_aussi() -> None:
    """`getaddrinfo(host=..., port=...)` est un appel valide : le nom d'hôte ne doit pas passer inaperçu parce qu'il est nommé.

    Une IP littérale et `AI_NUMERICHOST` : l'analyse est locale, AUCUNE requête de noms ne part, même sans la garde."""
    _exiger_la_garde()
    with refus_attendus() as refus:
        with pytest.raises(OSError, match="garde réseau"):
            socket.getaddrinfo(host=DOC_IP, port=80, flags=socket.AI_NUMERICHOST)
        assert refus and DOC_IP in str(refus[-1])


def test_un_envoi_udp_vers_un_hote_non_local_est_refuse() -> None:
    _exiger_la_garde()
    with refus_attendus(), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(0.2)
        with pytest.raises(OSError, match="garde réseau"):
            s.sendto(b"x", (DOC_IP, 9))


# ---------- ce qui reste permis (ancres : la garde sait laisser passer) ----------

def test_une_connexion_locale_reussit() -> None:
    _exiger_la_garde()
    ecoute = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        ecoute.bind(("127.0.0.1", 0))
        ecoute.listen(1)
        with _tcp() as s:
            s.connect(("127.0.0.1", ecoute.getsockname()[1]))
    finally:
        ecoute.close()


def test_la_resolution_locale_et_la_liaison_passive_restent_permises() -> None:
    _exiger_la_garde()
    assert socket.getaddrinfo("localhost", 80)
    assert socket.getaddrinfo(None, 0, flags=socket.AI_PASSIVE)       # liaison passive d'un serveur
    # L'hôte vide : la GARDE le permet (`hote_local("")` est testé plus bas) ; le résolveur du système, lui, peut le refuser
    # (`gaierror` sous Linux). Ce refus-là n'est pas celui de la garde (`ReseauInterdit`, une autre classe d'erreur).
    try:
        socket.getaddrinfo("", 0, flags=socket.AI_PASSIVE)
    except socket.gaierror:
        pass
    assert socket.getaddrinfo("127.0.0.1", 80)
    assert socket.gethostbyname("localhost")


def test_un_hote_vide_que_le_resolveur_du_systeme_refuse_n_est_pas_un_refus_de_la_garde(monkeypatch) -> None:
    """Sur le Linux du serveur d'intégration, `getaddrinfo("", ...)` échoue (`gaierror`, constaté à la CI publique de la v1.2.0) ; sur
    macOS il réussit. La garde, elle, PERMET l'hôte vide (`hote_local("")` est vrai) : un refus du résolveur du système n'est pas
    un refus de la garde, et le test des résolutions locales ne doit pas dépendre du système sur lequel il tourne.
    Le résolveur est simulé ici au niveau du système (`_socket`), sous la garde, comme sur ce serveur."""
    _exiger_la_garde()
    from tests import conftest as conf
    reel = socket._socket.getaddrinfo

    def resolveur_linux(hote, *args, **kwargs):
        if hote == "":
            raise socket.gaierror(-2, "Name or service not known")
        return reel(hote, *args, **kwargs)
    monkeypatch.setattr(socket._socket, "getaddrinfo", resolveur_linux)
    avant = len(conf.REFUS)
    test_la_resolution_locale_et_la_liaison_passive_restent_permises()
    assert len(conf.REFUS) == avant, "la garde n'a rien refusé : seul le résolveur du système a répondu non"


def test_un_socket_de_domaine_unix_n_est_pas_bloque(tmp_path, monkeypatch) -> None:
    _exiger_la_garde()
    monkeypatch.chdir(tmp_path)          # un chemin RELATIF : le chemin absolu d'un dossier temporaire dépasse la limite des sockets
    chemin = "s.sock"
    ecoute = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        ecoute.bind(chemin)
        ecoute.listen(1)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(chemin)
    finally:
        ecoute.close()


@pytest.mark.parametrize("hote,local", [
    ("127.0.0.1", True), ("127.9.9.9", True), ("::1", True), ("[::1]", True), ("localhost", True), ("LOCALHOST", True),
    ("0.0.0.0", True), ("::", True), (None, True), ("", True),
    ("::ffff:127.0.0.1", True),                       # IPv4 en IPv6 : la boucle locale, vue autrement
    ("::ffff:192.0.2.1", False),                      # IPv4-mappée non locale
    (DOC_IP, False), ("10.0.0.1", False), ("192.168.1.10", False), (DOC_NOM, False), ("localhost.example", False),
])
def test_ce_qui_est_local(hote, local) -> None:
    _exiger_la_garde()
    from tests.conftest import hote_local
    assert hote_local(hote) is local


# ---------- le témoin et les sessions imbriquées ----------

_CONFTEST_IMBRIQUE = ("from tests.conftest import aucun_refus_de_la_garde_reseau, pytest_configure, pytest_unconfigure"
                      "  # noqa: F401\n")


def test_un_refus_avale_par_le_code_teste_fait_echouer_quand_meme_son_test(pytester) -> None:
    """Le code sous test peut attraper l'`OSError` ; le registre des refus, lui, fait échouer le test."""
    _exiger_la_garde()
    pytester.makeconftest(_CONFTEST_IMBRIQUE)
    pytester.makepyfile(
        "import socket\n"
        "def test_avale():\n"
        "    try:\n"
        f"        socket.create_connection(({DOC_NOM!r}, 80), timeout=0.2)\n"
        "    except OSError:\n"
        "        pass\n")
    resultat = pytester.runpytest("-p", "no:cacheprovider")
    resultat.assert_outcomes(passed=1, errors=1)       # le refus est une erreur de démontage du test qui l'a provoqué
    assert any("garde réseau" in ligne for ligne in resultat.outlines)


def test_une_session_imbriquee_ne_retire_pas_la_garde_de_la_session_externe(pytester) -> None:
    """Les sessions `pytester` s'exécutent dans CE processus : la fin de la session interne ne doit rien rendre."""
    _exiger_la_garde()
    pytester.makeconftest(_CONFTEST_IMBRIQUE)
    pytester.makepyfile("def test_innocent():\n    pass\n")
    resultat = pytester.runpytest("-p", "no:cacheprovider")
    resultat.assert_outcomes(passed=1)
    assert _garde_posee(), "la session interne a rendu la garde réseau"
    assert subprocess.Popen.__name__ == "_PopenSansServeurDeProduction", "la session interne a rendu la garde de lancement"


def test_poser_la_garde_deux_fois_ne_l_enveloppe_pas_deux_fois() -> None:
    _exiger_la_garde()
    from tests import conftest as conf
    avant = (socket.socket.connect, socket.getaddrinfo, subprocess.Popen)
    conf.poser_la_garde_reseau()
    assert (socket.socket.connect, socket.getaddrinfo, subprocess.Popen) == avant


def test_seul_le_dernier_niveau_de_session_rend_les_gardes() -> None:
    """Un niveau intermédiaire (session imbriquée) ne rend rien ; le dernier rend la garde réseau ET celle de lancement."""
    _exiger_la_garde()
    from tests import conftest as conf
    etat = conf._ETAT
    avant = etat["profondeur"]
    try:
        etat["profondeur"] = avant + 1
        conf.pytest_unconfigure(None)                  # ferme un niveau ajouté : il en reste un
        assert _garde_posee() and subprocess.Popen.__name__ == "_PopenSansServeurDeProduction"
        etat["profondeur"] = 1
        conf.pytest_unconfigure(None)                  # ferme le DERNIER niveau : les gardes sont rendues
        assert not _garde_posee(), "la garde réseau n'a pas été rendue en fin de session"
        assert subprocess.Popen.__name__ != "_PopenSansServeurDeProduction", "la garde de lancement n'a pas été rendue"
    finally:
        conf.poser_la_garde_reseau()
        subprocess.Popen = conf._PopenSansServeurDeProduction
        etat["profondeur"] = avant
    assert _garde_posee()
