"""Dérivation de COÛT (FinOps) — pur, sans I/O, déterministe.

Le coût n'est JAMAIS une donnée première : c'est `f(usage mesuré, barème)`. Ces
fonctions ne lisent aucune base ; elles prennent des faits + un barème (`PricingSnapshot`)
et rendent un montant — ou `None` quand le barème manque (coût INCONNU, jamais un faux zéro).

## Règles de non-double-comptage (fondées sur les usages réels du soak initial)

- **OpenAI (Responses)** : `output_tokens` INCLUT DÉJÀ les jetons de raisonnement
  (`reasoning_tokens` en est un sous-ensemble). On facture donc `output_tokens` au tarif de
  sortie — le raisonnement N'EST JAMAIS ajouté une seconde fois. `reasoning_tok` reste stocké
  pour la VISIBILITÉ, pas pour le calcul.
- **Jetons en cache** : `cached_tokens` est un SOUS-ENSEMBLE de `input_tokens`, facturé à un
  tarif réduit. Facturable_entrée = `input - cached` ; le cache est facturé au tarif cache
  (à défaut, au tarif d'entrée). On ne compte donc jamais un jeton d'entrée deux fois.

## Base de coût — vérité d'usage ≠ vérité de coût

Un usage fournisseur peut être MESURÉ (réel), mais un coût DÉRIVÉ d'un barème (grille
tarifaire) n'est PAS un coût facturé réconcilié sur facture. On nomme donc la base du coût
d'après ce qu'elle EST :

`cloud_billed` → `cloud_derived` (coût DÉRIVÉ du prix catalogue du fournisseur, pas
                 réconcilié sur facture — la réconciliation facture serait une autre base,
                 non introduite ici).
`local_imputed` → `local_imputed` (rendu local ; l'argent est IMPUTÉ, jamais « actual »).

Les deux ne se mélangent jamais dans un même total ; les devises non plus.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Tarifs de jetons exprimés PAR MILLION de jetons (comme les grilles publiées) ; le coût
# divise par ce dénominateur. Les tarifs d'UNITÉ (ex. audio_second) s'appliquent directement
# à la quantité, sans dénominateur.
TOKEN_RATE_DENOMINATOR = 1_000_000

TOKEN_UNIT = "token"

# Unités NON-jeton connues, exprimées en secondes (la quantité mesurée est en secondes).
# Un tarif audio se consigne dans son unité NATIVE (ex. Voxtral facture par MINUTE) ; l'appelant
# convertit la durée mesurée vers l'unité du barème. On n'invente aucune conversion : facteurs exacts.
AUDIO_UNIT_SECONDS = {"audio_second": 1.0, "audio_minute": 60.0}

# Coût DÉRIVÉ d'un prix catalogue — jamais présenté comme « actual » facturé. Une base
# `invoice_reconciled` (réconciliée sur facture) serait distincte et n'est PAS introduite ici.
BASIS_CLOUD_DERIVED = "cloud_derived"
BASIS_LOCAL_IMPUTED = "local_imputed"


@dataclass(frozen=True)
class PricingSnapshot:
    """Vue normalisée d'une ligne `pricing_snapshot`. Immuable."""

    pricing_snapshot_id: Optional[str]
    provider: str
    model: str
    kind: str                      # cloud_billed | local_imputed
    unit: str                      # token | audio_second | …
    currency: Optional[str] = None
    input_rate: Optional[float] = None    # par 1e6 jetons
    output_rate: Optional[float] = None   # par 1e6 jetons (inclut le raisonnement)
    cached_rate: Optional[float] = None   # par 1e6 jetons (défaut : tarif d'entrée)
    unit_rate: Optional[float] = None     # par unité non-jeton (ex. par seconde audio)


def cost_basis_for(kind: str) -> str:
    """Base EXPLICITE : coût nuage DÉRIVÉ d'un catalogue (pas facturé-réconcilié), local IMPUTÉ."""
    return BASIS_CLOUD_DERIVED if kind == "cloud_billed" else BASIS_LOCAL_IMPUTED


def token_cost(
    *,
    in_tok: Optional[int],
    out_tok: Optional[int],
    cached_tok: Optional[int],
    snap: Optional[PricingSnapshot],
) -> Optional[float]:
    """Coût jetons = f(entrée facturable, cache, sortie). `None` si barème absent/incomplet.

    `out_tok` inclut déjà le raisonnement : on ne l'ajoute pas séparément.
    `cached_tok ⊂ in_tok` : on ne facture l'entrée pleine que sur la part non cachée.
    """
    if snap is None or snap.unit != TOKEN_UNIT:
        return None
    if snap.input_rate is None or snap.output_rate is None:
        return None
    inp = in_tok or 0
    out = out_tok or 0
    cached = cached_tok or 0
    billable_in = inp - cached
    if billable_in < 0:            # garde-fou : cached ne peut excéder l'entrée
        billable_in, cached = inp, 0
    cached_rate = snap.cached_rate if snap.cached_rate is not None else snap.input_rate
    total = billable_in * snap.input_rate + cached * cached_rate + out * snap.output_rate
    return total / TOKEN_RATE_DENOMINATOR


def unit_cost(
    *, quantity: Optional[float], snap: Optional[PricingSnapshot]
) -> Optional[float]:
    """Coût d'une unité NON-jeton (ex. seconde audio pour Whisper). `None` si absent.

    N'invente aucune sémantique de jeton : ne s'applique qu'à un barème dont l'unité
    n'est pas `token` et qui porte un `unit_rate`.
    """
    if snap is None or snap.unit == TOKEN_UNIT:
        return None
    if snap.unit_rate is None or quantity is None:
        return None
    return float(quantity) * snap.unit_rate
