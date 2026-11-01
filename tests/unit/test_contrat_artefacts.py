"""Un moteur qui ne déclare rien doit ÉCHOUER, pas se taire.

## Ce qui est réparé ici

`core/pipeline.py` faisait `if hasattr(engine, "transcribe_with_artefacts")`.
Aucun moteur n'implémentait ce crochet : la branche était morte, et son `else`
fabriquait un `Rapport([], [], [])` **indistinguable d'un détecteur qui a
tourné sans rien trouver**. Des génériques « Sous-titrage Société Radio-Canada »
restaient ainsi dans les documents, et l'un d'eux était indexé, sans que rien ne le
signale.

Le contrat retenu (voir `core/artifact_policy.py`, docstring du module) : la
DÉCLARATION devient obligatoire à l'enregistrement du moteur ; `transcribe()`
ne change pas ; quatre états fermés sont publiés.

## Pourquoi ces tests-là, et pas un test de protocole

`STTEngine` est un `Protocol` : du typage structurel, que RIEN ne vérifie à
l'exécution et que la chaîne de contrôle ne type-check pas non plus. Un test
statique aurait donc affirmé un contrat sans l'éprouver. Ces tests exercent la
fabrique et le pipeline réels.

Les imports de `core.artifact_policy` sont DANS les fonctions, pas en tête de
fichier : cela permet de faire tomber les tests de comportement sur le code
d'avant (le module n'y existe pas) au lieu de casser la collecte entière, et
c'est ainsi que le rouge initial a été observé.

## Critère d'acceptation

Chacun doit TOMBER quand on débranche ce qu'il mesure — vérifié par mutation :
suppression du refus dans `stt.engine_spec` (le test décisif tombe),
puis suppression de l'appel à `apply_policy` dans `core/pipeline.py` (les tests
de publication tombent).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from core import docid, stt

pytestmark = pytest.mark.unit

CREDIT = "Sous-titrage Société Radio-Canada"
BOUCLE = "Let's start a " + "hot " * 30


@pytest.fixture(autouse=True)
def _cache_propre():
    """Le cache de `core/stt.py` est global : sans cette remise à zéro, un test
    hériterait du moteur d'un autre et mesurerait le voisin."""
    stt._cache.clear()
    yield
    stt._cache.clear()


# --------------------------------------------------------------------------
# 1. Le test DÉCISIF : politique absente = refus avant tout travail
# --------------------------------------------------------------------------

class _SpecSansPolitique:
    """Une entrée de registre mal déclarée, comme en produirait une intégration
    tierce distraite — ou un `git revert` malheureux."""

    artifact_policy = None

    def __init__(self) -> None:
        self.appels: list[str] = []

    def factory(self, **kwargs):
        self.appels.append("fabrique")
        raise AssertionError(
            "la fabrique a été appelée : un modèle aurait pu être chargé alors "
            "que le contrat était déjà rompu"
        )


def test_un_moteur_sans_politique_est_refuse_AVANT_la_fabrique(monkeypatch) -> None:
    """Exigence du contrat (`core/stt.py`, `_exiger_une_politique`).

    « Retirer la politique d'un moteur du registre et vérifier que
    `get_engine()` échoue avant tout appel à `transcribe`. » La fabrique est le
    point où le moteur est construit et où ses poids seraient chargés : si elle
    n'est pas touchée, rien n'a été chargé.
    """
    faux = _SpecSansPolitique()
    monkeypatch.setitem(stt._REGISTRY, "whisper", faux)

    with pytest.raises(stt.MissingArtifactPolicy) as exc:
        stt.get_engine("whisper")

    assert faux.appels == [], (
        "le moteur a été instancié malgré l'absence de politique — le refus "
        "arrive trop tard"
    )
    assert "whisper" in str(exc.value)
    assert stt._cache == {}, "un moteur non conforme a été mis en cache"


def test_le_cache_ne_peut_pas_servir_un_moteur_devenu_non_conforme(monkeypatch) -> None:
    """Piège de ce dépôt : `get_engine` a un cache par configuration.

    Un premier appel réussi puis une politique retirée, et le cache servirait
    indéfiniment un moteur hors contrat — le refus serait décoratif.
    """
    premier = stt.get_engine("whisper")
    assert premier is not None and stt._cache, "le cache devrait être amorcé"

    monkeypatch.setitem(stt._REGISTRY, "whisper", _SpecSansPolitique())

    with pytest.raises(stt.MissingArtifactPolicy):
        stt.get_engine("whisper")


def test_enregistrer_un_moteur_sans_politique_est_refuse() -> None:
    """Second verrou : à la construction de la spécification elle-même.

    Le premier verrou (`engine_spec`) couvre les entrées qui n'ont pas emprunté
    ce constructeur. Celui-ci fait échouer l'erreur la plus probable — un moteur
    ajouté au registre — au plus près de sa cause.
    """
    with pytest.raises(stt.MissingArtifactPolicy):
        stt.EngineSpec(factory=lambda **kw: None, artifact_policy=None)

    with pytest.raises(TypeError):
        # Omission pure : aucun défaut ne doit attribuer une politique en douce.
        stt.EngineSpec(factory=lambda **kw: None)  # type: ignore[call-arg]


def test_le_pipeline_refuse_AVANT_de_lire_le_moindre_octet(monkeypatch, tmp_path) -> None:
    """« Aucun pipeline lancé, aucun audio chargé, aucun modèle chargé. »

    L'extraction audio précédait la résolution du moteur : une faute de contrat
    aurait coûté une conversion ffmpeg complète avant d'être vue. Le contrat est
    donc résolu en tête de `_run`.
    """
    from core import pipeline

    vus: list[str] = []
    for nom in ("validate_input", "probe_duration", "to_wav16k", "pcm_sha256"):
        monkeypatch.setattr(
            pipeline.media, nom,
            lambda *a, _n=nom, **k: vus.append(_n),
        )
    monkeypatch.setattr(
        pipeline, "get_engine",
        lambda *a, **k: vus.append("get_engine"),
    )
    monkeypatch.setitem(stt._REGISTRY, "whisper", _SpecSansPolitique())

    source = tmp_path / "a.wav"
    source.write_bytes(b"RIFF")
    with pytest.raises(stt.MissingArtifactPolicy):
        pipeline.run(source, engine_name="whisper", output_dir=tmp_path)

    assert vus == [], f"du travail a été fait avant le refus de contrat : {vus}"


# --------------------------------------------------------------------------
# 2. Ce que chaque moteur DÉCLARE, publié dans le document
# --------------------------------------------------------------------------

def _front_matter(contenu: str) -> dict:
    import yaml

    return yaml.safe_load(contenu.split("---", 2)[1]) or {}


def _corps(contenu: str) -> str:
    """Le texte lu par l'utilisateur, sans le front-matter.

    La distinction porte : un générique retiré est REPUBLIÉ dans le
    front-matter (« rien ne disparaît en silence »). Chercher son absence dans
    le fichier entier testerait donc le contraire de l'invariant.
    """
    return contenu.split("---", 2)[2]


@pytest.fixture
def corpus_jetable(isolated_stores, monkeypatch):
    from core import writer_md

    monkeypatch.setattr(writer_md, "OUTPUT_DIR", isolated_stores["output_dir"])
    return isolated_stores["output_dir"]


def _transcrire(monkeypatch, tmp_path, corpus, textes, *, engine_name="whisper"):
    """Fait tourner le pipeline réel avec un moteur bouchonné.

    Le moteur est faux — on ne teste pas Whisper — mais la fabrique, la
    politique, l'écriture du `.md` et la provenance sont les vraies.
    """
    from core import pipeline
    from core.stt_base import Segment, Transcript

    class FauxMoteur:
        name, model_name = engine_name, "faux-modele"

        def transcribe(self, wav_path, *, progress=None, on_partial=None):
            return Transcript(
                segments=[
                    Segment(float(i * 10), float(i * 10 + 5), texte)
                    for i, texte in enumerate(textes)
                ],
                full_text=" ".join(textes),
                language="fr",
            )

    monkeypatch.setattr(pipeline, "get_engine", lambda *a, **k: FauxMoteur())
    monkeypatch.setattr(pipeline.media, "validate_input", lambda p: p)
    monkeypatch.setattr(pipeline.media, "probe_duration", lambda p: 30.0)
    monkeypatch.setattr(pipeline.media, "to_wav16k", lambda p: p)
    monkeypatch.setattr(pipeline.media, "pcm_sha256", lambda p: "a" * 64)
    # L'archivage appelle ffmpeg : absent du runner CI, il ferait échouer un
    # test qui ne parle pas d'archivage.
    for archivage in ("archive_audio", "archive_faithful", "keep_original"):
        monkeypatch.setattr(pipeline.media, archivage, lambda *a, **k: None)
    identifiant = docid.nouveau()
    monkeypatch.setattr(pipeline, "_project_id_for_output", lambda d: identifiant)

    source = Path(tmp_path) / "reunion.wav"
    source.write_bytes(b"RIFF")
    _, contenu = pipeline.run(
        source, engine_name=engine_name, output_dir=corpus, timestamps=True,
    )
    return contenu


def test_whisper_CONTROLE_et_publie_ce_qu_il_a_retire(
        monkeypatch, tmp_path, corpus_jetable) -> None:
    """Le cas relevé dans le corpus : un générique de sous-titrage en milieu de
    fichier. Il doit sortir du corps, ET figurer dans le front-matter."""
    contenu = _transcrire(
        monkeypatch, tmp_path, corpus_jetable,
        ["Bonjour à tous, merci d'être venus.", CREDIT, "Reprenons l'ordre du jour."],
    )
    meta = _front_matter(contenu)

    assert meta.get("artifact_status") == "checked", (
        "le détecteur a-t-il réellement tourné ? artifact_status="
        f"{meta.get('artifact_status')!r} — méta : {sorted(meta)}"
    )
    assert meta.get("artifact_policy") == "artifacts.scrub"
    assert meta.get("artifact_policy_version") == 2   # suites sans espace depuis la v2
    retires = meta.get("removed_credits")
    assert retires and any(CREDIT in str(r) for r in retires), (
        f"le générique n'est pas consigné : {retires!r}")
    assert CREDIT not in _corps(contenu), "le générique est resté dans le corps"


def test_whisper_replie_une_boucle_en_laissant_sa_marque(
        monkeypatch, tmp_path, corpus_jetable) -> None:
    """Une boucle commence souvent par de la vraie parole : on replie, on ne
    supprime pas, et le lecteur voit ce qui s'est passé."""
    contenu = _transcrire(
        monkeypatch, tmp_path, corpus_jetable, ["Bonjour à tous.", BOUCLE],
    )
    meta = _front_matter(contenu)

    assert meta.get("artifact_status") == "checked"
    assert meta.get("repetition_loops"), f"boucle non signalée : {sorted(meta)}"
    assert "boucle de répétition repliée" in _corps(contenu)
    assert "Let's start a" in _corps(contenu), "la parole réelle a été perdue"


def test_un_transcript_PROPRE_declare_checked_sans_reserve(
        monkeypatch, tmp_path, corpus_jetable) -> None:
    """Garde sur l'instrument.

    Si les réserves étaient toujours remplies, les deux tests précédents
    seraient verts sans rien prouver. Et surtout : `checked` avec un rapport
    VIDE est une affirmation — « j'ai regardé, il n'y a rien » — que le
    document doit pouvoir porter, à la différence du silence d'avant.
    """
    contenu = _transcrire(
        monkeypatch, tmp_path, corpus_jetable,
        ["Bonjour à tous.", "Reprenons l'ordre du jour."],
    )
    meta = _front_matter(contenu)

    assert meta.get("artifact_status") == "checked"
    for reserve in ("repetition_loops", "removed_credits", "repeated_series"):
        assert not meta.get(reserve), f"{reserve} rempli sur un transcript propre"


def test_mistral_declare_UNCHECKED_avec_sa_raison_et_ne_nettoie_rien(
        monkeypatch, tmp_path, corpus_jetable) -> None:
    """`unchecked`, PAS `not_applicable` — la nuance est le sujet (voir
    `core/artifact_policy.py`, « Les quatre états sont FERMÉS »).

    Trois sorties propres ne prouvent pas qu'un moteur n'hallucine jamais.
    Appliquer à Voxtral un détecteur calibré sur Whisper serait tout aussi faux :
    il pourrait « nettoyer » du texte légitime. Le moteur ne touche donc à rien,
    et le document le dit avec la mesure qui le justifie.
    """
    contenu = _transcrire(
        monkeypatch, tmp_path, corpus_jetable,
        ["Bonjour à tous.", CREDIT], engine_name="mistral",
    )
    meta = _front_matter(contenu)

    assert meta.get("artifact_status") == "unchecked", sorted(meta)
    assert meta.get("artifact_status") != "not_applicable"
    raison = meta.get("artifact_reason") or ""
    assert "trois échantillons" in raison, f"raison publiée : {raison!r}"
    assert CREDIT in _corps(contenu), (
        "un détecteur calibré sur Whisper a été appliqué à Voxtral")
    assert not meta.get("removed_credits")


def test_la_provenance_porte_le_MEME_etat_que_le_front_matter(
        monkeypatch, tmp_path, corpus_jetable) -> None:
    """Deux endroits publient l'évaluation ; deux calculs = deux occasions de
    diverger, et c'est exactement ce qui était arrivé au modèle de profilage."""
    contenu = _transcrire(
        monkeypatch, tmp_path, corpus_jetable, ["Bonjour à tous.", CREDIT],
    )
    meta = _front_matter(contenu)
    artefacts = (meta.get("pipeline") or {}).get("artifacts") or {}

    assert artefacts.get("status") == meta.get("artifact_status") == "checked"
    assert artefacts.get("policy") == meta.get("artifact_policy")
    assert artefacts.get("policy_version") == meta.get("artifact_policy_version")


# --------------------------------------------------------------------------
# 3. Échec d'exécution : le transcript survit, le document le dit
# --------------------------------------------------------------------------

class _PolitiqueQuiExplose:
    id = "politique.en-panne"
    version = 7

    def __init__(self, message: str = "détecteur indisponible") -> None:
        self.message = message

    def apply(self, transcript):
        raise RuntimeError(self.message)


def _avec_politique(monkeypatch, politique) -> None:
    spec = dataclasses.replace(
        stt._REGISTRY["whisper"], artifact_policy=politique,
    )
    monkeypatch.setitem(stt._REGISTRY, "whisper", spec)


def test_une_politique_en_ECHEC_ne_fait_pas_perdre_la_transcription(
        monkeypatch, tmp_path, corpus_jetable) -> None:
    """Deuxième nature d'échec, à ne pas confondre avec l'absence de politique.

    Annuler une heure de transcription parce qu'un détecteur secondaire est
    tombé irait contre « rien ne disparaît en silence » ; prétendre `checked`
    serait pire. Le transcript survit et porte `failed`.
    """
    _avec_politique(monkeypatch, _PolitiqueQuiExplose())

    contenu = _transcrire(
        monkeypatch, tmp_path, corpus_jetable, ["Bonjour à tous.", CREDIT],
    )
    meta = _front_matter(contenu)

    assert meta.get("artifact_status") == "failed", sorted(meta)
    assert "détecteur indisponible" in (meta.get("artifact_error") or "")
    assert meta.get("artifact_policy") == "politique.en-panne"
    assert "Bonjour à tous." in _corps(contenu), "la parole a été perdue"
    # Rien n'a été retiré : le document ne doit surtout pas laisser croire
    # qu'un contrôle a eu lieu.
    assert CREDIT in _corps(contenu)
    assert not meta.get("removed_credits")


def test_l_erreur_publiee_est_ASSAINIE(monkeypatch, tmp_path, corpus_jetable) -> None:
    """Le `.md` se partage. Une politique propre à un moteur distant peut voir
    passer une clé d'API dans son message d'erreur ; elle ne doit pas atterrir
    dans un document."""
    jeton = "sk-" + "abcdefghijklmnopqrstuvwxyz0123456789"  # assemblée : voir le contrôle d'export
    _avec_politique(monkeypatch, _PolitiqueQuiExplose(f"refus amont {jeton}"))

    contenu = _transcrire(
        monkeypatch, tmp_path, corpus_jetable, ["Bonjour à tous."],
    )

    assert _front_matter(contenu).get("artifact_status") == "failed"
    assert jeton not in contenu, "un jeton a été publié dans le document"


# --------------------------------------------------------------------------
# 4. Les états sont fermés, et aucun moteur ne passe sans déclarer
# --------------------------------------------------------------------------

def test_les_quatre_etats_sont_FERMES() -> None:
    from core.artifact_policy import ArtifactStatus

    assert {e.value for e in ArtifactStatus} == {
        "checked", "not_applicable", "unchecked", "failed",
    }


def test_un_etat_invente_est_refuse_a_la_source() -> None:
    """Fermé veut dire fermé : ni l'évaluation ni la provenance n'acceptent une
    cinquième valeur, fût-elle rassurante."""
    from core import provenance
    from core.artifact_policy import ArtifactAssessment

    with pytest.raises(ValueError):
        ArtifactAssessment(
            status="probablement propre", policy_id="x", policy_version=1,
        )
    with pytest.raises(ValueError, match="fermés"):
        provenance.transcript_fingerprint(
            engine="whisper", model="m", language_requested=None, timestamps=True,
            diarized=False, profiled=False, input_pcm_sha256="a" * 64,
            artifact_status="probablement propre",
            artifact_policy="x", artifact_policy_version=1,
        )


def test_une_politique_passe_partout_SANS_raison_est_refusee() -> None:
    """« unchecked » sans justification, c'est le silence avec une étiquette."""
    from core.artifact_policy import ArtifactAssessment, ArtifactStatus, UncheckedPolicy

    with pytest.raises(ValueError):
        UncheckedPolicy(id="x", version=1, reason="   ")
    with pytest.raises(ValueError):
        ArtifactAssessment(
            status=ArtifactStatus.UNCHECKED, policy_id="x", policy_version=1,
        )


def test_TOUS_les_moteurs_declares_ont_une_politique_utilisable() -> None:
    """Un moteur ajouté demain sans politique fait tomber ce test.

    L'ancre de non-vacuité (`>= 2`) est là parce que `assert tout va bien` sur
    un registre devenu vide serait vert sans rien prouver.
    """
    assert len(stt._REGISTRY) >= 2, f"registre suspect : {sorted(stt._REGISTRY)}"
    assert set(stt.ENGINES) == set(stt._REGISTRY)
    for nom in stt.ENGINES:
        politique = stt.engine_spec(nom).artifact_policy
        assert callable(getattr(politique, "apply", None)), nom
        assert getattr(politique, "id", None), nom
        assert isinstance(getattr(politique, "version", None), int), nom


def _code_executable(fichier: Path) -> str:
    """Source privée de ses docstrings et de ses lignes de commentaire.

    Une MENTION en prose est de l'histoire — pourquoi ce crochet a été refusé —
    et doit rester possible ; ce qui est interdit, c'est du CODE qui l'appelle
    ou l'introspecte. Un balayage purement AST des attributs manquerait par
    ailleurs la forme d'origine du défaut, `hasattr(engine, "…")`, où le nom
    n'est qu'une chaîne.
    """
    source = fichier.read_text(encoding="utf-8")
    import ast

    docstrings: set[int] = set()
    for noeud in ast.walk(ast.parse(source)):
        if not isinstance(
            noeud, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        premier = noeud.body[0] if noeud.body else None
        if (
            isinstance(premier, ast.Expr)
            and isinstance(premier.value, ast.Constant)
            and isinstance(premier.value.value, str)
        ):
            docstrings.update(range(premier.lineno, (premier.end_lineno or 0) + 1))
    return "\n".join(
        "" if numero in docstrings or ligne.lstrip().startswith("#") else ligne
        for numero, ligne in enumerate(source.splitlines(), start=1)
    )


def test_le_crochet_MORT_a_disparu_du_CODE() -> None:
    """`transcribe_with_artefacts` mêlait transport STT et post-traitement,
    dupliquait les rappels de progression et créait une seconde signature à
    maintenir. Le laisser réapparaître rouvrirait la branche morte
    qu'on vient de fermer, et le `hasattr` avec elle : c'est la fabrique qui
    déclare, le pipeline n'a plus à deviner."""
    racine = Path(__file__).resolve().parents[2] / "core"
    fichiers = sorted(racine.glob("*.py"))

    assert len(fichiers) >= 20, (
        f"le balayage ne voit que {len(fichiers)} fichier(s) dans {racine} — "
        "ce n'est plus le code qui est lu")
    codes = {f.name: _code_executable(f) for f in fichiers}
    assert "def _run(" in codes["pipeline.py"], (
        "le balayage ne lit plus le corps du pipeline")

    fautifs = [nom for nom, code in codes.items()
               if "transcribe_with_artefacts" in code]
    assert fautifs == [], f"crochet mort réapparu dans : {fautifs}"
    assert "hasattr(" not in codes["pipeline.py"], (
        "le pipeline choisit de nouveau son traitement par introspection du "
        "moteur — c'est la branche silencieuse qui revient")


# ---------- Le contrat validait `apply`, et rien d'autre ----------

class _PolitiqueVersionInvalide:
    """`apply` appelable, mais une version qui n'est pas un entier."""
    id = "politique-cassee"
    version = "pas-un-entier"

    def apply(self, transcript):
        raise RuntimeError("le détecteur tombe")


class _PolitiqueSansId:
    id = ""
    version = 1

    def apply(self, transcript):  # pragma: no cover — refusée avant l'appel
        raise AssertionError("ne doit jamais être appelée")


def test_une_politique_a_la_VERSION_invalide_est_refusee_a_l_enregistrement() -> None:
    """Les TROIS éléments du protocole sont contrôlés à l'enregistrement.

    `_exiger_une_politique` ne contrôlait QUE la présence d'un `apply`
    appelable. Le protocole en déclare trois — `apply`, `id`, `version` — et
    deux n'étaient gardés nulle part.

    Le contrôle est ici, à l'enregistrement, pour la même raison que la
    politique absente : refuser AVANT la fabrique, avant qu'un modèle ne se
    charge sur un contrat déjà rompu.
    """
    with pytest.raises(stt.MissingArtifactPolicy, match="version"):
        stt.EngineSpec(factory=lambda **k: None,
                       artifact_policy=_PolitiqueVersionInvalide())


def test_une_politique_SANS_identifiant_est_refusee() -> None:
    """Un identifiant vide atteindrait le front-matter et la provenance : le
    document dirait quelle politique l'a contrôlé, et ce serait une chaîne
    vide."""
    with pytest.raises(stt.MissingArtifactPolicy, match="identifiant"):
        stt.EngineSpec(factory=lambda **k: None,
                       artifact_policy=_PolitiqueSansId())


def test_le_rapport_d_ECHEC_ne_tombe_pas_a_son_tour() -> None:
    """Le cas le plus gênant.

    `apply_policy` promet que le transcript SURVIT à une panne du détecteur.
    Son gestionnaire d'exception faisait pourtant
    `int(getattr(policy, "version", 0) or 0)` : une politique mal déclarée qui
    tombe faisait tomber son PROPRE rapport d'échec.

    Le contrat n'était donc pas tenu dans le cas précis pour lequel il
    existait. On garde ici la ceinture — même si le refus à
    l'enregistrement rend le cas improbable, ce chemin ne doit jamais lever.
    """
    class Bancale:
        id = "bancale"
        version = "pas-un-entier"

        def apply(self, transcript):
            raise RuntimeError("le détecteur tombe")

    from core import artifact_policy
    from core.stt_base import Segment, Transcript

    transcript = Transcript(
        segments=[Segment(0.0, 1.0, "bonjour")], full_text="bonjour", language="fr")
    rendu, evaluation = artifact_policy.apply_policy(Bancale(), transcript)

    assert rendu is transcript, "le transcript n'a pas survécu à la panne"
    assert evaluation.status is artifact_policy.ArtifactStatus.FAILED
    assert evaluation.error, "un échec sans erreur lisible n'est pas un rapport"


# ---------- La frontière de PUBLICATION, l'autre moitié ----------

def _rapport_vide():
    from core.artifacts import Rapport
    return Rapport([], [], [])


def test_une_version_NON_ENTIERE_ne_peut_pas_etre_publiee() -> None:
    """L'enregistrement était fermé, la publication ne l'était pas.

    `EngineSpec` refuse désormais une politique mal déclarée. Mais rien
    n'impose qu'un `ArtifactAssessment` recopie les métadonnées de la politique
    enregistrée : une politique dont `apply()` rend une évaluation incohérente
    publiait encore une version en chaîne, jusque dans le front-matter et la
    provenance — dont les annotations `int` n'agissent pas à l'exécution.

    Ce test construit l'évaluation DIRECTEMENT. Passer par la fabrique
    reconduirait le défaut : garder un morceau et pas l'autre.
    """
    from core.artifact_policy import ArtifactAssessment, ArtifactStatus

    with pytest.raises(ValueError, match="version"):
        ArtifactAssessment(
            status=ArtifactStatus.CHECKED, policy_id="scrub",
            policy_version="pas-un-entier", rapport=_rapport_vide())


def test_une_version_NULLE_ou_negative_ne_peut_pas_etre_publiee() -> None:
    """Zéro n'est pas une version : il dirait « contrôlé par la politique v0 »,
    qui n'existe pas. Le seul état qui l'autorise est `failed`, et pour une
    raison précise — voir le test suivant."""
    from core.artifact_policy import ArtifactAssessment, ArtifactStatus

    for mauvaise in (0, -1):
        with pytest.raises(ValueError, match="version"):
            ArtifactAssessment(
                status=ArtifactStatus.CHECKED, policy_id="scrub",
                policy_version=mauvaise, rapport=_rapport_vide())


def test_un_ECHEC_peut_publier_la_version_ZERO_et_c_est_voulu() -> None:
    """Le repli est PRÉSERVÉ, explicitement.

    `_version_sure` rend 0 quand la version d'une politique est illisible —
    précisément pour qu'une politique structurellement cassée puisse quand même
    publier son rapport d'échec. Interdire ce zéro ferait lever le chemin qui
    existe pour que le transcript SURVIVE à la panne : on retomberait dans le
    défaut du rapport d'échec qui tombe à son tour, par l'autre bout.

    Un `0` sur un `failed` se lit « la version n'a pas pu être établie », ce qui
    est vrai. Le même `0` sur un `checked` mentirait.
    """
    from core.artifact_policy import ArtifactAssessment, ArtifactStatus

    evaluation = ArtifactAssessment(
        status=ArtifactStatus.FAILED, policy_id="bancale",
        policy_version=0, error="le détecteur est tombé")

    assert evaluation.policy_version == 0
    assert evaluation.status is ArtifactStatus.FAILED


def test_un_BOOLEEN_n_est_pas_une_version() -> None:
    """`True` est un `int` en Python, et `isinstance(True, int)` vaut vrai. Sans
    ce cas, une version booléenne passerait le contrôle de type et publierait
    `policy_version: true` dans un champ que le lecteur attend numérique."""
    from core.artifact_policy import ArtifactAssessment, ArtifactStatus

    with pytest.raises(ValueError, match="version"):
        ArtifactAssessment(
            status=ArtifactStatus.CHECKED, policy_id="scrub",
            policy_version=True, rapport=_rapport_vide())


# ---------- Le miroir, sur l'autre champ du protocole ----------

class _PolitiqueIdNumerique:
    """`apply` appelable, version valide, mais un identifiant qui n'est pas texte."""
    id = 123
    version = 1

    def apply(self, transcript):  # pragma: no cover — refusée avant l'appel
        raise AssertionError("ne doit jamais être appelée")


def test_un_identifiant_NON_TEXTE_est_refuse_a_l_enregistrement() -> None:
    """`ArtifactPolicy.id` est annoncé `str`, et l'annotation n'agit pas.

    Contrôler seulement que l'identifiant n'est pas VIDE, via un `str(...)`
    qui convertit au passage, laisserait passer `123`. Le vide et le type sont
    deux propriétés.
    """
    with pytest.raises(stt.MissingArtifactPolicy, match="identifiant"):
        stt.EngineSpec(factory=lambda **k: None,
                       artifact_policy=_PolitiqueIdNumerique())


def test_un_identifiant_NON_TEXTE_ne_peut_pas_etre_publie() -> None:
    """L'autre frontière, testée séparément — sinon on garde encore une moitié.

    Rien n'oblige un `apply()` à recopier l'identifiant de la politique
    enregistrée. Le YAML publiait `artifact_policy: 123`, un nombre là où la
    provenance attend du texte, et son annotation `str` n'agit pas davantage à
    l'exécution.
    """
    from core.artifact_policy import ArtifactAssessment, ArtifactStatus

    with pytest.raises(ValueError, match="identifiant"):
        ArtifactAssessment(
            status=ArtifactStatus.CHECKED, policy_id=123,
            policy_version=1, rapport=_rapport_vide())


def test_un_identifiant_TEXTE_reste_accepte() -> None:
    """Ancre de non-vacuité : un refus systématique ferait passer les deux
    tests ci-dessus sans rien garder."""
    from core.artifact_policy import ArtifactAssessment, ArtifactStatus

    evaluation = ArtifactAssessment(
        status=ArtifactStatus.CHECKED, policy_id="scrub",
        policy_version=1, rapport=_rapport_vide())
    assert evaluation.policy_id == "scrub"
    assert evaluation.front_matter()["artifact_policy"] == "scrub"
