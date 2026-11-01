"""Voix par compte (Q11) : regroupement, noms, routes, attribution.

Une voix appartient au propriétaire des projets de ses documents. Le test
d'étanchéité route par route vit dans `test_etancheite_comptes.py`.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from api import auth, cli
from api import main as api_main
from core import docid, library, projects, voice_registry, voiceprint

pytestmark = [pytest.mark.integration, pytest.mark.sans_session]

MOT_DE_PASSE = "un mot de passe solide"
MEME_PERSONNE = [1.0] + [0.0] * 255
AUTRE_PERSONNE = [0.0, 1.0] + [0.0] * 254


@pytest.fixture
def bibliotheque(isolated_stores, tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(auth, "CODE_PATH", tmp_path / ".memoglyph-installation")
    auth.creer_compte("admin", MOT_DE_PASSE, admin=True)
    auth.creer_compte("alice", MOT_DE_PASSE)
    auth.creer_compte("bruno", MOT_DE_PASSE)
    # Comme au démarrage de l'API : la racine reçoit son identité d'abord.
    projects.ensure_inbox(isolated_stores["output_dir"])
    return isolated_stores["output_dir"]


def connecte(identifiant: str) -> TestClient:
    client = TestClient(api_main.app)
    assert client.post("/api/auth/connexion", json={
        "identifiant": identifiant, "mot_de_passe": MOT_DE_PASSE}).status_code == 200
    return client


def projet_de(racine, nom: str, proprietaire) -> tuple:
    if proprietaire is None:
        dossier = racine / nom
        return dossier, projects.create(dossier, name=nom)
    return projects.create_owned(racine, name=nom, owner=proprietaire)


def document_diarise(dossier, nom: str, projet_id: str, empreinte) -> str:
    identite = docid.nouveau()
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / nom).write_text(
        f"---\nid: {identite}\nmeeting_id: {docid.nouveau()}\nproject_id: {projet_id}\n"
        f"title: {nom}\ndiarization: true\n---\n\nTexte\n", encoding="utf-8")
    voiceprint.save_embeddings(identite, {"Speaker 1": empreinte})
    return identite


def voix(nom: str) -> int:
    return voiceprint.assignments(nom)["Speaker 1"]["voice_id"]


def indexer(*noms: str) -> None:
    library.reindex()
    for nom in noms:
        voiceprint.index_name(nom)


# ---------- (1) Chaque voix appartient à un compte ----------

def test_la_meme_personne_chez_deux_comptes_fait_deux_voix(bibliotheque) -> None:
    dossier_a, projet_a = projet_de(bibliotheque, "Alpha", "alice")
    dossier_b, projet_b = projet_de(bibliotheque, "Beta", "bruno")
    document_diarise(dossier_a, "a1.md", projet_a.id, MEME_PERSONNE)
    document_diarise(dossier_a, "a2.md", projet_a.id, MEME_PERSONNE)
    document_diarise(dossier_b, "b1.md", projet_b.id, MEME_PERSONNE)

    indexer("a1.md", "a2.md", "b1.md")

    assert voix("a1.md") == voix("a2.md")      # même compte : la voix est reconnue
    assert voix("b1.md") != voix("a1.md")      # autre compte : jamais la même voix


def test_les_voix_sans_proprietaire_ne_sont_vues_que_des_administrateurs(bibliotheque) -> None:
    dossier, ancien = projet_de(bibliotheque, "Ancien", None)
    document_diarise(dossier, "vieux.md", ancien.id, MEME_PERSONNE)
    indexer("vieux.md")
    voiceprint.rename(voix("vieux.md"), "Voix d'avant")

    assert [v["display"] for v in connecte("admin").get(
        "/api/voices", params={"view_scope": "all"}).json()] == ["Voix d'avant"]
    assert connecte("alice").get("/api/voices", params={"view_scope": "all"}).json() == []


# ---------- (2) Q11 : un nom ne passe jamais d'un compte à l'autre ----------

def _uid(voice_id: int):
    conn = voiceprint._conn()
    try:
        return voiceprint._uid_de(conn, voice_id)
    finally:
        conn.close()


def _voix_melee(racine) -> int:
    """Une voix d'avant le cloisonnement : deux documents de A, un de B, regroupés."""
    dossier_a, projet_a = projet_de(racine, "Alpha", "alice")
    dossier_b, projet_b = projet_de(racine, "Beta", "bruno")
    for dossier, nom, projet in ((dossier_a, "a1.md", projet_a), (dossier_a, "a2.md", projet_a),
                                 (dossier_b, "b1.md", projet_b)):
        identite = document_diarise(dossier, nom, projet.id, MEME_PERSONNE)
        library.reindex()
        # `projets=None` : le regroupement global, sans borne par compte.
        voiceprint.index_file(identite, {"Speaker 1": MEME_PERSONNE}, name=nom)
    melee = voix("a1.md")
    assert voix("b1.md") == melee
    voiceprint.rename(melee, "Lina")
    return melee


def test_le_nom_d_une_voix_melee_n_est_jamais_rendu_a_l_autre_compte(bibliotheque) -> None:
    melee = _voix_melee(bibliotheque)
    bruno = connecte("bruno")

    vues = bruno.get("/api/voices", params={"view_scope": "all"}).json()
    lecture = bruno.get("/api/transcript/b1.md", params={"view_scope": "all"}).json()

    assert "Lina" not in json.dumps(vues) and "Lina" not in json.dumps(lecture)
    assert [v["label"] for v in vues] == [None]
    # Une voix qui déborde du compte ne se renomme pas depuis lui.
    assert bruno.patch(f"/api/voices/{melee}", params={"view_scope": "all"},
                       json={"label": "Intrus"}).status_code == 404


def test_la_reindexation_scinde_la_voix_melee_et_garde_nom_et_identite_a_la_majorite(
    bibliotheque,
) -> None:
    melee = _voix_melee(bibliotheque)
    uid = _uid(melee)
    markdown = {p: p.read_bytes() for p in bibliotheque.rglob("*.md")}

    bilan = voiceprint.reindex_all()

    assert bilan["scindees"] == 1
    a1, b1 = voiceprint.assignments("a1.md")["Speaker 1"], voiceprint.assignments("b1.md")["Speaker 1"]
    assert a1["label"] == "Lina" and voix("a2.md") == a1["voice_id"]
    assert b1["voice_id"] != a1["voice_id"] and b1["label"] is None
    assert _uid(a1["voice_id"]) == uid and _uid(b1["voice_id"]) != uid
    assert {p: p.read_bytes() for p in bibliotheque.rglob("*.md")} == markdown
    # Le registre autoritatif garde le nom, rattaché aux seuls membres de A.
    assert voice_registry.etiquette(uid)[0] == "Lina"
    # Une reconstruction ne refait pas la voix mêlée.
    assert voiceprint.reindex_all()["scindees"] == 0
    assert voix("b1.md") != voix("a1.md")


# ---------- (3) Routes des voix bornées au compte ----------

def test_les_routes_des_voix_passent_par_la_portee_du_compte(bibliotheque) -> None:
    dossier_a, projet_a = projet_de(bibliotheque, "Alpha", "alice")
    dossier_b, projet_b = projet_de(bibliotheque, "Beta", "bruno")
    document_diarise(dossier_a, "a1.md", projet_a.id, MEME_PERSONNE)
    document_diarise(dossier_a, "a2.md", projet_a.id, AUTRE_PERSONNE)
    document_diarise(dossier_b, "b1.md", projet_b.id, MEME_PERSONNE)
    indexer("a1.md", "a2.md", "b1.md")
    alice, bruno = connecte("alice"), connecte("bruno")
    va1, va2, vb = voix("a1.md"), voix("a2.md"), voix("b1.md")

    assert {v["id"] for v in alice.get("/api/voices", params={"view_scope": "all"}).json()} == {va1, va2}
    assert {v["id"] for v in bruno.get("/api/voices", params={"view_scope": "all"}).json()} == {vb}
    for reponse in (
        bruno.patch(f"/api/voices/{va1}", params={"view_scope": "all"}, json={"label": "X"}),
        bruno.post(f"/api/voices/{vb}/merge", params={"view_scope": "all"}, json={"target_id": va1}),
        alice.post(f"/api/voices/{va1}/merge", params={"view_scope": "all"}, json={"target_id": vb}),
        alice.get("/api/voices", params={"view_scope": projet_b.id}),
    ):
        assert reponse.status_code == 404
        assert str(va1) not in reponse.json()["detail"]
    assert alice.patch(f"/api/voices/{va1}", params={"view_scope": "all"},
                       json={"label": "Lina"}).status_code == 200
    assert alice.post(f"/api/voices/{va2}/merge", params={"view_scope": "all"},
                      json={"target_id": va1}).status_code == 200
    assert voix("a2.md") == va1 and voix("b1.md") == vb
    # Réindexation et backfill des voix : maintenance d'administrateur.
    assert alice.post("/api/voices/reindex", params={"view_scope": "all"}).status_code == 404
    assert alice.post("/api/voices/backfill", params={"view_scope": "all"}).status_code == 404


# ---------- (4) L'attribution emporte les voix ----------

def test_attribuer_la_bibliotheque_donne_ses_voix_au_compte(bibliotheque, capsys) -> None:
    dossier, ancien = projet_de(bibliotheque, "Ancien", None)
    document_diarise(dossier, "vieux.md", ancien.id, MEME_PERSONNE)
    indexer("vieux.md")
    voiceprint.rename(voix("vieux.md"), "Voix d'avant")
    alice = connecte("alice")
    assert alice.get("/api/voices", params={"view_scope": "all"}).json() == []

    assert cli.main(["attribuer-bibliotheque", "alice", "--appliquer"]) == 0
    capsys.readouterr()

    vues = alice.get("/api/voices", params={"view_scope": "all"}).json()
    assert [(v["id"], v["display"]) for v in vues] == [(voix("vieux.md"), "Voix d'avant")]
    assert alice.patch(f"/api/voices/{voix('vieux.md')}", params={"view_scope": "all"},
                       json={"label": "Lina"}).status_code == 200


# ---------- (5) Q12, part restrictive ----------

def test_aucune_suggestion_de_nom_ne_vient_d_un_autre_compte(bibliotheque) -> None:
    melee = _voix_melee(bibliotheque)
    voiceprint.rename(melee, None)
    # Le profil de A a reconnu « Lina » dans ses deux documents.
    conn = voiceprint._conn()
    try:
        conn.execute("UPDATE voice_members SET role_hint = 'interviewee (Lina)' "
                     "WHERE name IN ('a1.md', 'a2.md')")
        conn.commit()
        suggestion_a = voiceprint.suggested_name(conn, melee)
    finally:
        conn.close()
    assert suggestion_a == "Lina"   # le témoin : la suggestion existe bien, chez A

    vues_b = connecte("bruno").get("/api/voices", params={"view_scope": "all"}).json()

    assert "Lina" not in json.dumps(vues_b)
    assert [v["suggestion"] for v in vues_b] == [None]
