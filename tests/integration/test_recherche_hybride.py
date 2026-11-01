"""Recherche hybride : les deux index, leur synchronisation, et la fusion.

Le dense retrouve une question reformulée mais rate un terme littéral (un nom
propre, un sigle) ; le lexical retrouve le terme exact. La fusion garde le meilleur
des deux. Ces tests vérifient les invariants sans lesquels ce gain disparaîtrait en
silence.
"""

from __future__ import annotations


import pytest

from core import semantic


def _vec(n: int = 0) -> list[float]:
    from core.config import EMBED_DIM
    v = [0.0] * EMBED_DIM
    v[n % EMBED_DIM] = 1.0
    return v


@pytest.fixture
def corpus(isolated_stores, monkeypatch):
    """Trois documents, embeddings déterministes — on teste la MÉCANIQUE de
    fusion, pas la qualité d'un modèle."""
    from core import docid, library, projects

    projects.ensure_inbox(isolated_stores["output_dir"])
    projet = projects.load(isolated_stores["output_dir"]).id

    textes = {
        "a.md": "Le projet Allbirds avance bien cette semaine.\n\n"
                "Nous parlons de chaussures et de matériaux durables.",
        "b.md": "Rien à voir : ici on discute budget et calendrier.\n\n"
                "Les échéances du trimestre sont tenues.",
        "c.md": "Un troisième document sans rapport avec les deux autres.\n\n"
                "Il sert de bruit de fond pour la recherche.",
    }
    for nom, corps in textes.items():
        (isolated_stores["output_dir"] / nom).write_text(
            f"---\nid: {docid.nouveau()}\nmeeting_id: {docid.nouveau()}\n"
            f"project_id: {projet}\ntitle: {nom}\n---\n\n{corps}\n",
            encoding="utf-8")
    library.reindex()

    compteur = {"n": 0}

    def faux_embed(textes_, **kw):
        out = []
        for _ in textes_:
            out.append(_vec(compteur["n"]))
            compteur["n"] += 1
        return out

    monkeypatch.setattr(semantic.embed, "embed_documents", faux_embed)
    monkeypatch.setattr(semantic.embed, "embed_query", lambda t, **kw: _vec(999))
    semantic.reindex_all()
    return isolated_stores


def _compte(table: str) -> int:
    conn = semantic._conn()
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def test_chaque_morceau_entre_dans_les_deux_index(corpus) -> None:
    """RRF fusionne par `chunk_id` : un morceau absent d'un index est invisible
    pour la moitié du système."""
    assert _compte("chunks") > 0
    assert _compte("chunks_fts") == _compte("chunks")


def test_un_index_lexical_desynchronise_declenche_une_reconstruction(
        corpus, capsys) -> None:
    """Le défaut d'un `chunks_fts` ajouté à une base existante : à sa création il
    était VIDE, aucun document n'avait changé, et la réindexation incrémentale
    n'avait rien à faire. `retrieve` retombait silencieusement sur le dense
    seul — la moitié de l'hybride absente, sans un mot."""
    conn = semantic._conn()
    try:
        conn.execute("DELETE FROM chunks_fts")
        conn.commit()
    finally:
        conn.close()
    assert _compte("chunks_fts") == 0

    semantic.reindex_all()

    assert _compte("chunks_fts") == _compte("chunks")
    assert "désynchronisé" in capsys.readouterr().out, (
        "la reconstruction doit être ANNONCÉE, pas faite en douce"
    )


def test_un_morceau_supprime_quitte_aussi_l_index_lexical(corpus) -> None:
    """Sans suppression explicite, le morceau d'hier resterait interrogeable en
    lexical alors qu'il a disparu du vectoriel — et RRF remonterait un fantôme."""
    avant = _compte("chunks_fts")
    from core import library
    ancien = (corpus["output_dir"] / "a.md").read_text(encoding="utf-8")
    entete = ancien.split("---")[1]
    (corpus["output_dir"] / "a.md").write_text(
        f"---{entete}---\n\nTexte entièrement remplacé, bien plus court.\n",
        encoding="utf-8")
    library.reindex()
    semantic.reindex_all()

    assert _compte("chunks_fts") == _compte("chunks")
    conn = semantic._conn()
    try:
        restes = conn.execute(
            "SELECT COUNT(*) FROM chunks_fts WHERE text MATCH 'Allbirds'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert restes == 0, f"{avant - _compte('chunks_fts')} morceaux retirés, mais un fantôme reste"


def test_le_lexical_retrouve_un_terme_exact_que_le_dense_ignore(corpus) -> None:
    """Le cas qui justifie l'hybride : sur un terme littéral, le dense seul
    rate le bon passage.

    `k=1`, et c'est le point. Avec `k=3` le test passait même en neutralisant
    la fusion — les embeddings factices rendent tous les morceaux équidistants,
    le dense en ramène trois au hasard et « Allbirds » tombait parfois dedans.
    Une mutation l'a montré. À `k=1`, seul un vote lexical peut placer le bon
    morceau en tête.
    """
    passages = semantic.retrieve("Allbirds", k=1)

    assert passages, "aucun passage rendu"
    assert "Allbirds" in passages[0]["text"], (
        f"premier passage rendu : {passages[0]['text'][:60]!r}"
    )


def test_la_fusion_vote_par_rang_et_non_par_score() -> None:
    """Un BM25 et un cosinus ne sont pas comparables ; leurs rangs le sont.

    Un candidat présent dans les DEUX listes doit passer devant un premier de
    liste unique — c'est tout l'intérêt de la fusion, et le seul comportement
    qui la distingue d'une simple concaténation.
    """
    ordre = semantic._fusion_rrf([10, 20, 30], [20, 40, 50])

    assert ordre[0] == 20, f"le consensus doit gagner, ordre obtenu : {ordre}"
    assert set(ordre) == {10, 20, 30, 40, 50}


def test_une_requete_refusee_par_fts5_ne_casse_pas_la_recherche(
        corpus, capsys) -> None:
    """Le dense seul reste utilisable, et c'est ce que l'utilisateur avait
    avant l'hybride. Une syntaxe FTS5 invalide ne doit pas lui retirer ça."""
    conn = semantic._conn()
    try:
        rangs = semantic._rangs_lexicaux(conn, '"""" AND ((')
    finally:
        conn.close()

    assert rangs == []


def test_une_apostrophe_ne_fait_pas_lever_la_requete(corpus) -> None:
    """Régime courant en français, et la raison pour laquelle la requête est
    reconstruite plutôt que passée telle quelle à FTS5."""
    assert semantic.retrieve("qu'est-ce qu'on dit d'Allbirds", k=3) is not None


def test_la_reconstruction_complete_vide_les_deux_index(corpus, monkeypatch) -> None:
    """Un changement de modèle reconstruit tout : le lexical doit suivre, sinon
    il porterait les morceaux d'un découpage périmé."""
    conn = semantic._conn()
    try:
        conn.execute("UPDATE semantic_index_meta SET model = 'autre-modele'")
        conn.commit()
    finally:
        conn.close()

    semantic.reindex_all()

    assert _compte("chunks_fts") == _compte("chunks")
    assert semantic.index_spec()["model"] == semantic._nom_du_moteur()


def test_la_recherche_par_document_profite_aussi_de_la_fusion(corpus) -> None:
    """`semantic.search` est restée dense pure après l'étape E — l'onglet
    Recherche subissait donc encore la faiblesse du dense sur les termes exacts,
    alors que c'est justement là qu'on tape un nom propre.

    Par document, la fusion ne coûte rien sur une question reformulée : la dédup
    par document l'explique — un mauvais candidat lexical ne consomme plus une
    place du top-k, il est absorbé par un document déjà présent.
    """
    resultats = semantic.search("Allbirds", k=1)

    assert resultats, "aucun résultat"
    assert resultats[0]["name"] == "a.md", (
        f"document rendu : {resultats[0]['name']}"
    )


def test_la_recherche_ne_publie_plus_de_pourcentage_de_confiance(corpus) -> None:
    """L'interface affichait `score * 100` en pourcentage, ce qui se lit comme
    une confiance. Deux classements fusionnés n'en produisent aucune : un
    résultat classé premier pourrait afficher 45 % pendant que le second
    afficherait 60 %. Un chiffre faux vaut moins que pas de chiffre."""
    resultats = semantic.search("Allbirds", k=3)

    assert resultats
    assert all("score" not in r for r in resultats)
    assert [r["rang"] for r in resultats] == list(range(1, len(resultats) + 1))


# ---------- Suppression et cadrage : l'index lexical suit les deux ----------


def test_supprimer_un_document_le_retire_AUSSI_de_l_index_lexical(corpus) -> None:
    """`remove()` supprimait `vec_chunks` et `chunks`, jamais `chunks_fts`.

    Le texte d'un document supprimé restait donc dans l'index lexical. Le
    résultat fantôme était écarté à la jointure finale — donc invisible — mais
    il consommait une place du top-50 et SURVIVAIT à la suppression jusqu'au
    prochain `reindex_all()`. Une suppression doit valoir tout de suite.

    Critère : vérifier immédiatement, sans réindexation réparatrice.
    """
    conn = semantic._conn()
    try:
        avant = [r[0] for r in conn.execute(
            "SELECT c.id FROM chunks c JOIN transcripts t "
            "ON t.id = c.document_id OR (c.document_id IS NULL AND t.name = c.name) "
            "WHERE t.name = 'a.md'")]
    finally:
        conn.close()
    assert avant, "le document témoin n'a pas de morceau — le test regarderait à côté"

    semantic.remove("a.md")

    conn = semantic._conn()
    try:
        marques = ",".join("?" * len(avant))
        restants = [r[0] for r in conn.execute(
            f"SELECT rowid FROM chunks_fts WHERE rowid IN ({marques})", avant)]
    finally:
        conn.close()
    assert restants == [], (
        f"texte supprimé toujours présent dans l'index lexical : {restants}")
    assert _compte("chunks_fts") == _compte("chunks"), "les deux index ont divergé"


def test_un_autre_projet_ne_peut_pas_affamer_la_recherche_cadree(
        isolated_stores, monkeypatch) -> None:
    """Le plafond lexical tombait AVANT le filtre projet.

    Cinquante candidats étaient prélevés sur le corpus ENTIER, puis ceux d'un
    autre projet étaient écartés. Un projet bruyant sur les mots de la requête
    consommait donc toutes les places, et la recherche cadrée retombait en
    SILENCE sur le dense seul.

    Critère : plus de 50 correspondances mieux classées dans un
    AUTRE projet, une correspondance exacte dans le projet ciblé, et cette
    dernière doit contribuer au premier résultat hybride.
    """
    from core import docid, library, projects

    projects.ensure_inbox(isolated_stores["output_dir"])
    bruyant = projects.load(isolated_stores["output_dir"]).id
    dossier_cible = isolated_stores["output_dir"] / "cible"
    dossier_cible.mkdir()
    cible = projects.create(dossier_cible, name="cible").id

    # 60 documents dans le projet bruyant : au-delà de RRF_PROFONDEUR (50).
    for i in range(60):
        (isolated_stores["output_dir"] / f"bruit-{i:02d}.md").write_text(
            f"---\nid: {docid.nouveau()}\nmeeting_id: {docid.nouveau()}\n"
            f"project_id: {bruyant}\ntitle: bruit {i}\n---\n\n"
            "Allbirds Allbirds Allbirds Allbirds Allbirds.\n",
            encoding="utf-8")
    # UN seul document autorisé, dans le projet ciblé.
    (dossier_cible / "vise.md").write_text(
        f"---\nid: {docid.nouveau()}\nmeeting_id: {docid.nouveau()}\n"
        f"project_id: {cible}\ntitle: visé\n---\n\n"
        "Le partenariat Allbirds a été signé mardi.\n",
        encoding="utf-8")
    library.reindex()

    # Embeddings tous ORTHOGONAUX à la requête : le dense ne peut pas départager,
    # donc ce qui remonte vient forcément du lexical. Sans ça le test pourrait
    # passer grâce au dense et ne rien prouver du chemin corrigé.
    monkeypatch.setattr(semantic.embed, "embed_documents",
                        lambda ts, **k: [_vec(1) for _ in ts])
    monkeypatch.setattr(semantic.embed, "embed_query", lambda t, **k: _vec(2))
    semantic.reindex_all()

    conn = semantic._conn()
    try:
        vise = [r[0] for r in conn.execute(
            "SELECT c.id FROM chunks c JOIN transcripts t "
            "ON t.id = c.document_id OR (c.document_id IS NULL AND t.name = c.name) "
            "WHERE t.name = 'vise.md'")]
        assert vise, "le document ciblé n'a pas de morceau — le test regarderait à côté"

        clause, extra = semantic._project_clause(cible)
        rangs = semantic._rangs_lexicaux(conn, "Allbirds", semantic.RRF_PROFONDEUR,
                                         clause, extra)
    finally:
        conn.close()

    # C'est ICI que porte la correction, et le test la vise directement.
    #
    # Passer par `semantic.search()` ne prouverait RIEN : le chemin DENSE est
    # filtré par projet et rendrait `vise.md` de toute façon, famine lexicale ou
    # non. Ma première version faisait exactement cette erreur — elle passait
    # avec le défaut remis. Un test qui ne peut pas échouer ne mesure rien.
    assert vise[0] in rangs, (
        "le morceau AUTORISÉ n'atteint pas la fusion : les 60 documents de "
        f"l'autre projet ont consommé les {semantic.RRF_PROFONDEUR} places")
    assert len(rangs) <= semantic.RRF_PROFONDEUR

    # Et rien d'un autre projet ne doit sortir de la requête filtrée.
    conn = semantic._conn()
    try:
        marques = ",".join("?" * len(rangs))
        hors = conn.execute(
            "SELECT COUNT(*) FROM chunks c JOIN transcripts t "
            "ON t.id = c.document_id OR (c.document_id IS NULL AND t.name = c.name) "
            f"WHERE c.id IN ({marques}) AND t.project_id != ?", (*rangs, cible),
        ).fetchone()[0]
    finally:
        conn.close()
    assert hors == 0, f"{hors} candidat(s) lexicaux hors du projet ciblé"
