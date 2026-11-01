"""Manifeste de tarifs OFFICIELS ratifiés (FinOps phase 1B) — données, provenancées.

Ce module ne fait RIEN au chargement : c'est un manifeste. `usage_store.bootstrap_official_pricing`
l'importe EXPLICITEMENT (jamais automatiquement). Une `usage.db` neuve reste donc vide.

## Règle dure (phase 1B)

Un tarif n'entre ici QUE si le modèle CONFIGURÉ par Memoglyph se rattache SANS AMBIGUÏTÉ à un
tarif officiel publié. Un nom fictif, un alias flottant (`-latest`), ou un nom qui ne se mappe
pas de façon univoque à un modèle tarifé restent NON tarifés — coût NULL — et sont consignés
dans `RATIFICATION` avec la raison. On n'invente aucun tarif et on ne substitue aucun modèle.

Tarifs de jetons exprimés PAR MILLION de jetons (comme les grilles publiées).
"""
from __future__ import annotations

# Date de relevé des tarifs ci-dessous (source officielle consultée ce jour-là).
RETRIEVED_AT = "2026-08-09"

# --- Tarifs ratifiés (importés seulement sur action explicite) ----------------
OFFICIAL_RATES = [
    {
        # Modèle CONFIGURÉ par Memoglyph pour les CR OpenAI (config.OPENAI_CHAT_MODEL),
        # rattaché sans ambiguïté au modèle officiel du même nom exact.
        "provider": "openai",
        "model": "gpt-5.6-luna",
        "kind": "cloud_billed",
        "unit": "token",
        "currency": "USD",
        "input_rate": 0.20,      # $/1M jetons d'entrée
        "output_rate": 1.20,     # $/1M jetons de sortie (inclut le raisonnement, cf. note)
        "cached_rate": 0.02,     # $/1M jetons d'entrée EN CACHE (lecture, -90 %)
        "rate_source": "official_published",
        "source_url": "https://developers.openai.com/api/docs/models/gpt-5.6-luna",
        "retrieved_at": RETRIEVED_AT,
        "source": ("OpenAI Responses API : output_tokens INCLUT les jetons de raisonnement "
                   "(facturés au tarif de sortie, pas de double compte) ; cached_tokens = "
                   "lecture de cache d'entrée, tarif réduit."),
    },
    {
        # Modèle STT CONFIGURÉ par Memoglyph (config.MISTRAL_STT_MODEL) : identifiant DÉJÀ
        # version-épinglé (v26.02, publié le 04/02/2026), rattaché sans ambiguïté au modèle
        # officiel « Voxtral Mini Transcribe 2 » de MÊME id. Aucune modif de config nécessaire.
        "provider": "mistral",
        "model": "voxtral-mini-2602",
        "kind": "cloud_billed",
        "unit": "audio_minute",   # facturation NATIVE à la minute d'audio
        "currency": "USD",
        "unit_rate": 0.003,       # $/minute d'audio
        "rate_source": "official_published",
        "source_url": "https://docs.mistral.ai/models/voxtral-mini-transcribe-26-02",
        "retrieved_at": RETRIEVED_AT,
        "source": ("Voxtral Mini Transcribe 2 — modèle version-épinglé (v26.02), batch via "
                   "/v1/audio/transcriptions, 0,003 $/minute d'audio. DISTINCT de la variante "
                   "« Realtime » (0,006 $/min, autre id). Coût = durée fractionnaire × tarif "
                   "(cloud_derived, non réconcilié facture ; un éventuel arrondi fournisseur "
                   "n'est pas confirmé par la doc)."),
    },
    {
        # Identité de TARIFICATION du chat Mistral (voir OFFICIAL_ALIASES) : version épinglée
        # « Mistral Small 4 » (v26.03). Ce n'est PAS le nom configuré (alias `-latest`) — la
        # télémétrie garde l'alias, la tarification résout vers cette identité datée.
        "provider": "mistral",
        "model": "mistral-small-2603",
        "kind": "cloud_billed",
        "unit": "token",
        "currency": "USD",
        "input_rate": 0.15,       # $/1M jetons d'entrée
        "output_rate": 0.60,      # $/1M jetons de sortie
        # Pas de cached_rate : la grille Mistral Small n'expose aucun tarif de cache.
        "rate_source": "official_published",
        "source_url": "https://docs.mistral.ai/models/model-cards/mistral-small-4-0-26-03",
        "retrieved_at": RETRIEVED_AT,
        "source": ("Mistral Small 4 (v26.03) = mistral-small-2603 : 0,15/0,60 $/1M (in/out), USD. "
                   "Aucun tarif de cache publié. Pas de facturation séparée du raisonnement : "
                   "l'usage Mistral ne distingue que prompt/completion (facturés in/out)."),
    },
]

# Relevé des tarifs Anthropic (même source officielle, consultée ce jour-là).
ANTHROPIC_RETRIEVED_AT = "2026-10-25"
_SOURCE_ANTHROPIC = "https://platform.claude.com/docs/en/about-claude/pricing"

OFFICIAL_RATES += [
    {
        # Modèle par défaut du moteur Anthropic (config.ANTHROPIC_CHAT_MODEL), nom exact.
        "provider": "anthropic",
        "model": "claude-haiku-5-5",
        "kind": "cloud_billed",
        "unit": "token",
        "currency": "USD",
        "input_rate": 0.10,      # $/1M jetons d'entrée (requête jusqu'à 100 000 jetons)
        "output_rate": 0.50,     # $/1M jetons de sortie (réflexion comprise)
        "cached_rate": 0.01,     # $/1M jetons lus en cache
        "rate_source": "official_published",
        "source_url": _SOURCE_ANTHROPIC,
        "retrieved_at": ANTHROPIC_RETRIEVED_AT,
        "source": ("Claude Haiku 5.5, requêtes jusqu'à 100 000 jetons d'entrée : 0,10/0,50 $/1M "
                   "(in/out), cache lu 0,01 $/1M. Au-delà de 100 000 jetons, la requête entière "
                   "est facturée 0,50/2,50 $/1M (cache 0,05) : ce palier n'est pas modélisé ici, "
                   "le coût calculé d'une telle requête est donc sous-estimé."),
    },
    {
        "provider": "anthropic",
        "model": "claude-sonnet-5-5",
        "kind": "cloud_billed",
        "unit": "token",
        "currency": "USD",
        "input_rate": 2.00,
        "output_rate": 10.00,
        "cached_rate": 0.10,     # 0,05 × le tarif d'entrée sur Sonnet 5.5
        "rate_source": "official_published",
        "source_url": _SOURCE_ANTHROPIC,
        "retrieved_at": ANTHROPIC_RETRIEVED_AT,
        "source": ("Claude Sonnet 5.5 : 2/10 $/1M (in/out), cache lu 0,10 $/1M, même tarif sur "
                   "toute la fenêtre de 1M jetons."),
    },
]

# --- Tarifs À CONFIRMER (jamais importés) -------------------------------------
# Même forme qu'une entrée d'`OFFICIAL_RATES`, mais le tarif n'a PAS été relevé sur la
# source officielle : `retrieved_at` reste vide, et `usage_store` ne lit pas cette liste.
# Le coût du modèle reste donc NULL tant que l'entrée n'est pas promue. Promouvoir =
# consulter `source_url`, corriger les taux si besoin, renseigner `retrieved_at`, puis
# déplacer l'entrée dans `OFFICIAL_RATES` et passer sa ligne `RATIFICATION` à ratifiée.
# Un tarif importé est une preuve DATÉE et versionnée, jamais écrasée : importer une
# valeur non relevée l'aurait gravée dans chaque `usage.db`.
RATES_TO_CONFIRM = [
    {
        # Modèle de chat Mistral par défaut (config.MISTRAL_CHAT_MODEL_DEFAUT), id déjà
        # version-épinglé (Ministral 3 14B, v25.12).
        "provider": "mistral",
        "model": "ministral-14b-2512",
        "kind": "cloud_billed",
        "unit": "token",
        "currency": "USD",
        "input_rate": 0.20,       # $/1M jetons d'entrée (À CONFIRMER)
        "output_rate": 0.20,      # $/1M jetons de sortie (À CONFIRMER)
        "rate_source": "official_published",
        "source_url": "https://mistral.ai/pricing#api-pricing",
        "retrieved_at": None,
        "source": ("À CONFIRMER : 0,20/0,20 $/1M (in/out) est le tarif annoncé pour Ministral "
                   "3 14B à sa sortie, repris sans consulter la grille (pas d'accès réseau "
                   "lors de l'ajout). Aucun tarif de cache connu."),
    },
]

# --- Alias AUDITÉS : modèle OBSERVÉ -> identité de TARIFICATION ----------------
# DÉCISION PRODUIT (suivi Phase 1C) : pour la comptabilité, l'alias `mistral-small-latest` est
# résolu vers l'identité datée `mistral-small-2603`. La télémétrie continue d'enregistrer l'alias
# tel quel ; SEULE la tarification résout. Mapping EXACT — aucun autre modèle n'est concerné.
OFFICIAL_ALIASES = [
    {
        "provider": "mistral",
        "observed_model": "mistral-small-latest",
        "pricing_model": "mistral-small-2603",
        # Date PASSÉE explicite : le mapping vaut « à partir du relevé » — il couvre l'usage déjà
        # enregistré (télémétrie née ce jour-là), et un futur RE-CIBLAGE daté plus tard ne peut
        # PAS le supplanter rétroactivement (résolution par instant d'appel).
        "effective_from": RETRIEVED_AT,
        "source": ("Décision produit FinOps : `mistral-small-latest` résolu vers "
                   "`mistral-small-2603` (= Mistral Small 4, v26.03) pour la seule tarification. "
                   "Aucune réécriture de télémétrie ; aucun changement de comportement moteur."),
        "source_url": "https://docs.mistral.ai/models/model-cards/mistral-small-4-0-26-03",
        "retrieved_at": RETRIEVED_AT,
    },
]

# --- Hypothèses LOCALES conventionnelles (SÉPARÉES des tarifs officiels) -------
#
# Ce ne sont NI des coûts matériels mesurés, NI des tarifs de référence : ce sont des tarifs POSÉS par
# convention/configuration pour donner un ordre de grandeur au calcul local. D'où :
#   - base de coût / kind : `local_imputed` (jamais « actual ») ;
#   - provenance : `configured_manual` (JAMAIS `local_benchmark`, qui désignerait un vrai relevé) ;
#   - devise : USD ; barème immuable/versionné, `effective_from` daté.
# Structurellement DISTINCTES d'`OFFICIAL_RATES` : ne jamais mélanger une hypothèse avec un
# tarif fournisseur publié.
LOCAL_ASSUMED_AT = "2026-08-09"   # date d'entrée en vigueur de ces hypothèses

LOCAL_ASSUMPTIONS = [
    {
        # MLX local (CR) — config.MLX_LLM_MODEL.
        "provider": "mlx",
        "model": "mlx-community/Qwen3-4B-Instruct-2507-4bit",
        "kind": "local_imputed",
        "unit": "token",
        "currency": "USD",
        "input_rate": 0.10,       # $/1M jetons d'entrée (HYPOTHÈSE)
        "output_rate": 0.30,      # $/1M jetons de sortie (HYPOTHÈSE)
        "cached_rate": 0.02,      # $/1M jetons d'entrée en cache (HYPOTHÈSE)
        "rate_source": "configured_manual",
        "effective_from": LOCAL_ASSUMED_AT,
        "source": ("Hypothèse conventionnelle (configured_manual) — PAS un coût matériel mesuré "
                   "ni un benchmark. Ordre de grandeur pour le coût local imputé (USD)."),
    },
    {
        # Whisper local (STT) — config.WHISPER_MODEL. Unité NATIVE : la minute d'audio.
        "provider": "whisper",
        "model": "mlx-community/whisper-large-v3-turbo",
        "kind": "local_imputed",
        "unit": "audio_minute",
        "currency": "USD",
        "unit_rate": 0.0015,      # $/minute d'audio (HYPOTHÈSE)
        "rate_source": "configured_manual",
        "effective_from": LOCAL_ASSUMED_AT,
        "source": ("Hypothèse conventionnelle (configured_manual) — PAS un coût matériel mesuré "
                   "ni un benchmark. Ordre de grandeur pour le STT local imputé (USD/minute)."),
    },
]

# --- Modèles NON tarifés officiellement (consignés) ---------------------------
# Structure documentaire (aussi vérifiée par test) : aucun de ces modèles n'a de ligne dans
# OFFICIAL_RATES, donc leur coût reste NULL. On dit POURQUOI, sans inventer de tarif.
RATIFICATION = {
    "openai/gpt-5.6-luna": {
        "ratified": True,
        "reason": "Nom exact = modèle officiel tarifé ; grille publiée relevée.",
    },
    "mistral/mistral-small-latest": {
        "ratified": True,
        "via": "alias",
        "reason": ("DÉCISION PRODUIT (suivi Phase 1C) : l'alias est résolu — par un mapping AUDITÉ "
                   "et versionné (OFFICIAL_ALIASES) — vers l'identité tarifée `mistral-small-2603` "
                   "(Mistral Small 4). La télémétrie garde `mistral-small-latest` ; seule la "
                   "tarification résout. Pas de config ni de comportement moteur modifiés."),
    },
    "mistral/mistral-small-2603": {
        "ratified": True,
        "reason": ("Identité de tarification datée (Mistral Small 4, v26.03) : tarif officiel "
                   "0,15/0,60 $/1M USD relevé (docs.mistral.ai). Cible de l'alias chat."),
    },
    "mistral/voxtral-mini-2602": {
        "ratified": True,
        "reason": ("Id DÉJÀ version-épinglé (v26.02) = modèle officiel « Voxtral Mini Transcribe "
                   "2 » de MÊME id (docs.mistral.ai), distinct de la variante Realtime. Tarif "
                   "officiel relevé : 0,003 $/minute d'audio. Aucune substitution, aucune modif "
                   "de config (l'id configuré est déjà l'id tarifé)."),
    },
    "mistral/ministral-14b-2512": {
        "ratified": False,
        "reason": ("Modèle de chat Mistral par défaut, id version-épinglé. Tarif candidat "
                   "consigné dans RATES_TO_CONFIRM, non relevé sur la grille officielle : "
                   "coût NULL jusqu'à confirmation."),
    },
    "anthropic/claude-haiku-5-5": {
        "ratified": True,
        "reason": "Nom exact = modèle officiel tarifé ; grille publiée relevée le 25/10/2026.",
    },
    "anthropic/claude-sonnet-5-5": {
        "ratified": True,
        "reason": "Nom exact = modèle officiel tarifé ; grille publiée relevée le 25/10/2026.",
    },
    "mlx/qwen3-4b-instruct": {
        "ratified": False,
        "reason": ("Modèle LOCAL (MLX) : AUCUN tarif fournisseur officiel. Une HYPOTHÈSE "
                   "conventionnelle `configured_manual` est fournie (LOCAL_ASSUMPTIONS) pour un "
                   "coût `local_imputed` — ce n'est pas un tarif officiel ni un benchmark."),
    },
    "whisper/whisper-large-v3-turbo": {
        "ratified": False,
        "reason": ("STT LOCAL (Whisper MLX) : AUCUN tarif fournisseur officiel, pas de jetons. "
                   "Une HYPOTHÈSE `configured_manual` est fournie (LOCAL_ASSUMPTIONS) à l'unité "
                   "`audio_minute` pour un coût `local_imputed` — ni officiel ni benchmark."),
    },
}
