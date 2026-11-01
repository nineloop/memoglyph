"""L'index transporte la hiérarchie transcript → compte-rendu.

`type` et `source_transcript` étaient écrits dans le front-matter par
`core/report.py` mais JETÉS à l'indexation : `_row_from_file` est une liste
blanche qui ne les retenait pas. L'UI ne les a donc jamais vus, et la
bibliothèque affichait 32 documents à plat au lieu de 17 réunions.
"""

import pytest

from core import library

pytestmark = pytest.mark.unit


def _ecrire(tmp_path, nom, fm):
    import yaml
    p = tmp_path / nom
    p.write_text("---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True)
                 + "---\n\n# titre\n\ncorps\n", encoding="utf-8")
    return p


def test_un_compte_rendu_porte_son_type_et_son_parent(tmp_path):
    p = _ecrire(tmp_path, "x-cr.md", {
        "title": "x — compte-rendu", "type": "report",
        "source_transcript": "x.md", "language": "fr"})
    row = library._row_from_file(p)
    assert row["type"] == "report"
    assert row["source_transcript"] == "x.md"


def test_un_transcript_n_a_ni_type_ni_parent(tmp_path):
    row = library._row_from_file(_ecrire(tmp_path, "x.md", {"title": "x", "language": "fr"}))
    assert row["type"] is None
    assert row["source_transcript"] is None


@pytest.mark.parametrize("champ", [
    "repetition_loops", "removed_credits", "repeated_series",
    "unsupported_numbers", "removed_speaker_bindings", "citation_warnings",
])
def test_chaque_reserve_connue_est_comptee(tmp_path, champ):
    row = library._row_from_file(_ecrire(tmp_path, "x.md", {"title": "x", champ: ["a"]}))
    assert row["warnings"] == 1


def test_les_reserves_s_additionnent(tmp_path):
    row = library._row_from_file(_ecrire(tmp_path, "x.md", {
        "title": "x", "repetition_loops": ["a"], "removed_credits": ["b"],
        "removed_speaker_bindings": ["c"]}))
    assert row["warnings"] == 3


def test_une_reserve_VIDE_ne_compte_pas(tmp_path):
    """Le pipeline écrit `None` plutôt qu'une liste vide, mais une base ancienne
    peut contenir `[]` — un badge « ⚠ 0 » serait un mensonge."""
    row = library._row_from_file(_ecrire(tmp_path, "x.md", {
        "title": "x", "repetition_loops": [], "removed_credits": None}))
    assert row["warnings"] == 0


def test_un_fichier_SAIN_n_a_aucune_reserve(tmp_path):
    row = library._row_from_file(_ecrire(tmp_path, "x.md", {"title": "x", "language": "en"}))
    assert row["warnings"] == 0


def test_les_trois_colonnes_sont_transportees_jusqu_a_l_index():
    """Une colonne ajoutée au schéma mais absente de `_COLUMNS` ne serait
    jamais écrite — l'erreur est silencieuse."""
    for col in (
        "type", "source_transcript", "source_document_id",
        "meeting_id", "project_id", "warnings",
    ):
        assert col in library._COLUMNS, f"{col} manque à _COLUMNS"
        assert col in library._SCHEMA, f"{col} manque au schéma"


def test_la_migration_couvre_toute_colonne_du_schema(tmp_path, monkeypatch):
    """`CREATE TABLE IF NOT EXISTS` ne modifie pas une table existante : toute
    colonne du schéma doit aussi figurer dans la migration douce, sinon les
    bases déjà créées ne la voient jamais."""
    import sqlite3
    db = tmp_path / "t.db"
    # base « ancienne » : le schéma d'avant, sans les trois colonnes
    ancien = sqlite3.connect(db)
    ancien.execute("CREATE TABLE transcripts (name TEXT PRIMARY KEY, title TEXT, mtime REAL)")
    ancien.commit()
    ancien.close()

    monkeypatch.setattr(library, "DB_PATH", db)
    conn = library._conn()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(transcripts)")}
    for attendu in (
        "type", "source_transcript", "source_document_id",
        "meeting_id", "project_id", "warnings", "project",
    ):
        assert attendu in cols, f"{attendu} non ajoutée à une base existante"


def test_les_identites_relationnelles_sont_lues_du_markdown(tmp_path):
    from core import docid

    document_id = docid.nouveau()
    meeting_id = docid.nouveau()
    project_id = docid.nouveau()
    source_document_id = docid.nouveau()
    row = library._row_from_file(_ecrire(tmp_path, "x-cr.md", {
        "id": document_id,
        "meeting_id": meeting_id,
        "project_id": project_id,
        "source_document_id": source_document_id,
        "type": "report",
        "source_transcript": "ancien-nom.md",
    }))

    assert row["id"] == document_id
    assert row["meeting_id"] == meeting_id
    assert row["project_id"] == project_id
    assert row["source_document_id"] == source_document_id


def test_audit_signale_identites_yaml_et_rapport_orphelin(tmp_path, monkeypatch):
    from core import docid

    monkeypatch.setattr(library, "OUTPUT_DIR", tmp_path)
    shared = docid.nouveau()
    _ecrire(tmp_path, "valide.md", {"id": docid.nouveau(), "title": "valide"})
    _ecrire(tmp_path, "sans-id.md", {"title": "sans id"})
    _ecrire(tmp_path, "dup-a.md", {"id": shared, "title": "a"})
    _ecrire(tmp_path, "dup-b.md", {"id": shared, "title": "b"})
    _ecrire(tmp_path, "orphelin-cr.md", {
        "id": docid.nouveau(), "type": "report",
        "source_transcript": "absent.md", "title": "CR",
    })
    (tmp_path / "yaml-casse.md").write_text(
        "---\nid: [\n---\n\nCorps\n", encoding="utf-8"
    )

    audit = library.audit_corpus()

    assert audit["documents"] == 6
    assert audit["valid_ids"] == 4
    assert audit["issues"]["missing_or_invalid_ids"] == ["sans-id.md"]
    assert audit["issues"]["duplicate_ids"] == {
        shared: ["dup-a.md", "dup-b.md"],
    }
    assert audit["issues"]["orphan_reports"] == [{
        "path": "orphelin-cr.md", "source_transcript": "absent.md",
    }]
    assert list(audit["issues"]["invalid_yaml"]) == ["yaml-casse.md"]


# ---------- Motif nº1 : dériver les colonnes du DDL, pas d'une liste ----------

def _colonnes_du_ddl() -> set[str]:
    """Colonnes réellement déclarées dans `_SCHEMA`, lues depuis le DDL.

    Les commentaires SQL sont retirés d'abord : le contrôle voisin faisait
    `assert col in library._SCHEMA` — une recherche de SOUS-CHAÎNE, qu'un
    commentaire du DDL suffisait à satisfaire sans qu'aucune colonne existe.
    """
    import re

    corps = library._SCHEMA.split("(", 1)[1]
    corps = re.sub(r"--[^\n]*", "", corps)
    noms = set()
    for ligne in corps.split("\n"):
        ligne = ligne.strip()
        if not ligne or ligne.startswith((")", "CREATE", "PRIMARY", "FOREIGN", "UNIQUE")):
            continue
        premier = ligne.split()[0]
        if premier.isidentifier():
            noms.add(premier)
    return noms


def test_la_migration_couvre_TOUTE_colonne_du_DDL_pas_sept_noms_gelés():
    """`CREATE TABLE IF NOT EXISTS` ne modifie pas une table existante.

    Une colonne ajoutée au seul `_SCHEMA` reste donc absente des bases déjà
    créées, et le premier `reindex` rend `no such column`.

    Le contrôle qui existait vérifiait SEPT noms écrits à la main sur les
    vingt-quatre du schéma, et son auto-contrôle construisait `expected`
    DEPUIS `_MIGRATION_COLUMNS` avant de le comparer au `PRAGMA` — une
    tautologie. On dérive maintenant du DDL lui-même.
    """
    ddl = _colonnes_du_ddl()
    migration = {nom for nom, _ in library._MIGRATION_COLUMNS} | {"name"}
    oubliees = ddl - migration
    assert oubliees == set(), (
        f"colonne(s) du DDL absente(s) de la migration douce — les bases "
        f"existantes ne les verront jamais : {sorted(oubliees)}")


def test_la_migration_ne_declare_pas_de_colonne_FANTOME():
    """L'inverse : une entrée de migration sans colonne au DDL ferait ajouter
    une colonne que le schéma neuf n'a pas — deux formes de base divergentes."""
    ddl = _colonnes_du_ddl()
    migration = {nom for nom, _ in library._MIGRATION_COLUMNS}
    fantomes = migration - ddl
    assert fantomes == set(), (
        f"colonne(s) migrée(s) mais absente(s) du DDL : {sorted(fantomes)}")


def test_le_parseur_de_DDL_voit_bien_les_colonnes():
    """ANCRE DE NON-VACUITÉ — motif nº2.

    Si le parseur cessait de trouver quoi que ce soit, les deux tests
    ci-dessus compareraient deux ensembles vides et resteraient verts. C'est
    précisément ainsi que l'auto-contrôle précédent devenait creux.
    """
    ddl = _colonnes_du_ddl()
    assert len(ddl) >= 20, f"seulement {len(ddl)} colonne(s) vue(s) : {sorted(ddl)}"
    for attendue in ("name", "meeting_id", "project_id", "warnings", "type"):
        assert attendue in ddl, f"{attendue} non vue par le parseur : {sorted(ddl)}"


def test_le_parseur_ignore_les_COMMENTAIRES_du_ddl():
    """Le trou exact du contrôle précédent : `assert col in library._SCHEMA`
    était satisfait par un mot apparaissant dans un commentaire SQL."""
    import re

    faux = "CREATE TABLE t (\n  name TEXT,\n  -- colonne_fantome TEXT à venir\n)"
    corps = re.sub(r"--[^\n]*", "", faux.split("(", 1)[1])
    noms = {ligne.strip().split()[0] for ligne in corps.split("\n")
            if ligne.strip() and ligne.strip().split()[0].isidentifier()}
    assert "colonne_fantome" not in noms
    assert "name" in noms
