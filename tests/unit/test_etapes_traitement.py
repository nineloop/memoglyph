"""La progression « étape n sur N » se déduit des CODES de message, jamais du texte."""
import pytest

from api.jobs import plan_etapes
from core import etapes, messages

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("code, groupe", [
    ("step.audio.prepare", "transcription"), ("step.model.loading", "transcription"),
    ("step.transcription", "transcription"), ("step.transcription.whisper", "transcription"),
    ("step.transcription.done", "transcription"), ("step.upload.mistral", "transcription"),
    ("step.segments.format", "transcription"),
    ("step.diarization", "locuteurs"), ("step.diarization.analyzing", "locuteurs"),
    ("step.voiceprints.after_native_diarization", "locuteurs"),
    ("step.profiling", "profil"), ("step.profiling.running", "profil"), ("step.profiling.warmup", "profil"),
    ("step.report", "extraction"), ("step.report.chunks", "extraction"),
    ("step.report.classify", "extraction"), ("step.report.extract", "extraction"),
    ("step.report.organize", "organisation"), ("step.report.write", "redaction"),
    ("step.markdown", "ecriture"), ("step.done", "ecriture"),
])
def test_chaque_code_d_etape_appartient_a_un_groupe(code, groupe):
    assert etapes.groupe_etape(code) == groupe


def test_hors_table_le_groupe_est_nul():
    assert etapes.groupe_etape("code.inconnu") is None
    assert etapes.groupe_etape(None) is None


def test_le_total_varie_avec_les_options():
    assert plan_etapes(diarize=False, profile=False, report_on=False) == ["transcription", "ecriture"]
    assert plan_etapes(diarize=True, profile=False, report_on=False) == [
        "transcription", "locuteurs", "ecriture"]
    complet = plan_etapes(diarize=True, profile=True, report_on=True)
    assert complet == ["transcription", "locuteurs", "profil", "extraction", "organisation",
                       "redaction", "ecriture"]
    assert len(plan_etapes(diarize=False, profile=True, report_on=True)) == 6


def test_les_codes_emis_par_le_catalogue_sont_tous_classes_ou_deliberement_neutres():
    """Un code ajouté au catalogue sans groupe passerait inaperçu : on le liste."""
    emis = {m.code for m in (
        messages.preparation_audio(), messages.audio_pret(), messages.diarisation(),
        messages.profilage("m"), messages.compte_rendu(), messages.compte_rendu_extraction(),
        messages.compte_rendu_organisation(), messages.compte_rendu_redaction(),
        messages.ecriture_markdown(), messages.termine("x"), messages.transcription("w"),
    )}
    assert all(etapes.groupe_etape(c) for c in emis), emis
