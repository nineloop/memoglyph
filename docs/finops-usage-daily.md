# FinOps — `usage_daily` aggregation

> The **derived, rebuildable** rollup the Usage dashboard reads. No budgets/alerts,
> no privacy routing. `execution` / `model_call` remain the source of truth;
> `usage_daily` can be deleted and rebuilt from the raw usage records at any time.

## Grain (primary key)

```
day · project_id · provider · model · stage · exec_kind · currency · cost_basis
```

`day` = the **UTC date of `execution.started_at`** (`substr(started_at,1,10)`), so every stage
of one report shares one day. `project_id` is an **execution fact** (`execution.project_id`,
never inferred from filenames); **NULL** = legacy / project-less execution, kept **explicit**
(never silently remapped) and its own bucket. `currency` and `cost_basis` are in the key —
**currencies are never blended, `cloud_derived` and `local_imputed` are never blended, two
projects are never blended**, and unpriced usage (`cost_basis` NULL, `cost` NULL) is its own
bucket, never a fake zero.

## Two contributing units → one row per bucket

`usage_daily` is a `GROUP BY` over the 7 dims of a `UNION ALL` of two unit sources:

1. **call units** — one per `model_call`, at `stage = model_call.stage` (or `'(unspecified)'`).
   Carry the per-call measures: `n_model_calls`, `in/out/cached/reasoning_tok`, `cost`.
2. **execution units** — one per `execution`, carrying the execution-level measures
   `n_executions` (=1) and `wall_clock_s`. Its stage is a sentinel:
   - `'stt'` when the execution is an STT run (`audio_seconds` set, **zero calls**) — this row
     also carries `audio_seconds` and the **audio cost** (STT has no call rows to hold it);
   - `'(execution)'` otherwise (report/profile) — `cost` is **NULL** here, because the cost
     already lives on the call-unit rows. This is what prevents double counting.

Because every execution emits **exactly one** execution unit, `SUM(n_executions)` and
`SUM(wall_clock_s)` count each execution once, at any grouping. `SUM(cost)` never double counts:
report cost sits on call rows, STT cost on its `'stt'` row, `'(execution)'` rows are cost-free.

Bucket cost is `NULL` when no unit in the bucket is priced (`SUM(priced)=0`) — unpriced stays
unpriced.

## Maintenance (deterministic, off the critical path)

- **Full rebuild** — `rebuild_usage_daily()` : `DELETE FROM usage_daily` then one grouped
  `INSERT … SELECT` from raw. Idempotent, deterministic, the **authoritative** definition.
- **Incremental** — `_update_daily_for(conn, execution_id)` : recompute the affected
  **day partition(s)** (`DELETE WHERE day=?` then re-`INSERT` that day from raw). A day rebuilt
  from raw is identical to the full rebuild for that day, so incremental-over-all-days ≡ full
  rebuild. No per-bucket upsert, so nullable key dims (unpriced) need no sentinels.
- **Wiring** — `persist_execution` runs the incremental update **after** the raw facts commit,
  in an isolated best-effort block (rollback-on-error). Facts are already durable, so an
  aggregation failure loses nothing and **can never break STT/report generation**; a later
  full rebuild always reconciles. `reprice()` does a full rebuild after costs change.
  Raw usage/pricing facts are **never** mutated by any of this.

## Read API (read-only, aggregate-only)

`usage_store.usage_rollup(*, start, end, project_id, provider, model, stage, kind, group_by)` and
`GET /api/usage/rollup` read **only `usage_daily`** (never a `model_call` scan — proven by an
`EXPLAIN QUERY PLAN` test, including a project-scoped query). `group_by ∈ {day, project_id,
provider, model, stage, exec_kind, cost_basis}` (whitelisted; bad input falls back to `day`).
`currency` + `cost_basis` are always in the output so the caller can render cloud/local and
per-currency figures **separately**. Filters: day range (`start`/`end`), `project_id`, provider,
model, stage, kind. It returns aggregated series only — **never a raw execution ledger**. Opened
`mode=ro`; unavailable/empty ⇒ empty series, never a 500.

**Project scope** (`store` three-state `project_id`): the sentinel `UNSCOPED` (default) applies
no project filter (operator/global view); a value filters that one project; `None` targets
`project_id IS NULL` **explicitly** (legacy/project-less), never "all". The `GET /api/usage/rollup`
endpoint maps the app's `view_scope`: a project id → that project only; `view_scope=all` → global
(explicit opt-in); empty → the inbox project (**never global by default**) — so there is no
accidental cross-project leakage.

Dashboard mappings: headline cloud/local → `group_by=cost_basis`; spend over time →
`group_by=day`; provider/model mix → `group_by=provider|model`; cost by stage →
`group_by=stage` (ignore the `'(execution)'` sentinel, which carries wall-clock, not cost).

## Indexes

Unique grain index (`day,project_id,provider,model,stage,exec_kind,currency,cost_basis`) plus
targeted `day`, `(project_id,day)`, `(exec_kind,day)`, `(provider,model)`, `(stage,day)`. At 100k
executions / 250k calls across 12 projects the rollup reads **~300 daily rows** in **< 1 ms**
(global **0.6 ms**, project-scoped **0.4 ms**), vs the 250k-row call journal it never scans.

## Accounting

`cloud_derived` / `local_imputed` / `mixed` / NULL vocabulary is reaffirmed, currencies stay
native (no FX), and there is **no automatic historical reprice** — `usage_daily` only ever
re-derives from stored facts + snapshots. Pricing/bootstrap behaviour is untouched.

## Dashboard and drill-down

The Usage dashboard reads this rollup, scoped to a project, and offers a bounded list of executions
with a forensic detail view (`/api/usage/rollup`, and the execution list and detail routes in
`api/main.py`).
