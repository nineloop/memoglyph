"""La revérification du cœur traverse les absorbeurs réels.

Un document devenu « ultra-confidentiel » (cloud autorisé) PENDANT un travail arrête l'appel suivant d'un modèle gratuit. Les chemins de la chaîne qui
absorbent une panne de moteur (tranche perdue, classifieur en repli) ne doivent pas absorber ce refus : il traverse (`raise` nu),
rien n'est compté comme tranche perdue, et rien n'est parti vers le service. Chaque test appelle le VRAI absorbeur avec le VRAI
moteur nommé sur un transport simulé qui compte les requêtes.
"""
from __future__ import annotations

import importlib

import pytest

from core import sensitivity
from tests.unit.test_llm_nomme import CLE, Transport, environnement_propre  # noqa: F401 — fixture autouse réutilisée

pytestmark = pytest.mark.unit

GRATUIT = "auteur/un-modele:free"


@pytest.fixture
def gratuit_devenu_ultra(monkeypatch):
    nomme = importlib.import_module("core.llm_nomme")
    monkeypatch.setenv(nomme.PROFILS["openrouter"].variable_cle, CLE)
    monkeypatch.setenv(f"MEMOGLYPH_{nomme.PROFILS['openrouter'].variable_modele}", GRATUIT)
    t = Transport()
    monkeypatch.setattr(nomme, "client_sans_fuite", t.fabrique)
    monkeypatch.setattr(sensitivity, "_resolveur_sensibilite", lambda document_id: "ultra_confidential")
    monkeypatch.setattr(sensitivity, "autorise", lambda proprietaire: True)       # le cloud est autorisé : c'est la règle des gratuits qui refuse
    return t


def _envoi():
    return sensitivity.Envoi(sensibilite="standard", proprietaire="alice", document_id="doc-1")


def test_l_extraction_atomique_ne_compte_pas_le_refus_comme_tranche_perdue(gratuit_devenu_ultra) -> None:
    from core.experimental import stages
    diag: dict = {}
    with pytest.raises(sensitivity.ModeleGratuitRefuse):
        stages.extract_atomic(["premier extrait"], provider="openrouter", seed=1, diag=diag, envoi=_envoi())
    assert "stage1_extract" not in diag, "rien n'a été compté : l'étape ne s'est pas terminée en dégradé"
    assert gratuit_devenu_ultra.requetes == []


def test_le_map_reduce_ne_compte_pas_le_refus_comme_tranche_perdue(gratuit_devenu_ultra) -> None:
    from core import report
    with pytest.raises(sensitivity.ModeleGratuitRefuse):
        report.map_summarize_chunks(["premier extrait", "second extrait"], "fr", provider="openrouter", envoi=_envoi())
    assert gratuit_devenu_ultra.requetes == []


def test_le_classifieur_de_genre_ne_replie_pas_sur_le_gabarit_generique(gratuit_devenu_ultra) -> None:
    """Un refus n'est pas une panne à absorber : le gabarit sobre laisserait croire que le texte pouvait partir."""
    from core import report
    with pytest.raises(sensitivity.ModeleGratuitRefuse):
        report.classify_genre("[00:00:01] Bonjour tout le monde, on commence.", "fr", provider="openrouter", envoi=_envoi())
    assert gratuit_devenu_ultra.requetes == []


def test_le_profilage_leve_le_refus_et_n_envoie_rien(gratuit_devenu_ultra) -> None:
    """La levée est dans `profiling` ; c'est `pipeline.run` qui la transforme en avertissement (test de bout en bout à part)."""
    from core import profiling
    from core.stt_base import Segment, Transcript
    transcript = Transcript(segments=[Segment(0.0, 2.0, "bonjour tout le monde", None)],
                            full_text="bonjour tout le monde", language="fr")
    with pytest.raises(sensitivity.ModeleGratuitRefuse):
        profiling.profile(transcript, diarized=False, provider="openrouter", envoi=_envoi())
    assert gratuit_devenu_ultra.requetes == []
