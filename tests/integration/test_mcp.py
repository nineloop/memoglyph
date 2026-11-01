"""Serveur MCP en lecture seule (`api/mcp.py`) : portée du compte, ultra-confidentiel
écarté, aucune écriture, erreurs du protocole lisibles.

Corpus synthétique à deux comptes, écrit ici : Alice tient « Clients » (une réunion
de tarifs avec son compte rendu, un rachat ultra-confidentiel) ; Bruno tient « Atelier ».

Trois façons d'interroger le serveur, toutes par le SDK `mcp` : son client en mémoire
(`mcp.Client`) pour les outils, des lignes JSON-RPC sur `mcp.servir` pour ce que voit
le fil, et son client stdio sur un sous-processus pour le bout en bout.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

import pytest
import sqlite_vec
from mcp import Client, ClientSession, StdioServerParameters, stdio_client
from mcp.types import INVALID_PARAMS, METHOD_NOT_FOUND, CallToolResult

from api import auth, cli, mcp, partages, portee
from core import docid, ecl, library, projects

pytestmark = [pytest.mark.integration]


@pytest.fixture(autouse=True)
def _faits_actives(monkeypatch):
    """Ces tests supposent les faits de la réunion activés (`MEMOGLYPH_FAITS=1`)."""
    from core import config as _config

    monkeypatch.setattr(_config, "FAITS", True)

RACINE = Path(__file__).resolve().parents[2]


def _document(identite, reunion, projet, titre, corps, *, source=None, sensibilite="standard"):
    entete = (f"---\nid: {identite}\nmeeting_id: {reunion}\nproject_id: {projet}\ntitle: {titre}\n"
              f"date: '2026-03-01'\nlanguage: fr\nsensitivity: {sensibilite}\n")
    if source:  # un compte rendu nomme son transcript
        entete += f"type: report\nsource_transcript: {source[0]}\nsource_document_id: {source[1]}\n"
    return entete + f"---\n\n{corps}\n"


@pytest.fixture
def corpus(isolated_stores, tmp_path, monkeypatch):
    sortie = isolated_stores["output_dir"]
    from core import semantic
    from core.config import EMBED_DIM
    vecteur = [1.0] + [0.0] * (EMBED_DIM - 1)
    monkeypatch.setattr(semantic.embed, "embed_documents", lambda textes, **_k: [vecteur] * len(textes))
    monkeypatch.setattr(semantic.embed, "embed_query", lambda t, **_k: vecteur)
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(auth, "CODE_PATH", tmp_path / ".memoglyph-installation")
    for compte in ("alice", "bruno"):
        auth.creer_compte(compte, "un mot de passe solide")
    projects.ensure_inbox(sortie)
    d_clients, clients = projects.create_owned(sortie, name="Clients", owner="alice")
    d_atelier, atelier = projects.create_owned(sortie, name="Atelier", owner="bruno")
    reunions = {cle: docid.nouveau() for cle in ("tarifs", "rachat", "moule")}
    transcript_tarifs = docid.nouveau()
    (d_clients / "tarifs.md").write_text(_document(
        transcript_tarifs, reunions["tarifs"], clients.id, "Tarifs",
        "## Transcript\n\n**Alice** [00:00:10] après discussion on fixe le prix de vente à 25 euros\n"),
        encoding="utf-8")
    (d_clients / "tarifs-cr.md").write_text(_document(
        docid.nouveau(), reunions["tarifs"], clients.id, "Tarifs", "## Décisions\n\n"
        "- Le prix de vente est fixé à 25 euros [source 00:00:10]\n",
        source=("tarifs.md", transcript_tarifs)), encoding="utf-8")
    (d_clients / "rachat.md").write_text(_document(
        docid.nouveau(), reunions["rachat"], clients.id, "Rachat",
        "## Transcript\n\n**Alice** [00:00:05] c'est décidé, on rachète la société Tapir\n",
        sensibilite="ultra_confidential"), encoding="utf-8")
    (d_atelier / "moule.md").write_text(_document(
        docid.nouveau(), reunions["moule"], atelier.id, "Moule",
        "## Transcript\n\n**Bruno** [00:00:15] le moule du kiwi reste un problème ouvert\n"),
        encoding="utf-8")
    library.reindex()
    semantic.reindex_all()
    return {"sortie": sortie, "clients": clients, "atelier": atelier, "reunions": reunions}


def _compte(identifiant: str) -> auth.Utilisateur:
    return auth.Utilisateur(id=identifiant, identifiant=identifiant, admin=False)


def _appel(identifiant: str, nom: str, **arguments) -> CallToolResult:
    """Un appel d'outil par le client en mémoire du SDK, poignée de main comprise."""
    async def appeler() -> CallToolResult:
        async with Client(mcp.construire(_compte(identifiant))) as client:
            return await client.call_tool(nom, arguments)
    return asyncio.run(appeler())


def _contenu(resultat: CallToolResult) -> dict:
    assert not resultat.is_error, resultat
    return resultat.structured_content


def _texte(resultat: CallToolResult) -> str:
    return resultat.content[0].text


# ---------- Le fil : des lignes JSON-RPC sur `mcp.servir` ----------

# Une version de la poignée de main (`initialize`) que le SDK négocie.
_INITIALIZE = {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
    "protocolVersion": "2025-11-25", "capabilities": {},
    "clientInfo": {"name": "tests", "version": "0"}}}
_INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def _attend_reponse(message: object) -> bool:
    return isinstance(message, dict) and message.get("jsonrpc") == "2.0" and "id" in message


class _ClientLigneALigne(io.RawIOBase):
    """L'entrée du serveur : un message à la fois, le suivant après la réponse au
    précédent, comme un vrai client.

    Le SDK annule les requêtes en cours à la fin de l'entrée : un flux fini livré
    d'un bloc perdrait les dernières réponses.
    """

    def __init__(self, messages: list, sortie_lue) -> None:
        self._messages = list(messages)
        self._sortie_lue = sortie_lue
        self._attendues = 0

    def readable(self) -> bool:
        return True

    def readinto(self, tampon) -> int:
        echeance = time.monotonic() + 30
        while self._sortie_lue().count("\n") < self._attendues:
            if time.monotonic() > echeance:
                raise TimeoutError(f"{self._attendues} réponses attendues, sortie : {self._sortie_lue()!r}")
            time.sleep(0.005)
        if not self._messages:
            return 0
        message = self._messages.pop(0)
        donnees = ((message if isinstance(message, str) else json.dumps(message)) + "\n").encode()
        assert len(donnees) <= len(tampon)
        tampon[:len(donnees)] = donnees
        self._attendues += _attend_reponse(message)
        return len(donnees)


def _entree(messages: list, sortie_lue) -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BufferedReader(_ClientLigneALigne(messages, sortie_lue)), encoding="utf-8")


def _dialogue(identifiant: str, *messages) -> dict:
    """Les réponses de `mcp.servir` sur `sys.stdin` et `sys.stdout`, par identifiant de requête."""
    tampon = io.BytesIO()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("sys.stdin", _entree(list(messages), lambda: tampon.getvalue().decode("utf-8")))
        patch.setattr("sys.stdout", io.TextIOWrapper(tampon, encoding="utf-8"))
        mcp.servir(_compte(identifiant))
        brut = tampon.getvalue().decode("utf-8")  # avant que le `TextIOWrapper` ne le ferme
    reponses = [json.loads(ligne) for ligne in brut.splitlines()]
    assert len({r["id"] for r in reponses}) == len(reponses), reponses
    return {r["id"]: r for r in reponses}


def _appel_brut(ident: int, nom: str, **arguments) -> dict:
    return {"jsonrpc": "2.0", "id": ident, "method": "tools/call",
            "params": {"name": nom, "arguments": arguments}}


# ---------- Protocole ----------

def test_initialize_puis_la_liste_des_outils(corpus) -> None:
    reponses = _dialogue("alice", _INITIALIZE, _INITIALIZED,
                         {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    # La notification n'a pas de réponse : seules les deux requêtes en ont une.
    assert set(reponses) == {0, 2}
    init = reponses[0]["result"]
    assert init["capabilities"]["tools"] == {"listChanged": False}
    assert init["serverInfo"]["name"] == "memoglyph"
    outils = reponses[2]["result"]["tools"]
    assert {o["name"] for o in outils} == set(mcp.OUTILS)
    assert all(o["inputSchema"]["type"] == "object" for o in outils)
    assert all(o["annotations"]["readOnlyHint"] is True for o in outils)


def test_les_erreurs_du_protocole_sont_lisibles(corpus) -> None:
    reponses = _dialogue(
        "alice",
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},  # avant la poignée de main
        _INITIALIZE, _INITIALIZED,
        {"jsonrpc": "2.0", "id": 2, "method": "inconnue"},
        {"id": 3, "method": "tools/list"},  # pas du JSON-RPC 2.0
        "pas du json",
        _appel_brut(4, "effacer_tout"),
        _appel_brut(5, "rechercher", requete=""),
        _appel_brut(6, "rechercher", requete="prix", limite=500),
        {"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
    assert reponses[1]["error"]["code"] == INVALID_PARAMS
    assert reponses[2]["error"]["code"] == METHOD_NOT_FOUND
    # Une ligne illisible ou hors JSON-RPC 2.0 est écartée par le SDK, sans réponse,
    # et la session continue : la requête suivante est servie.
    assert 3 not in reponses and set(reponses) == {0, 1, 2, 4, 5, 6, 7}
    assert reponses[7]["result"]["tools"]
    # Outil inconnu, argument invalide : une erreur d'outil, lisible par l'agent.
    for ident, attendu in ((4, "Unknown tool: effacer_tout"),
                           (5, "« requete » : texte non vide attendu"), (6, "limite")):
        resultat = reponses[ident]["result"]
        assert resultat["isError"] is True and attendu in resultat["content"][0]["text"], resultat


# ---------- Portée ----------

def test_un_compte_ne_voit_que_ses_projets(corpus) -> None:
    noms_alice = {p["nom"] for p in _contenu(_appel("alice", "lister_projets"))["projets"]}
    noms_bruno = {p["nom"] for p in _contenu(_appel("bruno", "lister_projets"))["projets"]}
    assert "Clients" in noms_alice and "Atelier" not in noms_alice
    assert "Atelier" in noms_bruno and "Clients" not in noms_bruno
    titres_bruno = {r["title"] for r in _contenu(_appel("bruno", "lister_reunions"))["reunions"]}
    assert titres_bruno == {"Moule"}


def test_une_reunion_d_un_autre_compte_est_introuvable(corpus) -> None:
    for outil in ("lire_compte_rendu", "lire_faits"):
        resultat = _appel("bruno", outil, reunion=corpus["reunions"]["tarifs"])
        assert resultat.is_error and "introuvable" in _texte(resultat)
    resultat = _appel("bruno", "lister_reunions", projet=corpus["clients"].id)
    assert resultat.is_error


def test_un_partage_ouvre_la_reunion_et_son_retrait_la_referme(corpus) -> None:
    partage = partages.partager("alice", "bruno", "reunion", corpus["reunions"]["tarifs"])
    lu = _contenu(_appel("bruno", "lire_compte_rendu", reunion=corpus["reunions"]["tarifs"]))
    assert "[source 00:00:10]" in lu["compte_rendu"]
    assert partages.retirer("alice", partage["id"])
    assert _appel("bruno", "lire_compte_rendu", reunion=corpus["reunions"]["tarifs"]).is_error


def test_le_compte_rendu_garde_ses_citations_horodatees(corpus) -> None:
    lu = _contenu(_appel("alice", "lire_compte_rendu", reunion=corpus["reunions"]["tarifs"]))
    assert lu["titre"] == "Tarifs" and "25 euros [source 00:00:10]" in lu["compte_rendu"]


def test_les_faits_sont_rendus_a_verifier(corpus, monkeypatch) -> None:
    monkeypatch.setattr(ecl, "_modele", lambda *a, **k: {"faits": [
        {"type": "decision", "horodatage": "00:00:10",
         "citation": "on fixe le prix de vente à 25 euros",
         "enonce": "Le prix de vente est fixé à 25 euros."}]})
    monkeypatch.setattr(ecl, "moteur_local", lambda: (None, None))
    ecl.extraire("tarifs.md")
    lu = _contenu(_appel("alice", "lire_faits", reunion=corpus["reunions"]["tarifs"]))
    (fait,) = lu["faits"]
    assert fait["type"] == "decision" and fait["horodatage"] == "00:00:10"
    # Modèle du CR en repli local : « à vérifier », et aucune précision rendue.
    assert fait["a_verifier"] is True and "precision_mesuree" not in fait


def test_aucune_precision_n_est_rendue_quel_que_soit_le_modele(corpus, monkeypatch) -> None:
    monkeypatch.setattr(ecl, "_modele", lambda *a, **k: {"faits": [
        {"type": "decision", "horodatage": "00:00:10",
         "citation": "on fixe le prix de vente à 25 euros",
         "enonce": "Le prix de vente est fixé à 25 euros."}]})
    for fournisseur in ("anthropic", "openai"):
        ecl.extraire("tarifs.md", provider=fournisseur)
        (fait,) = _contenu(_appel("alice", "lire_faits", reunion=corpus["reunions"]["tarifs"]))["faits"]
        assert fait["a_verifier"] is False and "precision_mesuree" not in fait, fournisseur


def test_une_extraction_partielle_est_marquee(corpus, monkeypatch) -> None:
    monkeypatch.setattr(ecl, "_modele", lambda *a, **k: {"faits": [
        {"type": "decision", "horodatage": "00:00:10",
         "citation": "on fixe le prix de vente à 25 euros",
         "enonce": "Le prix de vente est fixé à 25 euros."}]})
    monkeypatch.setattr(ecl, "moteur_local", lambda: (None, None))
    document = ecl.extraire("tarifs.md")["document_id"]
    (fait,) = _contenu(_appel("alice", "lire_faits", reunion=corpus["reunions"]["tarifs"]))["faits"]
    assert fait["extraction_partielle"] is None
    # Le fichier qui fait foi d'une extraction partielle (2 tranches sur 5 en échec).
    contenu = {**ecl.lire_fichier(document), "partielle": {"tranches_en_echec": 2, "tranches": 5}}
    ecl.ecrire(contenu)
    (fait,) = _contenu(_appel("alice", "lire_faits", reunion=corpus["reunions"]["tarifs"]))["faits"]
    assert fait["extraction_partielle"] == {"tranches_en_echec": 2, "tranches": 5}


# ---------- Ultra-confidentiel ----------

def test_l_ultra_confidentiel_n_est_jamais_rendu(corpus) -> None:
    liste = _contenu(_appel("alice", "lister_reunions"))
    assert "Rachat" not in {r["title"] for r in liste["reunions"]}
    assert liste["ecartees_ultra_confidentielles"] == 1
    trouve = _contenu(_appel("alice", "rechercher", requete="rachète la société"))
    assert all("Tapir" not in json.dumps(r, ensure_ascii=False) for r in trouve["resultats"])
    assert trouve["ecartes_ultra_confidentiels"] >= 1
    resultat = _appel("alice", "lire_compte_rendu", reunion=corpus["reunions"]["rachat"])
    assert resultat.is_error and "Tapir" not in resultat.model_dump_json()


def test_la_recherche_ecarte_un_document_standard_d_une_reunion_ultra_confidentielle(corpus) -> None:
    """Q16 : un document ultra-confidentiel ferme toute sa réunion, la recherche comprise."""
    from core import semantic
    (corpus["sortie"] / "Clients" / "rachat-annexe.md").write_text(_document(
        docid.nouveau(), corpus["reunions"]["rachat"], corpus["clients"].id, "Annexe",
        "## Transcript\n\n**Alice** [00:00:20] le pangolin signe l'annexe\n"), encoding="utf-8")
    library.reindex()
    semantic.reindex_all()
    # Ancre : l'annexe, standard, est bien trouvée par la recherche elle-même.
    assert "rachat-annexe.md" in {r["name"] for r in semantic.search("pangolin", k=10,
                                                                     project_id=corpus["clients"].id)}

    trouve = _contenu(_appel("alice", "rechercher", requete="pangolin"))

    assert all("pangolin" not in json.dumps(r, ensure_ascii=False) for r in trouve["resultats"])
    assert trouve["ecartes_ultra_confidentiels"] >= 1


# ---------- Lecture seule ----------

def _empreintes(*racines) -> dict[str, frozenset[str]]:
    """Fichier par fichier : un échec nomme ce qui a changé.

    Une base se compare par son CONTENU (`iterdump`) : en mode WAL, fermer la dernière
    connexion reporte dans le fichier des pages écrites plus tôt (par l'indexation),
    sans rien changer aux données. Ses fichiers `-wal` et `-shm` ne sont pas des données.
    """
    sortie = {}
    for racine in racines:
        for chemin in sorted(racine.rglob("*")):
            if not chemin.is_file() or chemin.name.endswith(("-wal", "-shm")):
                continue
            if chemin.suffix == ".db":
                with closing(sqlite3.connect(chemin)) as con:
                    con.enable_load_extension(True)
                    sqlite_vec.load(con)  # l'index sémantique a des tables vec0
                    sortie[str(chemin)] = frozenset(con.iterdump())
            else:
                sortie[str(chemin)] = frozenset({hashlib.sha256(chemin.read_bytes()).hexdigest()})
    return sortie


def _ecarts(avant: dict, apres: dict) -> dict[str, list[str]]:
    """Par fichier, ce qui est apparu (+) ou a disparu (-), en quelques lignes."""
    sortie = {}
    for nom in avant.keys() | apres.keys():
        plus, moins = apres.get(nom, frozenset()) - avant.get(nom, frozenset()), \
            avant.get(nom, frozenset()) - apres.get(nom, frozenset())
        if plus or moins:
            sortie[nom.rsplit("/", 1)[-1]] = ([f"+ {x[:120]}" for x in sorted(plus)][:4]
                                             + [f"- {x[:120]}" for x in sorted(moins)][:4])
    return sortie


def test_aucun_outil_n_ecrit(corpus, isolated_stores, tmp_path) -> None:
    racines = {isolated_stores["output_dir"], auth.DB_PATH.parent, library.DB_PATH.parent}
    # Une installation en service a déjà ses tables de partages et de faits : leur
    # création à la première lecture est un schéma, pas une donnée.
    portee.partages_actifs(_compte("alice"))
    ecl.faits(frozenset({corpus["clients"].id}))
    avant = _empreintes(*racines)
    messages = [_INITIALIZE, _INITIALIZED, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    for i, (nom, arguments) in enumerate([
            ("lister_projets", {}), ("lister_reunions", {}), ("rechercher", {"requete": "prix"}),
            ("lire_compte_rendu", {"reunion": corpus["reunions"]["tarifs"]}),
            ("lire_faits", {"reunion": corpus["reunions"]["tarifs"]}),
            ("lire_compte_rendu", {"reunion": corpus["reunions"]["rachat"]})], 3):
        messages.append(_appel_brut(i, nom, **arguments))
    reponses = _dialogue("alice", *messages)
    assert len(reponses) == sum(_attend_reponse(m) for m in messages)
    assert _ecarts(avant, _empreintes(*racines)) == {}


# ---------- Commande ----------

def test_la_commande_refuse_un_compte_inconnu(corpus, capsys) -> None:
    assert cli.main(["mcp", "personne"]) == 1
    assert "Aucun compte actif" in capsys.readouterr().err


def test_la_commande_sert_un_compte_actif(corpus, monkeypatch, capsys) -> None:
    # Pendant le service, `sys.stdout` est détourné : la sortie se lit sur son tampon.
    tampon = sys.stdout.buffer
    monkeypatch.setattr("sys.stdin", _entree(
        [_INITIALIZE, _INITIALIZED, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}],
        lambda: tampon.getvalue().decode("utf-8")))
    assert cli.main(["mcp", "alice"]) == 0
    reponses = {r["id"]: r for r in map(json.loads, capsys.readouterr().out.splitlines())}
    assert reponses[1]["result"]["tools"]


# ---------- Bout en bout : sous-processus et client stdio du SDK ----------

# Le serveur lancé comme `./memoglyph mcp`, mais sur les magasins isolés de ce test :
# les redirections de `tests/conftest.py` (racine de session, transmise par
# l'environnement), puis celles de `isolated_stores` et de `corpus`, vérifiées l'une
# et l'autre : une redirection défaite arrête le processus avant toute lecture. Une lecture de réunion écrit sur
# la sortie standard, en Python et sur le descripteur 1 : ni l'un ni l'autre ne doit
# atteindre le protocole.
_LANCEUR = r"""
import os, sys
from pathlib import Path
import tests.conftest as conftest
if conftest.chemins_non_rediriges():
    sys.exit("redirection des magasins défaite")
racine, identifiant = Path(sys.argv[1]), sys.argv[2]
from api import auth, cli
from core import dedup, library, semantic, voice_registry, voiceprint
from core.config import EMBED_DIM
sortie, base = racine / "output", racine / "index.db"
library.OUTPUT_DIR = sortie
for module in (library, dedup, semantic, voiceprint):
    module.DB_PATH = base
voiceprint.SIDECAR = sortie / ".voiceprints.json"
voice_registry.FICHIER = sortie / ".voices.json"
auth.DB_PATH, auth.CODE_PATH = racine / "auth.db", racine / ".memoglyph-installation"
vecteur = [1.0] + [0.0] * (EMBED_DIM - 1)
semantic.embed.embed_documents = lambda textes, **_k: [vecteur] * len(textes)
semantic.embed.embed_query = lambda t, **_k: vecteur
if not all(Path(p).is_relative_to(racine) for p in (library.DB_PATH, library.OUTPUT_DIR, auth.DB_PATH)):
    sys.exit("redirection des magasins du test défaite")
lire = library.get_meeting
def bavard(*a, **k):
    print("bruit python")
    os.write(1, b"bruit descripteur\n")
    return lire(*a, **k)
library.get_meeting = bavard
sys.exit(cli.main(["mcp", identifiant]))
"""


def test_bout_en_bout_par_le_client_stdio_du_sdk(corpus, tmp_path) -> None:
    import os

    parametres = StdioServerParameters(
        command=sys.executable, args=["-c", _LANCEUR, str(tmp_path), "alice"], cwd=str(RACINE),
        # Le serveur tourne dans un autre processus : l'option des faits lui est passée
        # par l'environnement, comme le ferait `.env`.
        env={**{cle: os.environ[cle] for cle in ("PYTEST_MEMOGLYPH_RACINE_ISOLEE", "MEMOGLYPH_USAGE_DB")},
             "MEMOGLYPH_FAITS": "1"})
    journal = tmp_path / "serveur.err"
    parasites: list[Exception] = []

    async def recevoir(message) -> None:
        if isinstance(message, Exception):  # une ligne de la sortie qui n'est pas du protocole
            parasites.append(message)

    async def dialogue():
        with journal.open("w", encoding="utf-8") as erreurs:
            async with asyncio.timeout(60), stdio_client(parametres, errlog=erreurs) as (lecture, ecriture):
                async with ClientSession(lecture, ecriture, message_handler=recevoir) as session:
                    init = await session.initialize()
                    outils = await session.list_tools()
                    lu = await session.call_tool("lire_compte_rendu",
                                                 {"reunion": corpus["reunions"]["tarifs"]})
                    cache = await session.call_tool("lire_compte_rendu",
                                                    {"reunion": corpus["reunions"]["rachat"]})
                    return init, outils, lu, cache

    init, outils, lu, cache = asyncio.run(dialogue())
    erreurs = journal.read_text(encoding="utf-8")
    assert init.server_info.name == "memoglyph", erreurs
    assert {o.name for o in outils.tools} == set(mcp.OUTILS)
    assert not lu.is_error and "25 euros [source 00:00:10]" in lu.structured_content["compte_rendu"]
    assert cache.is_error and "Tapir" not in cache.model_dump_json()
    assert parasites == []
    assert "bruit python" in erreurs and "bruit descripteur" in erreurs
