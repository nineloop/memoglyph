"""Le titre lisible d'une réunion partout où le slug paraissait encore.

Un document importé porte `title: <slug>` (le pipeline écrit `slugify(source.stem)`)
et `source: <fichier d'origine>`. L'index, la recherche (plein texte et par le sens),
le MCP, l'Assistant, les voix, les doublons et la corbeille rendaient le slug. Chaque
test ci-dessous échoue avec lui. Aucun `.md` n'est réécrit : les fichiers sont
relus à l'identique à la fin.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from mcp import Client

from api import auth, mcp
from core import dedup, docid, library, llm, projects, rag, semantic, voiceprint

pytestmark = [pytest.mark.integration]

SOURCE = "AMI IS1009a, réunion de lancement d'une télécommande.wav"
SLUG = "ami-is1009a-réunion-de-lancement-dune-télécommande"
LISIBLE = "AMI IS1009a, réunion de lancement d'une télécommande"
CORPS = ("## Transcript\n\n**Speaker 1** [00:00:10] on lance la télécommande, "
         "le prix de vente visé est de vingt-cinq euros\n")


def _entete(**champs) -> str:
    return "---\n" + "".join(f"{cle}: {valeur}\n" for cle, valeur in champs.items()) + "---\n\n"


@pytest.fixture
def corpus(isolated_stores, tmp_path, monkeypatch):
    from core.config import EMBED_DIM

    vecteur = [1.0] + [0.0] * (EMBED_DIM - 1)
    monkeypatch.setattr(semantic.embed, "embed_documents", lambda textes, **_k: [vecteur] * len(textes))
    monkeypatch.setattr(semantic.embed, "embed_query", lambda t, **_k: vecteur)
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(auth, "CODE_PATH", tmp_path / ".memoglyph-installation")
    auth.creer_compte("alice", "un mot de passe solide")

    sortie = isolated_stores["output_dir"]
    projects.ensure_inbox(sortie)
    dossier, projet = projects.create_owned(sortie, name="Clients", owner="alice")
    reunion, transcript = docid.nouveau(), docid.nouveau()
    commun = {"meeting_id": reunion, "project_id": projet.id, "date": "'2026-03-01'",
              "language": "fr"}
    (dossier / f"{SLUG}.md").write_text(
        _entete(id=transcript, **commun, title=SLUG, source=f'"{SOURCE}"') + CORPS,
        encoding="utf-8")
    (dossier / f"{SLUG}-cr.md").write_text(
        _entete(id=docid.nouveau(), **commun, title=f'"{SLUG} — compte-rendu"', type="report",
                source_transcript=f"{SLUG}.md", source_document_id=transcript)
        + f"# Compte-rendu — {SLUG}\n\n## Décisions\n\n"
        "- Le prix de vente visé est de vingt-cinq euros [source 00:00:10]\n",
        encoding="utf-8")
    library.reindex()
    semantic.reindex_all()
    fichiers = {p: p.read_bytes() for p in sorted(Path(sortie).rglob("*.md"))}
    yield {"sortie": sortie, "dossier": dossier, "projet": projet, "reunion": reunion,
           "transcript": transcript}
    # Aucun Markdown n'a été réécrit : seul ce qui est rendu change.
    assert {p: p.read_bytes() for p in fichiers if p.exists()} == {
        p: b for p, b in fichiers.items() if p.exists()}


# ---------- Index ----------

def test_l_index_porte_le_titre_lisible_du_transcript_et_du_compte_rendu(corpus) -> None:
    titres = {item["name"]: item["title"] for item in library.list_transcripts()}
    assert titres == {f"{SLUG}.md": LISIBLE, f"{SLUG}-cr.md": f"{LISIBLE} — compte-rendu"}
    assert library.get_meeting(corpus["reunion"])["title"] == LISIBLE


def test_un_compte_rendu_indexe_seul_prend_aussi_le_titre_lisible(corpus) -> None:
    # `sync_file` (après génération) n'a que le CR sous la main : le média vient de l'index.
    chemin = corpus["dossier"] / f"{SLUG}-cr.md"
    library.forget(chemin.name)
    library.sync_file(chemin)
    assert {i["name"]: i["title"] for i in library.list_transcripts()}[chemin.name] == (
        f"{LISIBLE} — compte-rendu")


def test_un_index_d_une_version_precedente_est_relu(corpus, isolated_stores) -> None:
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(isolated_stores["db_path"])) as conn:
        conn.execute("UPDATE transcripts SET title = name")
        conn.execute("UPDATE library_meta SET version = 4 WHERE component = 'library_index'")
        conn.commit()
    library.reindex()  # mtime inchangé : seule la version d'index force la relecture
    assert {i["title"] for i in library.list_transcripts()} == {LISIBLE, f"{LISIBLE} — compte-rendu"}


# ---------- Recherche ----------

def test_la_recherche_plein_texte_rend_titre_lisible_et_source_du_compte_rendu(corpus) -> None:
    resultats = {r["name"]: r for r in library.search("vingt-cinq")}
    assert resultats[f"{SLUG}.md"]["title"] == LISIBLE
    rapport = resultats[f"{SLUG}-cr.md"]
    assert rapport["title"] == f"{LISIBLE} — compte-rendu"
    assert rapport["source"] == SOURCE


def test_la_recherche_par_le_sens_rend_titre_lisible_et_source_du_compte_rendu(corpus) -> None:
    resultats = {r["name"]: r for r in semantic.search("prix de vente", k=10)}
    assert resultats[f"{SLUG}.md"]["title"] == LISIBLE
    rapport = resultats[f"{SLUG}-cr.md"]
    assert rapport["title"] == f"{LISIBLE} — compte-rendu"
    assert rapport["source"] == SOURCE


def test_les_doublons_ne_rangent_pas_le_compte_rendu_parmi_les_versions(corpus) -> None:
    # La source du CR n'est PAS recopiée dans l'index : il n'est pas une version du média.
    (corpus["dossier"] / f"{SLUG}-1.md").write_text(
        _entete(id=docid.nouveau(), meeting_id=corpus["reunion"], project_id=corpus["projet"].id,
                title=SLUG, source=f'"{SOURCE}"', language="fr") + CORPS, encoding="utf-8")
    library.reindex()
    groupes = dedup.find_groups(project_id=corpus["projet"].id)
    membres = [m for g in groupes for m in g["members"] if g["source"] == SOURCE]
    assert sorted(m["name"] for m in membres) == [f"{SLUG}-1.md", f"{SLUG}.md"]
    assert {m["title"] for m in membres} == {LISIBLE}


# ---------- Assistant ----------

def test_l_assistant_cite_ses_sources_par_le_titre_lisible(corpus, monkeypatch) -> None:
    invites: list[str] = []
    monkeypatch.setattr(llm, "is_available", lambda **_k: True)
    monkeypatch.setattr(llm, "chat", lambda _s, invite, **_k: invites.append(invite) or "Vingt-cinq euros [1].")
    reponse = rag.ask("Quel prix de vente ?", project_id=corpus["projet"].id, provider="mlx")
    assert [s["title"] for s in reponse["sources"]] == [LISIBLE]
    assert f"« {LISIBLE} »" in invites[0] and SLUG not in invites[0]


# ---------- MCP ----------

def _mcp(nom: str, **arguments) -> dict:
    """Un appel d'outil par le client en mémoire du SDK `mcp`."""
    async def appeler():
        serveur = mcp.construire(auth.Utilisateur(id="alice", identifiant="alice", admin=False))
        async with Client(serveur) as client:
            return await client.call_tool(nom, arguments)
    resultat = asyncio.run(appeler())
    assert not resultat.is_error, resultat
    return resultat.structured_content


def test_le_mcp_liste_recherche_et_lit_sous_le_titre_lisible(corpus) -> None:
    assert [r["title"] for r in _mcp("lister_reunions")["reunions"]] == [LISIBLE]
    titres = {r["name"]: r["title"] for r in _mcp("rechercher", requete="prix de vente")["resultats"]}
    assert titres == {f"{SLUG}.md": LISIBLE, f"{SLUG}-cr.md": f"{LISIBLE} — compte-rendu"}
    lu = _mcp("lire_compte_rendu", reunion=corpus["reunion"])
    assert lu["titre"] == LISIBLE
    # Le CR sur disque garde son H1 slug ; la lecture rend le titre lisible.
    assert lu["compte_rendu"].splitlines()[0] == f"# Compte-rendu — {LISIBLE}"
    assert "vingt-cinq euros [source 00:00:10]" in lu["compte_rendu"]


# ---------- Voix, corbeille, plan d'archivage ----------

def test_une_occurrence_de_voix_porte_titre_lisible_et_source(corpus) -> None:
    voiceprint.index_file(f"{SLUG}.md", {"Speaker 1": [1.0] + [0.0] * 255})
    [voix] = voiceprint.list_voices()
    [occurrence] = voix["occurrences"]
    assert occurrence["title"] == LISIBLE
    assert occurrence["source"] == SOURCE


def test_le_plan_d_archivage_annonce_les_titres_lisibles(corpus) -> None:
    plan = library.archive_plan(f"{SLUG}.md")
    assert [d["title"] for d in plan] == [f"{LISIBLE} — compte-rendu", LISIBLE]


def test_la_corbeille_rend_titre_lisible_et_source(corpus) -> None:
    assert library.archive(f"{SLUG}-cr.md")
    [rapport] = library.trash_items()
    assert rapport["title"] == f"{LISIBLE} — compte-rendu"
    assert rapport["source"] == SOURCE
