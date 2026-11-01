"""L'Assistant en cloud n'envoie jamais un passage ultra-confidentiel : prouvé sur la vraie chaîne.

Les tests de l'Assistant de `test_ultra_confidentiel.py` remplacent `semantic.retrieve`
par des passages qui portent DÉJÀ leur sensibilité. Or `retrieve` ne la rendait pas :
`rag.ask` lisait `None`, normalisé en `standard`, et tout partait. Ici, rien n'est
fabriqué entre le disque et le fournisseur : `.md` écrits sur disque, vrai index, vrai
`retrieve`, vraie route `/api/ask`. Seuls les embeddings (déterministes) et le
fournisseur (qui capture la requête entière) sont simulés.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api import main as api_main
from api.jobs import JobManager
from core import docid, library, llm, projects, rag, semantic, sensitivity
from core.config import EMBED_DIM
from tests.conftest import COMPTE_DE_TEST

pytestmark = pytest.mark.integration

CLOUD = ("mistral", "openai", "anthropic")
QUESTION = "Où en est le rachat ?"


def _vecteur(texte: str) -> list[float]:
    """Embeddings déterministes : un passage qui parle du rachat est au plus près de la question."""
    v = [0.0] * EMBED_DIM
    if "rachat" in texte.lower():
        v[0] = 1.0
    else:
        v[1] = 1.0
    return v


@pytest.fixture
def chaine(monkeypatch, tmp_path, isolated_stores):
    monkeypatch.setattr(semantic.embed, "embed_documents", lambda textes, **_k: [_vecteur(t) for t in textes])
    monkeypatch.setattr(semantic.embed, "embed_query", lambda t, **_k: _vecteur(t))
    monkeypatch.setattr(api_main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(api_main, "jobs", JobManager())
    requetes: list[str] = []

    def fournisseur(system, prompt, **options):
        # La requête ENTIÈRE : consigne système, invite et options. Un titre qui
        # partirait ailleurs que dans l'invite serait une fuite aussi.
        requetes.append(f"{system}\n{prompt}\n{options!r}")
        return "réponse [1]"

    monkeypatch.setattr(llm, "is_available", lambda **k: True)
    monkeypatch.setattr(llm, "chat", fournisseur)
    inbox = projects.create(isolated_stores["output_dir"], name=projects.INBOX_NAME, kind="inbox",
                            owner=COMPTE_DE_TEST)
    return {"client": TestClient(api_main.app), "inbox": inbox, "requetes": requetes,
            "sortie": isolated_stores["output_dir"]}


def _ecrire(chaine, nom: str, titre: str, sensibilite: str, texte: str, reunion: str | None = None) -> str:
    (chaine["sortie"] / nom).write_text(
        f"---\nid: {docid.nouveau()}\nmeeting_id: {reunion or docid.nouveau()}\n"
        f"project_id: {chaine['inbox'].id}\ntitle: {titre}\nlanguage: fr\nsensitivity: {sensibilite}\n"
        f"---\n\n## Transcript\n\n**Alice** [00:00:01] {texte}\n", encoding="utf-8")
    return nom


def _indexer() -> None:
    library.reindex()
    semantic.reindex_all()


def _demander(chaine, fournisseur: str):
    return chaine["client"].post("/api/ask", json={
        "question": QUESTION, "llm_provider": fournisseur,
        "scope": {"mode": "project", "project_id": chaine["inbox"].id}})


def _secrets_absents(requetes: list[str], *secrets: str) -> None:
    assert requetes, "le fournisseur n'a pas été appelé"
    for requete in requetes:
        for secret in secrets:
            assert secret not in requete, f"« {secret} » est parti vers le fournisseur"


@pytest.mark.parametrize("fournisseur", CLOUD)
def test_en_cloud_ni_le_texte_ni_le_titre_d_un_ultra_confidentiel_ne_part(chaine, fournisseur) -> None:
    _ecrire(chaine, "rachat.md", "Comité RACHAT-TITRE", "ultra_confidential",
            "Le rachat se fera à RACHAT-SECRET millions.")
    _ecrire(chaine, "point.md", "Point budget", "standard", "Le rachat du matériel est reporté.")
    _indexer()

    reponse = _demander(chaine, fournisseur)

    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert [s["name"] for s in corps["sources"]] == ["point.md"]
    assert corps["passages_ecartes"] == 1
    assert "matériel" in chaine["requetes"][0]
    _secrets_absents(chaine["requetes"], "RACHAT-SECRET", "RACHAT-TITRE")


def test_en_cloud_que_de_l_ultra_confidentiel_est_refuse(chaine) -> None:
    _ecrire(chaine, "rachat.md", "Comité", "ultra_confidential", "Le rachat se fera à RACHAT-SECRET millions.")
    _indexer()

    reponse = _demander(chaine, "openai")

    assert reponse.status_code == 403
    assert reponse.json()["detail"] == sensitivity.REFUS_CLOUD
    assert chaine["requetes"] == []


def test_en_local_tout_passe(chaine) -> None:
    _ecrire(chaine, "rachat.md", "Comité", "ultra_confidential", "Le rachat se fera à RACHAT-SECRET millions.")
    _ecrire(chaine, "point.md", "Point budget", "standard", "Le rachat du matériel est reporté.")
    _indexer()

    reponse = _demander(chaine, "mlx")

    assert reponse.status_code == 200, reponse.text
    assert sorted(s["name"] for s in reponse.json()["sources"]) == ["point.md", "rachat.md"]
    assert "passages_ecartes" not in reponse.json()
    assert "RACHAT-SECRET" in chaine["requetes"][0]


def test_un_transcript_standard_d_une_reunion_ultra_confidentielle_est_ecarte(chaine) -> None:
    """Q16, la règle du MCP : un document ultra-confidentiel ferme toute sa réunion."""
    reunion = docid.nouveau()
    _ecrire(chaine, "version-1.md", "Comité v1", "standard", "Le rachat se fera à RACHAT-SECRET millions.",
            reunion=reunion)
    _ecrire(chaine, "version-2.md", "Comité v2", "ultra_confidential", "Annexe chiffrée.", reunion=reunion)
    _ecrire(chaine, "point.md", "Point budget", "standard", "Le rachat du matériel est reporté.")
    _indexer()

    reponse = _demander(chaine, "anthropic")

    assert reponse.status_code == 200, reponse.text
    assert [s["name"] for s in reponse.json()["sources"]] == ["point.md"]
    _secrets_absents(chaine["requetes"], "RACHAT-SECRET", "Comité v1")


def test_un_document_passe_en_ultra_confidentiel_apres_indexation_est_ecarte(chaine) -> None:
    """Le changement passe par la vraie route : aucune réindexation sémantique ensuite."""
    _ecrire(chaine, "rachat.md", "Comité", "standard", "Le rachat se fera à RACHAT-SECRET millions.")
    _ecrire(chaine, "point.md", "Point budget", "standard", "Le rachat du matériel est reporté.")
    _indexer()
    assert _demander(chaine, "mistral").status_code == 200
    assert "RACHAT-SECRET" in chaine["requetes"][-1], "ancre : avant le changement, le passage part"

    changement = chaine["client"].patch("/api/transcript/rachat.md/sensitivity",
                                        params={"view_scope": chaine["inbox"].id},
                                        json={"sensitivity": "ultra_confidential"})
    assert changement.status_code == 200, changement.text
    chaine["requetes"].clear()

    reponse = _demander(chaine, "mistral")

    assert reponse.status_code == 200, reponse.text
    assert [s["name"] for s in reponse.json()["sources"]] == ["point.md"]
    _secrets_absents(chaine["requetes"], "RACHAT-SECRET")


def test_l_ultra_confidentiel_en_tete_ne_prend_aucune_place_du_top_k(chaine) -> None:
    """Sept passages ultra-confidentiels mieux classés que huit passages permis : l'invite
    cloud porte les six meilleurs permis, pas zéro."""
    for i in range(7):
        _ecrire(chaine, f"secret-{i}.md", f"Comité {i}", "ultra_confidential",
                f"Rachat, rachat, rachat : le rachat se fera à RACHAT-SECRET-{i} millions.")
    for i in range(8):
        _ecrire(chaine, f"point-{i}.md", f"Point {i}", "standard", f"Point d'étape {i} : le rachat attendra.")
    _indexer()
    classement = [p["name"] for p in semantic.retrieve(QUESTION, k=15, project_id=chaine["inbox"].id)]
    assert all(n.startswith("secret-") for n in classement[:7]), f"ancre : l'ultra est en tête ({classement})"

    reponse = _demander(chaine, "openai")

    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert len(corps["sources"]) == 6
    assert all(s["name"].startswith("point-") for s in corps["sources"])
    assert corps["passages_ecartes"] == 7
    _secrets_absents(chaine["requetes"], "RACHAT-SECRET", "Comité")


def test_retrieve_rend_la_sensibilite_effective_de_chaque_passage(chaine) -> None:
    reunion = docid.nouveau()
    _ecrire(chaine, "version-1.md", "Comité v1", "standard", "Le rachat est signé.", reunion=reunion)
    _ecrire(chaine, "version-2.md", "Comité v2", "ultra_confidential", "Le rachat est chiffré.", reunion=reunion)
    _ecrire(chaine, "point.md", "Point", "confidential", "Le rachat du matériel est reporté.")
    _indexer()

    vus = {p["name"]: p["sensitivity"]
           for p in semantic.retrieve(QUESTION, k=6, project_id=chaine["inbox"].id)}

    assert vus == {"version-1.md": "ultra_confidential", "version-2.md": "ultra_confidential",
                   "point.md": "confidential"}


def test_rag_ask_appele_directement_respecte_la_regle(chaine) -> None:
    _ecrire(chaine, "rachat.md", "Comité RACHAT-TITRE", "ultra_confidential",
            "Le rachat se fera à RACHAT-SECRET millions.")
    _ecrire(chaine, "point.md", "Point budget", "standard", "Le rachat du matériel est reporté.")
    _indexer()

    reponse = rag.ask(QUESTION, project_id=chaine["inbox"].id, provider="openai")

    assert [s["name"] for s in reponse["sources"]] == ["point.md"]
    assert reponse["passages_ecartes"] == 1
    _secrets_absents(chaine["requetes"], "RACHAT-SECRET", "RACHAT-TITRE")
