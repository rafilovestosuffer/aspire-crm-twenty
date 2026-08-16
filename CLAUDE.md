# GHL → Twenty Feature Audit — Aspire Tech

## Goal
Inventory every GoHighLevel feature Aspire **actually uses**, and decide for each
whether Twenty (self-hosted) + n8n can replace it. Output is one CSV, one row per
feature, with an evidence-backed disposition.

This repo is the audit *instrument*, and it also holds the **scripted Twenty
schema** (`reference/twenty_schema.yaml` + `scripts/twenty_provision.py`) so the
object model rebuilds in one command instead of being clicked through the UI. It
is not the Twenty deployment itself — no Docker config, no server settings.

## Hard rules — never violate

1. **READ-ONLY against GHL.** No POST / PUT / PATCH / DELETE to any GHL endpoint,
   ever. The puller refuses non-GET methods at the transport layer; do not remove
   that guard.
2. **No PII.** Do not pull contact records, conversation bodies, message content,
   call recordings, or form-submission payloads. Configuration objects only.
   Endpoints classified `volume` in the registry return counts only — the body is
   discarded before it is written to disk.
3. **Secrets stay in `.env`.** Never print, echo, log, or commit `GHL_TOKEN`,
   `TWENTY_API_KEY`, or any session JWT. The puller redacts `Authorization`
   headers from all error output.
4. **Never commit `raw/`, `workflows/`, or `.env`.** They contain company data.
   `scripts/`, `reference/`, `docs/`, and `out/feature_audit.csv` are committed.
5. **Rate limit: 100 requests / 10 seconds, 200,000 / day.** Exponential backoff
   on 429. Never remove the throttle to "speed it up".
6. **Every GHL request needs `Version: 2021-07-28`** (or `v3` where the registry
   says so). Omitting it produces an error that looks like an auth failure.

## Evidence rule
No row in the output CSV may claim `in_use = yes/no` without a value in the
`evidence` column naming its source: an endpoint path, a snapshot CSV row, or a
dated UI observation. "I think we use it" is not evidence. Unknown is a valid
answer and must be recorded as `unknown`, never guessed.

## Disposition values
`TWENTY` · `N8N` · `TWENTY+N8N` · `DROP` · `ESCALATE` · `UNKNOWN`

`ESCALATE` = cannot be done by Twenty or n8n alone and needs a vendor/budget
decision (bulk email, SMS, voice, telephony numbers, funnels/websites, payments).
Do not hide these — a short, honest ESCALATE list is what makes the rest credible.

## Layout
```
reference/ghl_feature_taxonomy.csv   master checklist — the spine, nothing gets missed
reference/ghl_endpoints.yaml         endpoint registry driving the puller
reference/twenty_schema.yaml         25-object GHL parity model for Twenty
scripts/ghl_pull.py                  read-only GHL puller (registry-driven)
scripts/twenty_probe.py              probes the live Twenty instance for capability truth
scripts/twenty_provision.py          builds the object model via the Metadata API
scripts/build_audit.py               merges pulls + taxonomy → out/feature_audit.csv
raw/                                 GHL pull output          (gitignored)
workflows/                           workflow internals       (gitignored)
out/                                 reports                  (feature_audit.csv committed)
docs/                                method, findings, open decisions
```

## Verified facts — do not re-derive, do not contradict without a new source
- GHL public API is **read-only for workflows** and exposes workflow *metadata
  only* (id, name, status, dates). Step/action internals are **not** available on
  any documented endpoint. Confirmed by a standing feature request, unresolved
  since 2022. → Workflow internals require the authenticated-session route
  (`docs/02-ghl-api-findings.md`).
- `GET /custom-fields/object-key/{key}` supports **Custom Objects and Company
  only** — not Contacts/Opportunities. Use `GET /locations/{id}/customFields`
  for those.
- Twenty's `Send Email` workflow action sends from a **synced mailbox, one
  recipient at a time**. Twenty has no campaign engine, no suppression list, no
  domain warm-up, no SMS, no telephony, no forms, no landing pages.
- Twenty workflow triggers: record created / updated / created-or-updated /
  deleted, manual, schedule (cron, UTC), inbound webhook.
- Twenty workflow actions: create / update / delete / search / upsert record,
  iterator, filter, delay, send email, form, code, HTTP request, AI agent (soon).

### Twenty REST query syntax — verified against the live instance

Getting these wrong is usually **silent**, not an error. Enforced by
`scripts/validate_workflow_queries.py`; run it after touching any workflow.

| Purpose | Correct | Wrong, and what happens |
|---|---|---|
| filter | `?filter=field[eq]:value` | `?filter[field][eq]=value` — **ignored, returns the whole table** |
| multiple | `?filter=a[eq]:1,b[eq]:2` (comma = AND) | — |
| in | `?filter=status[in]:[A,B]` | bare comma list → 400 |
| substring | `?filter=domainName.primaryLinkUrl[ilike]:%acme%` | — |
| order | `?order_by=field[DescNullsLast]` | `orderBy=` — **silently ignored** |
| relation | `?filter=personId[eq]:<uuid>` | the relation is `person`, the filter key is `personId` |

**Every interpolated filter value must be `encodeURIComponent`-wrapped.** A
space, bracket or colon in the value corrupts the query into a 400. This broke
the error handler's own dedupe query on a workflow name containing brackets:
the failure was logged, the handler then errored, and **the alert was never
sent**. Values reaching these filters include user-typed emails and a slug taken
off a public URL. The generator lints for it.

- `/rest/metadata/*` **rejects every query string**, and `/rest/metadata/objects`
  embeds only a *slice* of each object's fields — so it is not a complete
  schema. Merge it with the keys of a real row from `/rest/{object}?limit=1`.
- **`/rest/metadata/*` is not served in-process.** Twenty makes an HTTP call to
  `${SERVER_URL}${path}` from inside the server container and returns the
  result (`rest-api-metadata.service.js`; `getServerUrl` prefers the env var
  over the request, so `SERVER_URL` always wins when set). If the *server*
  cannot resolve, reach or **trust** its own `SERVER_URL`, every metadata
  request answers **HTTP 500 with an empty body and no server log line** — the
  provisioner, the query validator and the stack check all fail at once and
  none of them names the cause. The core API is unaffected, which makes it look
  like an auth or key problem. Two things make it work behind a proxy:
  a compose **network alias** putting the public name on the Caddy container
  (otherwise it depends on the provider's hairpin NAT), and, on an internal CA,
  `NODE_EXTRA_CA_CERTS`. Caddy's PKI is `0700` root-owned and the app runs
  non-root, so the root must be **copied out** to `infra/certs/`, not mounted
  from `caddy-data`. Asserted by `scripts/verify_proxy_rules.py` and gated in
  `infra/deploy.sh`.
- Task body is `bodyV2`, a RICH_TEXT composite taking `{"markdown": "..."}`.
  A bare string is a 400; a plain `body` key is not a field.
- Default opportunity stages are `NEW, SCREENING, MEETING, PROPOSAL, CUSTOMER`.
  There is no `WON`.
- `seed_demo_data.py --wipe` deletes **only** seeded data, identified by the
  `.example.com` addresses (RFC 2606), the `[demo]` task prefix, and any foreign
  key pointing at a seeded parent — company, person *or* training account. Match
  on `companyId` alone and consent records and phishing baselines are missed,
  because they hang off a person and a training account; they then accumulate on
  every reseed. With no seeded parent found it deletes nothing, by design.

### n8n facts learned the hard way

- An expression ends at the **first `}}`**. A nested object literal
  (`{"a": {"b": $x}}`) closes it early and the remainder is a syntax error.
  Emit `} }`. The generator lints for this and refuses to write.
- `jsonBody` is only evaluated when the **whole** parameter starts with `=`.
- Sub-workflow references and `settings.errorWorkflow` resolve by **id, not
  name**. Named, they dangle silently — every workflow looks like it has error
  handling and none of it fires. `n8n_deploy.py` binds ids at deploy time.
- Form Trigger inputs post as `field-0`, `field-1`, … **by position**, not by
  label. The form path lives in `path` at typeVersion ≤ 2.1 and in
  `options.path` from 2.2; set both or the URL falls back to a random UUID.
- Code nodes run in a sandboxed task runner: use `$env`, never `process.env`.
- A Switch's `fallbackOutput` must be an existing index or `"extra"`.
- **`settings.errorWorkflow` fires only for PRODUCTION executions.** A manual
  run fails in the editor and the error workflow is never called, so testing
  failure handling by hand proves nothing. `SYS Failure Probe (dev)` is
  webhook-triggered for exactly this reason.
- A webhook answers on receipt, before the workflow runs: a 200 says nothing
  about the outcome. Assert on the record written, not the HTTP status.

## Working style
Verify empirically before writing logic against it. The GHL docs and the live API
disagree often enough that a script written blind fails in ten places at once.
The puller's first run is a **discovery run**: it records HTTP status and response
shape per endpoint and writes a coverage report. Read that before trusting output.

Spot-check classifications by hand — 5 rows against the real GHL UI. A
classification that is 90% right is indistinguishable from one that is 100% right
until go-live day.
