"""Un document ne doit pas déclarer un projet et vivre dans un autre.

`project_id` n'était vérifié que syntaxiquement ;
SQLite, le RAG et les vues filtrent ensuite dessus. Un `.md` déplacé à la main,
ou une réunion dont le déplacement s'est interrompu au milieu, se retrouvait
physiquement dans A et visible dans B.
"""

from __future__ import annotations

from core import library, projects


def _ecrire(dossier, nom, project_id):
    from core import docid
    (dossier / nom).write_text(
        "---\n"
        f"id: {docid.nouveau()}\n"
        f"meeting_id: {docid.nouveau()}\n"
        f"project_id: {project_id}\n"
        "title: essai\n"
        "---\n\ncorps\n",
        encoding="utf-8",
    )


def test_un_document_egare_est_signale(isolated_stores) -> None:
    racine = isolated_stores["output_dir"]
    projects.ensure_inbox(racine)
    vrai = projects.load(racine).id

    autre = racine / "Autre projet"
    autre.mkdir()
    projects.create(autre, name="Autre projet")
    id_autre = projects.load(autre).id
    assert id_autre != vrai

    # Le fichier vit dans « Autre projet » mais se déclare dans la boîte d'entrée.
    _ecrire(autre, "egare.md", vrai)

    diagnostic = library.audit_corpus()
    egares = diagnostic["issues"]["misplaced_documents"]

    assert len(egares) == 1
    assert egares[0]["declared_project_id"] == vrai
    assert egares[0]["actual_project_id"] == id_autre


def test_un_document_bien_place_ne_declenche_rien(isolated_stores) -> None:
    racine = isolated_stores["output_dir"]
    projects.ensure_inbox(racine)
    _ecrire(racine, "correct.md", projects.load(racine).id)

    assert library.audit_corpus()["issues"]["misplaced_documents"] == []


def test_un_dossier_sans_manifeste_ne_produit_pas_de_faux_positif(isolated_stores) -> None:
    """Sans manifeste, l'appartenance est INDÉCIDABLE — et affirmer serait pire
    que se taire. C'est le cas d'un sous-dossier libre créé à la main."""
    racine = isolated_stores["output_dir"]
    projects.ensure_inbox(racine)
    libre = racine / "vrac"
    libre.mkdir()
    _ecrire(libre, "sans-manifeste.md", projects.load(racine).id)

    assert library.audit_corpus()["issues"]["misplaced_documents"] == []


def test_un_deplacement_interrompu_ne_laisse_pas_le_corpus_a_moitie(
        isolated_stores, monkeypatch) -> None:
    """Preuve par injection d'échec au DEUXIÈME fichier.

    Sans annulation, le premier membre était déplacé et le second non — les
    deux portant déjà le nouveau `project_id`. Le membre resté sur place
    déclarait un projet et vivait dans un autre.
    """
    from pathlib import Path

    from core import docid

    racine = isolated_stores["output_dir"]
    projects.ensure_inbox(racine)
    source_id = projects.load(racine).id
    cible = racine / "Cible"
    cible.mkdir()
    projects.create(cible, name="Cible")

    reunion = docid.nouveau()
    for nom in ("a.md", "b.md"):
        (racine / nom).write_text(
            f"---\nid: {docid.nouveau()}\nmeeting_id: {reunion}\n"
            f"project_id: {source_id}\ntitle: {nom}\n---\n\ncorps de {nom}\n",
            encoding="utf-8")
    library.reindex()
    avant = {n: (racine / n).read_text(encoding="utf-8") for n in ("a.md", "b.md")}

    vrai_rename = Path.rename
    compte = {"n": 0}

    def rename_capricieux(self, cible_chemin):
        compte["n"] += 1
        if compte["n"] == 2:
            raise OSError("disque plein (simulé)")
        return vrai_rename(self, cible_chemin)

    monkeypatch.setattr(Path, "rename", rename_capricieux)
    try:
        library.move_to_project_id("a.md", projects.load(cible).id)
    except OSError:
        pass
    monkeypatch.setattr(Path, "rename", vrai_rename)

    # Ni déplacé, ni réécrit : l'état d'origine, intégralement.
    for nom in ("a.md", "b.md"):
        assert (racine / nom).exists(), f"{nom} n'est pas revenu à sa place"
        assert not (cible / nom).exists(), f"{nom} est resté dans la destination"
        assert (racine / nom).read_text(encoding="utf-8") == avant[nom]

    assert library.audit_corpus()["issues"]["misplaced_documents"] == []
