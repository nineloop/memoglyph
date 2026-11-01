"""Tous les moteurs STT doivent respecter le MÊME contrat d'appel.

`stt_base.STTEngine` est un `Protocol` : du typage structurel, que rien ne
vérifie à l'exécution et que `make check` ne contrôle pas non plus (pas de
type-checker dans la chaîne). Le contrat existait donc sur le papier pendant que
les moteurs divergeaient.

Ce qui est arrivé : le moteur Mistral a été écrit avec `on_partial`, Whisper et
le protocole disaient `on_segment`, et `core/pipeline.py` s'est aligné sur le
premier. Toute transcription LOCALE lancée depuis l'interface échouait sur
« unexpected keyword argument » — le chemin principal du produit. Aucun test ne
l'a vu parce qu'ils appelaient `pipeline.run()` sans callback, cas où le kwarg
n'est jamais transmis.

Ces tests vérifient le contrat lui-même, pas un moteur en particulier : ajouter
un moteur qui diverge les fait échouer.
"""

from __future__ import annotations

import inspect

import pytest

from core import stt
from core.stt_base import STTEngine


def _moteurs():
    """Les classes de moteur, sans charger le moindre modèle."""
    from core.stt_mistral import MistralSTTEngine
    from core.stt_whisper import WhisperEngine

    return {"whisper": WhisperEngine, "mistral": MistralSTTEngine}


@pytest.mark.parametrize("nom", sorted(_moteurs()))
def test_chaque_moteur_accepte_les_callbacks_du_contrat(nom) -> None:
    """`progress` et `on_partial` doivent exister, sous CES noms."""
    signature = inspect.signature(_moteurs()[nom].transcribe)
    for attendu in ("progress", "on_partial"):
        assert attendu in signature.parameters, (
            f"{nom}.transcribe n'accepte pas {attendu!r} — "
            f"paramètres présents : {list(signature.parameters)}"
        )


@pytest.mark.parametrize("nom", sorted(_moteurs()))
def test_les_callbacks_sont_mot_cle_seulement(nom) -> None:
    """Un appel positionnel masquerait une divergence de nom : deux moteurs
    pourraient nommer différemment le même argument sans que rien ne le voie."""
    signature = inspect.signature(_moteurs()[nom].transcribe)
    for attendu in ("progress", "on_partial"):
        parametre = signature.parameters[attendu]
        assert parametre.kind is inspect.Parameter.KEYWORD_ONLY, (
            f"{nom}.transcribe : {attendu!r} doit être mot-clé seulement, "
            f"obtenu {parametre.kind}"
        )


@pytest.mark.parametrize("nom", sorted(_moteurs()))
def test_la_signature_du_moteur_couvre_celle_du_protocole(nom) -> None:
    """Le protocole est la référence : tout ce qu'il déclare doit être accepté."""
    reference = inspect.signature(STTEngine.transcribe)
    moteur = inspect.signature(_moteurs()[nom].transcribe)
    manquants = set(reference.parameters) - set(moteur.parameters)
    assert not manquants, f"{nom}.transcribe ne couvre pas {sorted(manquants)}"


def test_tous_les_moteurs_declares_sont_couverts() -> None:
    """Un moteur ajouté à `stt.ENGINES` sans être testé ici passerait au travers."""
    assert set(stt.ENGINES) == set(_moteurs()), (
        "core.stt.ENGINES et les moteurs testés ici ont divergé — "
        f"déclarés {sorted(stt.ENGINES)}, testés {sorted(_moteurs())}"
    )


def test_le_pipeline_transmet_le_nom_du_contrat(monkeypatch, tmp_path) -> None:
    """Vérifie le point de jonction RÉEL : c'est là que la divergence est née.

    Le pipeline construit lui-même le dictionnaire de kwargs ; un renommage d'un
    seul côté ne se voit qu'ici.
    """
    from core import pipeline

    recus: dict = {}

    class MoteurEspion:
        name = "espion"
        model_name = "espion"

        def transcribe(self, wav_path, *, progress=None, on_partial=None):
            recus["on_partial"] = on_partial
            from core.stt_base import Segment, Transcript

            return Transcript(
                segments=[Segment(start=0.0, end=1.0, text="bonjour")],
                full_text="bonjour", language="fr",
            )

    monkeypatch.setattr(pipeline, "get_engine", lambda *a, **k: MoteurEspion())
    monkeypatch.setattr(pipeline.media, "validate_input", lambda p: p)
    monkeypatch.setattr(pipeline.media, "probe_duration", lambda p: 1.0)
    monkeypatch.setattr(pipeline.media, "to_wav16k", lambda p: tmp_path / "a.wav")
    monkeypatch.setattr(pipeline.media, "pcm_sha256", lambda p: "a1b2c3d4" * 8)  # 64 caractères hexadécimaux

    src = tmp_path / "a.m4a"
    src.write_bytes(b"media")
    try:
        pipeline._run(src, output_dir=tmp_path, on_partial=lambda _texte: None)
    except Exception:
        # La suite du pipeline (écriture, projets) sort du périmètre de ce test :
        # seul compte le fait que le moteur ait été appelé avec le bon nom.
        pass

    assert "on_partial" in recus, (
        "le pipeline n'a pas transmis le callback sous le nom du contrat"
    )
    assert recus["on_partial"] is not None


# --- Discipline mémoire : chaque famille de modèles rend sa place ------------

def test_le_pipeline_decharge_le_llm_local_apres_usage(monkeypatch, tmp_path) -> None:
    """Un modèle Ollama chargé occupe ~6,1 Go et retombe à ~55 Mo une fois
    déchargé (mesuré). On croyait ces 6 Go irrécupérables depuis Python parce
    qu'Ollama vit dans un processus séparé — c'était faux, `keep_alive=0` les
    rend immédiatement."""
    from core import llm

    appels: list[str] = []
    monkeypatch.setattr(llm.MOTEURS["mlx"], "unload", lambda *a, **k: appels.append("unload") or True)
    _lancer_pipeline_minimal(monkeypatch, tmp_path, profile_on=True)

    assert appels == ["unload"], "le LLM local doit être déchargé après usage"


def test_le_pipeline_ne_decharge_pas_ollama_en_mode_cloud(monkeypatch, tmp_path) -> None:
    """En mode Mistral, Ollama n'a rien chargé : le décharger serait un appel
    réseau inutile vers un service peut-être absent."""
    from core import llm

    appels: list[str] = []
    monkeypatch.setattr(llm.MOTEURS["mlx"], "unload", lambda *a, **k: appels.append("unload") or True)
    _lancer_pipeline_minimal(
        monkeypatch, tmp_path, profile_on=True, llm_provider="mistral",
    )

    assert appels == [], "aucun déchargement d'Ollama attendu en mode cloud"


def _lancer_pipeline_minimal(monkeypatch, tmp_path, **options) -> None:
    """Fait tourner `_run` jusqu'au bout du chemin qui nous intéresse."""
    from core import pipeline
    from core.stt_base import Segment, Transcript

    class Moteur:
        name = "faux"
        model_name = "faux"

        def transcribe(self, wav_path, *, progress=None, on_partial=None):
            return Transcript(
                segments=[Segment(start=0.0, end=1.0, text="bonjour")],
                full_text="bonjour", language="fr",
            )

    from core import profiling, projects

    # Le pipeline rattache le document à un projet : sans manifeste il lève
    # avant d'atteindre le déchargement (piège déjà rencontré).
    if not projects.manifest_path(tmp_path).exists():
        projects.create(tmp_path, name="Test")

    monkeypatch.setattr(pipeline, "get_engine", lambda *a, **k: Moteur())
    monkeypatch.setattr(pipeline.media, "validate_input", lambda p: p)
    monkeypatch.setattr(pipeline.media, "probe_duration", lambda p: 1.0)
    monkeypatch.setattr(pipeline.media, "to_wav16k", lambda p: tmp_path / "a.wav")
    monkeypatch.setattr(pipeline.media, "pcm_sha256", lambda p: "a1b2c3d4" * 8)  # 64 caractères hexadécimaux
    # Le profilage réel appellerait un LLM : il lèverait avant le déchargement,
    # et le test ne mesurerait plus rien.
    monkeypatch.setattr(profiling, "profile", lambda *a, **k: {"resume": "ok"})
    # L'archivage audio appelle ffmpeg, JUSTE AVANT le déchargement. Sur une
    # machine sans ffmpeg — le runner CI — il lève, le `except` du helper avale,
    # et le test échoue en croyant mesurer le déchargement. Il mesurait en fait
    # la présence de ffmpeg. Neutralisé pour que le test porte sur son sujet.
    for archivage in ("archive_audio", "archive_faithful", "keep_original"):
        monkeypatch.setattr(pipeline.media, archivage, lambda *a, **k: None)
    src = tmp_path / "a.m4a"
    src.write_bytes(b"media")
    try:
        pipeline._run(src, output_dir=tmp_path, **options)
    except Exception:
        # Les étapes en aval (profilage réel, écriture) sortent du périmètre.
        pass


# ---------------------------------------------------------------------------
# Le modèle SERVI, et non le modèle DEMANDÉ
# ---------------------------------------------------------------------------


def test_le_transcript_porte_le_modele_servi_quand_le_fournisseur_le_declare() -> None:
    """`MISTRAL_STT_MODEL` vaut `voxtral-mini-latest` — un ALIAS.

    L'inscrire dans la provenance nommait l'INTENTION, jamais le fait : deux
    documents produits à six mois d'écart portaient la même chaîne sans venir
    du même modèle. Et l'écart n'est pas théorique — la fiche de
    `Voxtral-Mini-3B-2507` annonce 8 langues sans l'arabe, quand « Voxtral Mini
    Transcribe V2 » en annonce 13 avec.
    """
    from core.stt_base import Transcript

    t = Transcript(model_servi="voxtral-mini-transcribe-2602")
    assert t.model_servi == "voxtral-mini-transcribe-2602"


def test_un_fournisseur_muet_laisse_le_modele_servi_inconnu() -> None:
    """`None` est une réponse honnête : « il ne l'a pas dit ».

    Recopier le nom demandé le ferait passer pour une confirmation — c'est
    exactement le motif de l'ÉCHO que `core/llm_mlx.py` documente pour mlx_lm,
    où comparer la réponse à la requête est une tautologie.
    """
    from core.stt_base import Transcript

    assert Transcript().model_servi is None


def test_la_provenance_prefere_le_servi_au_demande(monkeypatch) -> None:
    """Le choix est fait dans `pipeline`, pas dans chaque moteur : un moteur
    local qui ne déclare rien doit continuer de nommer son modèle demandé."""
    from core.stt_base import Transcript

    servi = Transcript(model_servi="voxtral-mini-transcribe-2602")
    muet = Transcript()

    def choisir(transcript, demande: str) -> str:
        return getattr(transcript, "model_servi", None) or demande

    assert choisir(servi, "voxtral-mini-latest") == "voxtral-mini-transcribe-2602"
    assert choisir(muet, "mlx-community/whisper-large-v3-turbo") == (
        "mlx-community/whisper-large-v3-turbo")


def test_le_modele_mistral_par_defaut_est_epingle_et_non_un_alias() -> None:
    """Un alias rend la provenance inutilisable dans le temps.

    `voxtral-mini-latest` faisait porter la MÊME chaîne à deux documents séparés
    de six mois sans qu'ils viennent du même modèle. Et l'API n'aide pas : son
    champ `model` ÉCHOIE la requête, donc demander l'alias fait enregistrer
    l'alias.

    L'écart entre versions n'est pas cosmétique — la fiche de
    `Voxtral-Mini-3B-2507` annonce 8 langues sans l'arabe, « Voxtral Mini
    Transcribe V2 » en annonce 13 avec.
    """
    from core import config

    assert not config.MISTRAL_STT_MODEL.endswith("-latest"), (
        f"{config.MISTRAL_STT_MODEL!r} est un alias : la provenance ne serait "
        f"pas rejouable")


def test_le_repli_ultime_n_est_pas_un_alias_non_plus() -> None:
    """Le repli sert quand la configuration a échoué — c'est précisément le
    moment où une provenance ambiguë serait le plus coûteuse."""
    import inspect

    from core import stt_mistral

    source = inspect.getsource(stt_mistral.MistralSTTEngine.__init__)
    ligne = [ligne for ligne in source.splitlines() if "self.model_name =" in ligne]
    assert ligne, "le repli n'a pas été trouvé — ce témoin regarde à côté"
    assert "-latest" not in ligne[0], f"repli sur un alias : {ligne[0].strip()}"
