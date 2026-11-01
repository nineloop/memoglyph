"""Partage v1, éprouvé par l'API de bout en bout.

Un projet ou une réunion, en lecture seule, avec un compte existant ; ni lien
public, ni partage avec soi-même. Le test d'étanchéité à trois comptes vit dans
`test_etancheite_comptes.py`.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api import auth
from api import main as api_main
from core import docid, library, projects, semantic, voiceprint

pytestmark = [pytest.mark.integration, pytest.mark.sans_session]

MOT_DE_PASSE = "un mot de passe solide"
VOIX = [1.0] + [0.0] * 255


def _vec(n: int) -> list[float]:
    from core.config import EMBED_DIM
    v = [0.0] * EMBED_DIM
    v[n % EMBED_DIM] = 1.0
    return v


@pytest.fixture
def bibliotheque(isolated_stores, tmp_path, monkeypatch):
    """Alice possède « Clients » (deux réunions, dont une ultra-confidentielle) et « Interne »."""
    sortie = isolated_stores["output_dir"]
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(auth, "CODE_PATH", tmp_path / ".memoglyph-installation")
    compteur = {"n": 0}

    def faux_embed(textes, **_kw):
        compteur["n"] += len(textes)
        return [_vec(compteur["n"] + i) for i in range(len(textes))]

    monkeypatch.setattr(semantic.embed, "embed_documents", faux_embed)
    monkeypatch.setattr(semantic.embed, "embed_query", lambda t, **kw: _vec(0))
    from core import rag
    monkeypatch.setattr(rag.llm, "is_available", lambda **_kw: True)
    monkeypatch.setattr(rag.llm, "chat", lambda _sys, prompt, **_kw: prompt)
    for compte in ("alice", "bruno", "carole"):
        auth.creer_compte(compte, MOT_DE_PASSE)
    projects.ensure_inbox(sortie)
    d_clients, clients = projects.create_owned(sortie, name="Clients", owner="alice")
    d_interne, interne = projects.create_owned(sortie, name="Interne", owner="alice")

    def document(dossier, nom, projet, corps, sensibilite="standard", reunion=None):
        identite, reunion = docid.nouveau(), reunion or docid.nouveau()
        (dossier / nom).write_text(
            f"---\nid: {identite}\nmeeting_id: {reunion}\nproject_id: {projet.id}\n"
            f"title: {nom}\nsensitivity: {sensibilite}\ndiarization: true\n---\n\n{corps}\n",
            encoding="utf-8")
        voiceprint.save_embeddings(identite, {"Speaker 1": VOIX})
        return identite, reunion

    _, r_clients = document(d_clients, "clients.md", clients, "Point clients sur le kiwi.")
    document(d_clients, "secret.md", clients, "Rachat confidentiel du tapir.", "ultra_confidential")
    _, r_interne = document(d_interne, "interne.md", interne, "Réunion interne sur le kiwi.")
    document(d_interne, "autre.md", interne, "Autre réunion interne sur le lama.")
    library.reindex()
    semantic.reindex_all()
    for nom in ("clients.md", "secret.md", "interne.md", "autre.md"):
        voiceprint.index_name(nom)
    voiceprint.rename(voiceprint.assignments("clients.md")["Speaker 1"]["voice_id"], "Lina")
    return {"sortie": sortie, "clients": clients, "interne": interne,
            "r_clients": r_clients, "r_interne": r_interne}


def connecte(identifiant: str) -> TestClient:
    client = TestClient(api_main.app)
    assert client.post("/api/auth/connexion", json={
        "identifiant": identifiant, "mot_de_passe": MOT_DE_PASSE}).status_code == 200
    return client


def partager(client, type_: str, cible: str, invite: str = "bruno"):
    return client.post("/api/partages", json={"type": type_, "cible": cible, "invite": invite})


def lus(client, portee: str = "shared") -> list[str]:
    return sorted(d["name"] for d in client.get("/api/library", params={"view_scope": portee}).json())


# ---------- (1) Partager, retirer ----------

def test_le_proprietaire_partage_puis_retire(bibliotheque) -> None:
    alice, bruno = connecte("alice"), connecte("bruno")
    clients = bibliotheque["clients"].id

    cree = partager(alice, "projet", clients)
    assert cree.status_code == 201
    partage = cree.json()["partage"]
    assert (partage["proprietaire"], partage["invite"], partage["nom"]) == ("alice", "bruno", "Clients")
    assert partager(alice, "projet", clients).json()["partage"]["id"] == partage["id"]  # idempotent
    assert [p["id"] for p in alice.get("/api/partages").json()["donnes"]] == [partage["id"]]
    assert [p["nom"] for p in bruno.get("/api/partages").json()["recus"]] == ["Clients"]
    assert lus(bruno) == ["clients.md"]

    assert bruno.delete(f"/api/partages/{partage['id']}").status_code == 404   # pas à lui
    assert alice.delete(f"/api/partages/{partage['id']}").status_code == 200
    assert lus(bruno) == []                                                    # requête suivante
    assert bruno.get("/api/partages").json()["recus"] == []


def test_ce_qui_ne_se_partage_pas(bibliotheque) -> None:
    alice, bruno = connecte("alice"), connecte("bruno")
    clients = bibliotheque["clients"].id
    ancien = projects.create(bibliotheque["sortie"] / "Ancien", name="Ancien")

    assert partager(alice, "projet", clients, "alice").status_code == 400      # soi-même
    assert partager(alice, "projet", clients, "inconnu").status_code == 400
    auth.desactiver_compte("carole")
    assert partager(alice, "projet", clients, "carole").status_code == 400     # inactif
    assert partager(bruno, "projet", clients, "carole").status_code == 404     # pas à lui
    assert partager(alice, "projet", ancien.id).status_code == 404             # sans propriétaire
    assert partager(alice, "projet", "all").status_code == 404
    # Aucun lien public : un partage ne s'ouvre que par une session du compte invité.
    assert TestClient(api_main.app).get("/api/library", params={"view_scope": "shared"}).status_code == 401


def test_un_proprietaire_desactive_ferme_ses_partages(bibliotheque) -> None:
    alice, bruno = connecte("alice"), connecte("bruno")
    partager(alice, "projet", bibliotheque["clients"].id)
    assert lus(bruno) == ["clients.md"]

    auth.desactiver_compte("alice")

    assert lus(bruno) == [] and bruno.get("/api/partages").json()["recus"] == []


# ---------- (2) Lecture seule, par le résolveur ----------

def test_l_invite_lit_cherche_et_interroge_ce_qui_lui_est_partage(bibliotheque) -> None:
    alice, bruno = connecte("alice"), connecte("bruno")
    partager(alice, "reunion", bibliotheque["r_interne"])
    shared = {"view_scope": "shared"}

    assert lus(bruno) == ["interne.md"]
    assert lus(bruno, "all") == []                                  # Q14 : all = mes projets
    assert [r["name"] for r in bruno.get("/api/search", params={**shared, "q": "kiwi"}).json()] \
        == ["interne.md"]
    assert {r["name"] for r in bruno.get(
        "/api/search", params={**shared, "q": "kiwi", "mode": "semantic"}).json()} == {"interne.md"}
    reponse = bruno.post("/api/ask", json={"question": "kiwi ?", "scope": {"mode": "shared"}})
    assert reponse.status_code == 200 and "Réunion interne sur le kiwi" in reponse.text
    assert "Point clients" not in reponse.text and "lama" not in reponse.text
    assert bruno.get("/api/transcript/interne.md", params=shared).status_code == 200
    assert bruno.get("/api/transcript/interne.md/download", params=shared).status_code == 200
    assert bruno.get(f"/api/meetings/{bibliotheque['r_interne']}", params=shared).status_code == 200
    assert bruno.get("/api/transcript/autre.md", params=shared).status_code == 404


def test_l_invite_ne_modifie_rien(bibliotheque) -> None:
    alice, bruno = connecte("alice"), connecte("bruno")
    clients = bibliotheque["clients"].id
    partager(alice, "projet", clients)
    chemin = bibliotheque["sortie"] / "Clients" / "clients.md"
    avant = chemin.read_bytes()

    for portee in ("shared", clients, "all"):
        p = {"view_scope": portee}
        for reponse in (
            bruno.delete("/api/transcript/clients.md", params={**p, "hard": "true"}),
            bruno.patch("/api/transcript/clients.md/sensitivity", params=p, json={"sensitivity": "standard"}),
            bruno.patch("/api/transcript/clients.md/project", params=p,
                        json={"destination_project_id": clients}),
            bruno.post("/api/transcript/clients.md/report", params=p, json={}),
            bruno.get("/api/transcript/clients.md/archive-plan", params=p),
            bruno.get("/api/transcript/clients.md/finops", params=p),
        ):
            assert reponse.status_code == 404, reponse.request.url
    for route in ("/api/trash", "/api/duplicates", "/api/usage/rollup", "/api/usage/executions"):
        assert bruno.get(route, params={"view_scope": "shared"}).status_code == 404, route
    assert chemin.read_bytes() == avant


# ---------- (3) Q12, part invité ----------

def test_l_invite_voit_les_noms_des_voix_partagees_sans_les_toucher(bibliotheque) -> None:
    alice, bruno = connecte("alice"), connecte("bruno")
    clients = bibliotheque["clients"].id
    lina = voiceprint.assignments("clients.md")["Speaker 1"]["voice_id"]
    # Bruno enregistre la même personne : sa voix reste la sienne.
    projet_b = bruno.post("/api/projects", json={"name": "Chez Bruno"}).json()["id"]
    identite = docid.nouveau()
    (bibliotheque["sortie"] / "Chez Bruno" / "bruno.md").write_text(
        f"---\nid: {identite}\nmeeting_id: {docid.nouveau()}\nproject_id: {projet_b}\n"
        "title: bruno.md\ndiarization: true\n---\n\nTexte\n", encoding="utf-8")
    library.reindex()
    voiceprint.save_embeddings(identite, {"Speaker 1": VOIX})
    voiceprint.index_name("bruno.md")
    partager(alice, "projet", clients)

    vues = bruno.get("/api/voices", params={"view_scope": "shared"}).json()
    lecture = bruno.get("/api/transcript/clients.md", params={"view_scope": "shared"}).json()

    assert [(v["id"], v["display"]) for v in vues] == [(lina, "Lina")]
    assert [o["name"] for o in vues[0]["occurrences"]] == ["clients.md"]   # ni secret.md, ni Interne
    assert lecture["voices"]["Speaker 1"]["display"] == "Lina"
    assert voiceprint.assignments("bruno.md")["Speaker 1"]["voice_id"] != lina
    for portee in ("shared", "all"):
        assert bruno.patch(f"/api/voices/{lina}", params={"view_scope": portee},
                           json={"label": "X"}).status_code == 404
        assert bruno.post(f"/api/voices/{lina}/merge", params={"view_scope": portee},
                          json={"target_id": voiceprint.assignments("bruno.md")["Speaker 1"]["voice_id"]}
                          ).status_code == 404
    assert "Lina" not in bruno.get("/api/voices", params={"view_scope": "all"}).text


# ---------- (4) Q16 : l'ultra-confidentiel ne se partage pas ----------

def test_une_reunion_ultra_confidentielle_ne_se_partage_pas(bibliotheque) -> None:
    alice = connecte("alice")
    secret = next(d for d in library.list_transcripts() if d["name"] == "secret.md")

    refus = partager(alice, "reunion", secret["meeting_id"])

    assert refus.status_code == 409
    assert "ultra-confidentiel" in refus.json()["detail"]
    assert alice.get("/api/partages").json()["donnes"] == []


def test_dans_un_projet_partage_l_ultra_confidentiel_reste_invisible(bibliotheque) -> None:
    alice, bruno = connecte("alice"), connecte("bruno")
    clients = bibliotheque["clients"].id
    partager(alice, "projet", clients)
    partager(alice, "reunion", bibliotheque["r_interne"])

    assert lus(bruno) == ["clients.md", "interne.md"]
    assert lus(bruno, clients) == ["clients.md"]
    assert bruno.get("/api/transcript/secret.md", params={"view_scope": "shared"}).status_code == 404
    assert "tapir" not in bruno.get("/api/search", params={"view_scope": "shared", "q": "tapir"}).text

    # Devenus ultra-confidentiels après le partage : ils disparaissent de la vue de l'invité.
    for nom in ("clients.md", "interne.md"):
        assert alice.patch(f"/api/transcript/{nom}/sensitivity", params={"view_scope": "all"},
                           json={"sensitivity": "ultra_confidential"}).status_code == 200
    assert lus(bruno) == []
    assert bruno.get("/api/transcript/interne.md", params={"view_scope": "shared"}).status_code == 404


# ---------- (5) Mode d'emploi de l'attribution ----------

@pytest.mark.parametrize("fichier,mot", [("README.md", "before creating any new document"),
                                         ("README.fr.md", "avant de créer le moindre document")])
def test_le_mode_d_emploi_dit_d_attribuer_avant_tout_nouveau_document(fichier, mot) -> None:
    from pathlib import Path
    texte = (Path(__file__).resolve().parents[2] / fichier).read_text(encoding="utf-8")
    assert "attribuer-bibliotheque" in texte and mot in texte


def test_une_reunion_partagee_seule_s_ouvre_avec_la_portee_de_son_projet(bibliotheque) -> None:
    """L'interface ouvre un document avec la portée de son projet d'origine."""
    alice, bruno = connecte("alice"), connecte("bruno")
    interne = bibliotheque["interne"].id
    partager(alice, "reunion", bibliotheque["r_interne"])

    assert lus(bruno, interne) == ["interne.md"]                     # pas « autre.md »
    assert bruno.get("/api/transcript/interne.md", params={"view_scope": interne}).status_code == 200
    assert bruno.get("/api/transcript/autre.md", params={"view_scope": interne}).status_code == 404
    assert bruno.get("/api/library", params={"view_scope": bibliotheque["clients"].id}).status_code == 404
