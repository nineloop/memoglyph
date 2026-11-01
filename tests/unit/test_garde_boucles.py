"""Garde anti-boucle de la transcription Whisper : détection, second décodage, repli.

Le cas d'origine : TS3003d (AMI), « 입 » répété 221 fois sans espace, trois fois, en
passe unique. Sans espace, le seuil par mots (8 mots, 60 %) ne le voyait pas.
"""
from __future__ import annotations

import pytest

from core import artifact_policy, artifacts
from core.stt_base import Segment, Transcript
from core.stt_whisper import redecoder_boucles

pytestmark = pytest.mark.unit

BOUCLE = "Just 12 minutes, but" + "입" * 221


def test_une_suite_sans_espace_est_une_boucle_au_seuil_et_pas_avant() -> None:
    assert artifacts.est_une_boucle(BOUCLE)
    assert artifacts.est_une_boucle("if" + "입" * artifacts.REPETITIONS_SUITE)
    assert not artifacts.est_une_boucle("if" + "입" * (artifacts.REPETITIONS_SUITE - 1))
    assert artifacts.motif_de_boucle(BOUCLE) == ("입", 221)


@pytest.mark.parametrize("texte", ["Ahhhhhhhhh", "hahahahahahahaha", "Mmmmmmmmh d'accord",
                                   "une phrase normale de dix mots, ici et là encore"])
def test_les_graphies_expressives_ne_sont_pas_des_boucles(texte) -> None:
    assert not artifacts.est_une_boucle(texte)


def test_le_repli_garde_la_parole_autour_et_laisse_sa_marque() -> None:
    replie = artifacts.replier_boucle("if" + "입" * 200 + " and then")
    assert replie == "if입입입 […200× « 입 » — boucle de répétition repliée] and then"
    # La boucle de mots garde sa forme d'avant.
    assert artifacts.replier_boucle("Let us start " + "hot " * 30).endswith(
        "[…30× « hot » — boucle de répétition repliée]")


def test_la_politique_consigne_la_suite_dans_le_front_matter() -> None:
    politique = artifact_policy.ScrubPolicy(id="artifacts.scrub", version=2)
    t = Transcript(segments=[Segment(0, 30, "Bonjour à tous."), Segment(782, 812, BOUCLE)])
    nettoye, evaluation = politique.apply(t)
    assert nettoye.segments[1].text.endswith("— boucle de répétition repliée]")
    assert "입" * 4 not in nettoye.full_text
    assert evaluation.front_matter()["repetition_loops"] == ["00:13:02 « 입 » ×221"]


def _decodeur(reponses: dict[tuple[float, float], list[Segment]], appels: list):
    def decoder(debut: float, fin: float) -> list[Segment]:
        appels.append((debut, fin))
        return reponses.get((debut, fin), [])
    return decoder


def test_une_boucle_est_re_decodee_une_fois_et_remplacee_si_le_second_passage_est_propre() -> None:
    appels: list = []
    propre = [Segment(782.0, 790.0, "Just 12 minutes, but we can do it."),
              Segment(790.0, 812.0, "Let's move on.")]
    segments = [Segment(0, 30, "Bonjour."), Segment(782.0, 812.0, BOUCLE), Segment(812, 830, "Merci.")]
    sortie, reparees = redecoder_boucles(segments, _decodeur({(782.0, 812.0): propre}, appels))
    assert appels == [(782.0, 812.0)], "une seule fois, sur la fenêtre du segment fautif"
    assert [s.text for s in sortie] == ["Bonjour.", *[s.text for s in propre], "Merci."]
    assert reparees == 1


def test_si_le_second_passage_boucle_encore_le_segment_reste_pour_etre_replie() -> None:
    appels: list = []
    toujours = [Segment(782.0, 812.0, "but" + "입" * 100)]
    segments = [Segment(782.0, 812.0, BOUCLE)]
    sortie, reparees = redecoder_boucles(segments, _decodeur({(782.0, 812.0): toujours}, appels))
    assert sortie == segments and reparees == 0 and len(appels) == 1
    # Vide aussi : rien n'est retiré en silence.
    sortie, _ = redecoder_boucles(segments, _decodeur({}, []))
    assert sortie == segments


def test_rien_n_est_re_decode_sans_boucle_ni_sur_une_fenetre_trop_courte() -> None:
    appels: list = []
    segments = [Segment(0, 30, "Bonjour à tous."), Segment(40.0, 40.5, "입" * 30)]
    sortie, reparees = redecoder_boucles(segments, _decodeur({}, appels))
    assert appels == [] and sortie == segments and reparees == 0
