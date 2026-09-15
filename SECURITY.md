# Security Policy

## Scope

nexusearch performs outbound network requests against arbitrary URLs returned
by search providers. The security-critical surface is:

- `url_safety.py` — SSRF defenses (DNS pinning, global-IP allowlist, CGNAT and
  non-global range rejection, redirect validation, body caps)
- `page_reader.py` — deep-read fetch pipeline (deadlines, proxy trust boundary)
- `discovery.py` — API key handling (SerpAPI keys travel in query strings)

## Reporting a Vulnerability

Please do **not** open a public issue for security reports.

Email the maintainer via the contact on
[github.com/AnathanWang](https://github.com/AnathanWang) or open a
[private security advisory](https://github.com/AnathanWang/nexusearch/security/advisories/new).

Include: affected version, a minimal reproducer (URL / input / config), and the
expected vs. actual behavior. We aim to acknowledge within 72 hours.

## Supported Versions

| Version | Supported |
|---|---|
| latest 0.5.x | ✅ |
| < 0.5 | ❌ (pre-1.0: only the latest minor receives fixes) |

## Consumer Responsibilities

- API keys are owned by the consumer application; never hard-code them.
- Treat fetched page content and LLM outputs as **untrusted data** (the library
  sanitizes prompt fields, but your own prompts/policies must too).
- If you expose search results to end users, validate URLs before rendering
  (the library validates before *fetching*, not before *displaying*).
