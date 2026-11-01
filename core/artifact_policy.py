"""Politique d'artefacts : ce qu'un moteur STT déclare avoir CONTRÔLÉ.

## Pourquoi ce module existe

L'appel central à `artifacts.scrub()` avait été remplacé par un crochet
optionnel par moteur, `transcribe_with_artefacts`. Aucun moteur ne
l'a jamais implémenté : la branche était morte, et son `else` fabriquait un
`Rapport([], [], [])` **indistinguable d'un détecteur qui a tourné sans rien
trouver**. Observé : six « Sous-titrage Société
Radio-Canada » consécutifs en milieu de fichier, et l'un d'eux était entré dans
l'index de recherche pendant que le document se disait propre.

Règle : le remède ne touche pas à
`STTEngine.transcribe` — les moteurs tiers ne doivent pas casser. C'est la
**déclaration d'intégration** qui devient obligatoire : chaque moteur est
enregistré dans `core/stt.py` avec une politique, et le pipeline fait toujours
`transcribe()` puis `apply_policy()` puis publication.

## Pourquoi PAS de politique no-op héritée

Une classe de base avec une implémentation no-op a été écartée :
`STTEngine` est un `Protocol`, pas une classe mère, et
un no-op ferait passer automatiquement tout moteur qui ne déclare précisément
RIEN — exactement l'inverse de la décision. Ici, un moteur sans politique est
refusé par la fabrique avant tout chargement.

## Les quatre états sont FERMÉS

`checked`, `not_applicable`, `unchecked`, `failed`. Aucun cinquième, et surtout
aucun « silence ». `not_applicable` n'a volontairement **aucune politique** qui
le produise aujourd'hui : ce serait une sur-affirmation. Zéro artefact sur trois
sorties Voxtral ne prouve pas qu'un moteur n'en produit jamais ; l'état honnête
est `unchecked` avec la mesure jointe. Écrire dès maintenant une politique
« sans objet » inutilisée recréerait la branche morte qu'on supprime.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .artifacts import Rapport, scrub
from .stt_base import Transcript


class ArtifactStatus(str, Enum):
    """Les quatre états publiables. Aucun autre n'est valide."""

    CHECKED = "checked"           # détecteur exécuté, rapport éventuellement vide
    NOT_APPLICABLE = "not_applicable"  # absence établie CONTRACTUELLEMENT
    UNCHECKED = "unchecked"       # politique passe-partout choisie, avec raison
    FAILED = "failed"             # détecteur prévu, mais en échec


@dataclass(frozen=True)
class ArtifactAssessment:
    """Ce qui a été contrôlé, par quoi, et avec quel résultat.

    Un état invalide est refusé À LA CONSTRUCTION plutôt qu'écrit dans le `.md` :
    c'est le seul moment où l'on sait encore que c'est une faute de
    programmation et non une observation.
    """

    status: ArtifactStatus
    policy_id: str
    policy_version: int
    rapport: Rapport | None = None
    reason: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        # L'ensemble est FERMÉ, et c'est ici qu'il se ferme : une chaîne hors
        # des quatre états lève au lieu d'être écrite dans le `.md`. Sans cette
        # ligne, `status="probablement propre"` traverserait tout le pipeline.
        object.__setattr__(self, "status", ArtifactStatus(self.status))
        # L'AUTRE frontière, l'identifiant. Rien n'oblige un `apply()`
        # à recopier l'identifiant de la politique enregistrée : fermer la
        # fabrique seule laisserait passer une moitié, comme pour la version.
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise ValueError(
                f"identifiant de politique invalide ({self.policy_id!r}) : une "
                "évaluation doit nommer sa politique par du TEXTE. Le "
                "front-matter publierait sinon `artifact_policy: 123`, un "
                "nombre là où la provenance attend un nom.")
        if self.status is ArtifactStatus.CHECKED and self.rapport is None:
            raise ValueError(
                "`checked` sans rapport : un détecteur qui a tourné rend un "
                "inventaire, fût-il vide"
            )
        if self.status in (ArtifactStatus.UNCHECKED, ArtifactStatus.NOT_APPLICABLE):
            if not (self.reason or "").strip():
                raise ValueError(
                    f"« {self.status.value} » sans raison : c'est précisément le "
                    "silence que ce contrat supprime"
                )
        if self.status is ArtifactStatus.FAILED and not (self.error or "").strip():
            raise ValueError("« failed » sans erreur : l'échec doit être lisible")

        # La frontière de PUBLICATION.
        #
        # `_exiger_une_politique` ferme l'ENREGISTREMENT, et c'est la moitié du
        # chemin : rien n'oblige un `apply()` à recopier les métadonnées de la
        # politique enregistrée. Une version en chaîne atteignait donc le
        # front-matter et la provenance, dont les annotations `int` n'agissent
        # pas à l'exécution.
        #
        # `bool` est exclu explicitement : `isinstance(True, int)` vaut vrai en
        # Python, et `policy_version: true` s'écrirait dans un champ que le
        # lecteur attend numérique.
        version = self.policy_version
        valide = isinstance(version, int) and not isinstance(version, bool)
        if self.status is ArtifactStatus.FAILED:
            # Zéro est AUTORISÉ ici, et seulement ici. `_version_sure` le rend
            # quand la version d'une politique est illisible — précisément pour
            # qu'une politique structurellement cassée publie quand même son
            # rapport d'échec. L'interdire ferait lever le chemin qui existe
            # pour que le transcript SURVIVE à la panne : ce serait le défaut
            # du transcript perdu à la panne, repris par l'autre bout.
            #
            # « 0 » sur un `failed` se lit « version non établie », ce qui est
            # vrai. Le même « 0 » sur un `checked` mentirait.
            if not valide or version < 0:
                raise ValueError(
                    f"version de politique invalide ({version!r}) : un échec "
                    "publie un entier ≥ 0, zéro signifiant « non établie »")
        elif not valide or version < 1:
            raise ValueError(
                f"version de politique invalide ({version!r}) : « "
                f"{self.status.value} » exige un entier ≥ 1. Elle voyage dans "
                "la provenance et sert à savoir quel détecteur a vu quel "
                "document.")

    def front_matter(self) -> dict[str, object]:
        """Les clés à fusionner dans le front-matter du `.md`.

        Les valeurs sont des types NUS, jamais l'énumération : `yaml.safe_dump`
        refuse une sous-classe de `str` (il indexe ses représentants sur le type
        EXACT), et le front-matter serait perdu à l'écriture.

        `artifact_status` est toujours présent — c'est tout l'objet du contrat.
        Les autres clés valent `None` quand elles n'ont pas de sens, et
        `writer_md` omet les `None` : un document ne prétend rien qu'il ne sache.
        """
        rapport = self.rapport
        return {
            "artifact_status": self.status.value,
            "artifact_policy": self.policy_id,
            "artifact_policy_version": self.policy_version,
            "artifact_reason": self.reason,
            "artifact_error": self.error,
            "repetition_loops": (rapport.boucles or None) if rapport else None,
            "removed_credits": (rapport.generiques or None) if rapport else None,
            "repeated_series": (rapport.series or None) if rapport else None,
        }


class ArtifactPolicy(Protocol):
    """Contrat d'une politique. `apply` ne doit jamais perdre de parole."""

    id: str
    version: int

    def apply(self, transcript: Transcript) -> tuple[Transcript, ArtifactAssessment]:
        ...


@dataclass(frozen=True)
class ScrubPolicy:
    """Politique `checked` : exécute le détecteur déterministe de `artifacts`.

    Calibré sur les hallucinations de Whisper (Koenecke et al., FAccT 2024) et
    sur 22 transcripts du corpus. Il replie les boucles en laissant leur marque
    et retire les seuls génériques de sous-titrage — rien ne disparaît en
    silence, tout est consigné dans le front-matter.
    """

    id: str
    version: int

    def apply(self, transcript: Transcript) -> tuple[Transcript, ArtifactAssessment]:
        segments, rapport = scrub(transcript.segments)
        modifie = len(segments) != len(transcript.segments) or any(
            neuf.text != ancien.text
            for neuf, ancien in zip(segments, transcript.segments)
        )
        if modifie:
            # `full_text` doit suivre le corps : il alimente le profilage et le
            # compte-rendu. Le laisser intact republierait le générique retiré
            # dans le prompt du LLM, donc potentiellement dans le CR.
            transcript = Transcript(
                segments=segments,
                full_text=" ".join(s.text for s in segments).strip(),
                language=transcript.language,
            )
        return transcript, ArtifactAssessment(
            status=ArtifactStatus.CHECKED,
            policy_id=self.id,
            policy_version=self.version,
            rapport=rapport,
        )


@dataclass(frozen=True)
class UncheckedPolicy:
    """Politique `unchecked` : aucun détecteur, et le document le DIT.

    Le seul choix honnête pour un moteur dont on n'a pas mesuré les artefacts.
    La raison est OBLIGATOIRE : « unchecked » sans justification redeviendrait
    du silence, avec une étiquette dessus.
    """

    id: str
    version: int
    reason: str

    def __post_init__(self) -> None:
        if not (self.reason or "").strip():
            raise ValueError(
                "une politique passe-partout doit porter la raison de son choix"
            )

    def apply(self, transcript: Transcript) -> tuple[Transcript, ArtifactAssessment]:
        return transcript, ArtifactAssessment(
            status=ArtifactStatus.UNCHECKED,
            policy_id=self.id,
            policy_version=self.version,
            reason=self.reason,
        )


# Un jeton (clé d'API, identifiant long) n'a rien à faire dans un `.md` que
# l'utilisateur partagera. Le détecteur d'aujourd'hui est local et n'en voit
# aucun, mais une politique propre à un moteur distant en verrait — et l'erreur
# est publiée, pas journalisée.
_JETON = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")
_LIMITE_ERREUR = 300


def _assainir(exc: BaseException) -> str:
    """Type + message borné, jetons effacés. Jamais la pile."""
    texte = f"{type(exc).__name__}: {exc}".strip()
    texte = _JETON.sub("***", texte)
    return texte[:_LIMITE_ERREUR] + ("…" if len(texte) > _LIMITE_ERREUR else "")


def _version_sure(policy: object) -> int:
    """La version d'une politique, sans jamais lever.

    Ce chemin sert le RAPPORT D'ÉCHEC. Y convertir sans filet faisait tomber le
    rapport lui-même quand la politique était mal déclarée — le transcript ne
    survivait alors pas, contrairement au contrat.

    `_exiger_une_politique` refuse désormais ces politiques à l'enregistrement,
    donc ce repli ne devrait jamais servir. Il reste parce qu'un chemin dont le
    métier est de rapporter une panne ne doit pas dépendre de la bonne santé de
    ce qui est tombé.
    """
    version = getattr(policy, "version", None)
    return version if isinstance(version, int) and not isinstance(version, bool) else 0


def apply_policy(
    policy: ArtifactPolicy, transcript: Transcript
) -> tuple[Transcript, ArtifactAssessment]:
    """Exécute la politique DÉCLARÉE et rend toujours une évaluation.

    Deux natures d'échec, à ne pas confondre :

    - **politique absente** — faute de contrat, traitée par `core/stt.py` avant
      qu'un octet d'audio ne soit lu ;
    - **échec d'exécution** — ici. Le transcript SURVIT et son `.md` porte
      `artifact_status: failed` avec l'erreur assainie. Annuler une heure de
      transcription pour un détecteur secondaire irait contre « rien ne
      disparaît en silence » ; prétendre `checked` serait pire.
    """
    try:
        return policy.apply(transcript)
    except Exception as exc:  # noqa: BLE001 — un détecteur en panne ne perd pas la parole
        return transcript, ArtifactAssessment(
            status=ArtifactStatus.FAILED,
            # `getattr` : un objet mal formé ne doit pas faire échouer le
            # rapport d'échec lui-même.
            policy_id=str(getattr(policy, "id", None) or "inconnue"),
            policy_version=_version_sure(policy),
            error=_assainir(exc),
        )
