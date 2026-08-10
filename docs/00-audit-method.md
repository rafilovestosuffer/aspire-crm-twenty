# Audit Method

**Objective.** Produce an evidence-backed answer to one question: *for every
GoHighLevel feature Aspire actually uses, can Twenty + n8n replace it — and if
not, what does it cost to replace it another way?*

**Deliverable.** `out/feature_audit.csv`, one row per feature, plus a short
management writeup naming the ESCALATE items and their cost.

**Not in scope.** Migrating data. Rebuilding workflows. Choosing replacement
vendors. This audit *sizes* those decisions; it does not make them.

---

## Governing principles

**1. Extract with tools, judge with people.** The blueprint is already in the
system as structured data. Screenshotting the UI photographs the wall instead of
reading the plumbing diagram — 200 workflows × 10 nodes is 2,000 clicks, several
days, and no way to know what you missed. Anything a script can pull, a script
pulls.

**2. Discard before you map.** Every feature retired is a feature nobody has to
migrate, test, or explain. The usage filter typically removes a third to a half
of the surface. Do it first, not last.

**3. Evidence or `unknown` — never a guess.** Every `in_use` value names its
source. A feature nobody has checked is `unknown`, and `unknown` is a legitimate,
reportable state. `no` retires something; guessing `no` silently kills a running
automation on go-live day and nobody notices for three weeks.

**4. Verify empirically, then automate.** The GHL docs and the live API disagree
often enough that a script written blind fails in ten places at once. So the
first run of the puller is a *discovery run*: it records status and response
shape per endpoint into `out/pull_coverage.csv` and treats failures as data, not
crashes. That gets the same safety as testing endpoints one at a time, in one
pass instead of fifteen.

**5. Config, never contacts.** The audit needs structure, not people. No contact
records, no message bodies, no call recordings, no submission payloads. This is
both a governance position and a practical one — Aspire sells compliance, and
the audit should meet the standard Aspire sells.

---

## Tiers

Each tier is independently useful. If the audit is cut short, the tiers completed
still stand on their own.

### Tier 0 — Access and scope *(blocks everything)*
Determine agency vs sub-account access, and how many sub-accounts are in scope.
Agency access unlocks the snapshot route; sub-account access closes it and makes
funnels/websites/dashboards a manual job. Create the read-only Private
Integration token, record the exact scope list granted — **that list defines what
the audit can and cannot reach, and belongs in the report.**

### Tier 1 — Structural pull *(≈60% of the surface, automated)*
`scripts/ghl_pull.py` walks the endpoint registry: custom fields, custom values,
tags, pipelines, calendars, forms, surveys, templates, products, invoices,
funnels, blogs, social accounts, users, workflow *names and statuses*.
Output: `raw/*.json` + `out/pull_coverage.csv`.

### Tier 2 — Snapshot diff *(agency only)*
Create a snapshot, export Snapshot Assets to CSV, diff against Tier 1. Anything
in the snapshot but not the pull is the manual-inspection list — and it is short.
Without agency access, skip and widen Tier 5.

### Tier 3 — Usage filter *(the discard pass)*
Compute DROP candidates from Tiers 1–2 before touching workflow internals.
Forms with no submissions, calendars with no bookings, channels with no
conversations, products with no orders. Note the known gap: **workflow execution
counts are not available via API** (`docs/02`, §2) — that signal comes from the
UI or the session route. Everything surviving this tier is what actually gets
audited.

### Tier 4 — Workflow internals *(the real work)*
Capture one workflow's step-tree XHR from DevTools, then replay across the
surviving workflow list. Checkpointed and resumable — the session token expires
mid-run and a restart-from-zero design will lose hours. One JSON per workflow
into `workflows/`, stored verbatim, parsed in a second pass.

### Tier 5 — Manual sweep *(what no API serves)*
Domains, DNS, phone/A2P registration, installed marketplace apps, Zapier/Make
connections, memberships, Voice AI prompts, dashboards. Roughly 15–20 pages.
**Copy page text, not screenshots** — text is searchable and diffable; an image
is neither.

### Tier 6 — Classification and report
`scripts/build_audit.py` merges everything into `out/feature_audit.csv`.
`criticality` and `effort_days` stay blank for a person to fill — they are
judgement, not data. Then **spot-check five rows by hand against the live GHL
UI.** If all five are right, trust the rest. If any is wrong, fix the rule and
re-run. A classification that is 90% right looks exactly like one that is 100%
right, right up until go-live.

---

## Disposition rules

| Value | Meaning |
|---|---|
| `TWENTY` | Native Twenty capability. Configuration only. |
| `N8N` | n8n reproduces it without Twenty involvement. |
| `TWENTY+N8N` | Record lives in Twenty, logic runs in n8n. |
| `DROP` | Evidence shows it is not in use. Retire it. |
| `ESCALATE` | Neither Twenty nor n8n can do it. Needs a vendor and a budget. |
| `UNKNOWN` | Not yet checked. Not a final state — the audit ends when this is zero. |

`ESCALATE` is not failure and must not be buried. It is the honest output of the
capability boundary in `docs/02` §6: Twenty is a record layer, and every feature
that reaches a human through a channel needs something else behind it. A short,
specific ESCALATE list beside a long TWENTY/N8N list is a credible report. A
report with no ESCALATE items is one nobody should believe.

---

## Parallel track: the Twenty side

The GHL inventory is only half the question. `scripts/twenty_probe.py` reads the
**live** instance — objects, fields, custom objects present — so the capability
matrix reflects the version actually deployed rather than the documentation.
Run it early: it also verifies MT-2 (the four Aspire custom objects) is complete.

---

## What this audit cannot tell you

State these plainly in the report rather than letting them be discovered later:

- Whether a workflow that *runs* is a workflow that *matters*. Execution counts
  measure activity, not value. Low-volume does not mean low-stakes — a renewal
  escalation that fires four times a year is critical.
- Performance of Twenty at Aspire's real data volume. Local Docker cannot
  establish this.
- Backup/restore behaviour under load, and TLS/reverse-proxy behaviour in
  production. Neither is testable locally.
- Anything living in a teammate's personal Zapier or Make account rather than in
  GHL. Ask; do not assume the absence of evidence is evidence of absence.
