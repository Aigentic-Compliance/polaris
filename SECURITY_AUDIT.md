# Security audit — POLARIS repository

329 tests, 98% coverage, stable across repeated runs. This audit covers the code-level security review requested, plus an honest account of what "SOC 2 compliant" actually requires and which parts of that this work does and doesn't touch.

---

## 1. The real finding: SSRF (Server-Side Request Forgery)

**Severity: High.** Before this audit, every URL the scraper fetched — from a scraped page's own links, or from search results — went straight to `session.get()` or `page.goto()` with zero validation. A malicious or compromised target page, or a manipulated search result, could point the scraper at:

- `http://169.254.169.254/latest/meta-data/iam/security-credentials/` — the AWS/GCP/Azure cloud metadata endpoint, the first thing any real SSRF attack targets, capable of exfiltrating cloud credentials if this ever runs on cloud infrastructure with an attached role.
- Any RFC 1918 private address (`10.x`, `172.16–31.x`, `192.168.x`) — an internal service on whatever network this runs on.
- `localhost`/`127.0.0.1` on any port — a local database, cache, or admin interface.
- Non-HTTP schemes (`file://`, `gopher://`, `data:`) — different attack classes entirely.

**First fix (incomplete, caught in the same session)**: `polaris/scraper/url_safety.is_safe_to_fetch(url)`, checked before the fetch at all five call sites. This blocked the direct case correctly — confirmed against every standard SSRF target. But it only checked the *original* URL. `requests.Session.get()` follows HTTP redirects automatically by default, and nothing re-validated the redirect target. **Proven exploitable directly**: two local servers, one returning a 302 to the other, confirmed the "internal" server's content was fetched and returned with zero re-validation — the textbook way real SSRF protections get bypassed in practice, and a gap in my own first pass at this fix, not a hypothetical.

**Actual fix**: `url_safety.safe_get()` — disables `requests`' automatic redirect following and manually walks each hop, re-validating with `is_safe_to_fetch()` before following it, with a max-redirect cap against loops. Wired into all four `requests`-based call sites (`pipeline._download`, `RequestsPageFetcher`, `discover_pdf_links`, `RobotsGate`). Re-ran the exact same two-server proof after the fix, with the redirect target deliberately failing validation: confirmed blocked, `UnsafeURLError` raised, target never fetched.

**Playwright needed a second, different fix — my first attempt at it didn't work, verified by testing it, not by trusting the API.** The initial approach used Playwright's `page.route()` to intercept and validate every request during rendering, on the reasoning that route interception would also catch redirects. Tested directly against the same two-server redirect setup: **the route handler fired exactly once, for the initial URL, and never again for the redirect target** — Chromium follows navigation-level 3xx redirects internally in a way `page.route()` does not re-intercept, confirmed by comparing against `page.on("request")` (which does see every hop) in the same test. Trusting the first approach without testing it against a real redirect would have shipped a fix that looked complete and wasn't — worth stating plainly since it's exactly the failure mode this whole audit is supposed to catch. Replaced with the mechanism that actually works: `page.on("request")` checks every request (navigation, redirect, or in-page script-issued fetch) against `is_safe_to_fetch()` and closes the page immediately on a match. Verified this is genuine prevention, not just discarding the result afterward — the target server's own log showed a `BrokenPipeError` when the connection was torn down mid-response, confirming the fetch itself was interrupted, not merely ignored.

39 dedicated tests across both mechanisms, including the exact proven bypass scenario for each, a legitimate-redirect-still-works case for each (confirming the fix isn't overbroad), and every standard SSRF target (cloud metadata, all three RFC 1918 ranges, loopback via IP and hostname, IPv6 loopback, four non-HTTP schemes).

**Honest residual risk, not fully closed by any of this**: a hostname could still resolve to a safe public IP at validation time and a different, unsafe IP by the time the actual TCP connection is made (DNS rebinding) — closing that fully needs pinning the resolved IP for the connection itself, not just the two-step resolve-then-check-then-connect pattern used here. Not built, given the current threat model (scraping known public compliance-document sites, not processing arbitrary adversarial input at scale) doesn't yet justify the added complexity — but this is a real gap, not a solved one, and should be revisited if that threat model changes.

---

## 2. Defense-in-depth hardening: path traversal in the raw store

**Severity: Low** (not currently exploitable — every current caller passes a value this couldn't happen with — but worth fixing on the "never trust a boundary to always be called correctly" principle, which matters more once other people start touching this code).

`raw_store.py` builds filesystem paths from a `file_hash` string. Every current caller passes the output of `sha256_of_bytes()`, so this was never actually reachable — but nothing *enforced* that. Fixed:

- `save_raw` now validates `file_hash` matches `sha256:<64 hex chars>` before constructing a path from it.
- `load_raw` — which reads a path directly from a **database row** (`SourceDocument.raw_uri`), not a hardcoded value — now rejects any path containing a `..` traversal segment, and validates the filename matches the same hash pattern, before ever opening it. A corrupted or maliciously-edited database row pointing at an arbitrary file (`../../../etc/passwd`) is refused, not silently read.

7 new tests, including one that specifically constructs a 64-hex-character filename *inside* a traversal path (`../../../tmp/<64 hex>.bin`) to confirm the fix checks the full path, not just the filename in isolation — my first draft of this fix only checked the filename and would have missed exactly that case.

---

## 3. Checked and found clean

A real audit reports what's fine, not just what's broken:

- **No hardcoded secrets or API keys** anywhere in source.
- **API keys are never logged** — checked every log statement that touches a classifier/qualifier object.
- **No SQL injection surface** — SQLAlchemy Core with parameterized queries throughout; no raw string formatting into SQL anywhere in the codebase.
- **No command injection surface** — zero uses of `subprocess`, `os.system`, or `os.popen` anywhere.
- **No deserialization risk** — zero uses of `pickle`, `eval`, or `exec`.
- **No disabled SSL verification** — `requests` defaults (`verify=True`) are never overridden.
- **XSS**: the Flask web UI uses `render_template_string` with Jinja2's default auto-escaping intact — no `|safe` filter, no `Markup()` calls anywhere that could reflect scraped content (document titles, rejection reasons) unescaped into the page. A `javascript:`-scheme URL could theoretically be rendered in an `<a href>` if it ever reached the registry, but the SSRF fix above already prevents any non-http/https URL from ever being fetched and registered in the first place, so this path is closed as a side effect.
- **Flask configuration**: `debug=False` explicitly set; `app.run()` has no `host=` argument, so it defaults to `127.0.0.1` (localhost-only) rather than being exposed to the network.
- **Dependency vulnerabilities**: ran `pip-audit` against `requirements.txt` — no known CVEs in any pinned dependency as of this session.

---

## 4. Known gaps, already on record, restated here because they're security-relevant

Not new findings — these were already flagged in earlier sessions, but a security audit is the right place to restate them plainly:

- **The web UI has no authentication at all.** Explicitly documented as a local dev tool, not a hosted product. If this is ever deployed anywhere beyond your own machine, or the `host=` binding above is ever changed to `0.0.0.0` for convenience, this becomes a real, immediate exposure — anyone who can reach it can trigger real paid Brave/Azure API calls with no login required.
- **No rate or cost budget** on search/LLM calls (C11.3 in the WP1 framework, still open). Combined with the point above: an unauthenticated endpoint with no request budget is a cost-based denial-of-service risk, not just a theoretical one.
- **No CSRF protection** on the search form. Low real-world severity given there's no session/authentication to abuse, but worth naming: any page on the internet could currently cause a visitor's browser to silently POST to this app if it's ever reachable from outside localhost.

---

## 5. On "SOC 2 compliant" — the honest scoping

This is the part I want to be direct about rather than let slide: **nothing above makes this repository "SOC 2 compliant," and no code change could.**

SOC 2 is a certification issued by a licensed CPA firm after examining an organization's actual *operational controls* — for the Type II report that matters commercially, over a 6–12 month evidence period. It's assessed against five Trust Services Criteria. Here's where today's work sits against each, honestly:

| Criterion | What today's work supports | What's still entirely organizational, not code |
|---|---|---|
| **Security** | SSRF closed, path traversal hardened, dependency scan clean, no injection/deserialization/command-injection surface found. | Access control *policy* (who's allowed to touch production, and how that's enforced and reviewed), a documented incident response plan, security awareness training, a vulnerability disclosure process, penetration testing by a third party. |
| **Availability** | N/A — this audit didn't touch uptime/redundancy. | An actual SLA, monitoring and alerting infrastructure, a disaster recovery plan with a tested RTO/RPO, documented backup procedures. |
| **Processing integrity** | Every accept/reject/classify decision already carries an explicit reason (a long-standing design principle of this repo, not new today). | Formal change-management sign-off process, a documented QA/release process beyond "the test suite passes." |
| **Confidentiality** | No secrets in logs or source; DB credentials never logged. | A data classification policy, encryption-at-rest for the database and raw store (currently plain SQLite and plain files — fine for a local dev tool, not fine for anything holding real client data at rest without further hardening), a documented data retention/deletion policy (the raw store's own unbounded growth, flagged as a known gap weeks ago, is directly relevant here). |
| **Privacy** | N/A — this repo doesn't currently process personal data as a primary concern, though scraped policy documents could incidentally contain names. | A privacy policy, a data subject access request process, vendor data-processing agreements with Azure/Brave covering what data leaves your infrastructure and how. |

**The practical read**: today's work is real, legitimate progress specifically on the *Security* criterion's technical-controls half. It's necessary. It is not sufficient, and it's not close to sufficient — SOC 2 is mostly about documented process and third-party verification, not code. If pursuing an actual SOC 2 report is a real goal, the next steps are organizational (a designated security policy owner, documented procedures, likely a compliance-automation tool like Vanta or Drata to manage the evidence trail) — not something I can build in this repository.
