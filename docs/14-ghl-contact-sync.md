# GHL Contact Sync — Keeping the Fallback Current

Added 25 Aug 2026, after Q1 was resolved (`docs/01-scope-and-questions.md`):
GHL stays running as the system of record; Twenty + n8n is a **continuity
fallback**, not a Phase 1 replacement. That reframed what "done" means here —
a fallback with three-week-old data is not a fallback, it is a snapshot. This
document covers `scripts/ghl_contact_sync.py`, the job that closes that gap.

## What it does

On each run:

1. Pulls every GHL business (company), contact (person) and opportunity via
   GET requests only.
2. Upserts each into Twenty, matched by a GHL id stored in a dedicated field
   (`ghlBusinessId` / `ghlContactId` / `ghlOpportunityId` — provisioned by
   `scripts/twenty_provision.py`'s `standard_extensions` pass), never by name
   or email. Names and emails change; the GHL id does not.
3. Maps opportunity pipeline stage through `reference/ghl_stage_map.yaml`,
   the same mapping the original migration audit built. A stage marked
   `drop: true` there (a closed-lost/ambiguous-closed stage) is skipped, not
   migrated as an open deal — for the same reason the audit gave: an open
   pipeline seeded with years of dead deals is worse than an incomplete one.
4. Writes one `automationRun` record per sync, success or failure — the same
   auditability every n8n workflow in this repo already has. Query
   `automationRun` where `workflowName = "GHL Contact Sync (cron)"` to see the
   run history.

## What it deliberately does not do

- **No delta/incremental fetch.** Every run re-pulls every company, contact
  and opportunity, in full, and upserts by id. This was a deliberate choice
  over guessing at an "updated since" filter: GHL's public API behaviour is
  not fully documented, and this repo's own findings (`CLAUDE.md`) record
  that a wrong assumption about GHL's API shape fails silently, not loudly —
  `/emails/builder` returned HTTP 200 and an empty list on a wrong key, not
  an error. A full re-pull upserted by id cannot silently miss an update the
  way an unverified delta filter could. Re-evaluate this if Aspire's contact
  volume grows by an order of magnitude and the full pull no longer
  comfortably fits inside GHL's rate ceiling (100 req/10s, 200,000/day).
- **No PII beyond CRM fields.** This is the one named exception to
  `CLAUDE.md`'s "no PII" rule (see that file for the exact scope) —
  contacts/companies/opportunities only: name, email, phone, deal value,
  pipeline stage. No conversation bodies, no call recordings, no
  form-submission payloads. Nothing pulled is ever written to a committed
  file; it goes GHL → memory → Twenty API and nowhere else. The only local
  artifact is `out/ghl_sync_log.json`, which holds counts and GHL ids only
  and is gitignored.
- **No two-way sync.** This is GHL → Twenty only. A change made directly in
  Twenty is never pushed back to GHL, and a future sync run will overwrite
  it with whatever GHL currently has for that record (matched by GHL id).
  Twenty is a read replica of GHL's CRM data for fallback purposes, not a
  second system of record to reconcile.

## Verification status — read this before scheduling it unattended

**This has not been run against a real GHL account.** The endpoint paths
(`/businesses/`, `/contacts/`, `/opportunities/search`), the response wrapper
keys (`businesses`, `contacts`, `opportunities`), and the pagination cursor
(`meta.startAfter` / `meta.startAfterId`) are written from GHL's public v2 API
documentation. `businesses` is the one piece already verified live (it is in
`reference/ghl_endpoints.yaml`, class `config`, confirmed working in the 18
Aug pull); `contacts` and `opportunities` are not — this repo currently holds
no GHL token to check them against (the one used for the 18 Aug pull was
deleted; see `docs/12-ghl-live-pull.md`).

Before trusting this unattended:

```bash
python scripts/ghl_contact_sync.py --discover
```

This makes GET requests only, pulls nothing further than shape, and writes
`out/ghl_sync_coverage.json` — the response's actual top-level keys, page
counts, and any mismatch between an expected key and what GHL actually
returned. Read it. If a wrapper key is wrong, the pull returns an empty list
looking exactly like success — the exact failure mode `CLAUDE.md` already
documents once for a different endpoint. Then:

```bash
python scripts/ghl_contact_sync.py --dry-run --limit 25
```

25 real records, mapped, nothing written to Twenty — check the counts and
error list make sense before removing `--limit` and `--dry-run`.

Only once both of those look right should `infra/ghl_sync.sh` go into cron.

## Setup

```bash
python scripts/twenty_provision.py          # adds the three ghl*Id fields if missing
cp .env.example .env                        # GHL_TOKEN, GHL_LOCATION, TWENTY_BASE_URL, TWENTY_API_KEY
python scripts/ghl_contact_sync.py --discover
python scripts/ghl_contact_sync.py --dry-run --limit 25
python scripts/ghl_contact_sync.py
crontab -e   # 0 */4 * * *  /path/to/infra/ghl_sync.sh
```

`infra/ghl_sync.sh` follows `infra/watchdog.sh`'s pattern exactly: on failure
it posts to `ALERT_WEBHOOK_URL` from `infra/.env`. The alert body is safe to
send — errors from the sync script carry GHL record ids and HTTP status text
only, never names, emails or phone numbers.

Every 4 hours is a starting default, not a requirement — GHL's rate ceiling
leaves headroom for hourly if fresher data is worth the extra calls.
