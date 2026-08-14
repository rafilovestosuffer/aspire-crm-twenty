# Runbook — Local Execution

Everything runs on your machine. The token never leaves it.

---

## Setup (once)

```bash
git clone https://github.com/rafilovestosuffer/aspire-crm-twenty.git
cd aspire-crm-twenty

cp .env.example .env
```

Fill in `.env`:

- `GHL_TOKEN` — Settings → Integrations → Private Integrations → create new.
  **Tick every scope ending `.readonly`. Tick nothing implying write.**
  Copy the token immediately; it is shown once and never again.
- `GHL_LOCATION` — from the sub-account URL:
  `app.gohighlevel.com/v2/location/`**`<THIS>`**`/settings/...`
- `GHL_COMPANY_ID` — agency id, or leave blank. Blank means agency-class
  endpoints are recorded as *skipped*, not *failed*.

Optional but recommended: `pip install -r scripts/requirements.txt`
(the scripts run on the standard library alone if you skip it).

---

## Step 1 — Verify auth by hand before running anything

```bash
set -a && source .env && set +a

curl -s "https://services.leadconnectorhq.com/locations/$GHL_LOCATION" \
  -H "Accept: application/json" \
  -H "Authorization: Bearer $GHL_TOKEN" \
  -H "Version: 2021-07-28" | head -c 600
```

The `Version` header is mandatory. Omit it and you get an error that reads like
an auth failure and costs you an afternoon.

If that returns your location object, auth works.

---

## Step 2 — One endpoint, then all of them

```bash
python scripts/ghl_pull.py --dry-run                # 43 endpoints, no calls
python scripts/ghl_pull.py --only custom_fields     # verify one
cat raw/custom_fields.json | head -40
```

If the shape looks right:

```bash
python scripts/ghl_pull.py                          # full discovery run
```

Then **read the coverage report before anything else**:

```bash
column -s, -t out/pull_coverage.csv | less -S
```

- `ok` — data retrieved
- `error 401/403` — missing scope, not a broken script. Note it; it is a stated
  limitation of the audit
- `error 404` — endpoint absent on this account tier. Expected for rows marked
  `unverified`
- `skipped` — agency-level, no company id

**Send me `out/pull_coverage.csv`.** It is counts and field names only — no
company data — and it tells me exactly what the account exposes so the next step
is written against reality rather than the docs.

---

## Step 3 — Probe Twenty

```bash
# .env: TWENTY_BASE_URL and TWENTY_API_KEY (Twenty → Settings → APIs)
python scripts/twenty_probe.py
```

Produces `out/twenty_capability.md` — the object model of the instance actually
deployed, plus verification that MT-2's four Aspire custom objects exist.

---

## Step 4 — Workflow internals

```bash
python scripts/ghl_workflow_capture.py --dry-run
```

Needs `.session_curl.txt` first:

1. Open any workflow in GHL
2. DevTools (F12) → Network → XHR
3. Reload. Find the response containing the step tree — not the workflow list
4. Right-click → Copy → **Copy as cURL (bash)**
5. Save into `.session_curl.txt` (gitignored)

```bash
python scripts/ghl_workflow_capture.py --limit 5     # verify shape first
```

Open one file in `workflows/` and confirm the steps are genuinely in there. A
response that parses is not necessarily the response you wanted.

```bash
python scripts/ghl_workflow_capture.py --published-only
```

The session JWT expires in hours. When it does the script stops cleanly and tells
you — re-capture the cURL and run again. Completed workflows are skipped. **Expect
this once or twice per full run. It is normal, not a failure.**

---

## Step 5 — Manual sweep

Work through `docs/04-manual-sweep.md` into `raw/manual_sweep.md`.
Copy page text, not screenshots.

---

## Step 6 — Build the report

```bash
python scripts/build_audit.py
```

Then, before trusting it: **open GHL and check five rows by hand.** If all five
are right, trust the rest. If any is wrong, tell me which and why — the rule gets
fixed and the whole thing re-runs. A classification that is 90% right is
indistinguishable from one that is 100% right until go-live day.

`criticality` and `effort_days` stay blank for you to fill. They are judgement,
not data, and a script that invented them would be lying convincingly.

---

## What to send back, and when

| After | Send | Why |
|---|---|---|
| Step 2 | `out/pull_coverage.csv` + the granted scope list | Tells me what the account exposes; scope list is a stated limitation in the report |
| Step 2 | `raw/workflows.json`, `raw/pipelines.json`, `raw/custom_fields.json` | Configuration only, no PII — lets me start the mapping |
| Step 3 | `out/twenty_capability.md` | Confirms what the deployed instance can absorb |
| Step 4 | 5–10 files from `workflows/` | I write the classifier against real shapes, then it runs over the rest |
| Step 6 | `out/feature_audit.csv` | I write the management report from it |

**Do not send** anything from `raw/` classed `volume`, `.env`, or
`.session_curl.txt`. The gitignore already blocks all three from commits; this is
about pasting.

---

## Safety properties, so you can state them if asked

- The puller issues **GET only** and refuses other methods at transport level
- Endpoints that could carry PII are classed `volume`: **bodies are discarded at
  pull time**, only counts and field names are written to disk
- No contact records, message bodies, call recordings or submission payloads are
  fetched by any script here
- Throttled to **80 requests / 10s** against GHL's 100/10s ceiling, with
  exponential backoff on 429
- Tokens are redacted from all error output
- `.env`, `raw/`, `workflows/`, `.session_curl.txt` are gitignored
