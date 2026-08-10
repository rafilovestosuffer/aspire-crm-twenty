# GHL → Twenty Feature Audit — Aspire Tech

## Goal
Inventory every GoHighLevel feature Aspire **actually uses**, and decide for each
whether Twenty (self-hosted) + n8n can replace it. Output is one CSV, one row per
feature, with an evidence-backed disposition.

This repo is the audit *instrument*. It is not the Twenty deployment.

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
scripts/ghl_pull.py                  read-only GHL puller (registry-driven)
scripts/twenty_probe.py              probes the live Twenty instance for capability truth
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

## Working style
Verify empirically before writing logic against it. The GHL docs and the live API
disagree often enough that a script written blind fails in ten places at once.
The puller's first run is a **discovery run**: it records HTTP status and response
shape per endpoint and writes a coverage report. Read that before trusting output.

Spot-check classifications by hand — 5 rows against the real GHL UI. A
classification that is 90% right is indistinguishable from one that is 100% right
until go-live day.
