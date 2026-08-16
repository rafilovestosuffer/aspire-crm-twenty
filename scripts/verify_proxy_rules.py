#!/usr/bin/env python3
"""Prove the reverse proxy actually enforces what the Caddyfile claims.

    python3 scripts/verify_proxy_rules.py             # infra/Caddyfile (VPS)
    python3 scripts/verify_proxy_rules.py --internal  # infra/Caddyfile.internal

The VPS Caddyfile does one security-critical thing: it exposes the public form
and inbound webhooks to anyone, and refuses the n8n editor to everyone outside
the office and VPN ranges. The editor holds every credential in the stack, so
if that rule is wrong the whole deployment is wrong — and it is wrong in a way
nothing else catches, because the site looks perfectly healthy either way.

Reading the file does not prove it. Caddy applies directives in its own order,
not the order they are written, so `respond @untrusted ... 403` placed above
`reverse_proxy` is only refused first because Caddy sorts it that way. That is
an implementation detail of the Caddy version in use, which makes it exactly
the kind of thing to assert rather than assume.

The two modes are checked differently, because the honest test differs:

  VPS       a throwaway Caddy runs the real infra/Caddyfile against the running
            stack. A rehearsal has no public DNS, so ACME cannot run and the
            file needs two mechanical edits — `auto_https off`, and the
            allowlist line the operator is told to replace anyway. Swapping
            that one line is how both branches of the rule get tested.

  internal  the real proxy is already running with real certificates from
            Caddy's own CA, so it is tested directly. Nothing is rewritten.

Exit 0 means the rules hold. Anything else names the rule that did not.
"""

from __future__ import annotations

import json
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTERNAL = "--internal" in sys.argv
CADDYFILE = ROOT / "infra" / ("Caddyfile.internal" if INTERNAL else "Caddyfile")
CONTAINER = "aspire-proxy-rule-test"
HOST_PORT = 8099

CRM = "crm.rule-test.local"
AUTO = "auto.rule-test.local"

G, R, Y, D, B, O = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m",
)

FAILS = 0


def check(label: str, got, want, why: str) -> None:
    global FAILS
    if got == want:
        print(f"  {G}ok{O}    {label:<44} {D}{got}{O}")
    else:
        print(f"  {R}FAIL{O}  {label:<44} got {got!r}, expected {want!r}")
        print(f"        {why}")
        FAILS += 1


def sh(*args: str, check_rc: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=check_rc)


def read_env() -> dict[str, str]:
    env: dict[str, str] = {}
    f = ROOT / "infra" / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def compose(*args: str, check_rc: bool = True) -> subprocess.CompletedProcess:
    """docker compose with this deployment's overlay, so container names are
    never guessed from a project name that could differ per checkout."""
    overlay = "docker-compose.internal.yml" if INTERNAL else "docker-compose.vps.yml"
    return sh("docker", "compose",
              "-f", str(ROOT / "infra" / "docker-compose.yml"),
              "-f", str(ROOT / "infra" / overlay),
              *args, check_rc=check_rc)


def compose_network() -> str:
    """The network the stack is on, so the proxy can reach server: and n8n:."""
    got = compose("ps", "-q", "n8n", check_rc=False)
    cid = got.stdout.strip().splitlines()
    if not cid:
        # Distinguish "nothing is running" from "the compose files would not
        # resolve" — the overlay's :? guards fail the command outright when a
        # required variable is unset, and reporting that as a stopped stack
        # sends you looking in the wrong place.
        err = got.stderr.strip()
        if err:
            sys.exit(f"could not read the stack:\n  {err}")
        sys.exit("the stack is not running — start it before running this")
    out = sh("docker", "inspect", cid[0],
             "--format", "{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}")
    net = out.stdout.strip()
    if not net:
        sys.exit("could not determine the compose network")
    return net


def client_subnet(net: str) -> str:
    out = sh("docker", "network", "inspect", net,
             "--format", "{{range .IPAM.Config}}{{.Subnet}}{{end}}")
    return out.stdout.strip()


# The agent proxy must not intercept a request to a loopback port or a local
# name; ProxyHandler({}) disables proxy lookup entirely for these openers.
def opener_for(cafile: str | None = None):
    handlers = [urllib.request.ProxyHandler({})]
    if cafile:
        handlers.append(urllib.request.HTTPSHandler(
            context=ssl.create_default_context(cafile=cafile)))
    return urllib.request.build_opener(*handlers)


def fetch(opener, url: str, host: str | None = None):
    """Return (status, headers). HTTP errors are results here, not exceptions."""
    req = urllib.request.Request(url, headers={"Host": host} if host else {})
    try:
        with opener.open(req, timeout=20) as r:
            return r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers)


# ---------------------------------------------------------------- VPS mode
def rewrite(allowlist: str) -> str:
    """The real Caddyfile with two mechanical edits, and nothing else.

    Every matcher, handler and directive under test stays byte-identical to
    the file that ships.
    """
    src = CADDYFILE.read_text()

    src, n = re.subn(r"(?m)^\{$", "{\n\tauto_https off", src, count=1)
    if n != 1:
        sys.exit("could not find the global options block in infra/Caddyfile")

    src, n = re.subn(
        r"not remote_ip [\d./ ]+", f"not remote_ip {allowlist}", src, count=1)
    if n != 1:
        sys.exit("could not find the editor allowlist in infra/Caddyfile")

    # Placeholder domains: the real ones do not resolve here, which would make
    # the Host header meaningless. The http:// scheme pins each site to port 80
    # — a bare hostname defaults to 443, and with auto_https off that serves
    # plaintext on the TLS port, which nothing can talk to.
    return (src.replace("{$CRM_DOMAIN}", f"http://{CRM}")
               .replace("{$AUTOMATION_DOMAIN}", f"http://{AUTO}"))


def start_proxy(conf: Path, net: str, op) -> None:
    sh("docker", "rm", "-f", CONTAINER, check_rc=False)
    sh("docker", "run", "-d", "--name", CONTAINER, "--network", net,
       "-p", f"127.0.0.1:{HOST_PORT}:80",
       "-v", f"{conf}:/etc/caddy/Caddyfile:ro",
       # `email` with an empty value is a Caddyfile PARSE error, not a warning:
       # an unset ACME_EMAIL takes the whole proxy down at startup. The compose
       # overlay guards it with :? and preflight checks it; here it only has to
       # be non-empty, because auto_https off means ACME never runs.
       "-e", "ACME_EMAIL=rehearsal@example.invalid",
       "caddy:2-alpine")
    for _ in range(40):
        time.sleep(0.5)
        status, _ = fetch(op, f"http://127.0.0.1:{HOST_PORT}/", CRM)
        if status:
            return
    logs = sh("docker", "logs", CONTAINER, check_rc=False)
    sh("docker", "rm", "-f", CONTAINER, check_rc=False)
    sys.exit(f"test proxy never came up:\n{logs.stdout}\n{logs.stderr}")


def run_vps() -> None:
    net = compose_network()
    subnet = client_subnet(net)
    op = opener_for()
    print(f"  {D}stack network   {net}{O}")
    print(f"  {D}test client in  {subnet}{O}\n")

    def get(path: str, host: str) -> int:
        return fetch(op, f"http://127.0.0.1:{HOST_PORT}{path}", host)[0]

    tmp = ROOT / "infra" / ".Caddyfile.ruletest"
    try:
        # ---- client OUTSIDE the allowlist: the internet's view -------------
        tmp.write_text(rewrite("203.0.113.0/24 198.51.100.0/24"))
        start_proxy(tmp, net, op)
        print(f"{D}Client outside the allowlist — this is what the internet sees{O}")

        check("editor refused", get("/", AUTO), 403,
              "the n8n editor is reachable from the internet — it holds every "
              "credential in the stack")
        check("editor REST API refused", get("/rest/workflows", AUTO), 403,
              "the editor API is open; workflows can be read or altered remotely")
        check("login page refused", get("/signin", AUTO), 403,
              "the sign-in page is exposed, inviting credential stuffing")

        check("public form reaches n8n", get("/form/aspire-contact", AUTO), 200,
              "the public form is blocked — no lead can ever be captured")
        # 404 from n8n, not 403 from Caddy: the request got through.
        check("inbound webhook reaches n8n", get("/webhook/does-not-exist", AUTO), 404,
              "inbound webhooks are blocked — every integration callback fails")
        check("test webhook reaches n8n", get("/webhook-test/nope", AUTO), 404,
              "webhook-test URLs are blocked, so nothing can be tested live")
        check("OAuth callback reaches n8n",
              get("/rest/oauth2-credential/callback", AUTO), 200,
              "the OAuth callback is blocked — no credential can complete its flow")

        # The CRM host has no allowlist by design: staff use it from anywhere.
        check("CRM host still served", get("/", CRM), 200,
              "the CRM itself is unreachable through the proxy")

        # ---- client INSIDE the allowlist: the office view ------------------
        print(f"\n{D}Same file, allowlist widened to the client — the office view{O}")
        tmp.write_text(rewrite(subnet))
        start_proxy(tmp, net, op)

        check("editor served to a trusted range", get("/", AUTO), 200,
              "the allowlist refuses even a client inside it — the office would "
              "be locked out of its own automation editor")
        check("editor API served to a trusted range", get("/rest/settings", AUTO), 200,
              "the editor API is refused inside the allowlist")
    finally:
        sh("docker", "rm", "-f", CONTAINER, check_rc=False)
        tmp.unlink(missing_ok=True)


# ----------------------------------------------------------- internal mode
def run_internal(env: dict[str, str]) -> None:
    """Test the proxy that is actually running, with its actual certificates."""
    crm = env.get("CRM_DOMAIN", "")
    auto = env.get("AUTOMATION_DOMAIN", "")
    if not crm or not auto:
        sys.exit("CRM_DOMAIN and AUTOMATION_DOMAIN must be set in infra/.env")

    # Caddy's own CA root. Verifying against it proves the chain is coherent —
    # -k would prove only that something answered on 443.
    ca = ROOT / "out" / "caddy-root.crt"
    ca.parent.mkdir(exist_ok=True)
    got = compose("exec", "-T", "caddy",
                  "cat", "/data/caddy/pki/authorities/local/root.crt", check_rc=False)
    if not got.stdout.strip():
        sys.exit("could not read Caddy's local CA root — is the internal stack up?")
    ca.write_text(got.stdout)
    print(f"  {D}verifying against {ca.relative_to(ROOT)} (Caddy's own CA){O}\n")

    op = opener_for(str(ca))
    plain = opener_for()

    print(f"{D}Both hosts, over TLS, through the running proxy{O}")
    status, hdrs = fetch(op, f"https://{crm}/")
    check("CRM served over TLS", status, 200,
          "the CRM is not reachable through the proxy")
    status_a, hdrs_a = fetch(op, f"https://{auto}/")
    check("automation host served over TLS", status_a, 200,
          "the automation host is not reachable through the proxy")
    check("public form served", fetch(op, f"https://{auto}/form/aspire-contact")[0],
          200, "the public form is not reachable — no lead can be captured")

    # A plain-HTTP visitor must be moved to HTTPS, not met with a connection
    # error. This is why auto_https disable_redirects is deliberately not set.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    nr = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), NoRedirect())
    code, h = fetch(nr, f"http://{crm}/")
    check("plain HTTP redirected to HTTPS", code, 308,
          "port 80 does not redirect — anyone typing the bare address gets an "
          "error or, worse, plaintext")
    check("redirect points at HTTPS", (h.get("Location") or "").startswith("https://"),
          True, f"Location was {h.get('Location')!r}")

    print(f"\n{D}Hardening headers{O}")
    for name, want in [("X-Content-Type-Options", "nosniff"),
                       ("X-Frame-Options", "SAMEORIGIN"),
                       ("Referrer-Policy", "strict-origin-when-cross-origin")]:
        check(f"{name} applied", hdrs.get(name), want,
              f"{name} is missing from responses through the proxy")
    check("Server header stripped", hdrs.get("Server"), None,
          "the proxy advertises what is behind it")
    # HSTS is deliberately absent internally: it pins browsers to HTTPS for a
    # year, turning a certificate mistake into an outage nobody can click past.
    check("HSTS absent (internal CA)", hdrs.get("Strict-Transport-Security"), None,
          "HSTS is set behind an internal CA — a certificate mistake becomes a "
          "site nobody can reach until the header expires")


def main() -> int:
    mode = "internal" if INTERNAL else "VPS"
    print(f"{B}Reverse proxy rules — infra/{CADDYFILE.name} ({mode}){O}\n")

    if INTERNAL:
        run_internal(read_env())
    else:
        run_vps()

    print("\n" + "=" * 64)
    if FAILS:
        print(f"  {R}{FAILS} proxy rule(s) do not behave as written.{O}")
        print("=" * 64)
        return 1
    print(f"  {G}Every proxy rule holds.{O}")
    if INTERNAL:
        print(f"  {D}Both hosts serve over TLS from Caddy's own CA, HTTP redirects,{O}")
        print(f"  {D}hardening headers are applied and the Server header is stripped.{O}")
    else:
        print(f"  {D}The editor is refused outside the allowlist and served inside it;{O}")
        print(f"  {D}forms, webhooks and the OAuth callback stay public.{O}")
    print("=" * 64)

    # Keyed by mode so a run of one does not erase the evidence of the other:
    # the VPS rules and the internal rules are different claims, and both are
    # worth being able to point at.
    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    record = out / "proxy_rule_verification.json"
    try:
        data = json.loads(record.read_text())
        if not isinstance(data, dict) or "modes" not in data:
            data = {"modes": {}}
    except (OSError, ValueError):
        data = {"modes": {}}

    data["modes"][mode] = {
        "verified_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "caddyfile": f"infra/{CADDYFILE.name}",
        "checks_passed": True,
    }
    record.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
