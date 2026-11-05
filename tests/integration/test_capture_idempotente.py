"""Une capture renvoyée ne crée ni seconde réunion ni second traitement.

Le compagnon renvoie seul une capture refusée faute de place, et aussi celle dont la réponse
s'est perdue. Chaque capture porte un `capture_id` (un UUID) : le serveur le range dans la
provenance de capture du front matter (durable, y compris après un redémarrage) et, pour un
traitement en cours, dans le traitement lui-même (sous le verrou du gestionnaire).
"""
from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Iterator

import pytest
import yaml
from fastapi.testclient import TestClient

from api import auth
from api import main as api_main
from api.jobs import JobManager
from core import docid, library, projects

pytestmark = [pytest.mark.integration, pytest.mark.sans_session]

MOT_DE_PASSE = "un mot de passe solide"


@pytest.fixture
def comptes(isolated_stores, tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(auth, "CODE_PATH", tmp_path / ".memoglyph-installation")
    monkeypatch.setattr(api_main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(api_main, "jobs", JobManager())
    auth.creer_compte("admin", MOT_DE_PASSE, admin=True)
    auth.creer_compte("alice", MOT_DE_PASSE)
    auth.creer_compte("bruno", MOT_DE_PASSE)
    return isolated_stores["output_dir"]


def connecte(identifiant: str) -> TestClient:
    client = TestClient(api_main.app)
    assert client.post("/api/auth/connexion", json={
        "identifiant": identifiant, "mot_de_passe": MOT_DE_PASSE}).status_code == 200
    return client


def jeton(client: TestClient) -> TestClient:
    """Un client sans cookie, comme le compagnon : seulement un jeton d'appareil."""
    reponse = client.post("/api/jetons", json={"nom": "MacBook de test"})
    assert reponse.status_code == 201, reponse.text
    return TestClient(api_main.app, headers={"Authorization": f"Bearer {reponse.json()['secret']}"})


class FauxPipeline:
    """Écrit un `.md` par appel, avec la provenance reçue ; peut attendre ou échouer."""

    def __init__(self) -> None:
        self.appels: list[dict] = []
        self.libere = threading.Event()
        self.libere.set()
        self.echouer_une_fois = False

    def __call__(self, path, **options):
        self.appels.append(options)
        meta = {"id": docid.nouveau(), "meeting_id": docid.nouveau(),
                "project_id": projects.load(options["output_dir"]).id, "title": "capture"}
        if options.get("capture"):
            meta["capture"] = options["capture"]
        sortie = options["output_dir"] / f"capture-{len(self.appels)}.md"
        if self.echouer_une_fois:
            # L'écriture précoce du vrai pipeline : un document marqué `interrupted`, puis l'échec.
            self.echouer_une_fois = False
            meta["interrupted"] = True
            sortie.write_text(f"---\n{yaml.safe_dump(meta, allow_unicode=True)}---\n\nDébut",
                              encoding="utf-8")
            raise RuntimeError("le moteur a planté")
        assert self.libere.wait(10), "pipeline jamais libéré"
        sortie.write_text(f"---\n{yaml.safe_dump(meta, allow_unicode=True)}---\n\nTexte",
                          encoding="utf-8")
        return sortie, "Texte"


def _fils_de_traitement() -> list[threading.Thread]:
    return [t for t in threading.enumerate()
            if t.is_alive() and getattr(getattr(t, "_target", None), "__qualname__", "").endswith("run.<locals>._worker")]


@pytest.fixture
def pipeline(comptes, monkeypatch) -> Iterator[FauxPipeline]:
    avant = {id(t) for t in _fils_de_traitement()}
    faux = FauxPipeline()
    monkeypatch.setattr(api_main.pipeline, "run", faux)
    monkeypatch.setattr(api_main.semantic, "index_name", lambda name: 0)
    monkeypatch.setattr(api_main.voiceprint, "index_name", lambda name: {})
    yield faux
    # Aucun fil de traitement ne survit à son test : un fil resté vivant appelle, quand il obtient le créneau global, le
    # `pipeline.run` patché à cet instant (celui du test SUIVANT) et tient ce créneau plus longtemps. La libération et
    # l'attente se font ICI, avant que `monkeypatch` ne défasse les remplacements (il est démonté après cette fixture).
    faux.libere.set()
    for fil in _fils_de_traitement():
        if id(fil) not in avant:
            fil.join(15)
            assert not fil.is_alive(), "un fil de traitement tient encore le créneau après le test"


def envoyer(client: TestClient, capture_id: str | None, **champs):
    donnees = {"capture_application": "zoom.us", "capture_mode": "visio", **champs}
    if capture_id is not None:
        donnees["capture_id"] = capture_id
    return client.post("/api/transcribe", files={"file": ("visio.wav", b"a")}, data=donnees)


def attendre(client: TestClient, job_id: str, statut: tuple[str, ...] = ("done", "error")) -> dict:
    for _ in range(1000):
        etat = client.get(f"/api/jobs/{job_id}").json()
        if etat["status"] in statut:
            return etat
        time.sleep(0.005)
    raise AssertionError("job sans fin")


def nouvel_id() -> str:
    return str(uuid.uuid4())


def fichiers_md(dossier) -> list[str]:
    return sorted(p.name for p in dossier.rglob("capture-*.md"))


def test_la_premiere_fois_est_normale_et_le_capture_id_est_range_dans_la_provenance(
        comptes, pipeline) -> None:
    alice = connecte("alice")
    cid = nouvel_id()

    reponse = envoyer(jeton(alice), cid.upper())   # la casse n'y change rien

    assert reponse.status_code == 200 and "job_id" in reponse.json()
    attendre(alice, reponse.json()["job_id"])
    assert pipeline.appels[0]["capture"] == {"application": "zoom.us", "mode": "visio", "capture_id": cid}
    library.reindex()
    nom = fichiers_md(comptes)[0]
    meta = alice.get(f"/api/transcript/{nom}", params={"view_scope": "all"}).json()["meta"]
    assert meta["capture"]["capture_id"] == cid


def test_meme_capture_id_apres_la_fin_rend_la_reunion_sans_rien_creer(comptes, pipeline) -> None:
    alice = connecte("alice")
    appareil = jeton(alice)
    cid = nouvel_id()
    premier = envoyer(appareil, cid).json()
    nom = attendre(alice, premier["job_id"])["result"]["name"]

    second = envoyer(appareil, cid)

    assert second.status_code == 200
    assert second.json()["deja_recue"] is True and second.json()["meeting"] == nom
    assert set(second.json()) == {"deja_recue", "meeting", "meeting_id", "project_id"}   # de quoi l'ouvrir
    assert len(pipeline.appels) == 1 and fichiers_md(comptes) == [nom]
    assert len(alice.get("/api/jobs").json()["traitements"]) == 1


def test_meme_capture_id_pendant_le_traitement_rend_le_meme_traitement(comptes, pipeline) -> None:
    alice = connecte("alice")
    appareil = jeton(alice)
    pipeline.libere.clear()
    cid = nouvel_id()
    premier = envoyer(appareil, cid).json()["job_id"]

    second = envoyer(appareil, cid)

    assert second.status_code == 200
    assert second.json() == {"deja_recue": True, "job_id": premier}
    assert len(alice.get("/api/jobs").json()["traitements"]) == 1
    pipeline.libere.set()
    attendre(alice, premier)
    assert len(pipeline.appels) == 1


def test_apres_un_redemarrage_la_provenance_suffit(comptes, pipeline, monkeypatch) -> None:
    alice = connecte("alice")
    appareil = jeton(alice)
    cid = nouvel_id()
    nom = attendre(alice, envoyer(appareil, cid).json()["job_id"])["result"]["name"]
    monkeypatch.setattr(api_main, "jobs", JobManager())   # les traitements ne vivent qu'en mémoire

    second = envoyer(appareil, cid)

    assert second.json()["deja_recue"] is True and second.json()["meeting"] == nom
    assert second.json()["meeting_id"]
    assert len(pipeline.appels) == 1


def test_un_autre_compte_n_est_jamais_deja_recu(comptes, pipeline) -> None:
    alice, bruno = connecte("alice"), connecte("bruno")
    cid = nouvel_id()
    nom = attendre(alice, envoyer(jeton(alice), cid).json()["job_id"])["result"]["name"]

    reponse = envoyer(jeton(bruno), cid)

    assert reponse.status_code == 200 and "deja_recue" not in reponse.json()
    attendre(bruno, reponse.json()["job_id"])
    assert len(pipeline.appels) == 2
    # Et la réunion d'Alice n'est jamais nommée à Bruno.
    assert nom not in reponse.text


def test_pendant_le_traitement_un_autre_compte_n_est_pas_deja_recu_non_plus(comptes, pipeline) -> None:
    alice, bruno = connecte("alice"), connecte("bruno")
    pipeline.libere.clear()
    cid = nouvel_id()
    envoyer(jeton(alice), cid)

    reponse = envoyer(jeton(bruno), cid)

    assert "deja_recue" not in reponse.json()
    pipeline.libere.set()


def test_deux_envois_simultanes_du_meme_capture_id_ne_font_qu_un_traitement(comptes, pipeline) -> None:
    alice = connecte("alice")
    appareil = jeton(alice)
    pipeline.libere.clear()
    cid = nouvel_id()
    reponses: list = []
    depart = threading.Barrier(2)

    def lancer() -> None:
        depart.wait()
        reponses.append(envoyer(appareil, cid).json())

    fils = [threading.Thread(target=lancer) for _ in range(2)]
    [f.start() for f in fils]
    [f.join(10) for f in fils]

    assert len(reponses) == 2
    nouveaux = [r for r in reponses if "deja_recue" not in r]
    deja = [r for r in reponses if r.get("deja_recue")]
    assert len(nouveaux) == 1 and len(deja) == 1
    assert deja[0]["job_id"] == nouveaux[0]["job_id"]
    pipeline.libere.set()
    attendre(alice, nouveaux[0]["job_id"])
    assert len(pipeline.appels) == 1


def test_un_traitement_en_echec_puis_renvoye_cree_un_nouveau_traitement(comptes, pipeline) -> None:
    alice = connecte("alice")
    appareil = jeton(alice)
    pipeline.echouer_une_fois = True
    cid = nouvel_id()
    premier = envoyer(appareil, cid).json()["job_id"]
    assert attendre(alice, premier)["status"] == "error"

    second = envoyer(appareil, cid)

    assert second.status_code == 200 and "deja_recue" not in second.json()
    assert second.json()["job_id"] != premier
    assert attendre(alice, second.json()["job_id"])["status"] == "done"
    # Le document laissé `interrupted` par l'échec n'a jamais compté comme « reçu ».
    assert len(pipeline.appels) == 2


def test_un_429_n_enregistre_rien(comptes, pipeline, monkeypatch) -> None:
    alice = connecte("alice")
    appareil = jeton(alice)
    monkeypatch.setattr(api_main, "jobs", JobManager(max_active=1))
    pipeline.libere.clear()
    occupe = envoyer(appareil, nouvel_id()).json()["job_id"]
    cid = nouvel_id()

    refuse = envoyer(appareil, cid)

    assert refuse.status_code == 429
    pipeline.libere.set()
    attendre(alice, occupe)
    assert len(pipeline.appels) == 1          # le refusé n'a rien lancé
    # La place s'est libérée : le renvoi passe, comme une première fois, et part au pipeline.
    renvoi = envoyer(appareil, cid)
    assert renvoi.status_code == 200 and "deja_recue" not in renvoi.json()
    attendre(alice, renvoi.json()["job_id"])
    assert len(pipeline.appels) == 2


def test_une_capture_deja_recue_est_reconnue_meme_quand_la_machine_est_occupee(
        comptes, pipeline, monkeypatch) -> None:
    alice = connecte("alice")
    appareil = jeton(alice)
    cid = nouvel_id()
    nom = attendre(alice, envoyer(appareil, cid).json()["job_id"])["result"]["name"]
    monkeypatch.setattr(api_main, "jobs", JobManager(max_active=1))
    pipeline.libere.clear()
    envoyer(appareil, nouvel_id())          # occupe l'unique place

    reponse = envoyer(appareil, cid)

    assert reponse.status_code == 200 and reponse.json()["meeting"] == nom
    pipeline.libere.set()


def test_la_destination_peut_changer_entre_deux_essais(comptes, pipeline) -> None:
    alice = connecte("alice")
    appareil = jeton(alice)
    cid = nouvel_id()
    nom = attendre(alice, envoyer(appareil, cid).json()["job_id"])["result"]["name"]
    autre = alice.post("/api/projects", json={"name": "Autre projet"}).json()["id"]

    reponse = envoyer(appareil, cid, destination_project_id=autre)

    assert reponse.json()["meeting"] == nom


def test_sans_capture_id_rien_ne_change(comptes, pipeline) -> None:
    alice = connecte("alice")
    appareil = jeton(alice)

    ids = [envoyer(appareil, None).json()["job_id"] for _ in range(2)]

    assert ids[0] != ids[1]
    for i in ids:
        attendre(alice, i)
    assert all("capture_id" not in (o["capture"] or {}) for o in pipeline.appels)
    assert len(pipeline.appels) == 2


@pytest.mark.parametrize("valeur", ["pas-un-uuid", "1234", "x" * 200, "zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz"])
def test_un_capture_id_invalide_est_refuse_avant_tout_travail(comptes, pipeline, valeur) -> None:
    alice = connecte("alice")

    reponse = envoyer(alice, valeur)

    assert reponse.status_code == 400 and pipeline.appels == []
