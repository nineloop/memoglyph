"""Profil d'EXÉCUTION du pipeline riche — mêmes CONTRATS, exécution adaptée.

Le contrat produit (unités atomiques validées, strates MAIN/SECONDARY/CLOSING/
NOISE, rétention non destructive, interprétation B4, gardes, normalisation
d'horodatage, provenance, contrat UI/stockage) est IDENTIQUE pour tous les
moteurs. Seule la POLITIQUE D'EXÉCUTION varie : taille de tranche, granularité
d'extraction, taille de lot de classification, granularité de rendu, plafonds de
tokens. Ces réglages n'altèrent aucun contrat — ils règlent le nombre d'appels.

Résolution par CAPACITÉ, pas par `provider ==` : un moteur LOCAL (petit modèle
Qwen, contexte modeste, JSON parfois malformé) reçoit le profil PRUDENT (valeurs
actuelles, inchangées) ; un moteur DISTANT/cloud (grand contexte, sortie
structurée fiable — Mistral aujourd'hui) reçoit un profil GRAND-CONTEXTE (moins
d'appels, plus gros). Le discriminant est `llm.est_local` — une capacité
existante (local vs distant), jamais une comparaison de nom de fournisseur.
"""
from __future__ import annotations

from dataclasses import dataclass

from core import llm


@dataclass(frozen=True)
class ExecProfile:
    """Réglages d'exécution (aucun impact sur les contrats). Voir module."""
    name: str
    chunk_tokens: int          # cible d'entrée par tranche (chunking)
    extract_max_tokens: int    # borne de sortie de l'extraction atomique
    classify_batch: int        # unités par appel de classification
    classify_max_tokens: int
    structure_max_tokens: int  # B4 (thèse/tensions) — 1 appel de toute façon
    render_batch: int          # unités par appel de rendu (dans une rubrique)
    render_max_tokens: int


# Profil PRUDENT = comportement ACTUEL, à l'octet près. C'est celui du chemin
# LOCAL (Qwen) de production : ne JAMAIS le modifier sans le vouloir.
#
# `extract_max_tokens` : 5 000 depuis les types `decision`, `action` et `open_question`.
# À 3 500, des tranches denses de ~2 040 jetons d'entrée étaient coupées (sortie
# tronquée, tranche perdue). Marge = jetons de sortie permis par jeton d'entrée
# (`extract_max_tokens / chunk_tokens`) : 2,5, au-dessus de la densité des tranches les
# plus denses ; le profil cloud garde la même.
CONSERVATIVE = ExecProfile(
    name="conservative",
    chunk_tokens=2000,
    extract_max_tokens=5000,
    classify_batch=10,
    classify_max_tokens=800,
    structure_max_tokens=1200,
    render_batch=12,
    render_max_tokens=1500,
)

# Profil GRAND-CONTEXTE pour moteurs cloud (Mistral) :
#   - `chunk_tokens` reste à 2000 : agrandir les tranches fait CONSOLIDER Mistral à
#     l'extraction → moins d'unités → perte de rétention.
#   - `classify_batch` reste à 10 : mistral-small échoue par intermittence le JSON
#     de classification (repli déterministe → `other`/SECONDARY) ; un gros lot
#     AMPLIFIE le rayon d'impact d'un échec (MAIN→SECONDARY). On le garde borné.
#   - SEUL le RENDU est regroupé (`render_batch` 12→40) : le rendu est POSTÉRIEUR à
#     la classification, il ne peut pas déplacer d'unité entre strates — gain
#     d'appels neutre en contenu/strates (rejets de garde inchangés).
# Extraction et classification (les gros postes) restent bloqués tant que la
# fiabilité JSON de classification et le couplage extraction/tranche ne sont pas
# traités (hors périmètre : prompts/modèle). Aucun basculement production ici.
LARGE_CONTEXT = ExecProfile(
    name="large_context",
    chunk_tokens=2000,          # = prudent : rétention d'extraction préservée
    extract_max_tokens=5000,    # = prudent : même marge de sortie par jeton d'entrée
    classify_batch=10,          # = prudent : borne le rayon d'un échec JSON
    classify_max_tokens=800,    # = prudent
    structure_max_tokens=1200,  # = prudent (1 appel de toute façon)
    render_batch=40,            # SEUL changement : moins d'appels de rendu (neutre)
    render_max_tokens=6000,     # relâché pour ne pas tronquer un gros lot de rendu
)


def resolve(provider: str) -> ExecProfile:
    """Profil d'exécution pour un fournisseur, PAR CAPACITÉ (local vs distant).

    Local (Qwen) → prudent (inchangé). Distant/cloud (Mistral, et tout futur
    moteur cloud) → grand-contexte. Aucun `provider ==` : discrimine sur
    `llm.est_local`, capacité déjà exposée par la façade.
    """
    # `llm.profil_prudent` : local, ou moteur de capacité inconnue (serveur compatible).
    return CONSERVATIVE if llm.profil_prudent(provider) else LARGE_CONTEXT
