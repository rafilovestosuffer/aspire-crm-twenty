#!/usr/bin/env python3
"""
Prove the replacement guide covers every GoHighLevel capability.

Checks the guide's text against three inventories:
  1. GHL workflow actions   — all 14 categories, from HighLevel's support portal
  2. GHL workflow triggers  — HighLevel's trigger list plus the 2026 additions
  3. reference/ghl_feature_taxonomy.csv

Run after editing the guide. A non-zero exit means something is uncovered.
The first run of this script found 7 genuinely missing features, which is
the reason it exists rather than a claim in a commit message.

Usage:
    python docs/migration-guide/verify_coverage.py
    python docs/migration-guide/verify_coverage.py --verbose
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
TAXONOMY = ROOT / "reference" / "ghl_feature_taxonomy.csv"

ACTIONS = {
    "Contact": ["Create Contact", "Find Contact", "Update Contact Field",
                "Add Contact Tag", "Remove Contact Tag", "Assign to User",
                "Remove Assigned User", "Edit Conversation", "Disable/Enable DND",
                "Add Note", "Add Task", "Copy Contact", "Delete Contact",
                "Modify Contact Engagement Score", "Add/Remove Contact Followers"],
    "Communication": ["Send Email", "Send SMS", "Send Slack Message", "Call",
                      "Messenger", "Instagram DM", "Manual Action", "GMB Messaging",
                      "Send Internal Notification", "Send Review Request",
                      "Conversation AI", "Facebook Interactive Messenger",
                      "Instagram Interactive Messenger", "Reply in Comments",
                      "WhatsApp", "Send Live Chat Message"],
    "Send Data": ["Webhook", "Google Sheets"],
    "Internal Tools": ["If Else", "Wait Step", "Goal Event", "Split",
                       "Update Custom Value", "Go To", "Remove from Workflow",
                       "Arrays", "Drip Mode", "Text Formatter", "Custom Code"],
    "Workflow AI": ["AI Prompt"],
    "Eliza": ["Eliza AI Appointment Booking", "Send to Eliza Agent Platform"],
    "Appointments": ["Update Appointment Status", "Generate One Time Booking Link"],
    "Opportunities": ["Create/Update Opportunity", "Remove Opportunity"],
    "Payments": ["Stripe One-Time Charge", "Send Invoice",
                 "Send Documents and Contracts"],
    "Marketing": ["Add to Google Analytics", "Add to Google AdWords",
                  "Add to Custom Audience", "Remove from Custom Audience",
                  "Facebook Conversion API"],
    "Affiliate": ["Add to Affiliate Manager", "Update Affiliate",
                  "Add/Remove from Affiliate Campaign"],
    "Courses": ["Course Grant Offer", "Course Revoke Offer"],
    "IVR": ["Gather Input on Call", "Play Message", "Connect to Call",
            "End Call", "Record Voicemail"],
    "Communities": ["Grant Group Access", "Revoke Group Access"],
}

TRIGGERS = {
    "Appointments": ["Appointment Status", "Customer Booked Appointment",
                     "Appointment No-Show"],
    "Contact": ["Birthday Reminder", "Contact Changed", "Contact Created",
                "Contact Tag", "Custom Date Reminder", "Note Added",
                "Note Changed", "Task Added", "Task Reminder"],
    "Contact Actions": ["Customer Replied", "Form Submitted",
                        "Order Form Submission", "Survey Submitted",
                        "Trigger Link Clicked", "Twilio Validation Error"],
    "Events": ["Call Status", "Email Events"],
    "Facebook": ["Facebook Lead Form Submitted"],
    "Membership": ["Category Completed", "Membership New Signup",
                   "Offer Access Granted", "Offer Access Removed",
                   "Product Access Granted", "Product Access Removed",
                   "Product Completed", "User Login"],
    "Opportunities": ["Opportunity Status Changed", "Pipeline Stage Changed",
                      "Stale Opportunities"],
    "Payments": ["Invoice", "Subscription"],
    "Shopify": ["Abandoned Checkout", "Order Placed", "Order Fulfilled"],
    "Web": ["Website Visit"],
}


def guide_text() -> str:
    raw = "".join(p.read_text(encoding="utf-8")
                  for p in sorted(HERE.glob("part*.html")))
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)).lower()


def covered(term: str, body: str) -> bool:
    """Present verbatim, or with every significant word somewhere in the guide."""
    t = term.lower()
    if t in body:
        return True
    head = re.split(r"[/(]", t)[0].strip()
    if len(head) > 6 and head in body:
        return True
    words = [w for w in re.findall(r"[a-z]+", t) if len(w) > 3]
    return bool(words) and all(w in body for w in words)


def check(title: str, groups: dict[str, list[str]], body: str,
          verbose: bool) -> list[str]:
    missing = []
    total = 0
    for category, items in groups.items():
        for item in items:
            total += 1
            if covered(item, body):
                if verbose:
                    print(f"    ok   {category}: {item}")
            else:
                missing.append(f"{category}: {item}")
    mark = "PASS" if not missing else "FAIL"
    print(f"[{mark}] {title}: {total - len(missing)}/{total}")
    for m in missing:
        print(f"       UNCOVERED → {m}")
    return missing


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify guide coverage")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    body = guide_text()
    print(f"Guide body: {len(body.split()):,} words\n")

    missing = check("GHL workflow actions (14 categories)", ACTIONS, body, args.verbose)
    missing += check("GHL workflow triggers", TRIGGERS, body, args.verbose)

    rows = list(csv.DictReader(TAXONOMY.open(encoding="utf-8", newline="")))
    gaps = [r for r in rows if not covered(r["feature"], body)]
    mark = "PASS" if not gaps else "FAIL"
    print(f"[{mark}] Feature taxonomy: {len(rows) - len(gaps)}/{len(rows)}")
    for r in gaps:
        print(f"       UNCOVERED → {r['id']} {r['feature']}")
    missing += [r["id"] for r in gaps]

    if missing:
        print(f"\n{len(missing)} item(s) uncovered. Add them to the guide, or "
              "correct the inventory if an item no longer exists in GHL.")
        return 1
    print("\nAll inventories fully covered.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
