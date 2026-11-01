"""Les mesures du test AMI à réponse connue, éprouvées sans modèle.

Le test `ml` (`tests/ml/test_ami_reponse_connue.py`) ne tourne pas en CI. Ses
fonctions de mesure, elles, sont pures : si l'une se trompait (un WER qui rend
toujours 0, un rattachement qui ne voit aucun locuteur), la porte ML rendrait
du vert sans rien mesurer. Elles sont donc vérifiées ici, sur des données
fabriquées.
"""

from __future__ import annotations

import zipfile

import pytest

from tests.ml.test_ami_reponse_connue import (
    MINIMUM_S, Mot, Segment, _role_ami, chevauchements, mots_de_reference,
    mots_normalises, rattachement, roles_profiles, segments, taux_erreur_mots,
)

pytestmark = pytest.mark.unit


def test_wer_nul_sur_un_texte_identique_et_juste_sur_une_substitution() -> None:
    assert taux_erreur_mots(["a", "b", "c", "d"], ["a", "b", "c", "d"]) == 0
    assert taux_erreur_mots(["a", "b", "c", "d"], ["a", "x", "c", "d"]) == 0.25
    assert taux_erreur_mots(["a", "b"], []) == 1.0
    assert taux_erreur_mots(["a"], ["a", "b", "c"]) == 2.0


def test_wer_refuse_une_reference_vide() -> None:
    with pytest.raises(ValueError):
        taux_erreur_mots([], ["a"])


def test_la_normalisation_retire_ponctuation_casse_et_hesitations() -> None:
    assert mots_normalises("Uh, Remote-control? Um... It's OK.") == [
        "remote", "control", "it's", "ok"]


def test_segments_et_roles_lus_dans_le_transcript() -> None:
    md = """---
speakers:
  Speaker 1: {role: project manager, talk_time: '06:24', share: 63%}
  Speaker 2: {role: marketing specialist, talk_time: '02:31', share: 25%}
---

**Speaker 1** [00:00:00] Okay, everybody ready?

**Speaker 2** [00:00:04] Yes.

**Speaker 1** [00:01:00] Let's start.
"""
    s = segments(md)
    assert [(x.debut, x.fin, x.locuteur) for x in s] == [
        (0, 4, "Speaker 1"), (4, 24, "Speaker 2"), (60, 80, "Speaker 1")]
    assert roles_profiles(md) == {"Speaker 1": "project manager",
                                  "Speaker 2": "marketing specialist"}


def test_rattachement_par_chevauchement_et_seuil_de_parole() -> None:
    mots = [Mot(0, 30, "A", "x"), Mot(30, 40, "B", "y"), Mot(40, 42, "C", "z")]
    segs = [Segment(0, 25, "Speaker 1", ""), Segment(25, 40, "Speaker 2", ""),
            Segment(40, 42, "Speaker 3", "")]
    table = chevauchements(segs, mots)
    assert table["Speaker 2"] == {"A": 5, "B": 10}
    r = rattachement(table)
    assert r["Speaker 1"] == ("A", 1.0, 25)
    assert r["Speaker 2"][0] == "B" and r["Speaker 2"][1] == pytest.approx(10 / 15)
    # Deux secondes de parole : sous MINIMUM_S, non jugé.
    assert 2 < MINIMUM_S and "Speaker 3" not in r


def test_les_roles_du_profil_sont_reconnus_en_anglais_et_en_francais() -> None:
    assert _role_ami("Project Manager") == "PM"
    assert _role_ami("cheffe de projet") == "PM"
    assert _role_ami("user interface designer") == "UI"
    assert _role_ami("designer industriel") == "ID"
    assert _role_ami("marketing specialist") == "ME"
    assert _role_ami("participant") is None


def test_la_reference_garde_les_mots_de_la_fenetre_sans_ponctuation(tmp_path) -> None:
    archive = tmp_path / "ami.zip"
    entete = '<?xml version="1.0"?><nite:root xmlns:nite="http://nite.sourceforge.net/">'
    with zipfile.ZipFile(archive, "w") as z:
        for p in "ABCD":
            z.writestr(f"words/R1.{p}.words.xml", entete + (
                f'<w starttime="5" endtime="6">{p}avant</w>'
                f'<w starttime="12" endtime="13">{p}dedans</w>'
                f'<w starttime="13" endtime="13" punc="true">.</w>'
                '<vocalsound starttime="14" endtime="15"/>'
                f'<w starttime="30" endtime="31">{p}apres</w></nite:root>'))
    mots = mots_de_reference(archive, "R1", 10, 10)
    assert [(m.debut, m.participant, m.texte) for m in mots] == [
        (2, p, f"{p}dedans") for p in "ABCD"]
