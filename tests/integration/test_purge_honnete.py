"""La purge doit être HONNÊTE : ne jamais déclarer propre ce qu'elle n'a pas
vérifié, ne jamais vérifier une clé différente de celle qu'elle a purgée, et ne
jamais choisir à la place de l'utilisateur quand la cible est ambiguë.

Ces cas passaient tous AVANT correctif — la suite existante ne les couvrait pas
parce que sa fixture neutralise `media.audio_path` et écrit un front-matter sans
`id:`, donc n'exerce jamais le chemin ULID, qui est celui du corpus réel.
"""

from __future__ import annotations

import pytest

from core import config, docid, library, media, purge, voiceprint
from core.config import VOICE_EMBED_DIM


def _document(output, nom: str, *, titre: str = "Reunion", source: str = "a.m4a") -> str:
    """Écrit un .md identifié (ULID) et l'indexe, comme le fait le pipeline."""
    ident = docid.nouveau()
    chemin = output / nom
    chemin.write_text(
        f"---\nid: {ident}\ntitle: {titre}\nsource: {source}\nlanguage: fr\n---\n\n"
        "**Speaker 1** [00:00:01] bonjour\n",
        encoding="utf-8",
    )
    library.sync_file(chemin)
    return ident


def _avec_artefacts(output, monkeypatch, ident: str, nom: str) -> "object":
    """Empreintes + audio rangés sous l'ULID, comme en production."""
    vec = [1.0] + [0.0] * (VOICE_EMBED_DIM - 1)
    voiceprint.index_file(ident, {"Speaker 1": vec}, name=nom)
    monkeypatch.setattr(voiceprint, "SIDECAR", output / ".voiceprints.json")
    voiceprint.save_embeddings(ident, {"Speaker 1": vec}, root=output)
    audio = output / ".audio"
    audio.mkdir(exist_ok=True)
    fichier = audio / f"{ident}.m4a"
    fichier.write_bytes(b"audio")
    monkeypatch.setattr(config, "AUDIO_DIR", audio)
    return fichier


def test_un_audio_survivant_est_signale_et_interdit_le_verdict_propre(
    isolated_stores, monkeypatch,
) -> None:
    """La vérification doit interroger la clé SOUS LAQUELLE on a purgé.

    Elle re-dérivait l'identité depuis le .md qu'elle venait de supprimer, donc
    retombait sur le stem et cherchait l'audio sous une clé où il n'a jamais
    été : un échec de suppression passait pour une absence de trace.
    """
    output = isolated_stores["output_dir"]
    ident = _document(output, "reunion.md")
    audio = _avec_artefacts(output, monkeypatch, ident, "reunion.md")

    def echoue(_identity, root=None):
        raise OSError("volume verrouille")

    monkeypatch.setattr(media, "drop_audio", echoue)

    resultat = purge.purge_transcript("reunion.md")

    assert audio.exists(), "pré-condition : l'audio doit avoir survécu"
    assert resultat["after"]["audio"] is True, "l'audio survivant doit être VU"
    assert resultat["clean"] is False
    assert any("volume verrouille" in e for e in resultat["errors"])


def test_des_empreintes_survivantes_sont_signalees(
    isolated_stores, monkeypatch,
) -> None:
    output = isolated_stores["output_dir"]
    ident = _document(output, "reunion.md")
    _avec_artefacts(output, monkeypatch, ident, "reunion.md")

    def echoue(_identity, root=None):
        raise OSError("sidecar verrouille")

    monkeypatch.setattr(voiceprint, "drop_embeddings", echoue)

    resultat = purge.purge_transcript("reunion.md")

    assert voiceprint.load_embeddings(ident, root=output), "pré-condition"
    assert resultat["after"]["embeddings"] > 0, "les empreintes restantes doivent être VUES"
    assert resultat["clean"] is False


def test_clean_ne_peut_pas_contredire_errors(isolated_stores, monkeypatch) -> None:
    """`clean` ignorait `errors` : les deux champs du même dict se contredisaient,
    et api/main.py ne lit `errors` que si `clean` est faux — l'erreur était jetée."""
    output = isolated_stores["output_dir"]
    ident = _document(output, "reunion.md")
    _avec_artefacts(output, monkeypatch, ident, "reunion.md")

    def echoue(_identity, root=None):
        raise OSError("panne")

    monkeypatch.setattr(media, "drop_audio", echoue)
    resultat = purge.purge_transcript("reunion.md")

    assert resultat["errors"], "pré-condition : une étape a échoué"
    assert resultat["clean"] is False, "clean ne doit jamais coexister avec des erreurs"


def test_une_cible_ambigue_est_refusee_sans_rien_supprimer(isolated_stores) -> None:
    """Deux passes sur le même média partagent `title` et `source`.

    `transcript_path` en choisissait un silencieusement : la purge détruisait
    un document que l'utilisateur n'avait pas désigné.
    """
    output = isolated_stores["output_dir"]
    _document(output, "notes.md", titre="Comite", source="comite.m4a")
    _document(output, "notes-1.md", titre="Comite", source="comite.m4a")
    a, b = output / "notes.md", output / "notes-1.md"

    resultat = purge.purge_transcript("Comite")

    assert resultat["clean"] is False
    assert a.exists() and b.exists(), "un refus ne doit RIEN supprimer"
    message = " ".join(resultat["errors"])
    assert "notes.md" in message and "notes-1.md" in message, (
        "le refus doit nommer les candidats pour que l'utilisateur puisse trancher"
    )


def test_un_second_appel_ne_detruit_pas_le_document_suivant(isolated_stores) -> None:
    """Le piège était refermé sur lui-même : le premier appel renvoyait une
    fausse erreur « traces restantes » désignant l'AUTRE document, ce qui
    invitait à réessayer — et le second appel le détruisait."""
    output = isolated_stores["output_dir"]
    _document(output, "notes.md", titre="Comite", source="comite.m4a")
    _document(output, "notes-1.md", titre="Comite", source="comite.m4a")
    b = output / "notes-1.md"

    purge.purge_transcript("Comite")
    purge.purge_transcript("Comite")

    assert b.exists(), "aucun appel ne doit détruire un document non désigné"


def test_purger_par_nom_de_fichier_reste_possible_et_precis(isolated_stores) -> None:
    """Le refus ne doit pas bloquer le cas légitime : un nom de fichier est
    sans ambiguïté, même quand un homonyme de titre existe."""
    output = isolated_stores["output_dir"]
    _document(output, "notes.md", titre="Comite", source="comite.m4a")
    _document(output, "notes-1.md", titre="Comite", source="comite.m4a")
    a, b = output / "notes.md", output / "notes-1.md"

    resultat = purge.purge_transcript("notes.md")

    assert not a.exists(), "le document désigné doit bien être supprimé"
    assert b.exists(), "l'homonyme ne doit pas être touché"
    assert resultat["clean"] is True
    assert resultat["errors"] == []


def test_le_manifeste_survit_a_un_echec_de_suppression_en_corbeille(
    isolated_stores, monkeypatch,
) -> None:
    """Le manifeste porte le nom d'origine, le projet et l'ULID : l'effacer
    alors que le Markdown a résisté détruit l'identité nécessaire à la reprise."""
    output = isolated_stores["output_dir"]
    _document(output, "reunion.md")
    assert purge.archive("reunion.md")["archived"]
    archive = library.trash_items()[0]["name"]

    reel = purge.Path.unlink

    def refuse(self, missing_ok=False):
        if self.parent.name == ".trash":
            raise OSError("corbeille verrouillee")
        return reel(self, missing_ok=missing_ok)

    # Pas de monkeypatch.undo() ici : il annulerait AUSSI l'isolation posée par
    # la fixture, et l'assertion irait lire la corbeille du vrai corpus.
    monkeypatch.setattr(purge.Path, "unlink", refuse)
    purge.purge_transcript("reunion.md")

    assert [i["name"] for i in library.trash_items()] == [archive], (
        "le manifeste doit rester tant que le Markdown de corbeille résiste"
    )


# --- Corbeille : le partage d'artefacts se juge sur la CLÉ DE STOCKAGE -------

def test_archiver_par_titre_supprime_bien_la_ligne_d_index(isolated_stores) -> None:
    """`library.forget` supprime par NOM DE FICHIER. Lui passer la chaîne
    d'appel (un titre) ne supprimait rien : la ligne survivait à l'archivage,
    et ce fantôme faisait ensuite passer le document pour vivant."""
    output = isolated_stores["output_dir"]
    _document(output, "notes.md", titre="Comite")

    assert library.archive("Comite") is True

    assert [t["name"] for t in library.list_transcripts()] == [], (
        "aucune ligne d'index ne doit survivre à l'archivage"
    )


def test_purger_une_corbeille_archivee_par_titre_ne_laisse_rien(
    isolated_stores, monkeypatch,
) -> None:
    """Le manifeste ne retient que la chaîne d'appel : elle ne suffit pas à
    retrouver l'index ni les artefacts. Le nom de fichier, lui, est
    reconstructible depuis la clé d'archive."""
    output = isolated_stores["output_dir"]
    ident = _document(output, "notes.md", titre="Comite")
    fichier = _avec_artefacts(output, monkeypatch, ident, "notes.md")

    assert library.archive("Comite") is True
    archive = library.trash_items()[0]["name"]
    resultat = purge.purge_archived(archive)

    assert resultat["deleted"] == archive
    assert resultat["shared_artifacts_preserved"] is False
    assert resultat["clean"] is True, f"traces restantes : {resultat['errors']}"
    assert not fichier.exists(), "l'audio ne doit pas rester orphelin"
    assert not voiceprint.load_embeddings(ident, root=output)


def test_un_document_vivant_partageant_la_cle_protege_les_artefacts(
    isolated_stores, monkeypatch,
) -> None:
    """Le garde-fou compare désormais des CLÉS DE STOCKAGE. Quand un document
    vivant partage la clé, ses artefacts durables doivent survivre."""
    output = isolated_stores["output_dir"]
    ident = _document(output, "notes.md", titre="Comite")
    fichier = _avec_artefacts(output, monkeypatch, ident, "notes.md")

    assert library.archive("notes.md") is True
    archive = library.trash_items()[0]["name"]
    # Un document ACTIF reprend la même identité (restauration, réimport).
    revenu = output / "notes.md"
    revenu.write_text(
        f"---\nid: {ident}\ntitle: Comite\nlanguage: fr\n---\n\n**Speaker 1** [00:00:01] a\n",
        encoding="utf-8",
    )
    library.sync_file(revenu)

    resultat = purge.purge_archived(archive)

    assert resultat["shared_artifacts_preserved"] is True
    assert fichier.exists(), (
        "PERTE DE DONNEES : l'audio d'un document VIVANT a ete detruit en vidant "
        "la corbeille"
    )
    assert voiceprint.load_embeddings(ident, root=output)


def test_la_copie_de_corbeille_est_retrouvee_par_nom_de_fichier(
    isolated_stores,
) -> None:
    """Archivé par titre, purgé par nom de fichier : la copie de corbeille
    n'était pas retrouvée et survivait à une purge annoncée complète."""
    output = isolated_stores["output_dir"]
    _document(output, "notes.md", titre="Comite")
    assert library.archive("Comite") is True
    assert library.trash_items(), "pré-condition : la corbeille contient la copie"

    resultat = purge.purge_transcript("notes.md")

    assert resultat["after"]["trash_copies"] == 0, (
        "la copie de corbeille doit être retrouvée et supprimée"
    )
    assert resultat["clean"] is True, f"traces restantes : {resultat['errors']}"


# --- Registre vocal : un fichier illisible ne doit pas emporter les autres ---

def _corpus_a_empreintes(output, monkeypatch, bases) -> None:
    from core import projects

    # NE PAS toucher à `voiceprint.DB_PATH` : la fixture `isolated_stores` l'a
    # déjà pointé sur la base temporaire. Le réassigner depuis `sqlite_db`
    # ramènerait le chemin RÉEL et ferait écrire dans le corpus de l'utilisateur.
    monkeypatch.setattr(voiceprint, "SIDECAR", output / ".voiceprints.json")
    projet = projects.ensure_inbox(output)
    vec = [1.0] + [0.0] * (VOICE_EMBED_DIM - 1)
    for base in bases:
        ident = docid.nouveau()
        chemin = output / f"{base}.md"
        chemin.write_text(
            f"---\nid: {ident}\nmeeting_id: {docid.nouveau()}\n"
            f"project_id: {projet.id}\ntitle: {base}\nlanguage: fr\n"
            "diarization: true\n---\n\n**Speaker 1** [00:00:01] bonjour\n",
            encoding="utf-8",
        )
        library.sync_file(chemin)
        voiceprint.save_embeddings(ident, {"Speaker 1": vec})


@pytest.mark.parametrize("nom_casse", ["aab.md", "nnn.md", "zzzz.md"])
def test_un_md_illisible_n_emporte_pas_le_reste_du_registre(
    isolated_stores, monkeypatch, nom_casse,
) -> None:
    """La boucle indexait au fil de l'eau, par ordre alphabétique, sans garde :
    un `.md` au codage invalide la faisait lever, et le nombre de documents
    rattachés dépendait du RANG du fichier fautif — 1/3, 2/3 ou 3/3.

    Un échec dont le résultat dépend d'un nom de fichier est bien plus difficile
    à diagnostiquer qu'un refus net. Le paramétrage vérifie les trois positions.
    """
    output = isolated_stores["output_dir"]
    _corpus_a_empreintes(output, monkeypatch, ("aaa", "mmm", "zzz"))
    (output / nom_casse).write_bytes(b"---\ntitle: casse\n---\n\n\xff\xfe pas UTF-8\n")

    resultat = voiceprint._reindex_all()

    assert resultat["indexed"] == 3, (
        f"les 3 documents sains doivent être rattachés quel que soit le rang du "
        f"fichier fautif — obtenu {resultat['indexed']} avec {nom_casse}"
    )
    assert [i.split(" : ")[0] for i in resultat["ignores"]] == [nom_casse], (
        "le fichier ignoré doit être NOMMÉ, pas seulement compté"
    )


def test_un_corpus_sain_ne_signale_aucun_fichier_ignore(
    isolated_stores, monkeypatch,
) -> None:
    """Une liste vide dit « vérifié, rien trouvé » ; une clé absente se lirait
    comme « rien à signaler ». La clé doit toujours être là."""
    output = isolated_stores["output_dir"]
    _corpus_a_empreintes(output, monkeypatch, ("aaa", "bbb"))

    resultat = voiceprint._reindex_all()

    assert resultat["indexed"] == 2
    assert resultat["ignores"] == []


# --- Le magasin autoritatif suit les décisions faites en base ---------------

def _voix_avec_membre(output, monkeypatch, doc: str, speaker: str = "Speaker 1") -> int:
    """Crée une voix par le chemin normal et retourne son id technique."""
    from core import voice_registry

    monkeypatch.setattr(voiceprint, "SIDECAR", output / ".voiceprints.json")
    monkeypatch.setattr(voice_registry, "FICHIER", output / ".voices.json")
    vec = [1.0] + [0.0] * (VOICE_EMBED_DIM - 1)
    voiceprint.index_file(doc, {speaker: vec}, name=f"{doc}.md")
    conn = voiceprint._conn()
    try:
        return conn.execute(
            "SELECT voice_id FROM voice_members WHERE document_id = ? OR name = ?",
            (doc, f"{doc}.md"),
        ).fetchone()[0]
    finally:
        conn.close()


def test_un_nom_saisi_en_base_rejoint_le_magasin_autoritatif(
    isolated_stores, monkeypatch,
) -> None:
    """La base est DÉRIVÉE : sans report, la prochaine réindexation efface le nom."""
    from core import docid as d, voice_registry

    output = isolated_stores["output_dir"]
    doc = d.nouveau()
    vid = _voix_avec_membre(output, monkeypatch, doc)

    voiceprint.rename(vid, "Ari")

    magasin = voice_registry.lire(output)
    nommees = [v for v in magasin["voix"].values() if v.get("label") == "Ari"]
    assert nommees, "le nom doit rejoindre le magasin autoritatif"
    assert nommees[0]["label_source"] == "manual"
    assert nommees[0].get("membres"), "les membres doivent être consignés avec le nom"


def test_effacer_un_nom_est_aussi_reporte(isolated_stores, monkeypatch) -> None:
    from core import docid as d, voice_registry

    output = isolated_stores["output_dir"]
    vid = _voix_avec_membre(output, monkeypatch, d.nouveau())
    voiceprint.rename(vid, "Ari")
    voiceprint.rename(vid, "")

    magasin = voice_registry.lire(output)
    assert not [v for v in magasin["voix"].values() if v.get("label")]


def test_une_fusion_est_journalisee_avant_la_suppression(
    isolated_stores, monkeypatch,
) -> None:
    """Après le DELETE, l'`uid` de la source est perdu : journaliser ensuite
    serait trop tard, et la décision deviendrait irrécupérable."""
    from core import docid as d, voice_registry

    output = isolated_stores["output_dir"]
    a = _voix_avec_membre(output, monkeypatch, d.nouveau(), "Speaker 1")
    # Une empreinte volontairement éloignée, pour obtenir une SECONDE voix.
    monkeypatch.setattr(voiceprint, "SIDECAR", output / ".voiceprints.json")
    doc_b = d.nouveau()
    vec_b = [0.0] * (VOICE_EMBED_DIM - 1) + [1.0]
    voiceprint.index_file(doc_b, {"Speaker 1": vec_b}, name=f"{doc_b}.md")
    conn = voiceprint._conn()
    try:
        b = conn.execute(
            "SELECT voice_id FROM voice_members WHERE document_id = ?", (doc_b,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert a != b, "pré-condition : deux voix distinctes"

    voiceprint.merge_voices(a, b)

    magasin = voice_registry.lire(output)
    assert len(magasin["fusions"]) == 1, "la DÉCISION de fusion doit être journalisée"


def test_un_magasin_en_echec_fait_echouer_le_renommage(
    isolated_stores, monkeypatch,
) -> None:
    """Un magasin en échec fait échouer l'action, il n'est pas seulement signalé.

    L'argument inverse, « le nom est déjà en base et l'utilisateur le voit, un
    magasin qui résiste doit être signalé, pas faire échouer l'action », paraît
    raisonnable et il est faux, parce qu'il
    contredit l'invariant du projet : `.voices.json` est AUTORITATIF et SQLite
    est dérivé. Un nom écrit en base mais pas dans le magasin est un nom que la
    prochaine réindexation efface — l'utilisateur a donc vu un succès qui
    n'existait pas, et il ne l'apprend qu'en constatant la disparition.

    Mieux vaut une erreur qu'il peut réessayer qu'une confirmation qu'il ne peut
    pas croire. L'écriture autoritative passe donc en premier, et son échec
    empêche la mutation dérivée.
    """
    from core import docid as d, voice_registry

    output = isolated_stores["output_dir"]
    vid = _voix_avec_membre(output, monkeypatch, d.nouveau())

    def refuse(*a, **k):
        raise OSError("magasin verrouillé")

    # `_ecrire` plutôt que `nommer` : une décision est publiée en UNE
    # écriture, et c'est celle-là qu'il faut casser.
    monkeypatch.setattr(voice_registry, "_ecrire", refuse)
    with pytest.raises(OSError):
        voiceprint.rename(vid, "Ari")

    conn = voiceprint._conn()
    try:
        assert conn.execute(
            "SELECT label FROM voices WHERE id = ?", (vid,)
        ).fetchone()[0] is None, "la base ne doit pas porter un nom que le magasin a refusé"
    finally:
        conn.close()
