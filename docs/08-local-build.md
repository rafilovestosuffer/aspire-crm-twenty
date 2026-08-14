# Local Build Runbook

From an empty laptop to a CRM you can demo. Roughly a day of hands-on work; the
first `up.sh` run takes ~10 minutes on its own while images download and Twenty
runs its migrations.

Everything here moves to the VPS later by changing `infra/.env` and adding one
overlay file. No rebuild.

---

## Prerequisites

- Docker Desktop (or Docker Engine + Compose v2)
- **6 GB RAM free.** Twenty alone wants 4 GB; Postgres, Redis and n8n share the rest
- Python 3.9+ for the scripts
- `openssl` (already on macOS and Linux)

---

## Step 1 — Start the stack

```bash
git clone https://github.com/rafilovestosuffer/aspire-crm-twenty.git
cd aspire-crm-twenty

./infra/up.sh
```

`up.sh` copies `infra/.env.example` to `infra/.env`, generates the three secrets,
pulls images, starts everything, and waits for Twenty to report healthy.

> **Back up `ENCRYPTION_KEY` and `N8N_ENCRYPTION_KEY` from `infra/.env` to
> somewhere other than this laptop.** Lose `ENCRYPTION_KEY` and every secret
> Twenty holds — OAuth tokens, connected mailboxes, application variables —
> becomes permanently unreadable. There is no recovery path.

The image tag is pinned to **v2.1.0**, the first release Twenty marks
production-ready for single-tenant self-hosting. Do not switch to `latest`: an
unattended image change can run migrations you did not choose.

## Step 2 — Create the workspace and keys

Both are scripted. Neither needs a browser, so a rebuild is reproducible rather
than a sequence of remembered clicks:

```bash
python scripts/bootstrap_workspace.py    # Twenty user, workspace, API key
python scripts/bootstrap_n8n.py          # n8n owner + API key
python scripts/n8n_credentials.py        # the two n8n credentials
```

`n8n_credentials.py` writes the `Twenty API` header-auth credential and an
`Aspire SMTP` credential. With the SMTP block in `infra/.env` left blank it
points at Mailpit, so the send path is fully exercisable with no mail account
and no risk of reaching a real contact.

Each script writes its key back into `infra/.env`. To do it by hand instead:
Twenty → Settings → API & Webhooks, and n8n → Settings → n8n API.

```bash
python scripts/stack_verify.py
```

Checks Postgres, both databases, Redis, Twenty's health endpoint, **the worker
container separately**, n8n, and both APIs. The worker gets its own check
because Twenty's UI looks completely healthy while the worker is dead — and a
dead worker means no scheduled workflows and no mailbox sync.

**Gate:** all green.

## Step 3 — Build the object model

```bash
python scripts/twenty_provision.py --dry-run     # prints every payload
python scripts/twenty_provision.py
python scripts/twenty_probe.py                   # → out/twenty_capability.md
```

25 objects, 132 fields, 20 relations. Idempotent — safe to re-run after a
partial failure.

**If relations fail:** `relationCreationPayload` shapes vary between Twenty
versions. Run `--skip-relations` to get the scalar fields in, then send me the
error. The scalar fields are the bulk of the value and nothing else depends on
relations landing first.

**Gate:** `twenty_probe.py` reports all four Aspire objects present.

## Step 4 — Seed the demo data

```bash
python scripts/seed_demo_data.py --dry-run
python scripts/seed_demo_data.py                 # ~6 minutes
```

448 records: 40 companies, 120 people, 35 opportunities, 25 subscriptions,
18 compliance engagements, 15 training accounts, 45 phishing baselines,
120 consent records, 30 tasks.

The data is deliberately shaped for the demo:

| Detail | Why |
|---|---|
| 8 subscriptions renewing within 90 days | The escalation workflow has real targets to find |
| 8 contacts opted out | Lets you *show* the consent gate refusing a send, not just describe it |
| 7 training accounts above 90% seat usage | A visible upsell signal |
| Phishing scores trending down over 3 quarters | Tells the account-health story in one chart |

Deterministic — every run produces identical records, so the demo looks the same
at rehearsal and on the day. `--wipe` resets.

**Gate:** no view shows an empty state.

## Step 5 — Native Twenty features

No n8n. Configuration only, and a big part of the demo.

**Email + calendar sync.** Settings → Accounts → connect a mailbox. Gmail /
Google Workspace and Microsoft sync two-way; IMAP and CalDAV also work. Restrict
which labels or folders sync, and set exclusions for personal and team addresses
(`support@`, `info@`). Threads and calendar events then appear on record
timelines, resyncing every ~5 minutes.

This is the honest answer to "where did the GHL inbox go?" for email — which is
likely most of Aspire's conversation volume.

**Saved views**, one per demo beat:

| View | Object | Filter |
|---|---|---|
| Renewals — next 90 days | Service Subscription | `renewalDate` within 90 days, sorted ascending |
| CMMC Level 2 accounts | Compliance Engagement | `cmmcLevel = Level 2` |
| Seats near capacity | Training Account | sort by consumed ÷ purchased |
| Pipeline | Opportunity | Kanban by stage |

**Two native workflows** so automation visibly lives in the CRM too — for
example *Opportunity stage → Proposal* creates a follow-up task.

**One non-admin role** with least privilege, to show it is not a toy.

## Step 6 — Deploy the automations

```bash
python scripts/build_n8n_workflows.py       # regenerate (already committed)
python scripts/validate_workflow_queries.py # every Twenty call vs live schema
python scripts/n8n_deploy.py --dev          # --dev adds the local alert sink
python scripts/prove_workflows.py           # run them and check what happened
```

Three gates, in that order, and they exist for different reasons.

**`build_n8n_workflows.py` refuses to emit** a workflow containing an
expression that would fail at runtime — chiefly a nested `}}`, which closes an
n8n expression early and turns the rest into a syntax error.

**`validate_workflow_queries.py`** checks every Twenty REST call against the
running instance. This is the important one. Twenty does not reliably reject a
malformed query: send the `filter[field][eq]=value` shape that n8n users reach
for first and it **ignores the filter and returns the whole table**. Nothing
errors. The workflow uses row 0 and carries on. That defect sent a lead
acknowledgement to an unrelated contact here, and the only visible symptom was
the wrong first name in the email.

The syntax the API actually wants:

| | Correct | Wrong, and silent |
|---|---|---|
| filter | `filter=field[eq]:value` | `filter[field][eq]=value` — returns everything |
| in | `filter=f[in]:[A,B]` | bare comma list — 400 |
| order | `order_by=field[DescNullsLast]` | `orderBy=` — ignored |

**`prove_workflows.py`** submits the real form, follows the real redirect,
triggers the real schedules, and then asks Twenty whether the records exist.
22 checks. Expect all of them to pass:

| Proves | Check |
|---|---|
| `LEAD Form Intake` | person, company, linked consent, scored task, acknowledgement to the right address |
| Consent gate | opted-out send **refused**, no mail, refusal logged in `messageLog` |
| `MSG Tracked Link Redirect` | 307 to the destination, `clickCount` incremented |
| `SYS Error Handler` | `automationRun` history written, chat alert delivered |
| `SUB Renewal Escalation`, `OPS Scheduled Sweeps` | run clean against seeded data |

Then `python scripts/n8n_deploy.py --activate`.

> Mailpit (`http://localhost:8025`) and the alert sink are **dev only**. Mailpit
> sits behind a compose profile the VPS never enables, and `n8n_deploy.py`
> refuses to deploy the sink without `--dev`, so production alerts can never be
> swallowed by it.

---

## The demo — 10 minutes

| # | Show | Say |
|---|---|---|
| 1 | A company record: people, subscription, compliance engagement, training account, phishing trend | "GoHighLevel can't model any of this. It forces everything into a deal." |
| 2 | *Renewals next 90 days*, the kanban, *Seats near capacity* | "Renewal exposure and upsell signals, both one click." |
| 3 | A contact's timeline with a real email thread and calendar event | "This is where the inbox went for email. Native, syncs every five minutes." |
| 4 | **Fill the public form live on screen** | "Watch." Person appears scored and assigned, task raised, acknowledgement sent, Slack pings. **"That form is hosted by our own stack. No third-party tool."** |
| 5 | Try to send to an opted-out contact | It refuses and logs why. "This is the control we tell clients to implement." |
| 6 | Run renewal escalation | Tasks and alerts appear. "GoHighLevel cannot do this — no subscription object." |
| 7 | The `automationRun` view | "Every run, success and failure. GoHighLevel exposes no execution history at all — that gap is what made the audit hard." |

**Order matters.** Lead the data model, land on the live form. Watching a
record appear from a form submission does more than any document.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Twenty never becomes healthy | First boot runs migrations; give it 5 minutes. Then `docker compose -f infra/docker-compose.yml logs server` |
| Login redirect loop | `SERVER_URL` does not match how you reach it, scheme included |
| `password authentication failed` | The Postgres password is baked into the volume. Either restore the original value or `./infra/up.sh --destroy` and start over |
| Scheduled workflows never fire | Worker container down. `docker compose ... logs worker` — the UI stays healthy regardless |
| Sluggish past ~10k records | `shm_size` and Postgres tuning are already set; confirm they applied |
| Twenty API returns 429 | 100 requests/minute. The scripts pace themselves; a custom loop must too |
| n8n form 404 on the production URL | Workflow must be **saved and active**; check `WEBHOOK_URL` matches `N8N_PUBLIC_URL` |
| Form submits but every field is null | Fields post as `field-0`, `field-1`… by position, not by label. Only affects scripted posts; a browser gets it right |
| Form URL is a UUID instead of `/form/aspire-contact` | The path moved into `options` at typeVersion 2.2. The generator sets both, so regenerate rather than editing in the UI |
| Workflow will not publish: "references workflow X which is not published" | Sub-workflow and error-workflow links resolve by id, not name. `n8n_deploy.py` binds them — deploy through it, not by importing JSON |
| A query returns far too many rows | Almost certainly the `filter[field][op]=` shape. Run `validate_workflow_queries.py` |
| n8n can't reach Twenty | Inside Docker the address is `http://server:3000`, not `localhost:3000` |

---

## Moving to the VPS

```bash
docker compose -f infra/docker-compose.yml -f infra/docker-compose.vps.yml up -d
```

The overlay binds both ports to loopback so only a reverse proxy reaches them.
In `infra/.env` change `SERVER_URL`, `N8N_HOST`, `N8N_PROTOCOL=https` and
`N8N_PUBLIC_URL` to the real hostnames. Terminate TLS at the proxy — several
Twenty and n8n features require a secure context.

**Before go-live:** restore a backup from `infra/backups/` into a scratch
database and record the date. An untested backup is not a backup.
