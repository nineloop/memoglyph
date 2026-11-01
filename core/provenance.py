"""Provenance autoritaire des documents générés par Memoglyph.

Le Markdown est la source de vérité : les paramètres qui peuvent changer son
contenu doivent donc y être écrits au moment de la génération. Ce module ne lit
aucun secret et ne tente jamais de deviner la provenance des anciens fichiers.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from . import config, docid, report_citations
from .artifact_policy import ArtifactStatus

FINGERPRINT_SCHEMA_VERSION = 1
# 3 → 4 : le bloc `artifacts` ne porte plus un booléen `enabled` mais
# l'ÉTAT produit par la politique du moteur, avec son identifiant et sa version.
# Un document produit avant ne se compare pas à un après.
#
# 4 → 5 et 5 → 6 : le modèle LLM consigné était résolu par un partage
# BINAIRE `mistral / sinon qwen3.5:9b`, donc faux pour tout document produit par
# MLX — dans le fingerprint comme dans le front-matter. La résolution vient
# maintenant du moteur lui-même. Les empreintes calculées avant décrivent un
# modèle qui n'a pas forcément tourné : elles ne DOIVENT pas comparer égal.
TRANSCRIPT_PIPELINE_VERSION = 5
REPORT_PIPELINE_VERSION = 6

# Ces versions décrivent les contrats qui influencent le contenu. Elles doivent
# être incrémentées lorsqu'une modification sémantique ne ressort pas déjà des
# paramètres consignés (prompt, filtre, fusion…).
AUDIO_NORMALIZATION_VERSION = 1
# La version du filtre d'artefacts NE VIT PLUS ICI : elle est portée par la
# politique déclarée dans `core/stt.py` et transmise par l'appelant. Deux
# constantes pour la même chose, c'est deux occasions de diverger — et la
# précédente décrivait un filtre qui ne tournait plus depuis un mois.
DIARIZATION_MERGE_VERSION = 1
PROFILING_PROMPT_VERSION = 1
REPORT_PROMPT_VERSION = 4
GENRE_CLASSIFIER_VERSION = 1
# v2 : `analyse_sportive` passe de 5 à 8 rubriques (contexte,
# personnes, choix/affaires, méthode/style, héritage ajoutés ; « désaccord »
# retiré au profit des jugements). Le `questions_sha256` change de toute façon —
# la version le rend LISIBLE dans la provenance sans avoir à comparer des sha.
REPORT_TEMPLATE_VERSION = 2
SPEAKER_BINDING_GUARD_VERSION = 1
NUMBER_GUARD_VERSION = 1
CITATION_CONTRACT_VERSION = report_citations.CONTRACT_VERSION

_TRANSCRIPT_IMPLEMENTATION = (
    "core/artifact_policy.py",
    "core/artifacts.py",
    "core/config.py",
    "core/diarization.py",
    "core/llm.py",
    "core/llm_base.py",
    "core/llm_mistral.py",
    "core/llm_mlx.py",
    "core/media.py",
    "core/merge.py",
    "core/names.py",
    "core/pipeline.py",
    "core/profiling.py",
    "core/provenance.py",
    "core/stt.py",
    "core/stt_base.py",
    "core/stt_mistral.py",
    "core/stt_whisper.py",
    "core/writer_md.py",
)

_REPORT_IMPLEMENTATION = (
    "core/config.py",
    "core/factcheck.py",
    "core/llm.py",
    "core/llm_base.py",
    "core/llm_mistral.py",
    "core/llm_mlx.py",
    "core/names.py",
    "core/provenance.py",
    "core/quality.py",
    "core/report.py",
    "core/report_citations.py",
    "core/templates.py",
    "core/writer_md.py",
)

_SHA256 = re.compile(r"[0-9a-f]{64}")


def _application_version(root: Path | None = None) -> str:
    """Version déclarée dans pyproject, sans supposer un package installé."""
    try:
        raw = tomllib.loads(
            ((root or config.ROOT) / "pyproject.toml").read_text(encoding="utf-8")
        )
        value = raw["project"]["version"]
        return str(value) if value else "unknown"
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError):
        return "unknown"


def _git_state(root: Path | None = None) -> dict[str, str | bool | None]:
    """Révision et état suivi, sans publier le détail des fichiers modifiés."""
    cwd = root or config.ROOT
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        ).stdout.strip()
        status = subprocess.run(
            [
                "git", "status", "--porcelain", "--untracked-files=normal", "--",
                "core", "pyproject.toml", "uv.lock",
            ],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {"revision": "unknown", "dirty": None}
    if not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
        revision = "unknown"
    return {"revision": revision.lower(), "dirty": bool(status.strip())}


def _implementation_sha256(files: Sequence[str], root: Path | None = None) -> str:
    """Hash du code pertinent, y compris quand le worktree est `dirty`."""
    base = root or config.ROOT
    digest = hashlib.sha256()
    try:
        for relative in files:
            path = base / relative
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    except FileNotFoundError as exc:
        # Un chemin DÉCLARÉ qui n'existe pas est une faute de PROGRAMMATION,
        # pas un aléa d'E/S. Elle était avalée : une faute de frappe dans
        # `_TRANSCRIPT_IMPLEMENTATION` — `artefacts.py` pour `artifacts.py` —
        # rendait `implementation_sha256 = "unavailable"` pour TOUS les
        # transcripts, le fingerprint restait scellé, et deux versions
        # différentes du code produisaient la même empreinte. Exactement la
        # propriété que ce module vend.
        raise FileNotFoundError(
            f"fichier d'implémentation déclaré mais introuvable : {exc.filename}. "
            f"Corrigez la liste dans core/provenance.py."
        ) from exc
    except OSError:
        # Aléa d'E/S réel (permissions, disque) : dégradation assumée, et le
        # champ le DIT plutôt que de prétendre à une empreinte.
        return "unavailable"
    return digest.hexdigest()


def _canonical_sha256(value: object) -> str:
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _seal(payload: dict[str, Any]) -> dict[str, Any]:
    """Ajoute le SHA-256 canonique du bloc, sans dépendre de l'ordre YAML."""
    sealed = dict(payload)
    sealed["fingerprint"] = _canonical_sha256(sealed)
    return sealed


def _base(kind: str, version: int, files: Sequence[str]) -> dict[str, Any]:
    code = _git_state()
    code["implementation_sha256"] = _implementation_sha256(files)
    return {
        "schema_version": FINGERPRINT_SCHEMA_VERSION,
        "kind": kind,
        "pipeline_version": version,
        "application_version": _application_version(),
        "code": code,
    }


def transcript_fingerprint(
    *,
    engine: str,
    model: str | None,
    language_requested: str | None,
    timestamps: bool,
    diarized: bool,
    profiled: bool,
    input_pcm_sha256: str,
    artifact_status: str,
    artifact_policy: str,
    artifact_policy_version: int,
    derived_from_document_id: str | None = None,
    diarization_model: str | None = None,
    profiling_model: str | None = None,
) -> dict[str, Any]:
    """Fingerprint des réglages EFFECTIFS d'une nouvelle transcription.

    Ces paramètres existent parce que ce bloc décrivait des traitements qui
    n'avaient pas eu lieu :

    - `artifacts.enabled` valait toujours `True` alors que le filtre était parké
      et sa branche morte ;
    - la diarisation était toujours attribuée à `config.DIARIZATION_MODEL`, donc
      une diarisation native de Voxtral se décrivait comme pyannote ;
    - le profilage portait toujours `config.LLM_MODEL`, donc un profilage
      Mistral se décrivait comme Qwen local.

    Deux documents produits par des moteurs différents pouvaient ainsi annoncer
    la même provenance. L'appelant passe désormais ce qu'il a réellement
    exécuté.

    `artifact_status` n'a **pas de défaut**, et c'est délibéré : un défaut,
    optimiste ou pessimiste, laisserait un appelant décrire un contrôle sans y
    penser. Une omission est un `TypeError` bruyant. L'état est en outre validé
    contre l'ensemble fermé de `core/artifact_policy` — une chaîne inventée ne
    peut pas entrer dans la provenance.
    """
    try:
        artifact_status = ArtifactStatus(artifact_status).value
    except ValueError as exc:
        raise ValueError(
            f"État d'artefacts inconnu : {artifact_status!r}. Les quatre états "
            f"sont fermés : {', '.join(e.value for e in ArtifactStatus)}."
        ) from exc
    if not _SHA256.fullmatch(input_pcm_sha256):
        raise ValueError("Hash PCM SHA-256 invalide")
    if derived_from_document_id is not None and not docid.est_valide(
        derived_from_document_id
    ):
        raise ValueError("Identifiant du document source invalide")
    payload = _base("transcript", TRANSCRIPT_PIPELINE_VERSION, _TRANSCRIPT_IMPLEMENTATION)
    payload.update({
        "audio": {
            "normalization_version": AUDIO_NORMALIZATION_VERSION,
            "sample_rate_hz": config.SAMPLE_RATE,
            "channels": 1,
            "sample_format": "s16",
            "input_pcm_sha256": input_pcm_sha256,
        },
        "lineage": {"derived_from_document_id": derived_from_document_id},
        "stt": {
            "engine": engine,
            "model": model or "unknown",
            "language_requested": language_requested or "auto",
            "chunk_seconds": config.STT_CHUNK_SEC,
            "cut_search_seconds": config.STT_CUT_SEARCH_SEC,
            "timestamps": timestamps,
        },
        "artifacts": {
            # L'état FERMÉ, pas un booléen : « pas de rapport » et « rapport
            # vide » se lisaient pareil, c'est ce qui a laissé six génériques de
            # sous-titrage entrer dans le corpus sans qu'un document le dise.
            "status": artifact_status,
            "policy": artifact_policy,
            "policy_version": artifact_policy_version,
        },
        "diarization": {
            "enabled": diarized,
            "model": (diarization_model or config.DIARIZATION_MODEL) if diarized else None,
            "speaker_count_constraint": "auto",
            "merge_version": DIARIZATION_MERGE_VERSION,
        },
        "profiling": {
            "enabled": profiled,
            # `"unknown"` et non `config.LLM_MODEL`, comme la ligne `stt` plus
            # haut. Ce repli codait en dur le modèle Ollama comme valeur
            # d'INCONNU : un appelant qui oublierait de passer son modèle
            # publiait `qwen3.5:9b` dans un fingerprint scellé, quel que soit le
            # moteur ayant tourné. Latent — le pipeline le passe toujours — mais
            # c'était un mensonge qui attendait son appelant, et il n'a rien à
            # faire dans une empreinte qui prétend décrire ce qui a eu lieu.
            "model": (profiling_model or "unknown") if profiled else None,
            "prompt_version": PROFILING_PROMPT_VERSION,
            "context_policy": "dynamic_v1",
            "temperature": 0.2,
            "seed": None,
        },
    })
    return _seal(payload)


def _source_pipeline(source: object) -> dict[str, str | None]:
    if not isinstance(source, Mapping):
        return {"status": "legacy_unknown", "pipeline_fingerprint": None}
    value = source.get("fingerprint")
    payload = {key: item for key, item in source.items() if key != "fingerprint"}
    if (
        isinstance(value, str)
        and _SHA256.fullmatch(value)
        and value == _canonical_sha256(payload)
    ):
        return {"status": "fingerprinted", "pipeline_fingerprint": value}
    return {"status": "invalid", "pipeline_fingerprint": None}


def report_fingerprint(
    *,
    source_pipeline: object,
    model: str,
    language: str | None,
    genre: str,
    num_ctx: int,
    questions: Sequence[str],
) -> dict[str, Any]:
    """Fingerprint du CR et liaison vers la génération de son transcript."""
    payload = _base("report", REPORT_PIPELINE_VERSION, _REPORT_IMPLEMENTATION)
    payload.update({
        "source": _source_pipeline(source_pipeline),
        "llm": {
            "model": model,
            "language": language or "auto",
            "num_ctx": num_ctx,
            "temperature": 0.2,
            "think": False,
            "seed": None,
        },
        "template": {
            "genre": genre,
            "template_version": REPORT_TEMPLATE_VERSION,
            "prompt_version": REPORT_PROMPT_VERSION,
            "classifier_version": GENRE_CLASSIFIER_VERSION,
            "questions_sha256": _canonical_sha256(list(questions)),
        },
        "guards": {
            "speaker_bindings_version": SPEAKER_BINDING_GUARD_VERSION,
            "number_guard_version": NUMBER_GUARD_VERSION,
            "number_guard_enabled": config.FACTCHECK_NUMBERS,
            "citation_contract_version": CITATION_CONTRACT_VERSION,
        },
    })
    return _seal(payload)
