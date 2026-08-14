# GHL → Twenty Migration Audit

The instrument for auditing which GoHighLevel features Aspire actually uses, and
whether Twenty (self-hosted) + n8n can replace each one.

This repo is both the audit tooling and the deployment: the Docker stack, the
object model, the demo data and the n8n workflow library. Twenty itself is not
vendored here — `infra/docker-compose.yml` runs the published image. The repo is
the recipe; Docker runs the kitchen.

## Read first

| Document | What it answers |
|---|---|
| [`docs/08-local-build.md`](docs/08-local-build.md) | **Start here to build.** Laptop to demo-ready CRM, step by step |
| [`docs/05-runbook.md`](docs/05-runbook.md) | The GHL audit runbook — exact commands and what to send back |
| [`docs/00-audit-method.md`](docs/00-audit-method.md) | How the audit runs — six tiers, disposition rules, what it cannot tell you |
| [`docs/01-scope-and-questions.md`](docs/01-scope-and-questions.md) | Open decisions still outstanding |
| [`docs/02-ghl-api-findings.md`](docs/02-ghl-api-findings.md) | Verified API facts with sources — read before writing anything against GHL |
| [`docs/03-replacement-stack.md`](docs/03-replacement-stack.md) | What "no GHL in Phase 1" actually requires. 52 ESCALATE rows → 8 vendor decisions |
| [`docs/04-manual-sweep.md`](docs/04-manual-sweep.md) | Tier 5 checklist for everything no API serves |
| [`docs/06-twenty-object-model.md`](docs/06-twenty-object-model.md) | The 25-object GHL parity model provisioned into Twenty |
| [`docs/07-build-plan.md`](docs/07-build-plan.md) | **The build plan.** What Twenty + n8n alone delivers, and the sprint sequence |
| [`CLAUDE.md`](CLAUDE.md) | Operating rules. Read-only, no PII, rate limits |

## Quick start

```bash
# Build the CRM (see docs/08-local-build.md)
./infra/up.sh                             # stack up, secrets generated
python scripts/stack_verify.py            # every layer, worker included
python scripts/twenty_provision.py        # 25 objects, 132 fields
python scripts/seed_demo_data.py          # 448 demo records
python scripts/n8n_deploy.py              # 6 workflows

# Audit GHL
cp .env.example .env          # fill in GHL_TOKEN, GHL_LOCATION
pip install -r scripts/requirements.txt   # optional

python scripts/ghl_pull.py --dry-run      # show the plan, call nothing
python scripts/ghl_pull.py --only custom_fields   # verify one endpoint first
python scripts/ghl_pull.py                # full discovery run
python scripts/twenty_probe.py            # probe the live Twenty instance
python scripts/twenty_provision.py --dry-run   # preview the object model
python scripts/twenty_provision.py         # build it
python scripts/build_audit.py             # → out/feature_audit.csv
```

Read `out/pull_coverage.csv` before drawing any conclusion from `raw/`. It records
which endpoints answered, which 401'd on a missing scope, and which do not exist
on this account tier.

## Layout

```
reference/ghl_feature_taxonomy.csv   111-feature master checklist — the spine
reference/ghl_endpoints.yaml         43 read-only endpoints driving the puller
reference/twenty_schema.yaml         25-object GHL parity model for Twenty
scripts/ghl_pull.py                  GET-only, throttled, PII-classified puller
scripts/twenty_probe.py              reads the live Twenty instance's real capability
scripts/twenty_provision.py          builds the object model via the Metadata API
scripts/build_audit.py               merges evidence + taxonomy → the report
scripts/build_n8n_workflows.py       generates the n8n workflow library
scripts/n8n_deploy.py                pushes workflows into n8n (idempotent)
n8n/workflows/                       6 workflows, 58 nodes — the foundation
infra/docker-compose.yml             Twenty + n8n + Postgres + Redis + backups
infra/up.sh                          one-command bring-up, generates secrets
scripts/stack_verify.py              verifies every layer independently
scripts/seed_demo_data.py            448 deterministic Aspire demo records
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
