from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from core import docid, library, llm_base, resources, semantic

def _brancher_embed(monkeypatch, fn) -> None:
    """Branche un faux moteur sur les DEUX rôles.

    `semantic` ne passe plus par `llm_ollama.embed` : la vectorisation vit dans
    `core.embed`, et passage et requête sont désormais des appels distincts —
    les confondre coûtait les préfixes que les modèles attendent.
    """
    monkeypatch.setattr(semantic.embed, "embed_documents", fn)
    monkeypatch.setattr(semantic.embed, "embed_query", lambda texte: fn([texte])[0])


pytestmark = pytest.mark.integration


def _vector(first: float, second: float = 0.0) -> list[float]:
    return [first, second] + [0.0] * (semantic.EMBED_DIM - 2)


def _write(
    output: Path, name: str, body: str, *, project_id: str | None = None,
    document_type: str | None = None) -> Path:
    path = output / name
    type_line = f"type: {document_type}\n" if document_type else ""
    path.write_text(
        f"---\nid: {docid.nouveau()}\nmeeting_id: {docid.nouveau()}\n"
        f"project_id: {project_id or docid.nouveau()}\n"
        f"{type_line}"
        f"title: {name}\nlanguage: fr\n---\n\n{body}\n",
        encoding="utf-8",
    )
    library.sync_file(path)
    return path


def test_index_search_retrieve_and_remove(monkeypatch, isolated_stores) -> None:
    output = isolated_stores["output_dir"]
    climate_project_id = docid.nouveau()
    sport_project_id = docid.nouveau()
    _write(
        output, "climat.md", "[00:01] Émissions et réchauffement climatique.",
        project_id=climate_project_id,
    )
    _write(
        output, "sport.md", "[00:02] Football et entraînement collectif.",
        project_id=sport_project_id,
    )
    _write(
        output, "climat-cr.md", "[00:03] Résumé du réchauffement climatique.",
        project_id=climate_project_id, document_type="report",
    )

    def embed(texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            normalized = text.lower()
            vectors.append(
                _vector(1.0, 0.0)
                if "climat" in normalized or "environnement" in normalized
                else _vector(0.0, 1.0)
            )
        return vectors

    _brancher_embed(monkeypatch, embed)

    assert semantic.index_name("climat.md") == 1
    assert semantic.index_name("sport.md") == 1
    assert semantic.index_name("climat-cr.md") == 1
    assert semantic.is_empty() is False

    results = semantic.search("environnement", k=2)
    assert {r["name"] for r in results} == {"climat.md", "climat-cr.md"}
    # `score` était une similarité 0..1 ; depuis la fusion RRF,
    # `search` rend un RANG. Un score de similarité n'a plus de sens quand
    # l'ordre vient de deux classements combinés — cf. `_dedup_by_transcript`.
    assert [r["rang"] for r in results] == list(range(1, len(results) + 1))
    assert all(result["document_id"] for result in results)
    assert all(result["meeting_id"] for result in results)
    assert {result["project_id"] for result in results} == {climate_project_id}

    retrieved = semantic.retrieve("environnement", k=1)
    assert retrieved[0]["name"] == "climat.md"
    assert retrieved[0]["document_id"]
    assert retrieved[0]["meeting_id"]
    assert retrieved[0]["project_id"] == climate_project_id
    assert retrieved[0]["project"] == "Non classé"
    assert "climat-cr.md" not in {
        result["name"] for result in semantic.retrieve("environnement", k=3)
    }
    # La portée l'emporte sur la proximité globale : indispensable au RAG.
    scoped = semantic.retrieve("environnement", k=1, project_id=sport_project_id)
    assert [result["name"] for result in scoped] == ["sport.md"]

    semantic.remove("climat.md")
    assert [r["name"] for r in semantic.search("environnement", k=1)] == ["climat-cr.md"]
    assert [r["name"] for r in semantic.retrieve("environnement", k=1)] == ["sport.md"]


def test_project_retrieval_fills_k_when_small_project_is_buried(
    monkeypatch, isolated_stores) -> None:
    """sqlite-vec applique k avant le filtre SQL : un petit projet doit rester
    retrouvable même si les premiers voisins appartiennent tous à un autre projet.
    """
    output = isolated_stores["output_dir"]
    large_project_id = docid.nouveau()
    small_project_id = docid.nouveau()

    for i in range(32):
        _write(
            output, f"large-{i:02}.md", f"Passage global très proche {i}.",
            project_id=large_project_id,
        )
    expected = {f"small-{i:02}.md" for i in range(3)}
    for i, name in enumerate(sorted(expected)):
        _write(
            output, name, f"Passage du petit projet plus éloigné {i}.",
            project_id=small_project_id,
        )

    def embed(texts: list[str]) -> list[list[float]]:
        return [
            _vector(0.0, 1.0) if "petit projet" in text.lower() else _vector(1.0)
            for text in texts
        ]

    _brancher_embed(monkeypatch, embed)
    semantic.reindex_all()

    retrieved = semantic.retrieve("Question globale", k=3, project_id=small_project_id)

    assert {result["name"] for result in retrieved} == expected
    assert len(retrieved) == 3
    assert {result["project_id"] for result in retrieved} == {small_project_id}


def test_project_retrieval_has_no_fixed_corpus_ratio_limit(
    monkeypatch, isolated_stores) -> None:
    """Même au-delà des trois fenêtres historiques (8, 64, 512 voisins),
    l'élargissement doit atteindre le petit projet plutôt que renvoyer zéro preuve.
    """
    output = isolated_stores["output_dir"]
    large_project_id = docid.nouveau()
    small_project_id = docid.nouveau()
    paragraphs = [
        f"Passage global très proche {i:03}. " + "contexte " * 70
        for i in range(520)
    ]
    _write(
        output, "large.md", "\n\n".join(paragraphs),
        project_id=large_project_id,
    )
    _write(
        output, "small.md", "Passage du petit projet volontairement éloigné.",
        project_id=small_project_id,
    )

    def embed(texts: list[str]) -> list[list[float]]:
        return [
            _vector(0.0, 1.0) if "petit projet" in text.lower() else _vector(1.0)
            for text in texts
        ]

    _brancher_embed(monkeypatch, embed)
    semantic.reindex_all()

    retrieved = semantic.retrieve("Question globale", k=1, project_id=small_project_id)

    assert [result["name"] for result in retrieved] == ["small.md"]
    assert retrieved[0]["project_id"] == small_project_id


def test_reindex_all_indexes_then_skips_unchanged(monkeypatch, isolated_stores) -> None:
    output = isolated_stores["output_dir"]
    _write(output, "one.md", "Premier contenu.")
    _write(output, "two.md", "Deuxième contenu.")
    _brancher_embed(monkeypatch, lambda texts: [_vector(1.0) for _ in texts])

    first = semantic.reindex_all()
    second = semantic.reindex_all()

    assert first == {"indexed_files": 2, "chunks": 2, "removed": 0}
    assert second == {"indexed_files": 0, "chunks": 0, "removed": 0}


def test_reindex_all_refreshes_a_modified_transcript(monkeypatch, isolated_stores) -> None:
    """Le .md est la source de vérité : s'il est édité à la main, la recherche
    sémantique ne doit pas continuer de répondre sur l'ancien texte."""
    output = isolated_stores["output_dir"]
    _write(output, "one.md", "Premier contenu.")
    _brancher_embed(monkeypatch, lambda texts: [_vector(1.0) for _ in texts]
    )
    semantic.reindex_all()

    _write(output, "one.md", "Contenu entièrement réécrit et bien différent.")
    stats = semantic.reindex_all()

    assert stats["indexed_files"] == 1  # détecté comme modifié
    conn = semantic._conn()
    try:
        texts = [r[0] for r in conn.execute("SELECT text FROM chunks WHERE name = 'one.md'")]
    finally:
        conn.close()
    assert any("réécrit" in t for t in texts)


def test_reindex_all_drops_orphan_chunks(monkeypatch, isolated_stores) -> None:
    output = isolated_stores["output_dir"]
    _write(output, "one.md", "Premier contenu.")
    _brancher_embed(monkeypatch, lambda texts: [_vector(1.0) for _ in texts]
    )
    semantic.reindex_all()

    (output / "one.md").unlink()
    library.reindex()
    assert semantic.reindex_all()["removed"] == 1
    assert semantic.is_empty()


def test_semantic_relation_survives_a_file_rename(monkeypatch, isolated_stores) -> None:
    output = isolated_stores["output_dir"]
    path = _write(output, "avant.md", "Une identité stable.")
    document_id = library.get_transcript(path.name)["meta"]["id"]
    _brancher_embed(monkeypatch, lambda texts: [_vector(1.0) for _ in texts]
    )
    semantic.reindex_all()

    path.rename(output / "apres.md")
    library.reindex()
    stats = semantic.reindex_all()

    assert stats["indexed_files"] == 0
    conn = semantic._conn()
    try:
        row = conn.execute(
            "SELECT name, document_id FROM chunks WHERE document_id = ?",
            (document_id,)).fetchone()
    finally:
        conn.close()
    assert tuple(row) == ("apres.md", document_id)


def test_embedding_is_computed_without_holding_a_sqlite_write_lock(
    monkeypatch, isolated_stores,
) -> None:
    """L'appel Ollama peut durer trois minutes : aucune transaction d'écriture
    ne doit rester ouverte pendant ce temps.
    """
    output = isolated_stores["output_dir"]
    db_path = isolated_stores["db_path"]
    _write(output, "one.md", "Contenu à vectoriser.")
    probe = sqlite3.connect(db_path)
    probe.execute("CREATE TABLE concurrency_probe (value TEXT)")
    probe.commit()
    probe.close()

    def embed_without_lock(texts: list[str]) -> list[list[float]]:
        writer = sqlite3.connect(db_path, timeout=0)
        try:
            writer.execute("PRAGMA busy_timeout = 0")
            writer.execute("INSERT INTO concurrency_probe VALUES ('libre')")
            writer.commit()
        finally:
            writer.close()
        return [_vector(1.0) for _ in texts]

    _brancher_embed(monkeypatch, embed_without_lock)

    assert semantic.index_name("one.md") == 1
    check = sqlite3.connect(db_path)
    try:
        assert check.execute("SELECT value FROM concurrency_probe").fetchone()[0] == "libre"
    finally:
        check.close()


@pytest.mark.parametrize(
    "invalid_vectors",
    [
        [],
        [[1.0] * (semantic.EMBED_DIM - 1)],
        [[float("nan")] + [0.0] * (semantic.EMBED_DIM - 1)],
        [_vector(1.0), _vector(1.0)],
    ],
    ids=["missing", "wrong-dimension", "non-finite", "too-many"])
def test_invalid_embeddings_are_rejected_without_destroying_the_previous_index(
    monkeypatch, isolated_stores, invalid_vectors,
) -> None:
    output = isolated_stores["output_dir"]
    _write(output, "one.md", "Ancien contenu indexé.")
    _brancher_embed(monkeypatch, lambda texts: [_vector(1.0) for _ in texts])
    semantic.index_name("one.md")

    _write(output, "one.md", "Nouveau contenu dont le vecteur est invalide.")
    _brancher_embed(monkeypatch, lambda _texts: invalid_vectors)

    with pytest.raises(semantic.InvalidEmbedding):
        semantic.index_name("one.md")

    conn = semantic._conn()
    try:
        stored = conn.execute(
            "SELECT text FROM chunks WHERE name = 'one.md'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert stored == "Ancien contenu indexé."


def test_semantic_reindex_respects_the_global_heavy_resource_lock(
    monkeypatch, isolated_stores) -> None:
    output = isolated_stores["output_dir"]
    _write(output, "one.md", "Contenu à vectoriser.")
    _brancher_embed(monkeypatch, lambda texts: [_vector(1.0) for _ in texts])
    result: list[object] = []

    def contender() -> None:
        try:
            semantic.reindex_all()
        except Exception as exc:  # le type exact est l'objet du contrat
            result.append(exc)

    with resources.heavy(label="occupant"):
        thread = threading.Thread(target=contender)
        thread.start()
        thread.join()

    assert len(result) == 1
    assert isinstance(result[0], resources.ResourcesBusy)


@pytest.mark.parametrize("change", ["model", "dimension", "chunker"])
def test_incompatible_semantic_index_is_refused_then_rebuilt_automatically(
    monkeypatch, isolated_stores, change,
) -> None:
    output = isolated_stores["output_dir"]
    _write(output, "one.md", "Contenu stable à réindexer.")

    calls: list[int] = []

    def embed(texts: list[str]) -> list[list[float]]:
        calls.append(len(texts))
        return [[1.0] + [0.0] * (semantic.EMBED_DIM - 1) for _ in texts]

    _brancher_embed(monkeypatch, embed)
    semantic.reindex_all()
    assert semantic.index_spec() == {
        "model": semantic._nom_du_moteur(),
        "dimension": semantic.EMBED_DIM,
        "chunker_version": semantic.CHUNKER_VERSION,
    }

    if change == "model":
        monkeypatch.setattr(semantic, "_nom_du_moteur", lambda: "autre-modele")
    elif change == "dimension":
        monkeypatch.setattr(semantic, "EMBED_DIM", 4)
    else:
        monkeypatch.setattr(semantic, "CHUNKER_VERSION", semantic.CHUNKER_VERSION + 1)

    with pytest.raises(semantic.SemanticIndexIncompatible):
        semantic.search("question", k=1)

    calls.clear()
    stats = semantic.reindex_all()

    assert stats["indexed_files"] == 1
    assert calls == [1]
    assert semantic.index_spec() == {
        "model": semantic._nom_du_moteur(),
        "dimension": semantic.EMBED_DIM,
        "chunker_version": semantic.CHUNKER_VERSION,
    }
    assert [item["name"] for item in semantic.search("question", k=1)] == ["one.md"]


def test_legacy_unversioned_chunks_trigger_a_full_rebuild(
    monkeypatch, isolated_stores) -> None:
    output = isolated_stores["output_dir"]
    _write(output, "one.md", "Contenu hérité.")
    calls: list[int] = []
    _brancher_embed(monkeypatch, lambda texts: calls.append(len(texts)) or [_vector(1.0) for _ in texts])
    semantic.reindex_all()
    conn = semantic._conn()
    try:
        conn.execute("DELETE FROM semantic_index_meta")
        conn.commit()
    finally:
        conn.close()

    calls.clear()
    stats = semantic.reindex_all()

    assert stats["indexed_files"] == 1
    assert calls == [1]
    assert semantic.index_spec() is not None


def test_failed_rebuild_never_declares_a_mixed_index_compatible(
    monkeypatch, isolated_stores,
) -> None:
    output = isolated_stores["output_dir"]
    _write(output, "one.md", "Premier document.")
    _write(output, "two.md", "Deuxième document.")
    _brancher_embed(monkeypatch, lambda texts: [_vector(1.0) for _ in texts])
    semantic.reindex_all()
    monkeypatch.setattr(semantic, "_nom_du_moteur", lambda: "nouveau-modele")
    calls = 0

    def fail_second(texts: list[str]) -> list[list[float]]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise llm_base.LLMError("coupure")
        return [_vector(1.0) for _ in texts]

    _brancher_embed(monkeypatch, fail_second)
    with pytest.raises(llm_base.LLMError, match="coupure"):
        semantic.reindex_all()

    assert semantic.index_spec() is None
    with pytest.raises(semantic.SemanticIndexIncompatible):
        semantic.search("question")

    _brancher_embed(monkeypatch, lambda texts: [_vector(1.0) for _ in texts])
    assert semantic.reindex_all()["indexed_files"] == 2
    assert semantic.index_spec()["model"] == "nouveau-modele"


# ---------- Audit externe, B4 : seules les paroles servent de preuve ----------

PROFIL_CONTRAIRE = (
    "# reunion-prix\n\n## 🗣️ Profil de la conversation\n\n**Type :** réunion\n\n"
    "Le groupe fixe le prix de vente à quatre-vingt-dix-neuf euros.\n\n"
    "**Thèmes :** prix · budget\n\n## Transcript\n\n"
    "**Speaker 1** [00:00:05] Le prix de vente sera de vingt-cinq euros.\n\n"
    "**Speaker 2** [00:00:12] D'accord pour vingt-cinq.\n"
)


def _embed_par_mot(textes: list[str]) -> list[list[float]]:
    return [_vector(1.0, 0.0) if "prix" in t.lower() else _vector(0.0, 1.0) for t in textes]


def test_un_profil_qui_contredit_le_transcript_ne_revient_jamais_comme_parole(
        monkeypatch, isolated_stores) -> None:
    _write(isolated_stores["output_dir"], "reunion-prix.md", PROFIL_CONTRAIRE)
    _brancher_embed(monkeypatch, _embed_par_mot)

    semantic.index_name("reunion-prix.md")
    passages = semantic.retrieve("prix de vente", k=6)
    resultats = semantic.search("quatre-vingt-dix-neuf", k=6)

    assert passages and all("quatre-vingt-dix-neuf" not in p["text"] for p in passages)
    assert all(p["origine"] == "transcript" and p["ts"] == "00:00:05" for p in passages)
    assert all("quatre-vingt-dix-neuf" not in r["snippet"] for r in resultats)
    assert [t for t, _ in semantic.chunk_body(PROFIL_CONTRAIRE)] == [
        "Le prix de vente sera de vingt-cinq euros. D'accord pour vingt-cinq."]


def test_un_compte_rendu_reste_cherchable_et_porte_son_origine(monkeypatch, isolated_stores) -> None:
    output = isolated_stores["output_dir"]
    projet = docid.nouveau()
    _write(output, "prix.md", "[00:00:05] Le prix de vente est fixé.", project_id=projet)
    _write(output, "prix-cr.md", "Le prix de vente a été fixé à vingt-cinq euros. [source 00:00:05]",
           project_id=projet, document_type="report")
    _brancher_embed(monkeypatch, _embed_par_mot)
    semantic.index_name("prix.md")
    semantic.index_name("prix-cr.md")

    resultats = {r["name"]: r["origine"] for r in semantic.search("prix", k=6)}
    passages = semantic.retrieve("prix", k=6)

    assert resultats == {"prix.md": "transcript", "prix-cr.md": "compte-rendu"}
    # Le RAG ne prend pas un compte-rendu pour preuve : ses sources remontent aux paroles.
    assert {p["name"] for p in passages} == {"prix.md"}


def test_un_index_d_une_version_anterieure_est_reconstruit_une_fois(monkeypatch, isolated_stores) -> None:
    _write(isolated_stores["output_dir"], "reunion-prix.md", PROFIL_CONTRAIRE)
    _brancher_embed(monkeypatch, _embed_par_mot)
    semantic.reindex_all()
    # Un index écrit par le découpage précédent : le profil mêlé aux paroles.
    conn = sqlite3.connect(semantic.DB_PATH)
    with conn:
        conn.execute("UPDATE semantic_index_meta SET chunker_version = ?", (semantic.CHUNKER_VERSION - 1,))
        conn.execute("UPDATE chunks SET text = text || ' quatre-vingt-dix-neuf'")
    conn.close()

    stats = semantic.reindex_all()

    assert stats["indexed_files"] == 1
    assert semantic.index_spec()["chunker_version"] == semantic.CHUNKER_VERSION
    assert all("quatre-vingt-dix-neuf" not in p["text"] for p in semantic.retrieve("prix", k=6))
    assert semantic.reindex_all()["indexed_files"] == 0, "une seule reconstruction"
