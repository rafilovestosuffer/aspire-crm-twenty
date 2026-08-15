# Build Plan — Twenty + n8n Only

**Decision taken:** build everything achievable with Twenty and n8n alone.
Third-party services are deferred, not designed out.

This document is the construction plan. It supersedes the sequencing in
`docs/03-replacement-stack.md`, which assumed vendors were selected first.

---

## 1. What "Twenty + n8n only" actually delivers

One correction to the earlier analysis, and it matters: **n8n hosts public
forms itself.** The `Form Trigger` node serves a real web form — multi-page,
file upload, validation — at a public URL. Forms were in the vendor column in
`docs/03`. They should not have been. That was wrong, and it moves a whole
cluster back in scope.

With that corrected, here is the honest coverage.

### Fully buildable — no third party, production-grade

| Capability | How | GHL parity |
|---|---|---|
| Complete data model, 25 objects | Twenty Metadata API, scripted | **Better** — GHL has no subscription object |
| All record automation | Twenty workflows | Equal |
| All multi-step / conditional automation | n8n | **Better** — retries, error paths, sub-workflows |
| **Public forms & surveys** | **n8n Form Trigger** | Equal for capture; no drag-drop designer |
| Lead capture, dedup, scoring, routing | n8n | **Better** — scoring rules are readable |
| Tracked links & click tracking | n8n webhook + redirect | Equal |
| Transactional / 1:1 email | n8n SMTP via company mailbox | Equal |
| Internal notifications | n8n → Slack | Equal |
| Scheduled work & absence detection | n8n Schedule Trigger | Equal — replaces stale/no-show/overdue triggers |
| Consent capture & enforcement | `consentRecord` + send sub-workflow | **Better** — enforced structurally, with proof |
| Nurture sequences | n8n state machine | Equal at low volume |
| Human approval gates | n8n Wait + resume URL | **Better** — GHL has no equivalent |
| AI: scoring, summarising, drafting | n8n LLM nodes | **Better** — choose the model |
| Automation observability | `automationRun` records | **Better** — GHL exposes none |
| Pipeline & activity reporting | Twenty views | Equal for sales reporting |

### Buildable with real effort, degraded against GHL

| Capability | Approach | Honest assessment |
|---|---|---|
| Booking pages | n8n serves the page; Google Calendar free/busy for availability; writes `appointment` | Works. Perhaps 3–5 days to build well. No buffers/round-robin unless coded |
| Landing pages | n8n `Respond to Webhook` returning HTML | Functional, not a page builder. Fine for a thank-you or a gated download; wrong tool for a marketing site |
| Bulk email | n8n SMTP, throttled, consent-checked | **Volume-limited.** See below |

### Bulk email — the one place to be careful

Sending campaigns through the company mailbox is viable at Aspire's likely
scale, but only with eyes open:

- **Daily caps are real.** Google Workspace allows roughly 2,000 external
  recipients per day; Microsoft 365 around 10,000. A few thousand B2B contacts
  fits. A consumer-scale list does not.
- **DKIM/SPF/DMARC already exist** on the company domain if Aspire uses
  Workspace or 365 — so the deliverability foundation is not missing, it is
  inherited.
- **The suppression gap closes itself in this build.** `consentRecord` plus the
  send sub-workflow enforces opt-out at send time, which is the thing an ESP
  would otherwise provide.
- **What is genuinely missing:** bounce and complaint feedback. SMTP gives no
  webhook, so hard bounces will not automatically write back into
  `consentRecord`. Mitigation: parse the bounce mailbox with n8n's IMAP trigger
  on a schedule. Not as good as an ESP webhook, but not nothing.
- **The risk that matters:** sending campaign volume from the mailbox that also
  carries genuine sales conversations puts *both* at risk if reputation slips.
  Use a subdomain sender if this goes beyond a few hundred a day.

### Genuinely impossible without a third party

Three things, and no amount of engineering changes them:

| Capability | Why it cannot be built |
|---|---|
| **SMS / MMS** | Requires a licensed carrier connection and A2P registration. This is regulatory, not technical — the same reason software cannot make itself a phone company |
| **Voice, phone numbers, IVR, recording** | Same |
| **WhatsApp Business** | Meta only issues API access through approved providers |

Everything else on the GHL surface has a path in this plan.

**Practical effect:** roughly **85% of the catalogued surface is buildable now**,
and the remaining 15% is concentrated in telephony. If the audit shows SMS and
voice are lightly used, the deferred vendor decision may stay deferred for a
long time.

---

## 2. Build sequence

Eight to nine weeks, one engineer, production-grade. Each sprint has a gate —
do not start the next until the gate passes.

### Sprint 0 · Infrastructure — 3 days

| Task | Detail |
|---|---|
| Provision host | 8 GB RAM minimum for Twenty + n8n + Postgres together |
| Generate secrets | Four separate `openssl rand -base64 32`. **Back up `TWENTY_ENCRYPTION_KEY` and `N8N_ENCRYPTION_KEY` off-host** — losing either loses every stored credential |
| Bring the stack up | `docker compose -f infra/docker-compose.yml up -d` |
| Reverse proxy + TLS | Two hostnames: CRM and automation. Both need real certificates |
| Verify the worker | `docker compose ps` — the Twenty UI looks healthy while the worker is dead |
| Confirm backups | Check a dump lands in `infra/backups/`, then **restore it into a scratch database** |

**Gate:** Twenty and n8n reachable over HTTPS; a restore has been performed, not
just a backup taken.

### Sprint 1 · Data model and n8n foundation — 1 week

| Task | Detail |
|---|---|
| Provision the object model | `python scripts/twenty_provision.py --dry-run` then for real. 25 objects, 132 fields, 20 relations |
| Create Twenty API key | Settings → API & Webhooks. Scope it to a role, not admin |
| Create n8n credentials | `python scripts/n8n_credentials.py` — writes `Twenty API` (header auth) and `Aspire SMTP`. Chat alerts use an incoming webhook (`ALERT_WEBHOOK_URL`), not a credential |
| Validate every Twenty call | `python scripts/validate_workflow_queries.py` — checks all 28 against the live schema |
| Deploy the workflows | `python scripts/n8n_deploy.py --dev` — inactive at first |
| Test the error handler | Force a failure; confirm the chat alert and the `automationRun` record |
| Test the send sub-workflow | Send to yourself. Then **flip consent to opted-out and confirm it refuses** |
| Prove the whole layer | `python scripts/prove_workflows.py` — 25 checks against the running stack |

**Gate:** a deliberate failure produces an alert, and the consent gate demonstrably blocks a send.

### Sprint 2 · Capture and the first real automations — 1 week

| Task | Detail |
|---|---|
| Activate the public form | Real submission → person, company, consent, task, acknowledgement, Slack |
| Activate tracked links | Create a `trackedLink`, click it, confirm the redirect and the count |
| Activate renewal escalation | Seed a subscription renewing in 90 days; confirm the task appears |
| Activate scheduled sweeps | Confirm the daily digest arrives |
| Add remaining forms | One workflow per GHL form that has real submissions |

**Gate:** every activated workflow has run end to end against real data at least once.

> **Demo here.** Renewal escalation needs no vendor and could not be built in
> GHL at all — it has no subscription object. A working demo does more for
> confidence than any document.

### Sprint 3 · Data migration — 1 week

| Task | Detail |
|---|---|
| Export from GHL | Read-only puller (`scripts/ghl_pull.py`) |
| **Export the suppression list** | **Before anything else.** Unrecoverable after termination |
| Load companies → people → opportunities | In that order, so relations resolve. Batch 60, respect 100 req/min |
| Load `consentRecord` | From the suppression export. **Reconcile counts on both sides** |
| Load subscriptions | The object GHL never had — map from deals plus custom fields |
| Reconcile | Record counts, spot-check 20 records field by field |

**Gate:** counts match; consent count matches exactly; 20 spot-checks clean.

### Sprint 4 · Full automation rebuild — 2 weeks

| Task | Detail |
|---|---|
| Inventory GHL workflows | Manual — the API exposes no internals (`docs/02` §1) |
| Discard pass | Drafts, zero-enrolment, duplicates. **Use a 365-day window for anything annual** |
| Rebuild in priority order | Internal-only → Twenty-native → capture → email |
| Verify each | Fires · same behaviour · fails safely (`docs/00` §Tier 6) |
| Spot-check five by hand | Against the live GHL definitions |

**Gate:** every surviving GHL workflow is rebuilt or explicitly marked dropped, with a reason.

### Sprint 5 · Hardening and parallel run — 2 weeks

| Task | Detail |
|---|---|
| Load test | Migrate a realistic volume; confirm the UI past 10k records |
| Rate-limit test | Run the heaviest job; confirm no 429 storms |
| Restore test | Full restore into a scratch stack. Record the date |
| Monitoring | Alert on: worker down, workflow failure rate, execution ceiling, disk |
| Runbook | Someone other than you must be able to operate it |
| Parallel run | Both systems live; reconcile daily |

**Gate:** the parallel-run exit criteria in `docs/migration-guide` §30.2 —
≥90% of new records originating in Twenty, ≥99% data parity, reporting within
2%, consent counts exact, automation failure rate under 1%.

### Sprint 6 · Cutover — 1 week

Follow the pre-cutover checklist. **Keep GHL read-only for 30–60 days after
cutover.** Termination is irreversible; the cost of keeping it is trivial
against discovering in week three that a form nobody inventoried was still
feeding leads.

---

## 3. Production requirements

These separate a working build from a production one. They are not optional
for a system holding client security-posture data at a company that sells
compliance.

### Environments

Two n8n instances, or at minimum two credential sets. A test execution that
emails a real client is a career-grade mistake and it happens on day one.

### Testing

| Layer | Approach |
|---|---|
| Workflow | Pin an execution, edit, re-run against the pinned data |
| Integration | A `[TEST]` company and person that every workflow can safely touch |
| Consent | Explicit test: opted-out person must never receive a send |
| Failure | Break a node deliberately; confirm the error workflow catches it |

### Secrets

`TWENTY_ENCRYPTION_KEY` and `N8N_ENCRYPTION_KEY` backed up **off this host**.
Twenty API key scoped and rotated on a schedule. No credential inline in a node,
ever — always the n8n credential store.

### Backup and restore

Nightly dumps are configured. **An untested backup is not a backup.** Restore
into a scratch stack before go-live and quarterly after. Record each test date.

### Monitoring

| Signal | Why |
|---|---|
| Twenty worker alive | The UI stays healthy while scheduled work silently stops |
| Workflow failure rate | Rising rate means something upstream changed |
| Executions per hour per workflow | Catches a runaway loop before it exhausts the API |
| Disk on the Postgres volume | Execution data grows fast; pruning is set to 14 days |
| Backup freshness | A backup job that silently died is the worst failure here |

### Handover

Runbook, workflows in Git, a named second person. Right now this project has one
point of failure and it is a person, which is on the risk register for a reason.

---

## 4. What already exists in this repo

| Component | Status |
|---|---|
| `infra/docker-compose.yml` | Full stack: Postgres, Redis, Twenty server + worker, n8n, nightly backup |
| `reference/twenty_schema.yaml` | 25 objects, 132 fields, 20 relations |
| `scripts/twenty_provision.py` | Idempotent three-pass provisioner |
| `scripts/build_n8n_workflows.py` | Generates the workflow library |
| `n8n/workflows/*.json` | **6 workflows, 58 nodes** — error handler, send email, public form, tracked links, renewal escalation, scheduled sweeps |
| `scripts/n8n_deploy.py` | Idempotent deploy, dependency-ordered |
| `scripts/ghl_pull.py` | Read-only GHL extraction |
| `scripts/build_audit.py` | Evidence → disposition |

The six workflows are the foundation, not the finished estate. They establish
every pattern the rest need: signature handling, consent enforcement, batching
against the rate limit, error routing, and `automationRun` logging. Workflows
seven through fifty are variations.

---

## 5. Next five actions

1. **Provision a host and bring the stack up.** Sprint 0 blocks everything.
2. **Export the GHL suppression list.** Independent of every other decision,
   and unrecoverable once the account closes.
3. **Run the provisioner** against the live Twenty instance. Send me any
   relation errors — payload shapes vary by version and it is a one-line fix.
4. **Deploy and test the six workflows.** Start with the error handler, then
   prove the consent gate blocks an opted-out send.
5. **Build renewal escalation into a demo.** No vendor, high visibility,
   impossible in GHL.

Then tell me what breaks. The next batch of workflows should be written against
your real field names and real GHL workflow definitions, not against assumptions.
