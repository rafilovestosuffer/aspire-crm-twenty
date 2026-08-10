# Replacement Stack — What "No GHL in Phase 1" Actually Requires

**Decision on record:** GoHighLevel is out entirely in Phase 1. Twenty + n8n
carry everything.

This document exists because that decision has a consequence the feature list
does not make obvious: **52 of 111 catalogued features currently classify as
ESCALATE** — meaning neither Twenty nor n8n can perform them. Presented as 52
problems, that reads as a failed plan. It is not. It is **eight decisions**, some
of which will evaporate once usage evidence arrives.

---

## Why these features have no home in Twenty or n8n

Twenty is a record layer. Its only sending capability is a workflow action
against a synced mailbox, **one recipient at a time** — no campaign engine, no
suppression list, no domain warm-up, no SMS, no telephony, no public forms, no
landing pages, no booking pages, no invoicing (`docs/02` §6).

n8n is an orchestration layer. It stores nothing and sends nothing. It can *call*
a sending service; it cannot *be* one.

So every GHL feature whose job is **reaching a person through a channel** needs a
third thing behind it. This is a structural property of the chosen architecture,
not a gap that configuration closes.

---

## The 52 ESCALATE rows collapse into eight decisions

| # | Cluster | Feature IDs | What must be bought or built | Likely to survive evidence? |
|---|---|---|---|---|
| A | **Email sending & deliverability** | EM-02…EM-09, DM-07 | ESP with API: campaigns, templates, suppression list, DKIM/SPF, dedicated domain, click tracking | **Yes — certain.** Aspire runs a newsletter (E-02) and outbound sequences |
| B | **SMS, voice & telephony** | SM-01…SM-07, CV-02, AI-01, RP-02 | CPaaS: numbers, A2P 10DLC, SMS, IVR, recording, voicemail | **Verify.** Depends entirely on whether sales actually dials from GHL |
| C | **Web presence** | FS-01…03, FW-01…05, BL-01…03, ST-03 | CMS/site platform + form tool | **Probably shrinks.** Check whether aspiretss.com is really hosted in GHL or only funnels are |
| D | **Scheduling & booking** | CL-01…04, RP-03 | Booking tool with API | **Likely yes.** Demo booking is core to a SOCaaS sales motion |
| E | **Commerce: payments, invoicing, quoting, contracts** | PY-01…07, FW-03 | Billing/invoicing + e-sign; CPQ maps to SA-04/SA-05 | **Verify.** Finance may already invoice outside GHL |
| F | **Omnichannel inbox** | CV-01, CV-03…07 | Shared inbox / helpdesk | **Verify.** Often enabled and barely used in B2B |
| G | **Memberships, courses, portal, certificates** | MC-01…04 | LMS / portal | **Probably drops.** Aspire already runs aspireelearning.com — likely duplicate |
| H | **Reputation & social** | RV-01…02, SO-01…03 | Review + social scheduling tools | **Verify.** Marketing may already own these outside GHL |

**Four of eight clusters (C, E, G, H) plausibly shrink to near-zero once evidence
arrives.** Cluster G in particular: Aspire operates a dedicated e-learning brand,
so GHL memberships are more likely a duplicate than a dependency. The honest
current estimate is **three to five real vendor decisions**, not 52 problems —
but that has to be *demonstrated by the audit*, not asserted now.

This is exactly why Tier 3 (the discard pass) runs before anything else.

---

## Sequencing risks — the items that cannot be compressed

These do not scale with effort. Adding people does not make them faster, and each
has burned real migrations. They belong on the project timeline **now**, not when
the audit finishes.

| Item | Lead time | Why it cannot be rushed |
|---|---|---|
| **A2P 10DLC registration** | Weeks | Carrier-side regulatory review. Unregistered traffic is filtered or blocked outright. Cannot start until the CPaaS vendor is chosen — so vendor choice, not migration, is the critical path |
| **Phone number porting** | Days–weeks | Numbers can be unreachable during the porting window. Irreversible on a short timescale. Any number printed on a website, email signature or contract is a live business dependency |
| **Email domain warm-up** | 2–6 weeks | Sending reputation is built by gradual volume ramp. A cold domain sending at full volume on day one lands in spam. **You cannot switch sending domains on Monday and campaign on Tuesday** |
| **Consent / suppression list export** | Must precede termination | Unsubscribe state lives in GHL. If the account closes before it is exported and loaded into the new ESP, previously-unsubscribed people get emailed again. That is a **compliance breach**, not an inconvenience — and it maps to F-09 and to the fact that Aspire *sells* compliance |
| **DNS / domain cutover** | Hours–days propagation | Must be choreographed with whatever replaces Cluster C |

**The consent export is the single highest-severity item in this document.**
Master context §8.3 treated consent as a deliberate exception that stays
authoritative in GHL. Removing GHL removes that home. Consent needs a new owner
named explicitly before any termination date is set.

---

## What this means for the sunset date (Q7)

With GHL retained, a sunset date was a governance nicety. With GHL removed, it is
a **contract termination date** gated by the longest lead time above — realistically
**warm-up plus A2P, not the migration itself**. A date chosen from the engineering
work alone will be wrong by roughly a month in the dangerous direction.

Recommended framing when this goes to the boss: *the migration is not the
critical path; vendor selection is.* Every week without a decision on Clusters A
and B is a week added to the end, because warm-up and A2P cannot begin until the
vendors exist.

---

## What this document does not do

It names **categories**, not products. Vendor selection needs pricing against
Aspire's real volumes — how many emails per month, how many SMS, how many numbers
— and those numbers come out of Tier 3. Recommending tools before the volume data
exists would be guessing, and the guess would be wrong in whichever direction is
most expensive.

Once Tier 3 lands, this document gets a second section: per-cluster options with
costs at Aspire's actual volumes.
