"""Le code de message d'un traitement en échec (chantier 003, finition) : jamais le texte brut à l'écran."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from core import erreurs, llm, sensitivity
from core.experimental.chunking import TranscriptNonCitable
from core.experimental.pipeline import ExtractionVide
from core.llm_base import LLMError, LLMMalformedOutputError

RACINE = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("exc,code", [
    (LLMError("service indisponible"), "step.error.modelUnavailable"),
    (LLMMalformedOutputError("JSON invalide"), "step.error.modelUnavailable"),
    (ExtractionVide(2, 2), "step.error.emptyExtraction"),
    (TranscriptNonCitable("aucun horodatage"), "step.error.notCitable"),
    (sensitivity.EnvoiRefuse("refusé"), "step.error.sendRefused"),
    (RuntimeError("boom"), "step.error.unexpected"),
    (KeyError("x"), "step.error.unexpected"),
])
def test_chaque_cause_a_son_code(exc, code) -> None:
    assert erreurs.code_de(exc) == (code, {})


def test_le_refus_des_gratuits_a_son_code_et_non_celui_de_l_envoi_refuse() -> None:
    """Chantier 006 : la recherche par la hiérarchie trouve le nom de la sous-classe AVANT celui de sa famille (`EnvoiRefuse`)."""
    assert erreurs.code_de(sensitivity.ModeleGratuitRefuse())[0] == "step.error.freeModelRestricted"


def test_la_limite_de_demandes_a_son_code_et_non_celui_de_la_panne_generale() -> None:
    from core.llm_nomme import LimiteDeDemandes
    assert erreurs.code_de(LimiteDeDemandes("Quota atteint"))[0] == "step.error.rateLimited"


def test_ancre_une_panne_ordinaire_d_un_fournisseur_nomme_garde_le_code_general() -> None:
    from core.llm_nomme import NommeLLMError
    assert erreurs.code_de(NommeLLMError("injoignable"))[0] == "step.error.modelUnavailable"


def test_une_sous_classe_garde_le_code_de_sa_famille() -> None:
    class PanneDeModele(LLMError):
        pass

    assert erreurs.code_de(PanneDeModele("x"))[0] == "step.error.modelUnavailable"


def test_la_table_nomme_de_vraies_classes() -> None:
    """La table se lit par nom de classe : un renommage la rendrait muette sans ce contrôle."""
    from core.llm_nomme import LimiteDeDemandes   # chantier 006 : deux entrées de plus, une par cause nouvelle
    vraies = {c.__name__ for c in (LLMError, LLMMalformedOutputError, ExtractionVide, TranscriptNonCitable,
                                   sensitivity.EnvoiRefuse, LimiteDeDemandes, sensitivity.ModeleGratuitRefuse)}
    assert set(erreurs.PAR_CLASSE) == vraies
    assert llm.LLMError is LLMError


def test_l_interface_a_chaque_code_dans_les_deux_langues() -> None:
    catalogue = (RACINE / "ui/src/lib/messages.ts").read_text(encoding="utf-8")
    for code in erreurs.CODES:
        assert len(re.findall(rf"^\s*'job\.{re.escape(code)}':", catalogue, flags=re.M)) == 2, code


def _attendre(manager, job):
    import time
    for _ in range(1000):
        if job.status in ("done", "error"):
            return
        time.sleep(0.005)
    raise AssertionError("traitement sans fin")


def test_un_traitement_en_echec_porte_le_code_de_sa_cause_et_garde_son_texte() -> None:
    from api.jobs import JobManager
    manager = JobManager()
    job = manager.create(owner="alice")

    def cible(_cb):
        raise ExtractionVide(3, 3)

    manager.run(job, cible)
    _attendre(manager, job)

    assert job.status == "error"
    assert job.error_code == "step.error.emptyExtraction"
    assert "Extraction vide" in job.error                     # le texte brut reste, pour les clients existants
    ligne = JobManager.entree_liste(job)
    assert ligne["erreur_code"] == "step.error.emptyExtraction" and ligne["erreur"] == job.error
    snapshot = JobManager.snapshot(job)
    assert snapshot["error_code"] == "step.error.emptyExtraction" and snapshot["error"] == job.error
    evenement = [e.payload for e in job.events if e.payload["type"] == "error"][0]
    assert evenement["code"] == "step.error.emptyExtraction" and evenement["error"] == job.error


def test_un_echec_inconnu_n_affiche_jamais_son_texte_brut_comme_phrase() -> None:
    from api.jobs import JobManager
    manager = JobManager()
    job = manager.create(owner="alice")
    manager.run(job, lambda _cb: (_ for _ in ()).throw(RuntimeError("Traceback: boum")))
    _attendre(manager, job)

    assert job.error_code == erreurs.INCONNU and job.error == "Traceback: boum"


def test_un_message_code_existant_garde_la_priorite() -> None:
    """« Trop d'attente » porte déjà son code (`core/messages.py`) : la table des causes ne le remplace pas."""
    from api.jobs import JobManager
    from core import resources
    manager = JobManager()
    job = manager.create(owner="alice")

    def cible(_cb):
        with resources.heavy(wait=0, label="x"), resources.heavy(wait=0, label="x"):
            raise AssertionError("imbriqué : sans effet")

    from core import messages
    manager.run(job, lambda _cb: (_ for _ in ()).throw(resources.ResourcesBusy(messages.trop_d_attente("x"))))
    _attendre(manager, job)

    assert job.error_code == "step.error.waitTooLong"
