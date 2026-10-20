#!/usr/bin/env python3
"""
Import the GHL record export (raw/records/, from ghl_export_records.py) into
Twenty. Phase 4 of the migration.

Kept the same as GHL, nothing dropped:
  businesses + contact.companyName  -> Company   (key: ghlCompanyKey)
  contacts                          -> Person    (key: ghlContactId)
  opportunities                     -> Opportunity (key: ghlOpportunityId)
  contact.dndSettings "active"      -> Consent Record, Opted Out (key: vendorReference)

GHL values with no standard Twenty home are kept in ghl* fields this script
creates: every GHL custom field that has data, the tags (multi-select), source,
dates, pipeline/stage/status. Twenty's own opportunity stage is a mapping for
the board view; ghlStage is the GHL truth.

Safe to re-run: records are matched on their GHL id, so a second run creates
only what is new and updates only what changed in GHL (by dateUpdated). That
is how GHL and the CRM stay in step until GHL is switched off.

Usage:
    python scripts/ghl_import_records.py --dry-run
    python scripts/ghl_import_records.py --limit 5        # first N contacts only
    python scripts/ghl_import_records.py                  # everything
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import deque
from pathlib import Path
from urllib import error, request
from urllib.parse import quote

try:
    import phonenumbers
except ImportError:  # pragma: no cover
    raise SystemExit("phonenumbers is required: pip install phonenumbers")

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw" / "records"
OUT = ROOT / "out"
RATE_LIMIT, RATE_WINDOW = 80, 60.0
BATCH = 60

COLORS = ["green", "turquoise", "sky", "blue", "purple",
          "pink", "red", "orange", "yellow", "gray"]

# GHL stage name -> Twenty's built-in stage, for the board view only.
STAGE_MAP = {
    "New Lead": "NEW",
    "Contacted": "SCREENING", "Lead Nurturing": "SCREENING",
    "Qualified": "MEETING", "Qualified Lead": "MEETING", "Hot Lead": "MEETING",
    "Evaluation": "MEETING", "Trial/Free Offer": "MEETING",
    "Referral Opportunity": "MEETING",
    "Opportunity": "PROPOSAL", "Negotiation": "PROPOSAL",
    "Proposal Sent": "PROPOSAL", "Awaiting Payment": "PROPOSAL",
    "Enrolled": "CUSTOMER", "Closed-Won": "CUSTOMER", "Success": "CUSTOMER",
    "Closed-Lost": "NEW", "Closed": "NEW",
}

CHANNELS = {"Email": "EMAIL", "SMS": "SMS", "Call": "VOICE", "WhatsApp": "WHATSAPP"}

PERSON_FIELDS = [
    ("ghlContactId", "GHL Contact ID", "TEXT"),
    ("ghlTags", "GHL Tags", "MULTI_SELECT"),
    ("ghlSource", "GHL Source", "TEXT"),
    ("ghlCompanyName", "GHL Company Name", "TEXT"),
    ("ghlCountry", "GHL Country", "TEXT"),
    ("ghlState", "GHL State", "TEXT"),
    ("ghlPostalCode", "GHL Postal Code", "TEXT"),
    ("ghlAddress", "GHL Address", "TEXT"),
    ("ghlWebsite", "GHL Website", "TEXT"),
    ("ghlTimezone", "GHL Timezone", "TEXT"),
    ("ghlAssignedTo", "GHL Assigned To", "TEXT"),
    ("ghlAttribution", "GHL First Attribution", "TEXT"),
    ("ghlDateAdded", "GHL Date Added", "DATE_TIME"),
    ("ghlDateUpdated", "GHL Date Updated", "DATE_TIME"),
    ("ghlPhone", "GHL Phone (original)", "TEXT"),
]
COMPANY_FIELDS = [
    ("ghlCompanyKey", "GHL Company Key", "TEXT"),
    ("ghlBusinessId", "GHL Business ID", "TEXT"),
]
OPPORTUNITY_FIELDS = [
    ("ghlOpportunityId", "GHL Opportunity ID", "TEXT"),
    ("ghlPipeline", "GHL Pipeline", "TEXT"),
    ("ghlStage", "GHL Stage", "TEXT"),
    ("ghlStatus", "GHL Status", "TEXT"),
    ("ghlSource", "GHL Source", "TEXT"),
    ("ghlAssignedTo", "GHL Assigned To", "TEXT"),
    ("ghlCreatedAt", "GHL Created At", "DATE_TIME"),
    ("ghlUpdatedAt", "GHL Updated At", "DATE_TIME"),
]


def read_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for dotenv in (ROOT / ".env", ROOT / "infra" / ".env"):
        if dotenv.exists():
            for line in dotenv.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env.setdefault(k.strip(), v.strip().strip("\"'"))
    return env


def option_value(label: str) -> str:
    v = re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_").upper()
    if not v or v[0].isdigit():
        v = "T_" + v
    return v


def field_name(field_key: str) -> str:
    """'contact.job_title' -> 'ghlJobTitle'. Twenty wants camelCase, short."""
    words = [w for w in re.split(r"[^A-Za-z0-9]+", field_key.split(".", 1)[-1]) if w]
    name = "ghl" + "".join(w[:1].upper() + w[1:].lower() for w in words)
    return name[:45]


def clean_label(text: str) -> str:
    text = re.sub(r"[^\x20-\x7E]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:60]


class Twenty:
    def __init__(self, base: str, key: str, dry_run: bool):
        self.base, self.key, self.dry_run = base.rstrip("/"), key, dry_run
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

    def call(self, method: str, path: str, body=None, write: bool = False):
        if self.dry_run and write:
            return 200, {"data": {}}, ""
        self._throttle()
        self.calls += 1
        data = json.dumps(body).encode() if body is not None else None
        req = request.Request(f"{self.base}{path}", data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.key}")
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "aspire-ghl-import/1.0")
        if data:
            req.add_header("Content-Type", "application/json")
        for attempt in range(5):
            try:
                with request.urlopen(req, timeout=120) as r:
                    return r.status, json.loads(r.read().decode() or "null"), ""
            except error.HTTPError as e:
                if e.code in (429, 502, 503, 504) and attempt < 4:
                    time.sleep(2 ** attempt * 3)
                    continue
                detail = e.read().decode("utf-8", "replace")[:400].replace(self.key, "***")
                return e.code, None, f"HTTP {e.code}: {detail}"
            except Exception as ex:  # noqa: BLE001
                if attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                return 0, None, str(ex).replace(self.key, "***")
        return 0, None, "exhausted retries"

    def list_all(self, plural: str, filt: str) -> list[dict]:
        """Every row matching filt. A page is capped at 200; page until done."""
        rows: list[dict] = []
        after = None
        total = None
        while True:
            q = f"?filter={quote(filt)}&limit=200"
            if after:
                q += f"&starting_after={quote(after)}"
            status, resp, err = self.call("GET", f"/rest/{plural}{q}")
            if status >= 400 or resp is None:
                raise SystemExit(f"  FAIL  list {plural}: {err}")
            data = resp.get("data", {}).get(plural, [])
            total = resp.get("totalCount", total)
            rows.extend(data)
            info = resp.get("pageInfo") or {}
            if not info.get("hasNextPage") or not data:
                break
            after = info.get("endCursor")
        if total is not None and len(rows) != total:
            raise SystemExit(f"  FAIL  list {plural}: read {len(rows)} of {total}")
        return rows

    def create_many(self, plural: str, records: list[dict]) -> tuple[list[str | None], list[str]]:
        """Batch create; on a batch error fall back to one at a time so one bad
        record cannot sink 59 good ones. Returns ids (None for failures), errors."""
        if self.dry_run:
            return ["dry-run"] * len(records), []
        status, resp, err = self.call("POST", f"/rest/batch/{plural}", records, write=True)
        if status < 400 and resp:
            created = next((v for v in resp.get("data", {}).values() if isinstance(v, list)), [])
            if len(created) == len(records):
                return [c.get("id") for c in created], []
        ids, errs = [], []
        for rec in records:
            s, r, e = self.call("POST", f"/rest/{plural}", rec, write=True)
            if s >= 400 and "PHONE" in e and "phones" in rec:
                # Twenty rejected the number; keep the contact, the original
                # stays in ghlPhone.
                rec = {k: v for k, v in rec.items() if k != "phones"}
                s, r, e = self.call("POST", f"/rest/{plural}", rec, write=True)
            node = next((v for v in (r or {}).get("data", {}).values() if isinstance(v, dict)), {})
            if s < 400 and node.get("id"):
                ids.append(node["id"])
            else:
                ids.append(None)
                errs.append(e[:300])
        return ids, errs

    def update(self, plural: str, rid: str, body: dict) -> str:
        s, _, e = self.call("PATCH", f"/rest/{plural}/{rid}", body, write=True)
        if s >= 400 and "PHONE" in e and "phones" in body:
            body = {k: v for k, v in body.items() if k != "phones"}
            s, _, e = self.call("PATCH", f"/rest/{plural}/{rid}", body, write=True)
        return "" if s < 400 else e[:300]


def all_fields(tw: Twenty, object_id: str) -> list[dict]:
    """Every field on an object, with ids and options.

    /rest/metadata/objects embeds only a slice of each object's fields, which
    made existing fields look missing. The GraphQL `fields` query, filtered to
    the object and paged, returns all of them."""
    out: list[dict] = []
    after = None
    while True:
        a = f', after:"{after}"' if after else ""
        q = (f'query {{ fields(paging:{{first:100{a}}}, filter:{{objectMetadataId:{{eq:"{object_id}"}}}}) '
             '{ pageInfo { hasNextPage endCursor } edges { node { id name type options } } } }')
        status, resp, err = tw.call("POST", "/metadata", {"query": q})
        if status >= 400 or not resp or resp.get("errors"):
            raise SystemExit(f"  FAIL  read fields: {err or json.dumps((resp or {}).get('errors'))[:300]}")
        page = resp["data"]["fields"]
        out += [e["node"] for e in page["edges"]]
        if not page["pageInfo"]["hasNextPage"]:
            return out
        after = page["pageInfo"]["endCursor"]


def ensure_fields(tw: Twenty, obj: dict, specs: list[tuple], tag_labels: list[str]) -> None:
    have = {f["name"]: f for f in all_fields(tw, obj["id"])}
    for name, label, ftype in specs:
        if ftype == "MULTI_SELECT":
            wanted = {}
            for lab in tag_labels:
                wanted.setdefault(option_value(lab), lab)
            if name in have:
                cur = have[name].get("options") or []
                missing = [v for v in wanted if v not in {o["value"] for o in cur}]
                if not missing:
                    continue
                opts = [dict(o) for o in cur] + [
                    {"label": wanted[v][:60], "value": v, "position": len(cur) + i,
                     "color": COLORS[(len(cur) + i) % len(COLORS)]}
                    for i, v in enumerate(missing)]
                s, _, e = tw.call("PATCH", f"/rest/metadata/fields/{have[name]['id']}",
                                  {"options": opts}, write=True)
                print(f"  {'ok' if s < 400 else 'FAIL'}    {obj['nameSingular']}.{name}: +{len(missing)} option(s) {e[:200]}")
                if s >= 400:
                    raise SystemExit(1)
                continue
            payload_opts = [{"label": lab[:60], "value": v, "position": i,
                             "color": COLORS[i % len(COLORS)]}
                            for i, (v, lab) in enumerate(wanted.items())]
        else:
            payload_opts = None
        if name in have:
            continue
        payload = {"objectMetadataId": obj["id"], "name": name, "label": label,
                   "type": ftype, "icon": "IconDatabaseImport",
                   "description": "Imported from GoHighLevel"}
        if payload_opts is not None:
            payload["options"] = payload_opts
        s, _, e = tw.call("POST", "/rest/metadata/fields", payload, write=True)
        print(f"  {'ok' if s < 400 else 'FAIL'}    {obj['nameSingular']}.{name} ({ftype}) {e[:200]}")
        if s >= 400:
            raise SystemExit(1)


# GHL stored 3,613 Bangladeshi mobiles as "+88" + "1XXXXXXXXX" — the 0 of
# +880 dropped — which parses as +881 (satellite) and Twenty rejects. The
# pattern is exact (88, then 1[3-9], then 8 digits) and every one is a valid
# +880 number once restored. The untouched GHL value is kept in ghlPhone.
BD_MISSING_ZERO = re.compile(r"^\+?88(1[3-9]\d{8})$")


def split_phone(raw: str) -> dict | None:
    """Twenty PHONES composite, or None when Twenty cannot hold the number
    (it is then kept only in ghlPhone rather than failing the contact)."""
    digits = re.sub(r"[^\d+]", "", raw)
    m = BD_MISSING_ZERO.match(digits)
    if m:
        digits = "+880" + m.group(1)
    try:
        n = phonenumbers.parse(digits, None)
    except phonenumbers.NumberParseException:
        return None
    region = phonenumbers.region_code_for_number(n) or ""
    if region in ("", "001", "ZZ"):
        return None
    return {"primaryPhoneNumber": str(n.national_number),
            "primaryPhoneCallingCode": f"+{n.country_code}",
            "primaryPhoneCountryCode": region,
            "additionalPhones": []}


def custom_value(v):
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    return v


def main() -> int:
    ap = argparse.ArgumentParser(description="Import GHL records into Twenty")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="first N contacts (and their deals)")
    ap.add_argument("--force-update", action="store_true",
                    help="rewrite existing records even if GHL dateUpdated is unchanged")
    ap.add_argument("--contact-ids", default="", help="only these GHL contact ids (comma list), for testing")
    args = ap.parse_args()

    env = read_env()
    base, key = env.get("TWENTY_BASE_URL", ""), env.get("TWENTY_API_KEY", "")
    if not base or not key:
        print("TWENTY_BASE_URL and TWENTY_API_KEY must be set", file=sys.stderr)
        return 1
    load = lambda f: json.loads((RAW / f).read_text(encoding="utf-8"))  # noqa: E731
    contacts, opps = load("contacts.json"), load("opportunities.json")
    businesses, users = load("businesses.json"), load("users.json")
    pipelines, cfdefs, tagdefs = load("pipelines.json"), load("custom_fields.json"), load("tags.json")

    if args.contact_ids:
        pick = {s.strip() for s in args.contact_ids.split(",") if s.strip()}
        contacts = [c for c in contacts if c["id"] in pick]
        args.limit = len(contacts)
    if args.limit:
        contacts = contacts[:args.limit]
        cids = {c["id"] for c in contacts}
        opps = [o for o in opps if o.get("contactId") in cids]
    print(f"Import {'(DRY RUN) ' if args.dry_run else ''}-> {base}")
    print(f"  {len(contacts)} contacts, {len(opps)} opportunities in scope\n")

    tw = Twenty(base, key, args.dry_run)
    status, meta, err = tw.call("GET", "/rest/metadata/objects")
    if status >= 400:
        print(f"  FAIL  metadata: {err}")
        return 1
    objs = {o["nameSingular"]: o for o in meta["data"]["objects"]}

    # ---- fields -------------------------------------------------------------
    used_cf = {f["id"] for c in contacts for f in c.get("customFields") or []
               if f.get("value") not in (None, "", [])}
    cf_specs, cf_name, taken = [], {}, {n for n, _, _ in PERSON_FIELDS}
    for d in cfdefs:
        if d["id"] not in used_cf:
            continue
        name = field_name(d.get("fieldKey") or d["name"])
        while name in taken:
            name = name[:43] + str(len(taken) % 97)
        taken.add(name)
        cf_name[d["id"]] = (name, d["dataType"])
        cf_specs.append((name, clean_label(d["name"]) or name,
                         "NUMBER" if d["dataType"] == "NUMERICAL" else "TEXT"))
    tag_labels = sorted({t["name"] for t in tagdefs} |
                        {t for c in contacts for t in c.get("tags") or []})
    tag_value = {t: option_value(t) for t in tag_labels}

    print("Fields")
    ensure_fields(tw, objs["person"], PERSON_FIELDS + cf_specs, tag_labels)
    ensure_fields(tw, objs["company"], COMPANY_FIELDS, [])
    ensure_fields(tw, objs["opportunity"], OPPORTUNITY_FIELDS, [])
    print("  ok    fields in place\n")

    report: dict = {"created": {}, "updated": {}, "skipped_unchanged": {}, "failed": {}}
    failures: list[dict] = []

    def run_creates(plural: str, items: list[tuple[str, dict]]) -> dict[str, str]:
        made: dict[str, str] = {}
        for i in range(0, len(items), BATCH):
            chunk = items[i:i + BATCH]
            ids, errs = tw.create_many(plural, [body for _, body in chunk])
            for (k, _), rid in zip(chunk, ids):
                if rid:
                    made[k] = rid
            for e in errs:
                failures.append({"object": plural, "error": e})
            done = min(i + BATCH, len(items))
            if done == len(items) or (i // BATCH) % 20 == 0:
                print(f"  ..    {plural:<15} {done}/{len(items)} sent, {len(made)} created", flush=True)
        report["created"][plural] = len(made)
        report["failed"][plural] = report["failed"].get(plural, 0) + len(items) - len(made)
        return made

    # ---- companies ----------------------------------------------------------
    print("Companies")
    biz = {b["id"]: b for b in businesses}
    want_co: dict[str, dict] = {}
    for b in businesses:
        want_co[f"biz:{b['id']}"] = {"name": b["name"].strip(), "ghlCompanyKey": f"biz:{b['id']}",
                                     "ghlBusinessId": b["id"]}
    biz_names = {b["name"].strip().lower() for b in businesses}
    for c in contacts:
        nm = (c.get("companyName") or "").strip()
        if nm and nm.lower() not in biz_names:
            want_co.setdefault(f"name:{nm.lower()}", {"name": nm, "ghlCompanyKey": f"name:{nm.lower()}"})
    if args.limit:
        keep = {f"biz:{c['businessId']}" for c in contacts if c.get("businessId") in biz} | \
               {f"name:{(c.get('companyName') or '').strip().lower()}" for c in contacts}
        want_co = {k: v for k, v in want_co.items() if k in keep}
    existing_co = {r["ghlCompanyKey"]: r["id"] for r in
                   tw.list_all("companies", "ghlCompanyKey[is]:NOT_NULL")} if not args.dry_run else {}
    co_id = dict(existing_co)
    co_id.update(run_creates("companies", [(k, v) for k, v in want_co.items() if k not in existing_co]))
    print(f"  ok    companies: {len(want_co)} wanted, {len(existing_co)} already there\n")

    def company_for(c: dict) -> str | None:
        if c.get("businessId") in biz:
            return co_id.get(f"biz:{c['businessId']}")
        nm = (c.get("companyName") or "").strip().lower()
        if not nm:
            return None
        bn = next((b for b in businesses if b["name"].strip().lower() == nm), None)
        return co_id.get(f"biz:{bn['id']}" if bn else f"name:{nm}")

    # ---- people -------------------------------------------------------------
    print("People")
    uname = {u["id"]: (u.get("name") or f"{u.get('firstName', '')} {u.get('lastName', '')}").strip()
             for u in users}

    def person_body(c: dict) -> dict:
        cf = {f["id"]: f.get("value") for f in c.get("customFields") or []}
        job_id = next((d["id"] for d in cfdefs if d.get("fieldKey") == "contact.job_title"), None)
        des_id = next((d["id"] for d in cfdefs if d.get("fieldKey") == "contact.designation"), None)
        att = next((a for a in c.get("attributions") or [] if a.get("isFirst")), None)
        body = {
            "name": {"firstName": (c.get("firstName") or "").strip(),
                     "lastName": (c.get("lastName") or "").strip()},
            "emails": {"primaryEmail": (c.get("email") or "").strip(),
                       # GHL gives these as {"email": ..., "validEmailDate": ...}
                       "additionalEmails": [e.get("email") if isinstance(e, dict) else e
                                            for e in (c.get("additionalEmails") or [])
                                            if (e.get("email") if isinstance(e, dict) else e)]},
            "jobTitle": str(cf.get(job_id) or cf.get(des_id) or "")[:250],
            "city": c.get("city") or "",
            "companyId": company_for(c),
            "ghlContactId": c["id"],
            "ghlTags": [tag_value[t] for t in c.get("tags") or [] if t in tag_value] or None,
            "ghlSource": c.get("source") or "",
            "ghlCompanyName": c.get("companyName") or "",
            "ghlCountry": c.get("country") or "",
            "ghlState": c.get("state") or "",
            "ghlPostalCode": c.get("postalCode") or "",
            "ghlAddress": c.get("address1") or "",
            "ghlWebsite": c.get("website") or "",
            "ghlTimezone": c.get("timezone") or "",
            "ghlAssignedTo": uname.get(c.get("assignedTo"), c.get("assignedTo") or ""),
            "ghlAttribution": " / ".join(str(att.get(k)) for k in ("utmSessionSource", "medium", "utmSource", "utmMedium")
                                         if att and att.get(k)) if att else "",
            "ghlDateAdded": c.get("dateAdded"),
            "ghlDateUpdated": c.get("dateUpdated"),
        }
        if c.get("phone"):
            body["ghlPhone"] = c["phone"]
            ph = split_phone(c["phone"])
            if ph:
                body["phones"] = ph
        for fid, (fname, dtype) in cf_name.items():
            v = cf.get(fid)
            if v in (None, "", []):
                continue
            if dtype == "NUMERICAL":
                try:
                    body[fname] = float(v)
                except (TypeError, ValueError):
                    continue
            else:
                body[fname] = str(custom_value(v))
        return {k: v for k, v in body.items() if v is not None}

    existing_p = {} if args.dry_run else {
        r["ghlContactId"]: r for r in tw.list_all("people", "ghlContactId[is]:NOT_NULL")}
    new_p = [(c["id"], person_body(c)) for c in contacts if c["id"] not in existing_p]
    person_id = {k: r["id"] for k, r in existing_p.items()}
    person_id.update(run_creates("people", new_p))
    upd = same = 0
    for c in contacts:
        r = existing_p.get(c["id"])
        if not r:
            continue
        if not args.force_update and (r.get("ghlDateUpdated") or "")[:19] == (c.get("dateUpdated") or "")[:19]:
            same += 1
            continue
        e = tw.update("people", r["id"], person_body(c))
        if e:
            failures.append({"object": "people", "ghlId": c["id"], "error": e})
        else:
            upd += 1
    report["updated"]["people"], report["skipped_unchanged"]["people"] = upd, same
    print(f"  ok    people: {len(contacts)} in GHL, {len(existing_p)} already there, {upd} updated\n")

    # ---- opportunities ------------------------------------------------------
    print("Opportunities")
    stage = {s["id"]: (p["name"], s["name"]) for p in pipelines for s in p["stages"]}
    contact_co = {c["id"]: company_for(c) for c in contacts}

    def opp_body(o: dict) -> dict:
        pname, sname = stage.get(o.get("pipelineStageId"), ("", ""))
        body = {
            "name": (o.get("name") or "Untitled").strip()[:250],
            "stage": STAGE_MAP.get(sname, "NEW"),
            "pointOfContactId": person_id.get(o.get("contactId")),
            "companyId": contact_co.get(o.get("contactId")),
            "ghlOpportunityId": o["id"], "ghlPipeline": pname, "ghlStage": sname,
            "ghlStatus": o.get("status") or "", "ghlSource": o.get("source") or "",
            "ghlAssignedTo": uname.get(o.get("assignedTo"), o.get("assignedTo") or ""),
            "ghlCreatedAt": o.get("createdAt"), "ghlUpdatedAt": o.get("updatedAt"),
        }
        if (o.get("monetaryValue") or 0) > 0:
            body["amount"] = {"amountMicros": int(o["monetaryValue"] * 1_000_000), "currencyCode": "USD"}
        return {k: v for k, v in body.items() if v is not None}

    existing_o = {} if args.dry_run else {
        r["ghlOpportunityId"]: r for r in tw.list_all("opportunities", "ghlOpportunityId[is]:NOT_NULL")}
    run_creates("opportunities", [(o["id"], opp_body(o)) for o in opps if o["id"] not in existing_o])
    oupd = 0
    for o in opps:
        r = existing_o.get(o["id"])
        if r and (args.force_update or (r.get("ghlUpdatedAt") or "")[:19] != (o.get("updatedAt") or "")[:19]):
            e = tw.update("opportunities", r["id"], opp_body(o))
            if e:
                failures.append({"object": "opportunities", "ghlId": o["id"], "error": e})
            else:
                oupd += 1
    report["updated"]["opportunities"] = oupd
    print(f"  ok    opportunities: {len(opps)} in GHL, {len(existing_o)} already there, {oupd} updated\n")

    # ---- consent ------------------------------------------------------------
    print("Consent (GHL do-not-disturb)")
    want_cr = []
    for c in contacts:
        pid = person_id.get(c["id"])
        settings = dict(c.get("dndSettings") or {})
        if c.get("dnd"):
            settings.setdefault("All", {"status": "active"})
        for ch, st in settings.items():
            chan = CHANNELS.get(ch, "ALL" if ch == "All" else None)
            if not chan or (st or {}).get("status") != "active":
                continue
            ref = f"ghl:{c['id']}:{chan}"
            want_cr.append((ref, {"name": f"GHL DND {ch}", "channel": chan, "status": "OPTED_OUT",
                                  "effectiveAt": c.get("dateUpdated"), "vendorReference": ref,
                                  "proof": f"GoHighLevel dndSettings.{ch}=active at export",
                                  "personId": pid}))
    existing_cr = {} if args.dry_run else {
        r["vendorReference"]: r["id"] for r in
        tw.list_all("consentRecords", "vendorReference[like]:ghl%")}
    run_creates("consentRecords", [(k, v) for k, v in want_cr if k not in existing_cr])
    print(f"  ok    consent: {len(want_cr)} opt-outs in GHL, {len(existing_cr)} already there\n")

    OUT.mkdir(exist_ok=True)
    report["requests"] = tw.calls
    report["failures"] = failures[:200]
    (OUT / "ghl_import_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    nfail = len(failures)
    print("=" * 64)
    print(f"  created {report['created']}")
    print(f"  {nfail} failure(s), details in out/ghl_import_report.json, {tw.calls} request(s)")
    print("=" * 64)
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
