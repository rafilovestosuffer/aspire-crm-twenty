# Open Decisions and Blocking Questions

Recorded so they are answered deliberately rather than assumed. Numbered for
reference in the report and in JIRA.

---

## Q1 — Is GHL out entirely in Phase 1, or retained for sending? **(blocking)**

This is the question that sizes the whole project, and there are currently two
answers on record.

- **Master context §3.2, decision 1** — reviewed and annotated by the boss:
  GHL is *"retained as secondary CRM and sending platform."*
- **Current instruction:** *"in our first phase, we don't want to add GHL. We are
  only doing everything by Twenty."*

These lead to very different projects:

| | GHL retained for sending | GHL fully out in Phase 1 |
|---|---|---|
| Bulk email, SMS, voice, forms, booking pages, funnels | Stay in GHL — mapped, not replaced | Every one needs a **replacement vendor, contract and budget** |
| ~30 execution-layer items of the 82 | `RETAIN-GHL` | `ESCALATE` |
| Audit output | A mapping exercise | A mapping exercise **plus a procurement plan** |
| Realistic Phase 1 shape | Twenty as record layer | Twenty + n8n + 3–5 new paid services |

The reason this cannot be finessed: Twenty's `Send Email` action sends from a
synced mailbox, **one recipient at a time** (`docs/02` §6). It has no campaign
engine, no suppression list, no SMS, no telephony, no forms, no booking pages.
n8n can orchestrate a send but cannot be the sender. So "everything by Twenty"
is not achievable for any feature that reaches a person through a channel — not
because of a configuration gap, but because those capabilities do not exist in
either tool.

**What is needed:** confirmation of which reading is current. If Phase 1 really
does exclude GHL, the audit stays the same but its output becomes a vendor
shortlist with costs, and the timeline extends well past the current estimate.

---

## Q2 — Agency or sub-account access? **(blocking Tier 2)**

Check whether **Snapshots** appears in the settings menu.

- **Agency:** snapshot export is available. Tier 2 runs, and the funnel /
  website / dashboard inventory comes free.
- **Sub-account only:** Tier 2 is closed. Those inventories move to the manual
  sweep, adding roughly a day.

Also needed: **how many sub-accounts are in scope?** One location and twelve
locations are different audits. `raw/sub_accounts.json` answers this if the token
is agency-level.

---

## Q3 — What is the scope list on the token?

Tick every scope ending `.readonly`; tick nothing implying write. The exact list
varies by account tier — do not force it. **Paste the granted list back**, because
it defines the audit's reach and belongs in the report as a stated limitation.
Any endpoint returning 401/403 in `out/pull_coverage.csv` maps to a missing scope,
not a broken script.

---

## Q4 — Where does the token live, and where does the puller run?

The puller is designed to run **on Rafi's machine against a local clone**, with
`.env` gitignored and `raw/` never committed. Nothing about the audit requires
the token to be present in a cloud session.

Worth being explicit about, per master context §15.1: an assistant session is not
a privacy boundary — whatever it reads, it reads. The governance control that
actually matters is *what gets pulled* (configuration, never contacts), which the
registry enforces by classification. Where the script runs is a separate,
narrower question about where the credential sits.

---

## Q5 — Which evidence window? *(default: 90 days)*

90 days is the working default. Anything quarterly or annual — renewal cycles,
compliance review reminders, annual training resets — will look dead in a 90-day
window. Aspire sells annual compliance engagements, so this risk is real, not
theoretical. Suggested handling: run at 90 days for the discard pass, then
re-check anything named like a renewal or review workflow at 365 before dropping
it.

---

## Q6 — Where does workflow execution volume come from?

The plan discards workflows with no contacts in 90 days. **That number is not
available via the public API** (`docs/02` §2). It has to come from the workflow
execution-log view in the UI, or from the same authenticated-session route as the
internals. Confirm which, because the discard pass depends on it and it is the
one place the plan currently assumes data that does not exist.

---

## Q7 — Does the sunset date exist yet?

Still open from master context §12.1. If Phase 1 excludes GHL (Q1), a sunset date
stops being a governance nicety and becomes a **contract-termination date** with
budget consequences. It should be answered in the same conversation as Q1.

---

## Q8 — Denominator for the gap list

Carried from master context §12.2. When reporting completeness, state explicitly
whether the percentage is against *CRM capability in general* or against *the
82-item plan*. The two produce very different numbers and the difference will be
noticed.

---

## Standing note for the boss

This audit is not in the 82-item catalogue and has no Ref ID or sprint slot.
Realistically it is **2–3 weeks**, and MT-7 (the functional gap list) is blocked
behind it. Saying so before it starts costs nothing; saying so afterwards reads
as an excuse for sprint slippage.
