# GHL → Twenty Migration Audit

The instrument for auditing which GoHighLevel features Aspire actually uses, and
whether Twenty (self-hosted) + n8n can replace each one.

This repo is the audit tooling. It is not the Twenty deployment.

## Read first

| Document | What it answers |
|---|---|
| [`docs/00-audit-method.md`](docs/00-audit-method.md) | How the audit runs — six tiers, disposition rules, what it cannot tell you |
| [`docs/01-scope-and-questions.md`](docs/01-scope-and-questions.md) | Open decisions. **Q1 blocks project sizing** |
| [`docs/02-ghl-api-findings.md`](docs/02-ghl-api-findings.md) | Verified API facts with sources — read before writing anything against GHL |
| [`CLAUDE.md`](CLAUDE.md) | Operating rules. Read-only, no PII, rate limits |

## Quick start

```bash
cp .env.example .env          # fill in GHL_TOKEN, GHL_LOCATION
pip install -r scripts/requirements.txt   # optional

python scripts/ghl_pull.py --dry-run      # show the plan, call nothing
python scripts/ghl_pull.py --only custom_fields   # verify one endpoint first
python scripts/ghl_pull.py                # full discovery run
python scripts/twenty_probe.py            # probe the live Twenty instance
python scripts/build_audit.py             # → out/feature_audit.csv
```

Read `out/pull_coverage.csv` before drawing any conclusion from `raw/`. It records
which endpoints answered, which 401'd on a missing scope, and which do not exist
on this account tier.

## Layout

```
reference/ghl_feature_taxonomy.csv   111-feature master checklist — the spine
reference/ghl_endpoints.yaml         43 read-only endpoints driving the puller
scripts/ghl_pull.py                  GET-only, throttled, PII-classified puller
scripts/twenty_probe.py              reads the live Twenty instance's real capability
scripts/build_audit.py               merges evidence + taxonomy → the report
docs/                                method, findings, open decisions
raw/  workflows/  .env               company data — gitignored, never committed
out/feature_audit.csv                the deliverable
```

## Non-negotiables

- **GET only.** The puller blocks any other method at transport level.
- **No PII.** No contacts, no message bodies, no submission payloads. Endpoints
  classed `volume` have their bodies discarded at pull time; only counts survive.
- **80 requests / 10s** against a 100/10s ceiling, exponential backoff on 429.
- **`raw/`, `workflows/`, `.env` are never committed.**
- **Evidence or `unknown`.** No feature is marked in-use or retired without a
  named source. Guessing `no` is how a live automation dies silently at cutover.
