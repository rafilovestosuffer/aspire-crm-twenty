# GHL live pull — 18 August 2026

Read-only. GET only. No contact records, conversations, message bodies, or
form-submission payloads. The credential from chat was written to gitignored
`.env` only and is **not** repeated here.

## Result

**No GHL configuration was retrieved.** HighLevel rejected the value as a
bearer token. Nothing in `raw/` was written from this run.

| Probe (GET) | Host | HTTP | Body (redacted) |
|---|---|---|---|
| `/users/` | `services.leadconnectorhq.com` | 401 | `Invalid JWT` |
| `/locations/{id}` | same | 401 | `Invalid JWT` |
| `/v1/locations/` | `rest.gohighlevel.com` | 401 | `Unauthorized, Switch to the new API token.` |
| `/v1/custom-values/` | same | 401 | `Api key is invalid.` |

Without a `User-Agent`, the same GETs returned Cloudflare **1010** (blocked
Python-urllib). `scripts/ghl_pull.py` now sends a read-only User-Agent so the
next valid token is not stopped at the CDN.

## What was provided

A 36-character UUID. That is **not** a Private Integration Token.

GHL accepts, as `Authorization: Bearer …`:

- a **PIT** that starts with `pit-` and is shown **once** at creation, or
- a **JWT** (three `.`-separated segments)

A UUID is usually one of: the Private Integration *row id* in the UI (not the
secret), a location id, a company id, or an OAuth client id. None of those
authenticate `GET /users/` or `GET /locations/{id}`.

## What to paste so the pull can run

Two values, in `.env` (never in git, never in Slack):

```
GHL_TOKEN=pit-…          # copy the secret at creation, not the row id
GHL_LOCATION=            # from the sub-account URL:
                         # app.gohighlevel.com/v2/location/<THIS>/…
```

Create the token in GHL: **Settings → Integrations → Private Integrations**.
Tick every scope that ends in `.readonly`. Tick nothing that implies write.
The secret is shown once; if it was already dismissed, create a new PIT and
paste that. Delete the UUID-based integration afterward if it was only a row.

Optional: `GHL_COMPANY_ID=` if this is an **agency** token (unlocks snapshots
and the sub-account list). Leave blank on a sub-account PIT.

## What the next successful pull will write

`python3 scripts/ghl_pull.py` (GET only, throttled). Config JSON under
gitignored `raw/`. Coverage under `out/pull_coverage.csv`. Volume endpoints
store **counts and field names only**.

Then this note should be replaced with names/counts (workflows, forms,
calendars, pipelines, users, templates) — still no PII.

## Guardrails used this run

- Transport: GET. Non-GET is refused in `scripts/ghl_pull.py`.
- Token: `.env` only, mode `600`.
- No contact/conversation/message bodies requested.
