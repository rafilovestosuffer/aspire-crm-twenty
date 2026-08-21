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
| [`docs/06-twenty-object-model.md`](docs/06-twenty-object-model.md) | The 31-object GHL parity model provisioned into Twenty |
| [`docs/09-status-and-handover.md`](docs/09-status-and-handover.md) | **Start here.** What exists, what is proven, what is left, what it costs, how to run it |
| [`docs/11-internal-server-deployment.md`](docs/11-internal-server-deployment.md) | **Office server** (private IP, no inbound internet). Written for a first-time server admin |
| [`docs/10-vps-deployment.md`](docs/10-vps-deployment.md) | Company server: DNS, TLS, the editor allowlist, and what this doc does *not* do |
| [`docs/07-build-plan.md`](docs/07-build-plan.md) | **The build plan.** What Twenty + n8n alone delivers, and the sprint sequence |
| [`CLAUDE.md`](CLAUDE.md) | Operating rules. Read-only, no PII, rate limits |

## Quick start

**Laptop (demo):**

```bash
./infra/rebuild.sh          # zero to proven, ~12 min, stops at the first failure
```

**Company VPS:** DNS for `crm.aspiretss.com` and `auto.aspiretss.com` must
already point here. Then:

```bash
./infra/init-vps-env.sh --yes
nano infra/.env             # SMTP, office IPs, chat webhook, payment links
./infra/preflight.sh
./infra/deploy.sh
```

Full sequence, including Docker install: [`docs/10-vps-deployment.md`](docs/10-vps-deployment.md).

Laptop, step by step — see [`docs/08-local-build.md`](docs/08-local-build.md):

```bash
./infra/up.sh                             # stack up, secrets generated
python scripts/stack_verify.py            # every layer, worker included
python scripts/bootstrap_workspace.py     # Twenty user, workspace, API key
python scripts/bootstrap_n8n.py           # n8n owner + API key
python scripts/twenty_provision.py        # 31 objects, 198 field definitions
python scripts/seed_demo_data.py          # 448 demo records
python scripts/n8n_credentials.py         # Twenty header auth + SMTP
python scripts/validate_workflow_queries.py    # every query vs the live schema
python scripts/n8n_deploy.py --dev --activate  # 19 workflows (2 dev-only)
python scripts/prove_workflows.py         # 77 checks against the running stack
python scripts/implementation_audit.py    # honest per-feature status

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
reference/twenty_schema.yaml         31-object GHL parity model for Twenty
scripts/ghl_pull.py                  GET-only, throttled, PII-classified puller
scripts/twenty_probe.py              reads the live Twenty instance's real capability
scripts/twenty_provision.py          builds the object model via the Metadata API
scripts/build_audit.py               merges evidence + taxonomy → the report
scripts/build_n8n_workflows.py       generates the n8n workflow library
scripts/validate_workflow_queries.py every Twenty call vs the live schema
scripts/n8n_credentials.py           creates the n8n credentials headlessly
scripts/n8n_deploy.py                pushes workflows into n8n (idempotent)
scripts/prove_workflows.py           runs the workflows, asserts what happened
scripts/implementation_audit.py      per-feature status, measured not claimed
n8n/workflows/                       19 workflows — 17 real, 2 dev-only (alert sink, failure probe)
infra/docker-compose.yml             Twenty + n8n + Postgres + Redis + backups
infra/up.sh                          one-command bring-up, generates secrets
infra/rebuild.sh                     zero to proven, every step a gate (demo machine)
infra/preflight.sh                   is this server ready? run before deploying
infra/init-vps-env.sh                generate secrets + public hostnames on a VPS
infra/deploy.sh                      one-command server deploy, eleven gated steps
infra/harden-host.sh                 unattended-upgrades + fail2ban (run as root)
infra/watchdog.sh                    hourly stack_verify → chat webhook on failure
infra/backup-offsite.sh              rsync infra/backups/ off this disk
scripts/import_consent_csv.py        GHL suppression CSV → consentRecord
scripts/mark_enrolment_paid.py       PAYMENT_SENT → PAID after money cleared
infra/Caddyfile                      reverse proxy + Let's Encrypt (internet-facing)
infra/Caddyfile.internal             reverse proxy + local CA (office network)
scripts/verify_restore.py            restores a backup and compares row counts
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
