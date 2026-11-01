from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext

import pytest

from core import docid, library, provenance, report, writer_md


pytestmark = pytest.mark.unit


def _fixed_build(monkeypatch) -> None:
    monkeypatch.setattr(
        provenance,
        "_git_state",
        lambda: {"revision": "a" * 40, "dirty": False},
    )
    monkeypatch.setattr(provenance, "_implementation_sha256", lambda files: "b" * 64)
    monkeypatch.setattr(provenance, "_application_version", lambda: "0.test")


# L'évaluation d'artefacts n'a PAS de valeur par défaut dans la signature : un
# appelant doit dire ce que la politique du moteur a produit. Les tests aussi.
_ARTEFACTS_CONTROLES = {
    "artifact_status": "checked",
    "artifact_policy": "artifacts.scrub",
    "artifact_policy_version": 1,
}


def _transcript_fingerprint(monkeypatch) -> dict:
    _fixed_build(monkeypatch)
    return provenance.transcript_fingerprint(
        engine="whisper",
        model="mlx-community/test",
        language_requested=None,
        timestamps=True,
        diarized=True,
        profiled=True,
        input_pcm_sha256="c" * 64,
        **_ARTEFACTS_CONTROLES,
    )


def test_transcript_fingerprint_records_effective_content_settings(monkeypatch) -> None:
    fingerprint = _transcript_fingerprint(monkeypatch)

    assert fingerprint["schema_version"] == 1
    assert fingerprint["kind"] == "transcript"
    # Épinglée : une mutation a montré que la version du transcript
    # n'était contrôlée NULLE PART, alors que celle du CR l'était. On pouvait
    # donc changer le sens de l'empreinte sans l'incrémenter — et deux
    # documents produits sous des résolutions de modèle différentes auraient
    # comparé égal. Couverture asymétrique là où les deux contrats vivent
    # côte à côte.
    assert fingerprint["pipeline_version"] == 5
    assert fingerprint["application_version"] == "0.test"
    assert fingerprint["code"] == {
        "revision": "a" * 40,
        "dirty": False,
        "implementation_sha256": "b" * 64,
    }
    assert fingerprint["stt"] == {
        "engine": "whisper",
        "model": "mlx-community/test",
        "language_requested": "auto",
        "chunk_seconds": provenance.config.STT_CHUNK_SEC,
        "cut_search_seconds": provenance.config.STT_CUT_SEARCH_SEC,
        "timestamps": True,
    }
    assert fingerprint["diarization"]["model"] == provenance.config.DIARIZATION_MODEL
    # RETOURNÉ, pas supprimé. Cette assertion affirmait que le modèle
    # de profilage OMIS retombe sur `config.LLM_MODEL` — c'est-à-dire que le
    # modèle Ollama était la valeur d'INCONNU, quel que soit le moteur ayant
    # tourné. Un fingerprint scellé annonçait donc un modèle plausible plutôt
    # que d'avouer qu'il ne savait pas. « Inconnu » se lit et se cherche ; un
    # nom faux se croit.
    assert fingerprint["profiling"]["model"] == "unknown"
    assert fingerprint["audio"]["input_pcm_sha256"] == "c" * 64
    assert fingerprint["lineage"] == {"derived_from_document_id": None}


def test_fingerprint_hash_is_canonical_and_roundtrips_through_yaml(monkeypatch) -> None:
    fingerprint = _transcript_fingerprint(monkeypatch)
    payload = {key: value for key, value in fingerprint.items() if key != "fingerprint"}
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert fingerprint["fingerprint"] == hashlib.sha256(canonical).hexdigest()

    markdown = writer_md._yaml_frontmatter({"title": "test", "pipeline": fingerprint})
    meta, body = library._split_frontmatter(markdown + "\n\nCorps")
    assert meta["pipeline"] == fingerprint
    assert body.strip() == "Corps"


def test_implementation_hash_changes_with_dirty_source(tmp_path) -> None:
    module = tmp_path / "core" / "module.py"
    module.parent.mkdir()
    module.write_text("VALUE = 1\n", encoding="utf-8")
    before = provenance._implementation_sha256(("core/module.py",), root=tmp_path)

    module.write_text("VALUE = 2\n", encoding="utf-8")
    after = provenance._implementation_sha256(("core/module.py",), root=tmp_path)

    assert len(before) == 64
    assert before != after


def test_transcript_fingerprint_rejects_unverifiable_input(monkeypatch) -> None:
    _fixed_build(monkeypatch)
    common = {
        "engine": "whisper",
        "model": "model",
        "language_requested": "fr",
        "timestamps": True,
        "diarized": False,
        "profiled": False,
        **_ARTEFACTS_CONTROLES,
    }
    with pytest.raises(ValueError, match="Hash PCM"):
        provenance.transcript_fingerprint(**common, input_pcm_sha256="not-a-hash")
    with pytest.raises(ValueError, match="document source"):
        provenance.transcript_fingerprint(
            **common,
            input_pcm_sha256="c" * 64,
            derived_from_document_id="not-an-ulid",
        )


def test_report_links_source_fingerprint_and_marks_legacy(monkeypatch) -> None:
    source = _transcript_fingerprint(monkeypatch)
    current = provenance.report_fingerprint(
        source_pipeline=source,
        model="qwen:test",
        language="fr",
        genre="interview",
        num_ctx=4096,
        questions=["What happened?"],
    )
    legacy = provenance.report_fingerprint(
        source_pipeline=None,
        model="qwen:test",
        language="fr",
        genre="interview",
        num_ctx=4096,
        questions=["What happened?"],
    )
    tampered_source = dict(source)
    tampered_source["pipeline_version"] = 999
    tampered = provenance.report_fingerprint(
        source_pipeline=tampered_source,
        model="qwen:test",
        language="fr",
        genre="interview",
        num_ctx=4096,
        questions=["What happened?"],
    )

    assert current["source"] == {
        "status": "fingerprinted",
        "pipeline_fingerprint": source["fingerprint"],
    }
    assert legacy["source"] == {
        "status": "legacy_unknown",
        "pipeline_fingerprint": None,
    }
    assert tampered["source"] == {
        "status": "invalid",
        "pipeline_fingerprint": None,
    }
    # Passée de 4 à 5 : le fingerprint décrit désormais ce qui a
    # RÉELLEMENT tourné (filtre d'artefacts parké, moteur de diarisation et de
    # profilage réels), et les fichiers du chemin Mistral entrent dans le hash
    # d'implémentation. Un document produit avant ne se compare pas à un après.
    #
    # Puis de 5 à 6 : le modèle du CR était résolu par un partage
    # binaire `mistral / sinon qwen3.5:9b`, faux pour tout document produit par
    # MLX. Les empreintes d'avant décrivent un modèle qui n'a pas forcément
    # tourné — elles ne doivent pas comparer égal.
    assert current["pipeline_version"] == 6
    assert current["template"]["prompt_version"] == 4
    assert current["guards"]["citation_contract_version"] == (
        provenance.report_citations.CONTRACT_VERSION
    )


def test_fingerprints_never_copy_runtime_secrets(monkeypatch) -> None:
    _fixed_build(monkeypatch)
    monkeypatch.setattr(provenance.config, "HF_TOKEN", "secret-hf-marker")
    monkeypatch.setattr(
        provenance.config, "OLLAMA_HOST", "http://user:secret-ollama-marker@host"
    )
    source = provenance.transcript_fingerprint(
        engine="whisper",
        model="model",
        language_requested="fr",
        timestamps=True,
        diarized=True,
        profiled=True,
        input_pcm_sha256="c" * 64,
        **_ARTEFACTS_CONTROLES,
    )
    generated = provenance.report_fingerprint(
        source_pipeline=source,
        model="qwen:test",
        language="fr",
        genre="interview",
        num_ctx=4096,
        questions=["What happened?"],
    )

    serialized = json.dumps({"source": source, "report": generated})
    assert "secret-hf-marker" not in serialized
    assert "secret-ollama-marker" not in serialized


def test_make_report_writes_its_fingerprint_and_source_link(monkeypatch, tmp_path) -> None:
    source_pipeline = {"fingerprint": "c" * 64}
    source_meta = {
        "id": docid.nouveau(),
        "meeting_id": docid.nouveau(),
        "project_id": docid.nouveau(),
        "title": "Meeting",
        "language": "en",
        "pipeline": source_pipeline,
    }
    source_path = tmp_path / "meeting.md"
    source_path.write_text("source", encoding="utf-8")
    target = tmp_path / "meeting-cr.md"
    captured = {}
    report_call = {}
    prompts = []
    marker = {"kind": "report", "fingerprint": "d" * 64}

    monkeypatch.setattr(
        report.library,
        "get_transcript",
        lambda name: {
            "meta": source_meta,
            "body": (
                "## Conversation profile\n\n"
                "A previous LLM says something unsupported.\n\n"
                "## Transcript\n\n**Speaker 1** [00:00:04] Hello."
            ),
        },
    )
    monkeypatch.setattr(report.library, "transcript_path", lambda name: source_path)
    monkeypatch.setattr(report.resources, "heavy", lambda **kwargs: nullcontext())
    # Le bouchon portait sur le MOTEUR Ollama, pour vérifier que les
    # contraintes atteignaient l'implémentation sous SON vocabulaire
    # (`json_schema`). Ollama étant sorti du registre, il vise la
    # façade et le nom du contrat. Ce que chaque moteur en fait se vérifie
    # chez lui — `test_llm_mlx.py`, `test_llm_interface.py`.
    #
    # Sans le bouchon d'`is_available`, make_report exécute la vraie garde (GET
    # httpx vers Ollama) : un test marqué `unit` échouait sur arbre propre sans
    # serveur.
    from core import llm

    monkeypatch.setattr(llm, "is_available", lambda *args, **kwargs: True)
    def chat_json(system, instruction, **kwargs):
        prompts.append(instruction)
        report_call.update(kwargs)
        return {
            "sections": [
                {
                    "question_id": index, "answer_status": "answered",
                    "title": f"Section {index}",
                    "claims": [
                        {
                            "text": "The speaker says hello.",
                            "primary_evidence": {
                                "timestamp": "00:00:04", "quote": "Hello."},
                            "secondary_evidence": None,
                        }
                    ],
                }
                for index in range(
                    1, len(report.templates.questions("interview", "en")) + 1
                )
            ]
        }

    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: chat_json(*a, **k))
    monkeypatch.setattr(report, "classify_genre", lambda *args, **kwargs: "interview")
    monkeypatch.setattr(report.writer_md, "unique_path", lambda *args, **kwargs: target)
    monkeypatch.setattr(report.library, "sync_file", lambda path: None)
    monkeypatch.setattr(report.semantic, "index_name", lambda name: None)

    def fingerprint(**kwargs):
        captured.update(kwargs)
        return marker

    monkeypatch.setattr(report.provenance, "report_fingerprint", fingerprint)

    result = report.make_report("meeting.md")
    meta, _ = library._split_frontmatter(result["content"])

    assert meta["pipeline"] == marker
    assert captured["source_pipeline"] == source_pipeline
    # `report.LLM_MODEL` n'existe plus : le CR ne connaît plus de modèle par
    # défaut, il DEMANDE au moteur ce qu'il sert. `make_report` a `provider
    # = "ollama"` en défaut, d'où la résolution ci-dessous — qui suivra le
    # moteur le jour où ce défaut changera, au lieu de figer une constante.
    assert captured["model"] == report.llm.modele_servi(report.llm.FOURNISSEUR_DEFAUT)
    assert captured["genre"] == "interview"
    assert captured["num_ctx"] == 4096
    assert captured["questions"] == report.templates.questions("interview", "en")
    assert "OUTPUT CONTRACT" in prompts[0]
    # Le plafond TOTAL a quitté le contrat (`prompt_contract`) — c'était sa
    # dernière règle à état global, et la sélection l'applique de toute façon
    # après coup. Ce qui doit rester dans le prompt, c'est la borne PAR SECTION.
    assert "at most 10 claims" not in prompts[0]
    assert "at most 5 claims" in prompts[0]
    # `json_schema` était le nom d'Ollama. Le bouchon vise maintenant la
    # façade, donc le nom du CONTRAT — la traduction vers le vocabulaire de
    # chaque moteur lui appartient, et se vérifie chez lui.
    schema = report_call["schema"]
    claims = schema["properties"]["sections"]["items"]["properties"]["claims"]
    assert "maxItems" not in claims, "plafond par section retiré du schéma"
    assert "minItems" not in claims, (
        "une section `not_found` est légitimement vide")
    affirmation = claims["items"]["properties"]
    assert "evidence" not in affirmation, "la liste ouverte a disparu"
    assert set(affirmation) == {"text", "primary_evidence", "secondary_evidence"}
    assert '"timestamp":"HH:MM:SS"' in prompts[0]
    assert "A previous LLM says something unsupported." not in prompts[0]
    assert "**Speaker 1** [00:00:04] Hello." in prompts[0]
    assert meta["citation_audit"] == {
        "contract_version": 7,
        "status": "citation_integrity_verified",
        "claims": len(report.templates.questions("interview", "en")),
        "claims_with_valid_citation": len(
            report.templates.questions("interview", "en")
        ),
        "abstentions": 0,
        "citations": len(report.templates.questions("interview", "en")),
        "valid_citations": len(report.templates.questions("interview", "en")),
    }
    assert "citation_warnings" not in meta
    assert result["genre"] == "interview"


def test_la_provenance_ne_decrit_pas_un_traitement_qui_n_a_pas_eu_lieu() -> None:
    """La provenance décrit ce qui a RÉELLEMENT tourné (`core/provenance.py`).

    Le bloc annonçait `artifacts.enabled: true` alors que le filtre était parké,
    attribuait toute diarisation à pyannote même quand Voxtral l'avait faite, et
    tout profilage à Qwen même en mode Mistral. Deux documents produits par des
    moteurs différents pouvaient donc annoncer la même provenance.

    `enabled` a ensuite laissé place à l'ÉTAT produit par la politique du
    moteur : un booléen ne distinguait pas « détecteur exécuté, rien trouvé » de
    « aucun détecteur ». Les deux moteurs ne peuvent plus se décrire pareil.
    """
    commun = dict(
        language_requested=None, timestamps=True, diarized=True, profiled=True,
        input_pcm_sha256="a" * 64,
    )
    local = provenance.transcript_fingerprint(
        engine="whisper", model="large-v3", **_ARTEFACTS_CONTROLES, **commun)
    cloud = provenance.transcript_fingerprint(
        engine="mistral", model="voxtral-mini-latest",
        diarization_model="mistral:voxtral-mini-latest",
        profiling_model="mistral-small-latest",
        artifact_status="unchecked", artifact_policy="voxtral.non-mesure",
        artifact_policy_version=1, **commun)

    assert local["artifacts"]["status"] == "checked"
    assert local["artifacts"]["policy"] == "artifacts.scrub"
    assert cloud["artifacts"]["status"] == "unchecked", (
        "trois sorties propres ne prouvent pas qu'un moteur n'hallucine jamais")
    assert cloud["diarization"]["model"] == "mistral:voxtral-mini-latest"
    assert cloud["profiling"]["model"] == "mistral-small-latest"
    assert local["diarization"]["model"] != cloud["diarization"]["model"]
    assert local["fingerprint"] != cloud["fingerprint"], (
        "deux moteurs différents doivent produire deux empreintes différentes"
    )


def test_le_hash_d_implementation_couvre_le_chemin_cloud() -> None:
    """Une modification de `stt_mistral.py` doit déplacer l'empreinte.

    Ces fichiers manquaient des listes hachées : le chemin cloud pouvait changer
    sans que la provenance en garde trace.
    """
    for fichier in ("core/stt_mistral.py", "core/llm_mistral.py", "core/llm.py"):
        assert fichier in provenance._TRANSCRIPT_IMPLEMENTATION, fichier
    for fichier in ("core/llm_mistral.py", "core/llm.py"):
        assert fichier in provenance._REPORT_IMPLEMENTATION, fichier


# ---------- Motif nº1 : dériver de la RÉALITÉ ----------

def test_tout_fichier_declare_dans_la_provenance_EXISTE() -> None:
    """Le contrôle voisin vérifie l'APPARTENANCE de trois noms à la liste.

    Il ne vérifie jamais que la liste correspond au disque. Une faute de frappe
    — `core/artefacts.py` pour `core/artifacts.py` — passait donc, et
    `_implementation_sha256` rendait « unavailable » pour TOUS les transcripts
    sans qu'un test tombe : le fingerprint restait scellé, et deux versions du
    code produisaient la même empreinte.
    """
    from core import config

    declares = (provenance._TRANSCRIPT_IMPLEMENTATION
                + provenance._REPORT_IMPLEMENTATION)
    manquants = [rel for rel in declares if not (config.ROOT / rel).is_file()]
    assert manquants == [], (
        f"chemin(s) déclaré(s) mais absent(s) du disque : {manquants}")


def test_le_balayage_de_provenance_porte_sur_quelque_chose() -> None:
    """Ancre de non-vacuité — motif nº2."""
    assert len(provenance._TRANSCRIPT_IMPLEMENTATION) >= 10
    assert len(provenance._REPORT_IMPLEMENTATION) >= 5


def test_un_chemin_declare_INTROUVABLE_leve_au_lieu_de_se_taire() -> None:
    """Motif nº3 : aucune sortie de secours muette.

    `except OSError: return "unavailable"` avalait aussi le `FileNotFoundError`
    d'un chemin mal orthographié. On sépare : l'aléa d'E/S dégrade, la faute de
    programmation lève.
    """
    import pytest

    with pytest.raises(FileNotFoundError, match="déclaré mais introuvable"):
        provenance._implementation_sha256(("core/n_existe_pas_du_tout.py",))


def test_un_ALEA_d_E_S_degrade_toujours_sans_lever(monkeypatch) -> None:
    """Garde sur l'instrument : la séparation ne doit pas transformer une
    panne disque en plantage de transcription."""
    from pathlib import Path

    def refuse(self, *a, **k):
        raise PermissionError("disque refusé")

    monkeypatch.setattr(Path, "read_bytes", refuse)
    assert provenance._implementation_sha256(
        ("core/provenance.py",)) == "unavailable"


def test_le_front_matter_ne_contredit_pas_son_propre_fingerprint(tmp_path, monkeypatch) -> None:
    """Le front-matter et le fingerprint annoncent le MÊME modèle.

    `llm_model` de premier niveau portait encore `config.LLM_MODEL` après la
    première correction : un transcript profilé par Mistral annonçait Qwen au
    premier niveau et Mistral dans `pipeline.profiling.model`. Le document
    autoritatif se contredisait lui-même, et seul un test du FRONT-MATTER —
    pas du fingerprint — pouvait le voir.
    """
    from core import pipeline
    from core.stt_base import Segment, Transcript

    class FauxMoteur:
        name, model_name = "mistral", "voxtral-mini-latest"

        def transcribe(self, wav_path, **kw):
            return Transcript(segments=[Segment(0.0, 2.0, "bonjour", None)],
                              full_text="bonjour", language="fr")

    monkeypatch.setattr(pipeline, "get_engine", lambda *a, **k: FauxMoteur())
    monkeypatch.setattr(pipeline.media, "to_wav16k", lambda src, **k: src)
    monkeypatch.setattr(pipeline.media, "probe_duration", lambda src: 2.0)
    monkeypatch.setattr(pipeline.media, "pcm_sha256", lambda src: "f" * 64)
    monkeypatch.setattr(pipeline.media, "validate_input", lambda src: src)
    monkeypatch.setattr(pipeline, "_project_id_for_output", lambda d: "proj")
    from core import profiling
    monkeypatch.setattr(profiling, "profile", lambda *a, **k: {"speakers": {}})

    source = tmp_path / "a.wav"
    source.write_bytes(b"RIFF")
    sortie = tmp_path / "out"
    sortie.mkdir()
    _, contenu = pipeline.run(
        source, engine_name="mistral", llm_provider="mistral", profile_on=True,
        report_on=False, output_dir=sortie, timestamps=True)

    import yaml
    fm = yaml.safe_load(contenu.split("---")[1])
    niveau_un = fm.get("llm_model")
    dans_fingerprint = (fm.get("pipeline") or {}).get("profiling", {}).get("model")

    assert niveau_un == dans_fingerprint, (
        f"le document se contredit : llm_model={niveau_un!r} au premier niveau, "
        f"{dans_fingerprint!r} dans le fingerprint"
    )
    assert niveau_un != "qwen3.5:9b", "un profilage Mistral ne doit pas annoncer Qwen"


def test_un_COMPTE_RENDU_produit_par_MLX_annonce_le_modele_MLX(tmp_path, monkeypatch) -> None:
    """Le témoin symétrique du chemin TRANSCRIPT — et il manquait.

    Prouver le chemin TRANSCRIPT de bout en bout ne ferme pas la classe.
    Le compte-rendu, second document autoritatif, n'était gardé que par le
    contrôle STATIQUE — qui vérifie l'ABSENCE de constantes, pas la TRANSMISSION
    du bon fournisseur. Deux propriétés différentes.

    La mutation qui le montre :

        modele_reel = llm.modele_servi("ollama")   # au lieu de provider

    Elle respecte le garde AST, et elle rendait **1042 tests verts** — mesuré
    avant d'écrire ce test. La provenance fausse du CR MLX pouvait donc revenir
    sans qu'un contrôle bronche.
    """
    from core import config, library, llm, report

    source = tmp_path / "meeting.md"
    source.write_text("x", encoding="utf-8")
    monkeypatch.setattr(report.library, "transcript_path", lambda name: source)
    monkeypatch.setattr(
        report.library, "get_transcript",
        lambda name: {
            # Identité complète : le CR REFUSE une source sans les trois ULID,
            # et sans eux le test échouerait avant d'atteindre la provenance.
            "meta": {
                "id": docid.nouveau(),
                "meeting_id": docid.nouveau(),
                "project_id": docid.nouveau(),
                "title": "T", "date": "2026-01-01", "language": "en",
            },
            "body": "## Transcript\n\n**Speaker 1** [00:00:04] Hello.",
        },
    )
    monkeypatch.setattr(report.resources, "heavy", lambda **kw: nullcontext())
    monkeypatch.setattr(report.semantic, "index_file", lambda *a, **k: None)

    # Bouchonné sur la FAÇADE, et `modele_servi` laissé RÉEL : c'est lui l'objet
    # du test, tout le reste n'est que plomberie.
    #
    # Première version : bouchons posés sur `type(llm.MOTEURS["mlx"])`. Verte en
    # isolation, elle atteignait le VRAI serveur mlx_lm en suite complète —
    # `APIConnectionError` sur le port 8772. Un test dont le résultat dépend de
    # l'ordre d'exécution ne prouve rien de façon fiable, et celui-ci pouvait
    # DÉMARRER un serveur pendant `make check`. Viser la façade retire la
    # question de l'identité de classe.
    monkeypatch.setattr(llm, "is_available", lambda **k: True)
    monkeypatch.setattr(report, "classify_genre", lambda *a, **k: "generique")
    monkeypatch.setattr(report.writer_md, "unique_path", lambda *a, **k: tmp_path / "cr.md")
    monkeypatch.setattr(report.library, "sync_file", lambda path: None)

    # Une section par question du gabarit : le contrat de sortie les compte, et
    # en rendre moins fait lever avant qu'on ait pu regarder la provenance.
    attendues = len(report.templates.questions("generique", "en"))
    monkeypatch.setattr(
        llm, "chat_json",
        lambda *a, **k: {
            "sections": [
                {
                    "question_id": index, "answer_status": "answered", "title": f"Section {index}",
                    "claims": [{
                        "text": "The speaker says hello.",
                        "primary_evidence": {
                            "timestamp": "00:00:04", "quote": "Hello."},
                        "secondary_evidence": None,
                    }],
                }
                for index in range(1, attendues + 1)
            ]
        },
    )

    capture: dict = {}
    vrai_fingerprint = report.provenance.report_fingerprint

    def espion(**kwargs):
        capture.update(kwargs)
        return vrai_fingerprint(**kwargs)

    monkeypatch.setattr(report.provenance, "report_fingerprint", espion)

    resultat = report.make_report("meeting.md", provider="mlx")
    fm, _ = library._split_frontmatter(resultat["content"])

    assert fm["model"] == config.MLX_LLM_MODEL, (
        f"le CR annonce {fm['model']!r} alors que MLX a produit le texte"
    )
    assert capture["model"] == config.MLX_LLM_MODEL, "le fingerprint scellé ment"
    assert fm["model"] != config.LLM_MODEL, (
        "le compte-rendu MLX annonce le modèle Ollama — le fournisseur n'est "
        "plus transmis à la résolution"
    )


def test_un_transcript_profile_par_MLX_annonce_le_modele_MLX(tmp_path, monkeypatch) -> None:
    """Le même défaut, rouvert par le TROISIÈME moteur.

    La correction du test du front-matter ci-dessus était un `if provider in
    ("mistral", "cloud") ... else config.LLM_MODEL`. Un partage binaire ne
    s'étend pas : MLX tombait dans le `else`, et un transcript profilé par MLX
    annonçait `qwen3.5:9b` — au premier niveau ET dans le fingerprint scellé.

    Ce test est le pendant MLX du précédent. Il vaut par ce qu'il prouve que le
    contrôle statique ne peut pas prouver : la valeur qui atterrit réellement
    dans le document publié.
    """
    from core import config, pipeline, profiling
    from core.stt_base import Segment, Transcript

    class FauxMoteur:
        name, model_name = "whisper", "mlx-community/whisper-test"

        def transcribe(self, wav_path, **kw):
            return Transcript(segments=[Segment(0.0, 2.0, "bonjour", None)],
                              full_text="bonjour", language="fr")

    monkeypatch.setattr(pipeline, "get_engine", lambda *a, **k: FauxMoteur())
    monkeypatch.setattr(pipeline.media, "to_wav16k", lambda src, **k: src)
    monkeypatch.setattr(pipeline.media, "probe_duration", lambda src: 2.0)
    monkeypatch.setattr(pipeline.media, "pcm_sha256", lambda src: "f" * 64)
    monkeypatch.setattr(pipeline.media, "validate_input", lambda src: src)
    monkeypatch.setattr(pipeline, "_project_id_for_output", lambda d: "proj")
    monkeypatch.setattr(profiling, "profile", lambda *a, **k: {"speakers": {}})
    # Le déchargement dispatche sur le registre ; aucun serveur mlx ici.
    monkeypatch.setattr(pipeline.llm, "unload", lambda **k: False)

    source = tmp_path / "a.wav"
    source.write_bytes(b"RIFF")
    sortie = tmp_path / "out"
    sortie.mkdir()
    _, contenu = pipeline.run(
        source, engine_name="whisper", llm_provider="mlx", profile_on=True,
        report_on=False, output_dir=sortie, timestamps=True)

    import yaml
    fm = yaml.safe_load(contenu.split("---")[1])
    niveau_un = fm.get("llm_model")
    dans_fingerprint = (fm.get("pipeline") or {}).get("profiling", {}).get("model")

    assert niveau_un == dans_fingerprint, (
        f"le document se contredit : llm_model={niveau_un!r} au premier niveau, "
        f"{dans_fingerprint!r} dans le fingerprint"
    )
    assert niveau_un == config.MLX_LLM_MODEL
    assert niveau_un != config.LLM_MODEL, (
        "un profilage MLX annonce le modèle Ollama — le partage binaire est de "
        "retour"
    )
