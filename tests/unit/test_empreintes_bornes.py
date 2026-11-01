"""Empreintes extraites sur des bornes fournies — le chemin cloud.

Le modèle n'est pas chargé : ces tests portent sur la logique d'agrégation,
qui est là où les défauts se sont produits. La justesse des vecteurs eux-mêmes
(un locuteur ré-embarqué depuis les bornes cloud doit retrouver son empreinte
locale) se vérifie sur un vrai média, pas ici.
"""

from __future__ import annotations

import numpy as np
import pytest

from core import diarization


class _FauxInference:
    """Rend un vecteur par intervalle, selon une table fournie."""

    def __init__(self, table):
        self.table = table
        self.vus = []

    def crop(self, chemin, segment):
        self.vus.append((segment.start, segment.end))
        return np.asarray(self.table[(round(segment.start, 3), round(segment.end, 3))])


@pytest.fixture
def faux(monkeypatch):
    def poser(table):
        inf = _FauxInference(table)
        monkeypatch.setattr(diarization, "_modele_empreinte", lambda *a, **k: inf)
        return inf
    return poser


def test_un_seul_tour_non_fini_ne_contamine_pas_la_moyenne(faux, capsys) -> None:
    """Vécu sur le corpus : 1 crop NaN sur 102 rendait TOUT le locuteur NaN.

    `np.mean` propage un NaN à l'ensemble du vecteur, et le garde-fou d'alors
    testait `norme == 0` — or `NaN == 0` est faux. Le locuteur dominant
    recevait donc une voix neuve à chaque transcription.
    """
    faux({(0.0, 4.0): [1.0, 0.0], (4.0, 8.0): [1.0, 0.0],
          (8.0, 8.1): [float("nan"), float("nan")]})
    emb = diarization.empreintes_par_locuteur(
        "peu-importe.wav", {"Speaker 1": [(0.0, 4.0), (4.0, 8.0), (8.0, 8.1)]})

    assert "Speaker 1" in emb
    assert np.isfinite(emb["Speaker 1"]).all()
    assert emb["Speaker 1"] == pytest.approx([1.0, 0.0])
    assert "1/3 tour(s) non finis" in capsys.readouterr().out


def test_un_locuteur_entierement_non_fini_est_ecarte(faux) -> None:
    """Mieux vaut aucune empreinte qu'une empreinte de bruit : elle entrerait
    dans le regroupement et déplacerait des voix réelles."""
    faux({(0.0, 5.0): [float("nan"), 1.0]})
    emb = diarization.empreintes_par_locuteur(
        "peu-importe.wav", {"Speaker 1": [(0.0, 5.0)]})

    assert emb == {}


def test_un_locuteur_trop_bref_n_a_pas_d_empreinte(faux, capsys) -> None:
    inf = faux({(0.0, 1.0): [1.0, 0.0]})
    emb = diarization.empreintes_par_locuteur(
        "peu-importe.wav", {"Bref": [(0.0, 1.0)]}, duree_min=3.0)

    assert emb == {}
    assert inf.vus == [], "aucun crop ne devrait être tenté"
    assert "moins de 3 s de parole" in capsys.readouterr().out


def test_le_vecteur_rendu_est_normalise(faux) -> None:
    """Les empreintes stockées sont comparées au cosinus : une norme libre
    ferait dépendre la distance de la longueur du tour."""
    faux({(0.0, 5.0): [3.0, 4.0]})
    emb = diarization.empreintes_par_locuteur(
        "peu-importe.wav", {"Speaker 1": [(0.0, 5.0)]})

    assert float(np.linalg.norm(emb["Speaker 1"])) == pytest.approx(1.0)


def test_un_intervalle_inverse_est_ignore(faux) -> None:
    faux({(0.0, 5.0): [1.0, 0.0]})
    emb = diarization.empreintes_par_locuteur(
        "peu-importe.wav", {"Speaker 1": [(0.0, 5.0), (9.0, 2.0)]})

    assert emb["Speaker 1"] == pytest.approx([1.0, 0.0])


def test_des_vecteurs_qui_s_annulent_ne_donnent_pas_d_empreinte(faux) -> None:
    """Norme nulle : le seul cas que le filtre par crop ne peut pas attraper."""
    faux({(0.0, 5.0): [1.0, 0.0], (5.0, 10.0): [-1.0, 0.0]})
    emb = diarization.empreintes_par_locuteur(
        "peu-importe.wav", {"Speaker 1": [(0.0, 5.0), (5.0, 10.0)]})

    assert emb == {}
