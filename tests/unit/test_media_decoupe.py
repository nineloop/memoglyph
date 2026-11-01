"""Découpe d'audio : borner la durée d'un essai, et extraire les
passages saillants d'un compte-rendu.

Les deux usages partagent la primitive « couper entre deux instants ». Le second
est exigeant : chaque affirmation d'un CR porte l'horodatage de sa preuve
(`[source 00:07:44]`), et un extrait décalé de quelques secondes rate la
citation — sans que personne ne s'en aperçoive à l'écoute.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from core import media

# Ces tests fabriquent et découpent de VRAIS fichiers audio : sans ffmpeg ils
# n'ont rien à éprouver. Se sauter plutôt qu'échouer, comme le fait déjà
# `test_faithful_archive.py` — un CI sans ffmpeg doit signaler une couverture
# réduite, pas une régression.
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg absent")


def _silence(chemin, secondes: float) -> None:
    """Fabrique un vrai fichier audio, pour éprouver ffmpeg et pas un mock."""
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i",
         "anullsrc=r=16000:cl=mono", "-t", str(secondes), str(chemin)],
        capture_output=True, check=True,
    )


# --- Lecture des horodatages ------------------------------------------------

@pytest.mark.parametrize("valeur,attendu", [
    ("00:00:00", 0.0),
    ("00:07:44", 464.0),
    ("01:00:00", 3600.0),
    ("7:44", 464.0),          # forme courte, sans les heures
    ("[00:02:30]", 150.0),    # crochets du .md
])
def test_les_horodatages_connus_sont_lus(valeur, attendu) -> None:
    assert media.secondes_depuis_horodatage(valeur) == attendu


@pytest.mark.parametrize("valeur", ["", "abc", "00:00:00:00", "12", "00:99:00", "a:b"])
def test_un_horodatage_douteux_est_REFUSE_pas_devine(valeur) -> None:
    """Un horodatage mal lu produirait un extrait silencieusement décalé. Sur un
    collage de trente extraits, personne ne remonterait à la cause."""
    with pytest.raises(ValueError):
        media.secondes_depuis_horodatage(valeur)


# --- Extraction d'un intervalle ---------------------------------------------

def test_un_extrait_a_la_duree_demandee(tmp_path) -> None:
    source = tmp_path / "source.m4a"
    _silence(source, 30)

    sortie = media.extraire(source, 10.0, 18.0, tmp_path / "extrait.m4a")

    assert sortie.exists()
    obtenue = media.probe_duration(sortie)
    assert abs(obtenue - 8.0) < 0.5, f"8 s attendues, {obtenue:.2f} s obtenues"


def test_un_intervalle_vide_ou_inverse_est_refuse(tmp_path) -> None:
    source = tmp_path / "source.m4a"
    _silence(source, 10)
    for debut, fin in [(5.0, 5.0), (8.0, 3.0)]:
        with pytest.raises(ValueError, match="Intervalle"):
            media.extraire(source, debut, fin, tmp_path / "x.m4a")


# --- Découpe en tranches ----------------------------------------------------

def test_une_source_plus_courte_que_la_borne_donne_une_seule_tranche(tmp_path) -> None:
    source = tmp_path / "court.m4a"
    _silence(source, 12)

    tranches = media.decouper(source, tmp_path / "out", duree_max=600.0)

    assert len(tranches) == 1
    assert abs(media.probe_duration(tranches[0]) - 12.0) < 0.5


def test_la_decoupe_couvre_toute_la_duree(tmp_path) -> None:
    """Aucune seconde ne doit être perdue : une phrase tombée dans un trou
    n'existerait plus pour la recherche."""
    source = tmp_path / "source.m4a"
    _silence(source, 25)

    tranches = media.decouper(source, tmp_path / "out", duree_max=10.0)

    total = sum(media.probe_duration(t) for t in tranches)
    assert len(tranches) == 3
    assert abs(total - 25.0) < 1.0, f"25 s attendues au total, {total:.1f} s obtenues"


def test_une_tranche_residuelle_minuscule_est_absorbee(tmp_path) -> None:
    """Sans cette règle, une source de 20,3 s produirait une tranche de 0,3 s —
    un fichier presque vide, inutile et bruyant dans les résultats."""
    source = tmp_path / "source.m4a"
    _silence(source, 20.4)

    tranches = media.decouper(source, tmp_path / "out", duree_max=10.0)

    assert len(tranches) == 2, [media.probe_duration(t) for t in tranches]


def test_le_recouvrement_allonge_le_total_ce_n_est_PAS_gratuit(tmp_path) -> None:
    """Un recouvrement produit autant d'audio en plus, donc de temps de
    transcription et de vecteurs. Le présenter comme gratuit ferait décider sur
    une prémisse fausse."""
    source = tmp_path / "source.m4a"
    _silence(source, 30)

    sans = media.decouper(source, tmp_path / "sans", duree_max=10.0)
    avec = media.decouper(source, tmp_path / "avec", duree_max=10.0, recouvrement=2.0)

    total_sans = sum(media.probe_duration(t) for t in sans)
    total_avec = sum(media.probe_duration(t) for t in avec)
    assert total_avec > total_sans, "le recouvrement doit se payer, et se voir"


@pytest.mark.parametrize("duree_max,recouvrement", [(0, 0), (-5, 0), (10, -1), (10, 10)])
def test_des_bornes_incoherentes_sont_refusees(tmp_path, duree_max, recouvrement) -> None:
    """Un recouvrement aussi long qu'une tranche ne progresserait jamais."""
    source = tmp_path / "source.m4a"
    _silence(source, 5)
    with pytest.raises(ValueError):
        media.decouper(source, tmp_path / "out",
                       duree_max=duree_max, recouvrement=recouvrement)


# --- Transcription Voxtral découpée : les vraies tranches -------------------

def test_voxtral_envoie_de_vraies_tranches_aux_bonnes_durees(monkeypatch, tmp_path) -> None:
    """Un WAV synthétique de 25 s, seuil abaissé à 10 s par la configuration,
    recouvrement 2 s : trois tranches de 10, 10 et 9 s partent, au format d'envoi.
    `requests.post` est remplacé : rien ne quitte la machine."""
    import sys

    from core import config, stt_mistral

    source = tmp_path / "reunion.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
         "-t", "25", "-ac", "1", str(source)],
        capture_output=True, check=True,
    )
    monkeypatch.setitem(sys.modules, "mistralai", None)
    monkeypatch.setattr(stt_mistral.MistralSTTEngine, "_record_usage", lambda *a, **k: None)
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(config, "MISTRAL_STT_TRANCHE_S", 10.0)
    monkeypatch.setattr(config, "MISTRAL_STT_RECOUVREMENT_S", 2.0)
    envois: list[tuple[str, float]] = []
    chemins: list[str] = []

    class _Reponse:
        ok, status_code, text, headers = True, 200, "", {}

        def json(self) -> dict:
            return {"text": "bip", "segments": [{"start": 1.0, "end": 2.0, "text": "bip"}]}

    def _post(*_a, **kwargs):
        chemin = kwargs["files"]["file"].name
        chemins.append(chemin)
        envois.append((chemin.rsplit(".", 1)[-1], media.probe_duration(chemin)))
        return _Reponse()

    monkeypatch.setattr("requests.post", _post)
    attentes: list[float] = []
    moteur = stt_mistral.MistralSTTEngine(
        api_key="factice", rythme=stt_mistral.RythmeAudio(3600, dormir=attentes.append))

    assert stt_mistral._duree_audio(source) == pytest.approx(25.0)
    t = moteur.transcribe(source)

    assert [ext for ext, _ in envois] == ["m4a", "m4a", "m4a"]
    for (_, obtenue), attendue in zip(envois, (10.0, 10.0, 9.0)):
        assert abs(obtenue - attendue) < 0.3, f"{attendue} s attendues, {obtenue:.2f} s"
    assert [(s.start, s.end) for s in t.segments] == [(1.0, 2.0), (9.0, 10.0), (17.0, 18.0)]
    assert attentes == []
    # Ancre : les tranches vivaient bien dans le dossier balayé, qui est vide ensuite.
    assert len(envois) == 3 and all(str(config.CACHE_DIR) in c for c in chemins)
    assert list(config.CACHE_DIR.iterdir()) == []
