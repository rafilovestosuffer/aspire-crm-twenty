"""Prove the GENERATED scan node pages past Twenty's 200-row ceiling.

Creates enough form submissions to force a second page, runs the real nurture
workflow, and reads the execution to see how many pages the scan node emitted
and how many rows they add up to. Deletes everything it made.
"""
import json, sys, time, urllib.request, urllib.error
sys.path.insert(0, "scripts")
import prove_workflows as P

MARK = "pagingproof"
BASE = P.TWENTY; KEY = P.TWENTY_KEY
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def call(method, path, body=None):
    r = urllib.request.Request(BASE + path, method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    try:
        return json.load(op.open(r, timeout=30))
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} -> {e.code} {e.read()[:200].decode()}")

start = call("GET", "/rest/formSubmissions?limit=1")["totalCount"]
need = max(0, 205 - start)
print(f"formSubmissions now: {start}; creating {need} to cross the 200 ceiling")
made = []
try:
    for i in range(need):
        d = call("POST", "/rest/formSubmissions",
                 {"name": f"{MARK}-{i}",
                  "sourceUrl": {"primaryLinkUrl": f"https://example.com/{MARK}"}})
        made.append(d["data"]["createFormSubmission"]["id"])
        time.sleep(0.7)                    # 100 tokens per 60s, with headroom
        if i and i % 50 == 0: print(f"  ...{i}")
except BaseException:
    for x in made: call("DELETE", f"/rest/formSubmissions/{x}")
    raise
total = call("GET", "/rest/formSubmissions?limit=1")["totalCount"]
print(f"formSubmissions now: {total}")

s = P.N8nSession()
assert s.login("admin@aspiretss.com", "AspireDemo2026!"), "n8n login failed"
ids = P.api_workflows()
ex = s.run(ids["LEAD Nurture Sequence"], "Daily 13:00 UTC")
d = s.wait(ex)
run = d.get("data", {}).get("resultData", {}).get("runData", {})
err = d.get("data", {}).get("resultData", {}).get("error")
print("execution status:", d.get("status"), "| error:", (err or {}).get("message", "none"))

pages = rowsread = 0
for r in run.get("Recent form submissions", []):
    for out in r.get("data", {}).get("main", [[]]):
        pages = len(out)
        for item in out:
            rowsread += len(item["json"].get("data", {}).get("formSubmissions", []))
print(f"\nscan node emitted {pages} page(s), {rowsread} row(s); Twenty holds {total}")

for i in made:
    call("DELETE", f"/rest/formSubmissions/{i}")
    time.sleep(0.7)
print(f"cleaned up {len(made)} record(s)")

ok = pages >= 2 and rowsread == total and d.get("status") == "success"
print("\n" + ("PASS — the generated node paged past 200 and read every row"
              if ok else "FAIL — paging did not read the whole table"))
sys.exit(0 if ok else 1)
