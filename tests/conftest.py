"""Fixtures communes, et isolation des magasins de données pour TOUTE la suite.

Les magasins de Memoglyph vivent à la racine du dépôt : `output/` (documents,
registre des voix, audio), `index.db` et `usage.db`. Un test qui les atteint
écrit dans la bibliothèque de la personne qui lance `make check`, hook
pré-push compris. Une fixture à demander (`isolated_stores`) ne protège que
les tests qui y pensent.

D'où deux mécanismes, ici et pas dans un fichier de test :

- REDIRECTION, avant tout import de `core` : les chemins des magasins pointent
  vers un dossier temporaire de session. Les modules qui recopient ces chemins
  à l'import (`from .config import OUTPUT_DIR`, `DB_PATH = sqlite_db.DB_PATH`)
  héritent donc des chemins redirigés ;
- TÉMOIN : l'état des fichiers de la vraie racine (taille et date, jamais le
  contenu) est relevé avant le premier import et comparé après chaque test,
  puis en fin de session. Un écart fait échouer le test qui l'a causé, et la
  session.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest

# Variables par lesquelles git désigne un dépôt. Le crochet pré-push lancé depuis un
# arbre lié pose un `GIT_DIR` absolu : héritée, elle fait agir le `git init` d'un
# test sur ce dépôt-là, qui reçoit `core.bare = true`. Retirées au chargement de ce
# module, donc avant la collecte ; un test qui en a besoin les pose lui-même.
VARIABLES_GIT_DEPOT = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                       "GIT_OBJECT_DIRECTORY")
for _variable in VARIABLES_GIT_DEPOT:
    os.environ.pop(_variable, None)

# PORT DU SERVEUR DE MODÈLE PENDANT LES TESTS : jamais ceux de l'utilisateur (8770 à 8772). Un traitement de test resté
# vivant après la fin de son test a déjà lancé un vrai `mlx_lm.server` sur 8772. La garde est posée ICI, avant tout
# import de `core` (qui lit `MEMOGLYPH_MLX_PORT` à l'import), et non laissée à une habitude de la personne qui lance
# `pytest` ; le `Makefile` la pose aussi, pour que la valeur se lise sur la ligne de commande.
PORTS_DE_PRODUCTION = (8770, 8771, 8772)
PORT_D_ESSAI = "8773"
# Posé sans condition (une écriture, pas une lecture : `test_prefixes_env`) : quoi qu'ait exporté la personne qui lance
# les tests, la suite ne touche jamais aux ports de production.
os.environ.update({"MEMOGLYPH_MLX_PORT": PORT_D_ESSAI})


def _refuser_un_serveur_de_production(commande) -> None:
    """Refuse de lancer `mlx_lm.server` sur un port de production, quelle que soit la forme de `--port`."""
    morceaux = [str(m) for m in commande] if isinstance(commande, (list, tuple)) else str(commande).split()
    if not any("mlx_lm.server" in m for m in morceaux):
        return
    port = None
    for i, morceau in enumerate(morceaux):
        if morceau == "--port" and i + 1 < len(morceaux):
            port = morceaux[i + 1]
        elif morceau.startswith("--port="):
            port = morceau.split("=", 1)[1]
    if port is not None and port.isdigit() and int(port) in PORTS_DE_PRODUCTION:
        raise RuntimeError(
            f"refusé : un test ne lance pas de serveur de modèle sur le port de production {port} "
            f"(ports {PORTS_DE_PRODUCTION[0]} à {PORTS_DE_PRODUCTION[-1]}, ceux de l'utilisateur)")


import subprocess  # noqa: E402

_POPEN_REEL = subprocess.Popen


class _PopenSansServeurDeProduction(_POPEN_REEL):  # type: ignore[valid-type, misc]
    def __init__(self, args, *a, **k):
        _refuser_un_serveur_de_production(args)
        super().__init__(args, *a, **k)


# Posée pour TOUTE la session (y compris un fil de traitement qui survivrait à son test) et rendue en fin de session.
subprocess.Popen = _PopenSansServeurDeProduction  # type: ignore[misc]


def pytest_unconfigure(config) -> None:
    subprocess.Popen = _POPEN_REEL  # type: ignore[misc]


RACINE_REELLE = Path(__file__).resolve().parent.parent

# Les fichiers de données de la racine : magasins SQLite avec leurs journaux.
_BASES = ("index.db", "usage.db", "auth.db")
_FICHIERS_RACINE = tuple(f"{b}{s}" for b in _BASES for s in ("", "-wal", "-shm", "-journal"))
# Relevé rapide, après chaque test : la racine de `output/` et ses annexes.
# Le relevé complet de `output/` attend la fin de session.
_ANNEXES_OUTPUT = (".voices.json", ".voiceprints.json", ".verite-vocale.json",
                   ".verite-vocale", ".audio", ".experimental")


def _stat(chemin: Path) -> tuple[int, int] | None:
    try:
        st = chemin.stat()
    except FileNotFoundError:
        return None
    return (st.st_size, st.st_mtime_ns)


def releve_rapide(racine: Path) -> dict[str, tuple[int, int] | None]:
    """Taille et date des magasins de `racine` et de la racine de `output/`."""
    sortie = racine / "output"
    chemins = [racine / n for n in _FICHIERS_RACINE] + [sortie, racine / ".memoglyph-installation"]
    chemins += [sortie / n for n in _ANNEXES_OUTPUT]
    return {str(p.relative_to(racine)): _stat(p) for p in chemins}


def releve_complet(racine: Path) -> dict[str, tuple[int, int] | None]:
    """`releve_rapide`, plus chaque fichier et dossier sous `output/`."""
    releve = releve_rapide(racine)
    sortie = racine / "output"
    if sortie.is_dir():
        for p in sortie.rglob("*"):
            releve[str(p.relative_to(racine))] = _stat(p)
    return releve


def ecarts(avant: dict, apres: dict) -> list[str]:
    """Chemins créés, supprimés ou modifiés entre deux relevés."""
    return sorted(k for k in avant.keys() | apres.keys() if avant.get(k) != apres.get(k))


# Relevé AVANT le premier import de `core` : ce qui change ensuite sur la vraie
# racine vient de la session, quel que soit le module qui l'écrit.
_RELEVE_INITIAL = releve_complet(RACINE_REELLE)

# Une seule racine isolée par session, transmise par l'environnement : ce module
# peut être importé deux fois (`from tests.conftest import …` le charge sous un
# second nom), et un pytest lancé en sous-processus hérite de la même racine.
# Seul le processus qui l'a créée la supprime.
_CLE_RACINE = "PYTEST_MEMOGLYPH_RACINE_ISOLEE"
_RACINE_CREEE_ICI = _CLE_RACINE not in os.environ
if _RACINE_CREEE_ICI:
    os.environ[_CLE_RACINE] = tempfile.mkdtemp(prefix="memoglyph-tests-")
RACINE_ISOLEE = Path(os.environ[_CLE_RACINE])

# `usage.db` : la variable que lit `usage_store`.
os.environ.update({"MEMOGLYPH_USAGE_DB": str(RACINE_ISOLEE / "usage.db")})
# Le `.env` que lit `core.config`, à l'import comme par `load_env()` : celui de la racine
# isolée, absent. Le `.env` réel de la racine porte des clés et des réglages de la
# machine (ports, modèles) : aucun test n'en dépend ni ne le lit.
os.environ.update({"MEMOGLYPH_ENV_FILE": str(RACINE_ISOLEE / ".env")})

from core import config  # noqa: E402

config.OUTPUT_DIR = RACINE_ISOLEE / "output"
config.AUDIO_DIR = config.OUTPUT_DIR / ".audio"
# Créé comme sur une installation : du code en tire des conclusions
# (`writer_md._taken_names` ne consulte la corbeille que si `output/` existe).
config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

from core import sqlite_db  # noqa: E402

sqlite_db.ROOT = RACINE_ISOLEE
sqlite_db.DB_PATH = RACINE_ISOLEE / "index.db"

from core import usage_store  # noqa: E402

usage_store._DEFAULT_USAGE_DB = RACINE_ISOLEE / "usage.db"

from api import auth  # noqa: E402

auth.DB_PATH = RACINE_ISOLEE / "auth.db"
auth.CODE_PATH = RACINE_ISOLEE / ".memoglyph-installation"

from core.stt_base import Segment, Transcript  # noqa: E402


def chemins_non_rediriges() -> list[str]:
    """Les redirections de ce module qui ne tiennent plus.

    Un `importlib.reload(config)` recalcule les chemins réels : le test qui le
    fait doit rendre l'état d'avant.
    """
    attendus = {
        "config.OUTPUT_DIR": config.OUTPUT_DIR,
        "config.AUDIO_DIR": config.AUDIO_DIR,
        "sqlite_db.DB_PATH": sqlite_db.DB_PATH,
        "sqlite_db.ROOT": sqlite_db.ROOT,
        "usage_store._DEFAULT_USAGE_DB": usage_store._DEFAULT_USAGE_DB,
        "auth.DB_PATH": auth.DB_PATH,
        "auth.CODE_PATH": auth.CODE_PATH,
    }
    return [nom for nom, p in attendus.items() if not Path(p).is_relative_to(RACINE_ISOLEE)]


def _decrire(liste: list[str]) -> str:
    return ", ".join(liste[:10]) + (f" (+{len(liste) - 10})" if len(liste) > 10 else "")


@pytest.fixture(autouse=True)
def aucune_ecriture_dans_la_vraie_racine(request):
    """TÉMOIN : ce test n'a créé, modifié ni supprimé aucun magasin réel.

    Deux mesures, avant et après, pour la même raison que le témoin du port :
    un écart déjà présent avant le test ne l'accuse pas. Une redirection déjà
    défaite AVANT le test arrête la session : chaque test suivant écrirait dans
    les magasins réels.
    """
    perdus = chemins_non_rediriges()
    if perdus:
        pytest.exit(
            f"PRÉCONDITION : redirection des magasins défaite avant "
            f"{request.node.nodeid} ({', '.join(perdus)}). Session arrêtée avant "
            "toute écriture dans les magasins réels.",
            returncode=pytest.ExitCode.TESTS_FAILED,
        )
    avant = releve_rapide(RACINE_REELLE)
    yield
    fautes = []
    perdus = chemins_non_rediriges()
    if perdus:
        fautes.append(f"a défait la redirection des magasins ({', '.join(perdus)})")
    touches = ecarts(avant, releve_rapide(RACINE_REELLE))
    if touches:
        fautes.append(f"a écrit dans les magasins RÉELS ({_decrire(touches)}) : un "
                      "chemin échappe à la redirection de `tests/conftest.py`. Si "
                      "l'application tournait pendant les tests, relancer sans elle")
    if fautes:
        raise AssertionError(f"{request.node.nodeid} " + " ; ".join(fautes) + ".")


def pytest_sessionfinish(session, exitstatus):
    touches = ecarts(_RELEVE_INITIAL, releve_complet(RACINE_REELLE))
    if _RACINE_CREEE_ICI:
        shutil.rmtree(RACINE_ISOLEE, ignore_errors=True)
    if touches:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        message = (f"TÉMOIN : la session a modifié la vraie racine du dépôt "
                   f"({len(touches)} chemins : {_decrire(touches)}).")
        if reporter is not None:
            reporter.write_line(message, red=True, bold=True)
        else:
            print(message)


# Compte et session RÉELS du magasin isolé, partagés par toute la session de
# tests : la connexion est obligatoire sur toutes les routes /api, et
# les tests d'API la traversent comme un navigateur, par le cookie.
_IDENTIFIANT_DE_TEST = "session-de-test"
_MOT_DE_PASSE_DE_TEST = "mot-de-passe-de-test"
_JETONS_DE_TEST: list[str] = []


def jeton_de_session_de_test() -> str:
    if _JETONS_DE_TEST and auth.utilisateur_de_session(_JETONS_DE_TEST[-1]):
        return _JETONS_DE_TEST[-1]
    utilisateur = auth.verifier_identifiants(_IDENTIFIANT_DE_TEST, _MOT_DE_PASSE_DE_TEST)
    if utilisateur is None:
        utilisateur = auth.creer_compte(_IDENTIFIANT_DE_TEST, _MOT_DE_PASSE_DE_TEST, admin=True)
    _JETONS_DE_TEST.append(auth.ouvrir_session(utilisateur))
    return _JETONS_DE_TEST[-1]


# Le compte de `session_de_test`, pour un test qui lui attribue un projet ou un
# job : chaque compte a son « Non classé » et ne voit que ses
# projets, plus ceux sans propriétaire s'il est administrateur.
COMPTE_DE_TEST = _IDENTIFIANT_DE_TEST


def compte_de_test() -> auth.Utilisateur:
    return auth.utilisateur_de_session(jeton_de_session_de_test())


def _contenu_de_l_output_partage() -> set[str]:
    racine = RACINE_ISOLEE / "output"
    if not racine.is_dir():
        return set()
    return {str(p.relative_to(racine)) for p in racine.rglob("*") if p.is_file()}


def _etat_de_l_output_partage() -> dict[str, tuple[int, int] | None]:
    """Chaque fichier de l'`output/` partagé, avec sa taille et sa date : un test qui
    RÉÉCRIT un fichier laissé par un autre est aussi fautif que celui qui l'a créé."""
    racine = RACINE_ISOLEE / "output"
    return {chemin: _stat(racine / chemin) for chemin in _contenu_de_l_output_partage()}


@pytest.fixture(autouse=True)
def rien_ne_reste_dans_l_output_partage():
    """Un test ne laisse aucun fichier dans l'`output/` partagé de la session.

    Ce dossier est commun à tous les tests qui ne demandent pas `isolated_stores` :
    ce qu'un test y oublie, le suivant le lit. Lancée à l'envers, la suite le
    montrait (`.voices.json`, `.audio/faithful/` faisaient échouer `test_auth_api`).
    Le garde nomme le test fautif plutôt que sa victime.
    """
    avant = _etat_de_l_output_partage()
    yield
    apres = _etat_de_l_output_partage()
    laisses = sorted(chemin for chemin, stat in apres.items() if avant.get(chemin) != stat)
    if laisses:
        pytest.fail(f"fichier(s) laissé(s) ou réécrit(s) dans l'output/ partagé de la session : "
                    f"{laisses} ; utiliser `isolated_stores` ou `tmp_path`", pytrace=False)


@pytest.fixture(autouse=True)
def session_de_test(request):
    """Chaque `TestClient` porte le cookie d'une vraie session administrateur.

    Le middleware n'est pas contourné : il vérifie ce cookie comme celui d'un
    navigateur. Un test qui éprouve l'authentification elle-même porte le
    marqueur `sans_session`.

    Remplacement et restauration explicites, sans la fixture `monkeypatch` :
    la demander ici l'instancierait avant celles des modules de test, et elle
    serait défaite APRÈS leurs gardes de fin, qui verraient leurs propres
    valeurs encore modifiées.
    """
    if request.node.get_closest_marker("sans_session"):
        yield
        return
    from starlette.testclient import TestClient

    initialiser = TestClient.__init__

    def avec_session(self, *args, cookies=None, **kwargs):
        cookies = {auth.COOKIE: jeton_de_session_de_test(), **(cookies or {})}
        initialiser(self, *args, cookies=cookies, **kwargs)

    TestClient.__init__ = avec_session
    try:
        yield
    finally:
        TestClient.__init__ = initialiser


@pytest.fixture
def segment_factory() -> Callable[..., Segment]:
    def make(
        text: str = "Bonjour",
        *,
        start: float = 0.0,
        end: float = 1.0,
        speaker: str | None = None,
    ) -> Segment:
        return Segment(start=start, end=end, text=text, speaker=speaker)

    return make


@pytest.fixture
def transcript_factory(segment_factory) -> Callable[..., Transcript]:
    def make(
        texts: tuple[str, ...] = ("Bonjour",),
        *,
        language: str | None = "fr",
        speakers: tuple[str | None, ...] | None = None,
    ) -> Transcript:
        labels = speakers or (None,) * len(texts)
        segments = [
            segment_factory(text, start=float(i), end=float(i + 1), speaker=labels[i])
            for i, text in enumerate(texts)
        ]
        return Transcript(
            segments=segments,
            full_text=" ".join(texts),
            language=language,
        )

    return make


@pytest.fixture
def isolated_stores(monkeypatch, tmp_path) -> dict[str, object]:
    """Redirige tous les stores dérivés vers un espace temporaire commun."""
    from core import dedup, library, semantic, voice_registry, voiceprint

    output_dir = tmp_path / "output"
    db_path = tmp_path / "index.db"
    output_dir.mkdir()

    monkeypatch.setattr(library, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(library, "DB_PATH", db_path)
    monkeypatch.setattr(dedup, "DB_PATH", db_path)
    monkeypatch.setattr(semantic, "DB_PATH", db_path)
    monkeypatch.setattr(voiceprint, "DB_PATH", db_path)
    # Les deux annexes vivent hors base et sont calculées depuis OUTPUT_DIR :
    # sans redirection explicite, un test écrit dans le corpus RÉEL. Vécu deux
    # fois : 4 documents puis 43 voix de test dans le vrai registre.
    monkeypatch.setattr(voiceprint, "SIDECAR", output_dir / ".voiceprints.json")
    monkeypatch.setattr(voice_registry, "FICHIER", output_dir / ".voices.json")

    return {"output_dir": output_dir, "db_path": db_path}


@pytest.fixture(autouse=True)
def ecl_sans_modele_reel():
    """Un compte-rendu produit en test extrait aussi les faits ECL (`core.ecl`).

    Les tests du CR simulent leurs étapes (`run_stages`) sans simuler l'ECL, qui
    atteignait alors le vrai modèle : un serveur MLX démarré sur le port de
    production. Par défaut le modèle s'abstient ; les tests de l'ECL le
    remplacent par le leur.

    Un `MonkeyPatch` à lui : demander la fixture `monkeypatch` ici avancerait sa
    restauration après celle des gardes de fichier, qui verraient encore les
    valeurs d'un test.
    """
    from core import ecl

    patch = pytest.MonkeyPatch()
    patch.setattr(ecl, "_modele", lambda *_a, **_k: {"faits": []})
    # Ni le modèle local de l'ECL (Granite, s'il est dans le cache de la machine) : un
    # test ne dépend pas de ce que contient la machine, ni ne démarre de serveur.
    patch.setattr(ecl.config, "ECL_MLX_MODEL", "")
    yield
    patch.undo()


def _listener_sur(port: int) -> bool:
    """Quelqu'un écoute-t-il ? Une connexion TCP, jamais un `ps | grep`.

    `ps -eo command | grep mlx_lm` SE DÉTECTE LUI-MÊME — la chaîne cherchée
    figure dans la ligne de commande du shell qui exécute le grep. Mesuré : le
    contrôle annonçait « encore présent » après un arrêt réussi.
    """
    import socket

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


# Port -> « occupé au tout début de la session ? » (première mesure, jamais refaite) ;
# ports déjà signalés à l'utilisateur.
_ETAT_AU_DEPART: dict[int, bool] = {}
_SIGNALES: set[int] = set()


@pytest.fixture(autouse=True)
def aucun_listener_sur_le_port_de_production(request):
    """TÉMOIN : aucun test ne laisse un listener sur le port de production.

    Un test a laissé un vrai serveur sur le PORT DE PRODUCTION, vivant 14 heures.
    Conséquence : le mode local de l'utilisateur mort en silence, sans qu'aucun
    test ne s'en aperçoive — un test qui casse le produit après s'être déclaré
    vert.

    Il vit dans le `conftest` et non dans un fichier : il court sur CHAQUE test,
    y compris ceux qu'on écrira demain sans y penser, et il NOMME le coupable au
    lieu de laisser bissecter.

    DEUX MESURES, pas une. Ne regarder le port qu'APRÈS le test prouverait
    « un listener existe maintenant », jamais « ce test l'a démarré » : un
    résidu antérieur — run manuel, application ouverte, fuite d'une exécution
    précédente — ferait accuser le premier test venu. Un contrôle qui mesure
    une grandeur VOISINE de celle qu'il annonce, exactement ce que ce dépôt
    traque partout ailleurs. Déplacer ce garde ailleurs doit emporter les deux
    mesures : en perdre une ne se voit pas.

    Les verdicts sont donc DISTINCTS, et c'est le point :

    - port déjà pris au TOUT DÉBUT de la session → c'est l'instance de
      l'utilisateur : on ne dit rien contre les tests (une note, une fois). Sans
      cette exception, le crochet de poussée échouait dès que l'utilisateur
      laissait son application ouverte ;
    - port pris AVANT ce test, mais libre au début → précondition
      d'environnement apparue en cours de route. On échoue, mais on n'accuse
      personne : le test n'y est pour rien, et le nommer enverrait chercher le
      défaut au mauvais endroit ;
    - libre avant, pris après → CE test fuit, et on le nomme.
    """
    from core import config

    occupe_avant = _listener_sur(config.MLX_PORT)
    # TROISIÈME mesure, prise UNE fois : le port était-il déjà occupé au tout début
    # de la session ? Si oui, ce listener n'est ni d'un test ni d'un run antérieur
    # du dépôt, c'est celui de l'utilisateur (son instance ouverte pendant qu'il
    # développe) : il ne peut ni être accusé ni masquer une fuite (un test ne peut
    # pas ouvrir un second listener sur un port pris). On ne le compte pas contre
    # les tests, et on le dit une fois. Un listener apparu EN COURS de session
    # reste une précondition (cas ci-dessous), et une fuite reste nommée.
    occupe_au_depart = _ETAT_AU_DEPART.setdefault(config.MLX_PORT, occupe_avant)
    if occupe_au_depart and config.MLX_PORT not in _SIGNALES:
        _SIGNALES.add(config.MLX_PORT)
        import sys

        print(
            f"\n[tests] le port {config.MLX_PORT} est occupé dès le départ de la session "
            "(votre instance ?) : le témoin de fuite ne s'applique pas à ce port.",
            file=sys.stderr,
        )
    yield
    occupe_apres = _listener_sur(config.MLX_PORT)

    if occupe_au_depart and occupe_avant:
        return  # l'instance de l'utilisateur écoutait déjà : rien à reprocher à ce test
    if occupe_avant:
        raise AssertionError(
            f"PRÉCONDITION : le port de production {config.MLX_PORT} était déjà "
            f"occupé AVANT {request.node.nodeid}. Ce test n'en est pas la cause "
            "— un run antérieur ou un serveur lancé à la main a fui. Libère le "
            f"port (lsof -nP -iTCP:{config.MLX_PORT} -sTCP:LISTEN) et relance."
        )
    if occupe_apres:
        raise AssertionError(
            f"{request.node.nodeid} a laissé un listener sur le port de "
            f"PRODUCTION {config.MLX_PORT} — le port était LIBRE avant lui. Un "
            "serveur de test doit prendre un port éphémère et être arrêté par "
            "une garantie de nettoyage."
        )
