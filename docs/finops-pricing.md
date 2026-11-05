# FinOps — Pricing provenance & ratification

> Pricing policy only. The dashboard is described in [finops-usage-daily.md](finops-usage-daily.md). No budgets/alerts,
> no FX, no invoice reconciliation. Cost stays `cloud_derived` / `local_imputed` / **NULL**.

The pricing mechanism is generic and versioned. The policy is **auditable and grounded in real
evidence** — and, crucially, refuses to price anything it cannot establish from an authoritative
source.

## Cost-basis vocabulary (unchanged, reaffirmed)

- `cloud_derived` — cost derived from a provider's **published list price** (not invoice-reconciled).
- `local_imputed` — local compute; money **imputed**, never "actual".
- `mixed` — execution rollup sentinel when calls span currencies/bases (never summed).
- **NULL** — no applicable pricing snapshot; cost unknown (never a fake zero).

`cloud_actual` / `invoice_reconciled` are **not** used.

## Pricing provenance (`pricing_snapshot.rate_source`)

Every snapshot records where its number came from:

| `rate_source` | meaning | requires |
|---|---|---|
| `official_published` | a provider's published rate card | `source_url` + `retrieved_at` (enforced) |
| `configured_manual` | a rate typed by the operator | — |
| `local_benchmark` | an imputed local rate (hosted-equivalent reference cost) | — (never a hidden default) |

Snapshots remain **immutable and versioned**; a corrected rate is a new version, never an overwrite.

## Model inventory & ratification (evidence retrieved 2026-08-09)

Exact models Memoglyph uses, and whether their price could be established:

| Role | Configured model | Verdict | Evidence / reason |
|---|---|---|---|
| OpenAI reports | `gpt-5.6-luna` | **ratified** | Official: $0.20/1M in, $1.20/1M out, $0.02/1M cached — USD. [developers.openai.com](https://developers.openai.com/api/docs/models/gpt-5.6-luna) |
| Anthropic reports | `claude-haiku-5-5` (default) | **ratified** (retrieved 2026-10-25) | Official: $0.10/1M in, $0.50/1M out, $0.01/1M cached, for prompts up to 100,000 tokens (above: $0.50/$2.50/$0.05 for the whole request, not modelled — cost of such a request is underestimated). [platform.claude.com](https://platform.claude.com/docs/en/about-claude/pricing) |
| Anthropic meeting facts | `claude-sonnet-5-5` | **ratified** (retrieved 2026-10-25) | Official: $2/1M in, $10/1M out, $0.10/1M cached, same rate across the 1M window. [platform.claude.com](https://platform.claude.com/docs/en/about-claude/pricing) |
| Mistral reports | `mistral-small-latest` | **ratified via alias** | Product decision: the alias resolves — through an **audited, versioned alias mapping** — to the priced identity `mistral-small-2603` ("Mistral Small 4", $0.15/$0.60 /1M USD). Local usage tracking keeps the alias; only pricing resolves. [docs.mistral.ai](https://docs.mistral.ai/models/model-cards/mistral-small-4-0-26-03) |
| — pricing identity | `mistral-small-2603` | **ratified** | Mistral Small 4 (v26.03), $0.15/1M in, $0.60/1M out, USD, no cached rate, no separate reasoning billing. |
| Mistral STT | `voxtral-mini-2602` | **ratified** | Id is **already version-pinned** = official "Voxtral Mini Transcribe 2" (same id), **$0.003/min** USD, distinct from the Realtime variant. [docs.mistral.ai](https://docs.mistral.ai/models/voxtral-mini-transcribe-26-02) |
| MLX reports | `mlx-community/Qwen3-4B-Instruct-2507-4bit` | **local** | No provider price → `local_imputed`, no default rate. |
| Whisper STT | `mlx-community/whisper-large-v3-turbo` | **local** | Local STT → `local_imputed` (unit `audio_second`), no default rate. |

The verdicts are encoded in `core/pricing_official.py` (`OFFICIAL_RATES` + `RATIFICATION`) and
checked by tests. **Rates:** `gpt-5.6-luna`, `voxtral-mini-2602`, and `mistral-small-2603`
(the last reached from `mistral-small-latest` via an audited alias). Everything else is NULL.
Nothing is inferred or fuzzy-substituted.

### Model-alias mapping (observed → pricing identity)

Some local usage records hold a **configured alias** (`mistral-small-latest`) that is not itself a priced
identity. An explicit, **audited, versioned** `model_alias` mapping (in `usage.db`, provenance-
rich, imported only via `bootstrap_official_pricing`) resolves it to a priced identity for
**costing only**:

- `mistral / mistral-small-latest` → `mistral-small-2603` (product decision).

The mapping is **exact** — only that `(provider, observed_model)` resolves; no other model
inherits it, and no fuzzy matching. `model_call.model` **keeps the observed value** (never
rewritten); the pricing lookup resolves the identity and records the resolved `pricing_snapshot_id`,
so history stays reproducible. Without the mapping (or its target rate), cost stays **NULL**.

### Provider billing semantics (as applied)

- **OpenAI (gpt-5.6-luna, Responses API):** `output_tokens` **already includes** reasoning
  tokens → billed at the output rate; `reasoning_tokens` is stored for visibility only, never
  added again. `cached_tokens` ⊂ `input_tokens` → the non-cached remainder at the input rate,
  cached at the reduced cached rate. No double-counting.
- **Anthropic (Messages API):** `output_tokens` includes thinking tokens → output rate.
  `input_tokens` excludes cache reads and writes, so the engine records input as
  `input_tokens + cache_read_input_tokens + cache_creation_input_tokens`, with
  `cache_read_input_tokens` as the cached part (cache writes, billed 1.25× input, are counted
  at the input rate).
- **Mistral STT (voxtral-mini-2602):** billed **per minute of audio** ($0.003/min, USD). Cost
  is computed on the STT execution's measured `audio_seconds`, converted to minutes (native
  unit `audio_minute`) × rate → `cloud_derived`. It's list-price-derived, **not** invoice-
  reconciled: any provider-side minute rounding is not confirmed by the docs and is not modelled.
- **Mistral reports (mistral-small-latest → mistral-small-2603):** measured prompt/completion
  tokens only (Mistral usage doesn't split reasoning), billed at $0.15/$0.60 per 1M, USD, no
  cached rate. Priced via the audited alias; the observed model id is preserved in the usage records.

## Local costing policy (conventional assumptions)

MLX/Whisper have no official provider price, so Memoglyph ships **conventional local
assumptions** (`core/pricing_official.py::LOCAL_ASSUMPTIONS`, kept **separate** from
`OFFICIAL_RATES`) — an order-of-magnitude figure, **not** a measured hardware cost:

| model | unit | rate (USD) | basis | provenance |
|---|---|---|---|---|
| `mlx-community/Qwen3-4B-Instruct-2507-4bit` | token | in $0.10, out $0.30, cached $0.02 /1M | `local_imputed` | `configured_manual` |
| `mlx-community/whisper-large-v3-turbo` | audio_minute | $0.0015 /audio-minute | `local_imputed` | `configured_manual` |

Provenance is `configured_manual` — **never `local_benchmark`** (which would claim a real
measured local cost) and never "actual". These are immutable/versioned snapshots with an explicit
`effective_from`. Measured facts remain available (MLX in/out/cached tokens + wall-clock;
Whisper `audio_seconds` + wall-clock); machine/runtime metadata is **not** captured (PII/surface).
A real hosted-equivalent local rate (`local_benchmark`) is not provided.

## Zero-touch initialization

The embedded catalogue (official OpenAI/Anthropic/Mistral-Small/Voxtral rates + the audited Mistral alias
+ the two local assumptions) initializes **automatically on normal startup** — no bootstrap
command, no DB operation, no UI action:

- `usage_store.import_catalogue(conn)` — imports official rates + audited aliases + local
  assumptions; idempotent, additive/versioned, never overwrites (raises; used by tests).
- `usage_store.initialize_catalogue(path=None)` — **best-effort app seam**: opens `usage.db`,
  imports the catalogue, commits — and **never raises**. Idempotent on every launch; **never
  calls `reprice()`** (history untouched).
- Wired into the FastAPI **`lifespan`** (`api/main.py::_startup_finops`), which `uvicorn
  api.main:app` (i.e. `./start.sh`) runs at boot. **Hard rule:** the seed lives here, **not** in
  `usage_store.connect()`, which stays a pure DB/schema primitive (seeds nothing).
- `usage_store.import_pricing(conn, entries)` remains the low-level explicit importer (idempotent,
  versioned, provenance-rich); `bootstrap_official_pricing(conn)` imports the official subset.

A fresh `usage.db` therefore has **zero** rows until either normal startup or an explicit import
runs. Currencies stay native; USD and EUR are never summed; there is no FX. Startup never reprices.

## Reconciliation

`gpt-5.6-luna` reconciles: a representative **real measured** usage record from an early
soak run (547 in / 452 out / 68 reasoning) computes to `(547·0.20 + 452·1.20)/1e6 = $0.0006518`
under the ratified formula — exactly the stored cost (test `test_reconciliation_officielle_gpt56luna`).
No paid API calls were spent; existing measured usage was reused. Mistral/local can't be
reconciled because they aren't ratified — stated, not faked.
