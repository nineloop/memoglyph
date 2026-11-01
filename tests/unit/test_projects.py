from __future__ import annotations

import pytest

from core import docid, projects


pytestmark = pytest.mark.unit


def test_initialisation_is_simulated_then_idempotent(tmp_path) -> None:
    root = tmp_path / "output"
    root.mkdir()
    (root / "Client A").mkdir()

    preview = projects.initialize(root, simulation=True)

    assert preview == {
        "simulation": True,
        "planned": [
            {"name": "Non classé", "kind": "inbox", "directory": "."},
            {"name": "Client A", "kind": "regular", "directory": "Client A"},
        ],
        "created": [],
        "existing": [],
    }
    assert not (root / projects.MANIFEST_NAME).exists()
    assert not (root / "Client A" / projects.MANIFEST_NAME).exists()

    applied = projects.initialize(root, simulation=False)
    inbox = projects.load(root)
    client = projects.load(root / "Client A")

    assert [item["name"] for item in applied["created"]] == ["Non classé", "Client A"]
    assert inbox.kind == "inbox"
    assert inbox.name == "Non classé"
    assert client.kind == "regular"
    assert client.name == "Client A"
    assert docid.est_valide(inbox.id)
    assert docid.est_valide(client.id)
    assert inbox.id != client.id

    second = projects.initialize(root, simulation=False)
    assert second["created"] == []
    assert [item["id"] for item in second["existing"]] == [inbox.id, client.id]


def test_corrupt_manifest_is_reported_never_replaced(tmp_path) -> None:
    root = tmp_path / "output"
    root.mkdir()
    manifest = root / projects.MANIFEST_NAME
    manifest.write_text("id: pas-un-ulid\nname: Non classé\nkind: inbox\n", encoding="utf-8")

    with pytest.raises(projects.ProjectManifestError):
        projects.initialize(root, simulation=False)

    assert "pas-un-ulid" in manifest.read_text(encoding="utf-8")


def test_create_regular_project_writes_stable_identity(tmp_path) -> None:
    root = tmp_path / "output"
    root.mkdir()
    project = projects.create(root / "Produit", name="Produit")

    reloaded = projects.load(root / "Produit")

    assert project == reloaded
    assert project.kind == "regular"
    assert docid.est_valide(project.id)


def test_ensure_inbox_creates_only_on_an_empty_corpus(tmp_path) -> None:
    fresh = tmp_path / "fresh"
    inbox = projects.ensure_inbox(fresh)
    assert inbox.name == "Non classé"
    assert projects.ensure_inbox(fresh).id == inbox.id

    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "ancien.md").write_text("corpus", encoding="utf-8")
    with pytest.raises(projects.ProjectManifestError, match="migration explicite"):
        projects.ensure_inbox(legacy)
    assert not (legacy / projects.MANIFEST_NAME).exists()


def test_resolve_uses_stable_id_and_rejects_unknown_scope(tmp_path) -> None:
    root = tmp_path / "output"
    inbox = projects.create(root, name=projects.INBOX_NAME, kind="inbox")
    client = projects.create(root / "Client", name="Client")

    assert projects.resolve(root, inbox.id) == (root, inbox)
    assert projects.resolve(root, client.id) == (root / "Client", client)
    with pytest.raises(projects.ProjectManifestError, match="inconnu"):
        projects.resolve(root, "all")


# ---------- Propriétaire ----------

def test_un_manifeste_en_version_1_reste_lu_sans_proprietaire(tmp_path) -> None:
    dossier = tmp_path / "Ancien"
    dossier.mkdir()
    identite = docid.nouveau()
    (dossier / projects.MANIFEST_NAME).write_text(
        f"version: 1\nid: {identite}\nname: Ancien\ncreated_at: '2026-01-01T00:00:00+00:00'\n"
        "kind: regular\n", encoding="utf-8")

    projet = projects.load(dossier)

    assert (projet.id, projet.version, projet.owner) == (identite, 1, None)


def test_un_projet_neuf_est_ecrit_en_version_2_avec_son_proprietaire(tmp_path) -> None:
    projet = projects.create(tmp_path / "Produit", name="Produit", owner="alice")

    texte = (tmp_path / "Produit" / projects.MANIFEST_NAME).read_text(encoding="utf-8")
    assert texte.startswith("version: 2\n")
    assert "owner: alice\n" in texte
    assert projects.load(tmp_path / "Produit") == projet
    assert projet.owner == "alice"


def test_deux_comptes_nomment_un_projet_pareil_dans_deux_dossiers(tmp_path) -> None:
    root = tmp_path / "output"
    dossier_a, projet_a = projects.create_owned(root, name="Clients", owner="alice")
    dossier_b, projet_b = projects.create_owned(root, name="Clients", owner="bruno")

    assert dossier_a == root / "Clients"
    assert dossier_b == root / "Clients (bruno)"
    assert projet_a.id != projet_b.id and projet_b.name == "Clients"
    # Le même compte retrouve son projet plutôt que d'en créer un second.
    assert projects.create_owned(root, name="Clients", owner="ALICE") == (dossier_a, projet_a)


def test_le_non_classe_d_un_compte_est_cree_a_la_demande(tmp_path) -> None:
    root = tmp_path / "output"
    root.mkdir()
    (root / "ancien.md").write_text("corpus", encoding="utf-8")
    projects.initialize(root, simulation=False)

    dossier, inbox = projects.personal_inbox(root, "alice")

    assert (inbox.kind, inbox.name, inbox.owner) == ("inbox", projects.INBOX_NAME, "alice")
    assert dossier == root / "Non classé (alice)"
    # Le « Non classé » de la racine, sans propriétaire, n'est pas attribué en passant.
    assert projects.load(root).owner is None
    assert projects.personal_inbox(root, "alice") == (dossier, inbox)
    # La migration relit ce sous-dossier sans le prendre pour un conflit.
    assert projects.initialize(root, simulation=True)["planned"] == []


def test_sur_une_bibliotheque_vierge_le_premier_compte_recoit_la_racine(tmp_path) -> None:
    root = tmp_path / "output"
    racine = projects.ensure_inbox(root)

    dossier, inbox = projects.personal_inbox(root, "admin")

    assert (dossier, inbox.id, inbox.owner) == (root, racine.id, "admin")
    assert projects.personal_inbox(root, "bruno")[0] == root / "Non classé (bruno)"


def test_attribuer_simule_puis_applique_sans_toucher_aux_markdown(tmp_path) -> None:
    root = tmp_path / "output"
    root.mkdir()
    (root / "Client A").mkdir()
    note = root / "Client A" / "note.md"
    note.write_text("---\ntitle: Note\n---\n\nCorps", encoding="utf-8")
    (root / "Libre").mkdir()
    (root / "Libre" / "x.md").write_text("x", encoding="utf-8")
    projects.initialize(root, simulation=False)
    (root / "Sans manifeste").mkdir()
    (root / "Sans manifeste" / "y.md").write_text("y", encoding="utf-8")
    projects.create_owned(root, name="Déjà", owner="bruno")
    avant_note = (note.read_bytes(), note.stat().st_mtime_ns)
    manifestes_avant = {p: p.read_bytes() for p in root.rglob(projects.MANIFEST_NAME)}

    simulation = projects.attribuer(root, "alice")

    assert simulation["simulation"] is True
    assert [p["name"] for p in simulation["sans_proprietaire"]] == ["Non classé", "Client A", "Libre"]
    assert simulation["sans_manifeste"] == ["Sans manifeste"]
    assert {p: p.read_bytes() for p in root.rglob(projects.MANIFEST_NAME)} == manifestes_avant

    identites = {p["id"] for p in simulation["sans_proprietaire"]}
    applique = projects.attribuer(root, "alice", simulation=False)

    assert applique["simulation"] is False
    assert {p["id"] for p in applique["sans_proprietaire"]} == identites
    for dossier in (root, root / "Client A", root / "Libre"):
        projet = projects.load(dossier)
        assert (projet.owner, projet.version) == ("alice", 2) and projet.id in identites
    assert projects.load(root / "Déjà").owner == "bruno"
    assert (note.read_bytes(), note.stat().st_mtime_ns) == avant_note
    assert projects.attribuer(root, "alice")["sans_proprietaire"] == []


@pytest.mark.parametrize("portee,attendu", [
    (None, ("", [])),
    ("P1", ("c = ?", ["P1"])),
    (frozenset({"P2", "P1"}), ("c IN (?, ?)", ["P1", "P2"])),
    (frozenset(), ("0", [])),
])
def test_filtre_sql_d_une_portee(portee, attendu) -> None:
    assert projects.filtre_sql("c", portee) == attendu


def test_une_portee_vide_ne_voit_rien() -> None:
    assert projects.dans_la_portee("P1", None)
    assert projects.dans_la_portee("P1", frozenset({"P1"}))
    assert not projects.dans_la_portee("P1", frozenset())
    assert not projects.dans_la_portee(None, frozenset({"P1"}))
    assert projects.meme_compte("Alice", "alice") and not projects.meme_compte(None, None)
