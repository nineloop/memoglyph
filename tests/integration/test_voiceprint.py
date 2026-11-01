from __future__ import annotations

import threading

import pytest

from core import docid, library, projects, resources, voiceprint


pytestmark = pytest.mark.integration


def _vec(x: float, y: float = 0.0) -> list[float]:
    return [x, y] + [0.0] * 254


def test_cosine_and_mean() -> None:
    assert voiceprint._cosine(_vec(1), _vec(1)) == pytest.approx(1.0)
    assert voiceprint._cosine(_vec(1), _vec(0, 1)) == pytest.approx(0.0)
    assert voiceprint._cosine(_vec(0), _vec(1)) == 0.0
    assert voiceprint._mean([[1.0, 3.0], [3.0, 5.0]]) == [2.0, 4.0]
    assert voiceprint._mean([]) == []


def test_similar_embeddings_join_same_voice(isolated_stores) -> None:
    first = voiceprint.index_file("one.md", {"Speaker 1": _vec(1.0)}, now=1)
    second = voiceprint.index_file("two.md", {"Speaker 1": _vec(0.99, 0.01)}, now=2)

    assert second["Speaker 1"] == first["Speaker 1"]
    voices = voiceprint.list_voices()
    assert len(voices) == 1
    assert voices[0]["n_members"] == 2


def test_dissimilar_embeddings_create_distinct_voices(isolated_stores) -> None:
    first = voiceprint.index_file("one.md", {"Speaker 1": _vec(1.0)})
    second = voiceprint.index_file("two.md", {"Speaker 1": _vec(0.0, 1.0)})

    assert first["Speaker 1"] != second["Speaker 1"]
    assert len(voiceprint.list_voices()) == 2


def test_two_speakers_in_same_file_cannot_claim_same_voice(isolated_stores) -> None:
    assigned = voiceprint.index_file(
        "dialogue.md",
        {"Speaker 1": _vec(1.0), "Speaker 2": _vec(1.0)},
    )

    assert assigned["Speaker 1"] != assigned["Speaker 2"]


def test_reindexing_same_file_preserves_voice_id_and_label(isolated_stores) -> None:
    assigned = voiceprint.index_file("one.md", {"Speaker 1": _vec(1.0)})
    voice_id = assigned["Speaker 1"]
    voiceprint.rename(voice_id, "Alice")

    reassigned = voiceprint.index_file("one.md", {"Speaker 1": _vec(1.0)})

    assert reassigned["Speaker 1"] == voice_id
    assert voiceprint.assignments("one.md")["Speaker 1"]["display"] == "Alice"


def test_rename_merge_and_remove(isolated_stores) -> None:
    one = voiceprint.index_file("one.md", {"Speaker 1": _vec(1.0)})["Speaker 1"]
    two = voiceprint.index_file("two.md", {"Speaker 1": _vec(0.0, 1.0)})["Speaker 1"]
    voiceprint.rename(one, "Alice")

    assert voiceprint.assignments("one.md")["Speaker 1"] == {
        "voice_id": one,
        "label": "Alice",
        "label_source": "manual",
        "display": "Alice",
    }

    voiceprint.merge_voices(two, one)
    assert voiceprint.assignments("two.md")["Speaker 1"]["voice_id"] == one
    assert len(voiceprint.list_voices()) == 1
    assert voiceprint.list_voices()[0]["n_members"] == 2

    voiceprint.remove("one.md")
    assert voiceprint.assignments("one.md") == {}
    assert voiceprint.list_voices()[0]["n_members"] == 1


def test_embeddings_from_meta_skips_invalid_values() -> None:
    encoded = voiceprint.encode_embedding(_vec(1.0))

    assert voiceprint.embeddings_from_meta(
        {"speaker_embeddings": {"Speaker 1": encoded, "Broken": "not base64"}}
    ) == {"Speaker 1": pytest.approx(_vec(1.0))}
    assert voiceprint.embeddings_from_meta({}) == {}


def test_is_empty_tracks_members(isolated_stores) -> None:
    assert voiceprint.is_empty() is True
    voiceprint.index_file("one.md", {"Speaker 1": _vec(1.0)})
    assert voiceprint.is_empty() is False


def test_voice_relation_is_keyed_by_document_id(isolated_stores) -> None:
    document_id = docid.nouveau()
    first = voiceprint.index_file(
        document_id, {"Speaker 1": _vec(1.0)}, name="avant.md",
    )
    second = voiceprint.index_file(
        document_id, {"Speaker 1": _vec(1.0)}, name="apres.md",
    )

    assert second == first
    conn = voiceprint._conn()
    try:
        rows = conn.execute(
            "SELECT name, document_id FROM voice_members WHERE document_id = ?",
            (document_id,),
        ).fetchall()
    finally:
        conn.close()
    assert [tuple(row) for row in rows] == [("apres.md", document_id)]


def test_sidecar_uses_document_id_as_its_key(tmp_path) -> None:
    import json

    document_id = docid.nouveau()
    voiceprint.save_embeddings(document_id, {"Speaker 1": _vec(1.0)}, root=tmp_path)

    raw = json.loads((tmp_path / ".voiceprints.json").read_text(encoding="utf-8"))
    assert list(raw) == [document_id]
    assert voiceprint.load_embeddings(document_id, root=tmp_path)["Speaker 1"] == pytest.approx(_vec(1.0))


def test_project_scope_filters_occurrences_and_llm_suggestions(isolated_stores) -> None:
    output = isolated_stores["output_dir"]
    inbox = projects.create(output, name=projects.INBOX_NAME, kind="inbox")
    client = projects.create(output / "Client", name="Client")
    first_id, second_id = docid.nouveau(), docid.nouveau()
    for directory, name, document_id, project_id in (
        (output, "inbox.md", first_id, inbox.id),
        (output / "Client", "client.md", second_id, client.id),
    ):
        directory.mkdir(parents=True, exist_ok=True)
        directory.joinpath(name).write_text(
            f"---\nid: {document_id}\nmeeting_id: {docid.nouveau()}\n"
            f"project_id: {project_id}\ntitle: {name}\n---\n\nTexte",
            encoding="utf-8",
        )
    library.reindex()
    first = voiceprint.index_file(
        first_id, {"Speaker 1": _vec(1.0)}, name="inbox.md",
        roles={"Speaker 1": "Invitée (Alice)"},
    )["Speaker 1"]
    second = voiceprint.index_file(
        second_id, {"Speaker 1": _vec(0.99, 0.01)}, name="client.md",
        roles={"Speaker 1": "Invitée (Bob)"},
    )["Speaker 1"]
    assert second == first

    inbox_voices = voiceprint.list_voices(project_id=inbox.id)
    client_voices = voiceprint.list_voices(project_id=client.id)

    assert inbox_voices[0]["n_members"] == 1
    assert inbox_voices[0]["suggestion"] == "Alice"
    assert inbox_voices[0]["occurrences"][0]["project_id"] == inbox.id
    assert inbox_voices[0]["occurrences"][0]["project"] == projects.INBOX_NAME
    assert client_voices[0]["suggestion"] == "Bob"
    assert voiceprint.visible_in_project(first, inbox.id) is True
    assert voiceprint.visible_in_project(first, docid.nouveau()) is False


@pytest.mark.parametrize("operation", ["single", "all"])
def test_voice_indexing_respects_the_global_heavy_resource_lock(
    isolated_stores, operation,
) -> None:
    result: list[object] = []

    def contender() -> None:
        try:
            call = voiceprint.index_name if operation == "single" else voiceprint.reindex_all
            result.append(call("missing.md") if operation == "single" else call())
        except Exception as exc:
            result.append(exc)

    with resources.heavy(label="occupant"):
        thread = threading.Thread(target=contender)
        thread.start()
        thread.join()

    assert len(result) == 1
    assert isinstance(result[0], resources.ResourcesBusy)
