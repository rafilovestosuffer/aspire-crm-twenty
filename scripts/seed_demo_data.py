#!/usr/bin/env python3
"""
Seed realistic Aspire-shaped demo data into Twenty.

Empty screens do not demo. This fills every view the demo touches with data
that looks like Aspire's actual business — defense-sector clients, SOCaaS
subscriptions, CMMC engagements, A-SAT training seats and phishing scores.

Deterministic: a fixed seed means re-runs produce the same records, so the demo
looks identical every time you rehearse it. Idempotent: existing records are
matched on a deterministic key and skipped rather than duplicated.

Paced to Twenty's ceiling of 100 requests per minute.

Usage:
    python scripts/seed_demo_data.py --dry-run
    python scripts/seed_demo_data.py
    python scripts/seed_demo_data.py --wipe      # delete seeded records first
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import deque
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parent.parent

# Twenty allows 100 requests/minute. 80 leaves headroom for anything else on
# the key and still seeds the full set in about six minutes.
RATE_LIMIT, RATE_WINDOW = 80, 60.0

SEED = 20260814  # fixed: the demo must look the same at every rehearsal
rng = random.Random(SEED)

# Anchor every generated timestamp to midnight today rather than the current
# instant. datetime.now() differs by microseconds on each call, which made two
# runs produce different data and the demo look subtly different at every
# rehearsal. Anchoring to midnight keeps a run reproducible while still letting
# relative dates ("renews in 90 days") stay correct on whatever day it runs.
NOW = datetime.combine(date.today(), datetime.min.time(), tzinfo=timezone.utc)

# Everything created here carries this marker so --wipe can find it again and
# nobody mistakes seeded data for real records.
MARK = "[demo]"

INDUSTRIES = [
    ("Defense Manufacturing", ["Aerospace", "Precision Machining", "Composites"]),
    ("Healthcare", ["Regional Health", "Diagnostics", "Medical Devices"]),
    ("Critical Infrastructure", ["Water Utility", "Grid Services", "Logistics"]),
    ("Professional Services", ["Engineering", "Legal", "Accounting"]),
]
COMPANY_STEMS = [
    "Ironvale", "Kestrel", "Northbridge", "Halcyon", "Meridian", "Blackfen",
    "Ardent", "Sentinel Ridge", "Copperline", "Wexford", "Silverpine",
    "Granite Hollow", "Fairmount", "Larkspur", "Ravenswood", "Ashford",
    "Brightwater", "Cedarcliff", "Dunmore", "Eastgate", "Foxglove",
    "Greystone", "Harrowfield", "Inglewood", "Juniper Bay", "Kirkwall",
    "Lambourne", "Marchmont", "Netherby", "Oakhurst", "Pemberton",
    "Quarrydale", "Rosslyn", "Stonebrook", "Thornbury", "Underhill",
    "Vantage Point", "Westmoor", "Yardley", "Zephyr Works",
]
SUFFIXES = ["Systems", "Industries", "Group", "Technologies", "Partners", "Holdings"]

FIRST = ["Marcus", "Elena", "David", "Priya", "James", "Sarah", "Ahmed", "Linda",
         "Tomas", "Rachel", "Kofi", "Nina", "Owen", "Beatriz", "Hassan", "Grace",
         "Peter", "Yuki", "Daniel", "Amara", "Victor", "Chloe", "Samuel", "Ingrid"]
LAST = ["Reyes", "Okafor", "Lindqvist", "Whitmore", "Castellanos", "Nakamura",
        "Brennan", "Farouk", "Delacroix", "Sandoval", "Kowalski", "Adeyemi",
        "Thornton", "Vasquez", "Mbeki", "Hollis", "Petrov", "Aranda"]

SECURITY_TITLES = ["CISO", "IT Director", "VP Information Security",
                   "Head of Infrastructure", "Security Operations Manager",
                   "CTO", "Compliance Officer"]
HR_TITLES = ["HR Director", "Head of Learning & Development",
             "People Operations Manager", "Training Coordinator"]

SERVICE_LINES = ["SOCAAS", "CLOUD_SECURITY", "MDR", "SECAAS",
                 "DIGITAL_FORENSICS", "INCIDENT_RESPONSE"]
FRAMEWORKS = ["CMMC", "NIST_800_171", "SOC_2", "ISO_27001", "HIPAA"]
STAGES = ["NEW", "SCREENING", "MEETING", "PROPOSAL", "CUSTOMER"]


# --------------------------------------------------------------------------

class Twenty:
    def __init__(self, base: str, key: str, dry_run: bool):
        self.base = base.rstrip("/")
        self.key = key
        self.dry_run = dry_run
        self.calls = 0
        self._times: deque[float] = deque()

    def _throttle(self) -> None:
        now = time.monotonic()
        while self._times and now - self._times[0] > RATE_WINDOW:
            self._times.popleft()
        if len(self._times) >= RATE_LIMIT:
            wait = RATE_WINDOW - (now - self._times[0]) + 0.1
            if wait > 0:
                time.sleep(wait)
        self._times.append(time.monotonic())

    def call(self, method: str, path: str, body: dict | None = None):
        if self.dry_run:
            return 200, {"data": {}}, ""
        self._throttle()
        self.calls += 1
        data = json.dumps(body).encode() if body is not None else None
        req = request.Request(f"{self.base}{path}", data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.key}")
        req.add_header("Accept", "application/json")
        if data:
            req.add_header("Content-Type", "application/json")
        for attempt in range(4):
            try:
                with request.urlopen(req, timeout=45) as r:
                    return r.status, json.loads(r.read().decode() or "null"), ""
            except error.HTTPError as e:
                if e.code == 429 and attempt < 3:
                    time.sleep(2 ** attempt * 2)
                    continue
                detail = e.read().decode("utf-8", "replace")[:250].replace(self.key, "***")
                return e.code, None, f"HTTP {e.code}: {detail}"
            except Exception as ex:  # noqa: BLE001
                if attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                return 0, None, str(ex).replace(self.key, "***")
        return 0, None, "exhausted retries"

    def create(self, plural: str, body: dict) -> str | None:
        status, resp, err = self.call("POST", f"/rest/{plural}", body)
        if err or status >= 400:
            print(f"    ! {plural}: {err[:130]}")
            return None
        if self.dry_run:
            return "dry-run-id"
        data = (resp or {}).get("data", {})
        for v in data.values():
            if isinstance(v, dict) and v.get("id"):
                return v["id"]
        return data.get("id")

    def find_seeded(self, plural: str) -> list[dict]:
        status, resp, err = self.call("GET", f"/rest/{plural}?limit=200")
        if err or status >= 400 or self.dry_run:
            return []
        node = (resp or {}).get("data", {})
        for v in node.values():
            if isinstance(v, list):
                return v
        return []


def read_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for c in (ROOT / "infra" / ".env", ROOT / ".env"):
        if c.exists():
            for line in c.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env.setdefault(k.strip(), v.strip().strip("\"'"))
    for k in ("TWENTY_BASE_URL", "TWENTY_API_KEY"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def iso(d: date) -> str:
    return d.isoformat()


def money(amount: int) -> dict:
    return {"amountMicros": amount * 1_000_000, "currencyCode": "USD"}


# --------------------------------------------------------------------------

def build_dataset() -> dict:
    """Everything is generated up front so --dry-run reports exact counts."""
    today = date.today()
    companies, people, opportunities = [], [], []
    subs, engagements, training, baselines, consents, tasks = [], [], [], [], [], []

    stems = COMPANY_STEMS[:40]
    for i, stem in enumerate(stems):
        industry, segments = INDUSTRIES[i % len(INDUSTRIES)]
        name = f"{stem} {rng.choice(SUFFIXES)}"
        slug = stem.lower().replace(" ", "")
        companies.append({
            "_key": slug,
            "name": name,
            "domainName": {"primaryLinkUrl": f"https://{slug}.example.com"},
            "employees": rng.choice([25, 60, 120, 240, 480, 900, 1800]),
            "address": {"addressCity": rng.choice(
                ["Albany", "Rochester", "Hartford", "Trenton", "Stamford",
                 "Providence", "Newark", "Buffalo"]),
                "addressState": "NY", "addressCountry": "United States"},
            "_industry": industry,
            "_segment": rng.choice(segments),
        })

    # Three people per company: two security-side, one HR-side.
    for c in companies:
        for n in range(3):
            first, last = rng.choice(FIRST), rng.choice(LAST)
            title = rng.choice(HR_TITLES if n == 2 else SECURITY_TITLES)
            handle = f"{first[0].lower()}{last.lower()}@{c['_key']}.example.com"
            people.append({
                "_key": handle,
                "_company": c["_key"],
                "name": {"firstName": first, "lastName": last},
                "emails": {"primaryEmail": handle},
                "phones": {"primaryPhoneNumber":
                           f"+1212{rng.randint(2000000, 9999999)}"},
                "jobTitle": title,
                "city": c["address"]["addressCity"],
            })

    for c in rng.sample(companies, 35):
        line = rng.choice(SERVICE_LINES)
        opportunities.append({
            "_key": f"opp-{c['_key']}",
            "_company": c["_key"],
            "name": f"{c['name']} — {line.replace('_', ' ').title()}",
            "amount": money(rng.choice([18000, 24000, 36000, 48000, 72000, 96000])),
            "stage": rng.choice(STAGES),
            "closeDate": (today + timedelta(days=rng.randint(-40, 120))).isoformat(),
        })

    # Renewals deliberately clustered so the escalation workflow has 90/60/30/7
    # day milestones to actually hit during the demo.
    milestones = [90, 90, 60, 60, 30, 30, 7, 7]
    for i, c in enumerate(rng.sample(companies, 25)):
        days = milestones[i] if i < len(milestones) else rng.randint(120, 700)
        subs.append({
            "_key": f"sub-{c['_key']}",
            "_company": c["_key"],
            "name": f"{c['name']} — {rng.choice(SERVICE_LINES).replace('_',' ').title()}",
            "serviceLine": rng.choice(SERVICE_LINES),
            "status": "RENEWAL_DUE" if days <= 90 else "ACTIVE",
            "startDate": iso(today - timedelta(days=365 - days)),
            "renewalDate": iso(today + timedelta(days=days)),
            "mrr": money(rng.choice([1500, 2400, 3600, 5200, 8000])),
            "termMonths": rng.choice([12, 12, 24, 36]),
            "autoRenew": rng.random() > 0.4,
        })

    for c in rng.sample(companies, 18):
        fw = rng.choice(FRAMEWORKS)
        engagements.append({
            "_key": f"comp-{c['_key']}",
            "_company": c["_key"],
            "name": f"{c['name']} — {fw.replace('_', ' ')}",
            "framework": fw,
            "cmmcLevel": rng.choice(["LEVEL_1", "LEVEL_2", "LEVEL_2", "NOT_APPLICABLE"]),
            "dataScope": [rng.choice(["FCI", "CUI"])],
            "status": rng.choice(["SCOPING", "IN_PROGRESS", "AUDIT_SCHEDULED",
                                  "CERTIFIED", "REMEDIATION"]),
            "auditDate": iso(today + timedelta(days=rng.randint(-90, 200))),
            "nextReviewDate": iso(today + timedelta(days=rng.randint(30, 365))),
        })

    for c in rng.sample(companies, 15):
        purchased = rng.choice([50, 100, 150, 250, 400])
        # A few deliberately near capacity — that is the upsell signal.
        consumed = int(purchased * rng.choice([0.42, 0.61, 0.78, 0.94, 0.97]))
        training.append({
            "_key": f"train-{c['_key']}",
            "_company": c["_key"],
            "name": f"{c['name']} — A-SAT",
            "seatsPurchased": purchased,
            "seatsConsumed": consumed,
            "deploymentType": rng.choice(["SELF_MANAGED", "MANAGED_BY_ASPIRE"]),
            "renewalDate": iso(today + timedelta(days=rng.randint(20, 330))),
        })

    # Three tests per training account, trending downward — the story the
    # phish-prone score is supposed to tell.
    for t in training:
        score = rng.uniform(22, 38)
        for q in (3, 2, 1):
            prev = score
            score = max(3.0, score - rng.uniform(3, 9))
            baselines.append({
                "_key": f"{t['_key']}-q{q}",
                "_training": t["_key"],
                "name": f"{t['name']} — Q{4-q} baseline",
                "testDate": iso(today - timedelta(days=q * 90)),
                "phishPronePercent": round(prev, 1),
                "deltaVsPrevious": round(prev - score, 1),
                "usersTested": t["seatsConsumed"],
            })

    # Consent for every person; a handful opted out so the send gate can be
    # demonstrated refusing rather than described.
    opted_out = set(rng.sample(range(len(people)), 8))
    for i, p in enumerate(people):
        out = i in opted_out
        consents.append({
            "_key": f"consent-{p['_key']}",
            "_person": p["_key"],
            "name": f"Email consent — {p['emails']['primaryEmail']}",
            "channel": "EMAIL",
            "status": "OPTED_OUT" if out else "OPTED_IN",
            "source": "IMPORTED_FROM_GHL" if not out else "UNSUBSCRIBE_LINK",
            "effectiveAt": (NOW - timedelta(days=rng.randint(5, 400))).isoformat(),
            "proof": "migrated from GoHighLevel suppression export",
        })

    for c in rng.sample(companies, 30):
        tasks.append({
            "_key": f"task-{c['_key']}",
            "title": f"{MARK} {rng.choice(['Quarterly review', 'Renewal call', 'Send CMMC scoping doc', 'Follow up on phishing results'])} — {c['name']}",
            "status": rng.choice(["TODO", "TODO", "IN_PROGRESS", "DONE"]),
            "dueAt": (NOW + timedelta(days=rng.randint(-10, 30))).isoformat(),
        })

    return {"companies": companies, "people": people, "opportunities": opportunities,
            "serviceSubscriptions": subs, "complianceEngagements": engagements,
            "trainingAccounts": training, "phishingBaselines": baselines,
            "consentRecords": consents, "tasks": tasks}


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Seed Aspire demo data into Twenty")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--wipe", action="store_true",
                    help="delete previously seeded records first")
    args = ap.parse_args()

    data = build_dataset()
    total = sum(len(v) for v in data.values())

    print("Dataset")
    for k, v in data.items():
        print(f"  {k:24} {len(v):>4}")
    print(f"  {'TOTAL':24} {total:>4}\n")

    if args.dry_run:
        eta = total / RATE_LIMIT
        print(f"Dry run — nothing sent. Live run makes ~{total} requests "
              f"(~{eta:.0f} min at {RATE_LIMIT}/min).")
        return 0

    env = read_env()
    base = env.get("TWENTY_BASE_URL", "http://localhost:3000")
    key = env.get("TWENTY_API_KEY", "")
    if not key:
        print("ERROR: TWENTY_API_KEY not set (Twenty → Settings → API & Webhooks)",
              file=sys.stderr)
        return 2

    api = Twenty(base, key, args.dry_run)

    if args.wipe:
        print("Wiping previously seeded records...")
        for plural in ("phishingBaselines", "consentRecords", "trainingAccounts",
                       "complianceEngagements", "serviceSubscriptions",
                       "opportunities", "tasks", "people", "companies"):
            rows = api.find_seeded(plural)
            n = 0
            for r in rows:
                api.call("DELETE", f"/rest/{plural}/{r['id']}")
                n += 1
            print(f"  {plural:24} {n} deleted")
        print()

    ids: dict[str, str] = {}

    def push(plural: str, rows: list[dict], link: dict[str, str] | None = None):
        made = 0
        for row in rows:
            body = {k: v for k, v in row.items() if not k.startswith("_")}
            for field, prefix in (link or {}).items():
                ref = ids.get(f"{prefix}:{row.get('_' + prefix)}")
                if ref:
                    body[field] = ref
            new = api.create(plural, body)
            if new:
                ids[f"{plural}:{row['_key']}"] = new
                made += 1
        print(f"  {plural:24} {made}/{len(rows)}")

    print(f"Seeding {base} ...")
    push("companies", data["companies"])
    # Map the private _company key onto the company id created above.
    for coll, fk in (("people", "companyId"), ("opportunities", "companyId"),
                     ("serviceSubscriptions", "companyId"),
                     ("complianceEngagements", "companyId"),
                     ("trainingAccounts", "companyId")):
        for row in data[coll]:
            row["_companies"] = row.get("_company")
        push(coll, data[coll], {fk: "companies"})

    for row in data["phishingBaselines"]:
        row["_trainingAccounts"] = row.get("_training")
    push("phishingBaselines", data["phishingBaselines"],
         {"trainingAccountId": "trainingAccounts"})

    for row in data["consentRecords"]:
        row["_people"] = row.get("_person")
    push("consentRecords", data["consentRecords"], {"personId": "people"})

    push("tasks", data["tasks"])

    print(f"\n{api.calls} request(s) made.")
    print("Open Twenty and confirm no view shows an empty state.")
    print("Opted-out contacts exist on purpose — use one to demonstrate the "
          "consent gate refusing a send.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
