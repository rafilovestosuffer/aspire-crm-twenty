# Open Decisions and Blocking Questions

Recorded so they are answered deliberately rather than assumed. Numbered for
reference in the report and in JIRA.

---

## Q1 — Is GHL out entirely in Phase 1, or retained for sending? **RESOLVED 25 Aug 2026**

There were two conflicting answers on record; this closes it.

- **Master context §3.2, decision 1** — reviewed and annotated by the boss:
  GHL is *"retained as secondary CRM and sending platform."*
- **Earlier instruction (superseded):** *"in our first phase, we don't want to
  add GHL. We are only doing everything by Twenty."*

**Answer: GHL is retained.** GHL keeps running as the system of record — this
build is not replacing it in Phase 1. Its purpose is **business continuity**:
if the GHL subscription lapses, is forgotten, or the account is otherwise
lost, Aspire can keep operating — contacts, pipeline, and core workflows —
on infrastructure it owns, instead of going dark. It is insurance, not a
cutover.

This means:

| | Answer: GHL retained for sending |
|---|---|
| Bulk email, SMS, voice, forms, booking pages, funnels | Stay in GHL — mapped, not replaced |
| ~30 execution-layer items of the 82 | `RETAIN-GHL` |
| Audit output | A mapping exercise, not a procurement plan |
| Realistic Phase 1 shape | Twenty as record layer + n8n automation, running in parallel with GHL as a fallback, not instead of it |

The reason this could not be finessed either way: Twenty's `Send Email` action
sends from a synced mailbox, **one recipient at a time** (`docs/02` §6). It has
no campaign engine, no suppression list, no SMS, no telephony, no forms, no
booking pages. n8n can orchestrate a send but cannot be the sender. Under the
retained-GHL answer this is not a gap to close — GHL keeps doing those jobs.

**Follow-on:** the `ESCALATE` rows in `out/feature_audit.csv` and the vendor
list in `docs/03-replacement-stack.md` should be re-read against this —
several of those "needs a vendor decision" items are actually `RETAIN-GHL`
under this answer and do not need new budget. Re-pass the audit's
dispositions before treating that ESCALATE list as a procurement ask.

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

Still open from master context §12.1, but lower stakes now that Q1 is resolved
as GHL-retained: there is no Phase 1 cutover, so no contract-termination date
is forced by this project. GHL's own subscription/renewal date is still worth
tracking, since the whole point of this build is to survive that date lapsing
unexpectedly — but it is no longer a blocking question for this audit.

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
