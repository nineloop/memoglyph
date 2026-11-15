"""Les refus de la création d'un projet, du déplacement d'une réunion et d'une réunion ambiguë portent un code stable.

Le code s'ajoute : statut, phrase (`detail`) et en-têtes sont ceux d'avant. Un test par code, avec le refus réel (jamais une
exception simulée), pour que la phrase et le statut attendus soient ceux que l'API envoie.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api import main as api_main
from api.jobs import JobManager
from core import docid, projects
from tests.conftest import COMPTE_DE_TEST

pytestmark = pytest.mark.integration


@pytest.fixture
def client(monkeypatch, tmp_path, isolated_stores) -> TestClient:
    monkeypatch.setattr(api_main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(api_main, "jobs", JobManager())
    projects.ensure_inbox(isolated_stores["output_dir"])
    projects.personal_inbox(isolated_stores["output_dir"], COMPTE_DE_TEST)
    return TestClient(api_main.app, raise_server_exceptions=False)


def _refus(reponse, statut: int, code: str, phrase: str | None = None) -> dict:
    assert reponse.status_code == statut
    corps = reponse.json()
    assert corps["code"] == code
    assert set(corps) == {"detail", "code"}, "une phrase et un code, rien d'autre"
    if phrase is not None:
        assert corps["detail"] == phrase
    return corps


def _document(dossier, nom: str, *, projet: projects.Project, meeting_id: str) -> None:
    dossier.joinpath(nom).write_text(
        f"---\nid: {docid.nouveau()}\nmeeting_id: {meeting_id}\nproject_id: {projet.id}\n"
        f"title: Revue\nsource: revue.mp3\n---\n\nSpeaker 1: bonjour\n",
        encoding="utf-8",
    )


# ---------- Création d'un projet ----------

def test_un_nom_de_projet_vide_porte_son_code(client) -> None:
    reponse = client.post("/api/projects", json={"name": "   "})
    _refus(reponse, 400, "project.name.invalid", "Nom de projet invalide")


def test_un_nom_de_projet_avec_un_point_initial_porte_son_code(client) -> None:
    reponse = client.post("/api/projects", json={"name": ".cache"})
    _refus(reponse, 400, "project.name.invalid", "Un nom de projet ne peut pas commencer par un point.")


def test_un_nom_de_projet_avec_un_separateur_porte_son_code(client) -> None:
    reponse = client.post("/api/projects", json={"name": "a/b"})
    _refus(reponse, 400, "project.name.invalid", "Un nom de projet ne peut pas contenir de séparateur de chemin.")


def test_un_nom_de_projet_indisponible_porte_son_code(client, isolated_stores) -> None:
    """Les deux dossiers possibles (`Climat` et `Climat (compte)`) existent, non vides et sans manifeste."""
    sortie = isolated_stores["output_dir"]
    for dossier in (sortie / "Climat", sortie / f"Climat ({COMPTE_DE_TEST})"):
        dossier.mkdir()
        (dossier / "autre.txt").write_text("occupé", encoding="utf-8")
    reponse = client.post("/api/projects", json={"name": "Climat"})
    corps = _refus(reponse, 409, "project.name.taken")
    assert "nom indisponible" in corps["detail"]


def test_un_autre_refus_du_manifeste_garde_sa_phrase_sans_code(client, monkeypatch) -> None:
    """Seul « nom indisponible » reçoit `project.name.taken` : un manifeste illisible n'est pas un nom pris."""
    def illisible(*_a, **_k):
        raise projects.ProjectManifestError("Manifeste projet illisible : x")
    monkeypatch.setattr(projects, "create_owned", illisible)
    reponse = client.post("/api/projects", json={"name": "Climat"})
    assert reponse.status_code == 409
    assert reponse.json() == {"detail": "Manifeste projet illisible : x"}

# ---------- Réunion ambiguë et déplacement ----------

@pytest.fixture
def deux_projets_une_reunion(isolated_stores) -> dict:
    """La même réunion dans deux projets du compte : sans projet désigné, elle est ambiguë."""
    sortie = isolated_stores["output_dir"]
    inbox = projects.ensure_inbox(sortie)
    a = projects.create(sortie / "A", name="A")
    b = projects.create(sortie / "B", name="B")
    meeting_id = docid.nouveau()
    _document(sortie / "A", "reunion.md", projet=a, meeting_id=meeting_id)
    _document(sortie / "B", "reunion-b.md", projet=b, meeting_id=meeting_id)
    api_main.library.reindex()
    return {"sortie": sortie, "inbox": inbox, "a": a, "b": b, "meeting_id": meeting_id}


def test_une_reunion_ambigue_porte_son_code(client, deux_projets_une_reunion) -> None:
    reponse = client.get(f"/api/meetings/{deux_projets_une_reunion['meeting_id']}", params={"view_scope": "all"})
    _refus(reponse, 409, "meeting.ambiguous",
           "Plusieurs de vos projets portent cette réunion : choisissez le projet (portée) ou passez par l'un de ses documents.")


def test_un_conflit_de_deplacement_garde_son_statut_et_porte_son_code(client, isolated_stores) -> None:
    """Le projet de destination porte déjà un document de même nom : le déplacement reste un 404, avec son code."""
    sortie = isolated_stores["output_dir"]
    inbox = projects.ensure_inbox(sortie)
    a = projects.create(sortie / "A", name="A")
    meeting_id = docid.nouveau()
    _document(sortie, "reunion.md", projet=inbox, meeting_id=meeting_id)
    api_main.library.reindex()
    # Le fichier de même nom arrive dans la destination après l'indexation (l'index refuse deux noms identiques).
    _document(sortie / "A", "reunion.md", projet=a, meeting_id=docid.nouveau())
    reponse = client.patch(
        f"/api/meetings/{meeting_id}/project", params={"view_scope": inbox.id},
        json={"destination_project_id": a.id},
    )
    _refus(reponse, 404, "meeting.move.conflict", "Réunion introuvable ou conflit dans la destination")
    assert (sortie / "reunion.md").exists(), "rien n'a bougé"
