"""URL allowlisting and DNS-pinned HTTPS fetch to reduce SSRF / TOCTOU risk."""

from __future__ import annotations

import http.client
import ipaddress
import logging
import re
import socket
import ssl
from urllib.parse import urljoin, urlsplit

logger = logging.getLogger(__name__)

MAX_RESPONSE_BYTES = 2_000_000

_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
        "metadata",
    }
)

# Conservative multi-part public suffixes (not a full PSL).
_MULTI_PART_SUFFIXES = frozenset(
    {
        "co.uk",
        "com.au",
        "co.nz",
        "co.jp",
        "com.br",
        "co.kr",
        "com.mx",
        "co.in",
        "org.uk",
        "net.au",
    }
)

_UNSAFE_HOST_RE = re.compile(r"[%\x00-\x1f\x7f]|。|．")


def is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """
    Reject any non-global address (SSRF harden).

    Prefer allowlist ``ip.is_global`` over private denylist so CGNAT
    (100.64.0.0/10), documentation, and other non-global ranges are blocked.
    """
    return not bool(ip.is_global)


def normalize_hostname(host: str) -> str | None:
    """Normalize hostname for policy checks. None if unsafe / empty."""
    raw = (host or "").strip().lower().rstrip(".")
    if not raw or raw in _BLOCKED_HOSTNAMES or raw.endswith(".localhost"):
        return None
    if _UNSAFE_HOST_RE.search(raw):
        return None
    if "/" in raw or "\\" in raw or "@" in raw or " " in raw:
        return None
    try:
        # IDNA for non-ascii labels; ascii hosts pass through.
        return raw.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError):
        return None


def _label_count(host: str) -> int:
    return len([p for p in host.split(".") if p])


def registrable_overlap(host_a: str, host_b: str) -> bool:
    """
    True if hosts are equal or one is a subdomain of the other.

    Rejects single-label expected domains (e.g. 'com') and bare multi-part
    public suffixes (e.g. 'co.uk') to reduce spoofing without a full PSL.
    """
    a_n = normalize_hostname(host_a)
    b_n = normalize_hostname(host_b)
    if not a_n or not b_n:
        return False
    a = a_n.removeprefix("www.")
    b = b_n.removeprefix("www.")
    if _label_count(b) < 2 or b in _MULTI_PART_SUFFIXES:
        return False
    if _label_count(a) < 2:
        return False
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def resolve_public_ip(host: str) -> str | None:
    """
    Resolve host once. Reject if any answer is non-global; return first global IP.
    Empty / failure → None.
    """
    host_n = normalize_hostname(host)
    if not host_n:
        return None

    try:
        literal = ipaddress.ip_address(host_n)
        if is_blocked_ip(literal):
            return None
        return str(literal)
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host_n, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        logger.debug("DNS resolve failed for host=%s", host_n)
        return None

    public: list[str] = []
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            continue
        if is_blocked_ip(ip):
            logger.debug("Blocked non-global IP %s for host=%s", ip, host_n)
            return None
        public.append(str(ip))
    return public[0] if public else None


def check_url_host(
    url: str,
    *,
    expected_domain: str | None = None,
    allow_http: bool = False,
) -> str | None:
    """
    Validate scheme/host/domain without DNS. Returns hostname or None if blocked.
    """
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None

    scheme = (parsed.scheme or "").lower()
    if allow_http:
        if scheme not in ("http", "https"):
            return None
    elif scheme != "https":
        return None

    host = normalize_hostname(parsed.hostname or "")
    if not host:
        return None

    if expected_domain and not registrable_overlap(host, expected_domain):
        return None

    try:
        if is_blocked_ip(ipaddress.ip_address(host)):
            return None
    except ValueError:
        pass

    return host


def is_safe_url(
    url: str,
    *,
    expected_domain: str | None = None,
    allow_http: bool = False,
    resolve_dns: bool = True,
) -> bool:
    """
    Allow only global http(s) URLs. Optionally require host to match expected_domain
    and reject hosts that resolve to non-global addresses.
    """
    host = check_url_host(url, expected_domain=expected_domain, allow_http=allow_http)
    if host is None:
        return False

    if not resolve_dns:
        return True

    return resolve_public_ip(host) is not None


def _read_limited(resp: http.client.HTTPResponse, max_bytes: int = MAX_RESPONSE_BYTES) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = resp.read(min(64 * 1024, max_bytes - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            logger.debug("Response truncated at %s bytes", max_bytes)
            chunks.append(chunk[: max(0, max_bytes - (total - len(chunk)))])
            break
        chunks.append(chunk)
    return b"".join(chunks)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection that dials a pre-resolved public IP with SNI=hostname."""

    def __init__(self, host: str, *, pinned_ip: str, port: int | None = None, timeout: float = 8.0):
        context = ssl.create_default_context()
        super().__init__(host, port=port, timeout=timeout, context=context)
        self._pinned_ip = pinned_ip

    def connect(self) -> None:
        sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        if self._context is None:
            raise RuntimeError("SSL context missing for pinned HTTPS connection")
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def pinned_https_get(
    url: str,
    *,
    expected_domain: str,
    timeout: float = 8.0,
    headers: dict[str, str] | None = None,
    max_redirects: int = 5,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> tuple[int, str, str]:
    """
    GET via DNS-pinned IP. Returns (status_code, final_url, body_text).
    On block/failure returns (0, url, "").
    """
    current = url
    req_headers = {
        "User-Agent": "Mozilla/5.0 (compatible; Nexusearch/0.1; +https://example.local)",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Connection": "close",
    }
    if headers:
        req_headers.update(headers)

    for _ in range(max_redirects + 1):
        try:
            parsed = urlsplit(current)
        except ValueError:
            return 0, current, ""

        if (parsed.scheme or "").lower() != "https":
            return 0, current, ""

        host = normalize_hostname(parsed.hostname or "")
        if not host or not registrable_overlap(host, expected_domain):
            return 0, current, ""

        pinned = resolve_public_ip(host)
        if not pinned:
            return 0, current, ""

        port = parsed.port or 443
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        conn: _PinnedHTTPSConnection | None = None
        try:
            conn = _PinnedHTTPSConnection(host, pinned_ip=pinned, port=port, timeout=timeout)
            conn.request("GET", path, headers=req_headers)
            resp = conn.getresponse()
            status = resp.status
            loc = resp.getheader("Location")
            body = _read_limited(resp, max_bytes=max_bytes)
            if status in (301, 302, 303, 307, 308) and loc:
                current = urljoin(current, loc)
                continue
            charset = "utf-8"
            ctype = resp.getheader("Content-Type") or ""
            if "charset=" in ctype.lower():
                charset = ctype.lower().split("charset=")[-1].split(";")[0].strip() or "utf-8"
            try:
                text = body.decode(charset, errors="replace")
            except LookupError:
                text = body.decode("utf-8", errors="replace")
            return status, current, text
        except Exception as e:  # noqa: BLE001
            logger.debug("Pinned HTTPS fetch failed for %s: %s", current, e)
            return 0, current, ""
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass

    return 0, current, ""
