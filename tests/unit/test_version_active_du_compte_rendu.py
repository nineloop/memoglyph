"""Régénérer un compte rendu AJOUTE une version : la plus récente est le document actif, les autres restent dans « Versions ».

L'interface montre en premier `reports[0]` (« Document actif ») et liste tous les autres dans l'onglet Versions : ce test fige l'ordre
que le serveur donne, sans qu'aucun code ne choisisse « la bonne » version.
"""
from __future__ import annotations

import pytest

from core import library

pytestmark = pytest.mark.unit


def _document(nom: str, mtime: float, type_: str | None = None) -> dict:
    return {"name": nom, "id": f"{nom}-id", "meeting_id": "M1", "project_id": "P1", "title": "point-prix", "mtime": mtime,
            "type": type_, "project": "Équipe", "source": "point prix.wav", "date": "2026-11-05", "warnings": 0}


def test_apres_deux_regenerations_la_plus_recente_est_active_et_les_autres_restent_listees() -> None:
    items = [
        _document("transcript.md", 100.0),
        _document("transcript-cr.md", 200.0, "report"),          # la première rédaction
        _document("transcript-cr-1.md", 300.0, "report"),        # une première régénération
        _document("transcript-cr-2.md", 400.0, "report"),        # une seconde, avec un autre fournisseur
    ]

    reunion = library._meeting_from_items("M1", items)

    noms = [item["name"] for item in reunion["reports"]]
    assert noms[0] == "transcript-cr-2.md"                      # la plus récente est le document actif
    assert sorted(noms) == ["transcript-cr-1.md", "transcript-cr-2.md", "transcript-cr.md"]   # aucune version n'est perdue
    assert noms == sorted(noms, key=lambda nom: {"transcript-cr.md": 0, "transcript-cr-1.md": 1, "transcript-cr-2.md": 2}[nom], reverse=True)


def test_l_ordre_ne_depend_pas_de_l_ordre_d_arrivee_des_documents() -> None:
    items = [_document("a-cr.md", 300.0, "report"), _document("t.md", 100.0), _document("b-cr.md", 200.0, "report")]
    assert [i["name"] for i in library._meeting_from_items("M1", items)["reports"]] == ["a-cr.md", "b-cr.md"]
    assert [i["name"] for i in library._meeting_from_items("M1", list(reversed(items)))["reports"]] == ["a-cr.md", "b-cr.md"]
