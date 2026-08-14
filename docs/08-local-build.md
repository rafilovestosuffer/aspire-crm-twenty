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
git checkout claude/ghl-twenty-migration-audit-z4za7f

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

1. Open **http://localhost:3000** → create the workspace and your admin user
2. **Settings → API & Webhooks → create key** → paste into `infra/.env` as
   `TWENTY_API_KEY`
3. Open **http://localhost:5678** → **Settings → n8n API → create key** → paste
   as `N8N_API_KEY`

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
python scripts/build_n8n_workflows.py     # regenerate (already committed)
python scripts/n8n_deploy.py --dry-run
python scripts/n8n_deploy.py              # deploys INACTIVE
```

In n8n, create three credentials before activating:

| Credential | Type | Value |
|---|---|---|
| `Twenty API` | Header Auth | Name `Authorization`, Value `Bearer <TWENTY_API_KEY>` |
| `Aspire SMTP` | Send Email (SMTP) | Host, port 587/465, user, app password |
| `Aspire Slack` | Slack API | Bot token with `chat:write` |

Then test in this order — each is a gate:

1. **`SYS Error Handler`** — break a node deliberately. Confirm the Slack alert
   *and* the `automationRun` record with status FAILED.
2. **`VEND Send Email`** — send to yourself. Then set a contact's consent to
   Opted Out and run again: **it must refuse and log the reason.**
3. **`LEAD Form Intake`** — activate, open the form URL, submit. Confirm person,
   company, consent record, score, owner, task, acknowledgement email, Slack ping.
4. **`MSG Tracked Link Redirect`** — create a `trackedLink`, click it, confirm
   the redirect and the incremented count.
5. **`SUB Renewal Escalation`** — run manually. It should find the 8 seeded
   subscriptions inside 90 days.
6. **`OPS Scheduled Sweeps`** — run manually, confirm the digest.

Then `python scripts/n8n_deploy.py --activate`.

**Expect two fixes here.** The form workflow's acknowledgement step references
the created person id through a node path that needs checking against a real
execution, and node `typeVersion`s differ between n8n releases. Both are
one-line edits in `scripts/build_n8n_workflows.py` — regenerate and redeploy.

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
