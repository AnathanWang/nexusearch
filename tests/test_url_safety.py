"""SSRF / URL allowlist and DNS-pinned fetch tests."""

import ipaddress

from nexusearch.url_safety import (
    check_url_host,
    is_blocked_ip,
    is_safe_url,
    pinned_https_get,
    registrable_overlap,
    resolve_public_ip,
)


def test_https_only_by_default():
    assert not is_safe_url("http://example.com/", expected_domain="example.com", resolve_dns=False)
    assert is_safe_url("https://example.com/", expected_domain="example.com", resolve_dns=False)
    assert check_url_host("https://example.com/", expected_domain="example.com") == "example.com"
    assert check_url_host("http://example.com/", expected_domain="example.com") is None


def test_blocks_localhost_and_metadata_hosts():
    assert not is_safe_url("https://localhost/", resolve_dns=False)
    assert not is_safe_url("https://127.0.0.1/", resolve_dns=False)
    assert not is_safe_url("https://169.254.169.254/", resolve_dns=False)
    assert not is_safe_url("https://metadata.google.internal/", resolve_dns=False)


def test_blocks_private_literal_ips():
    assert not is_safe_url("https://10.0.0.5/", resolve_dns=False)
    assert not is_safe_url("https://192.168.1.1/", resolve_dns=False)
    assert is_blocked_ip(ipaddress.ip_address("10.0.0.1"))


def test_expected_domain_pin():
    assert is_safe_url(
        "https://www.vendor.com/about",
        expected_domain="vendor.com",
        resolve_dns=False,
    )
    assert not is_safe_url(
        "https://evil.com/about",
        expected_domain="vendor.com",
        resolve_dns=False,
    )


def test_registrable_overlap():
    assert registrable_overlap("www.vendor.com", "vendor.com")
    assert registrable_overlap("shop.vendor.com", "vendor.com")
    assert not registrable_overlap("vendor.com.evil.com", "vendor.com")


def test_resolve_public_ip_rejects_private(monkeypatch):
    def fake_gai(host, *args, **kwargs):
        return [(None, None, None, None, ("10.0.0.9", 0))]

    monkeypatch.setattr("nexusearch.url_safety.socket.getaddrinfo", fake_gai)
    assert resolve_public_ip("evil.internal") is None


def test_resolve_public_ip_accepts_public(monkeypatch):
    def fake_gai(host, *args, **kwargs):
        return [(None, None, None, None, ("93.184.216.34", 0))]

    monkeypatch.setattr("nexusearch.url_safety.socket.getaddrinfo", fake_gai)
    assert resolve_public_ip("example.com") == "93.184.216.34"


def test_pinned_get_blocks_redirect_to_private(monkeypatch):
    calls = {"n": 0}

    def fake_resolve(host: str):
        if host == "vendor.com":
            return "93.184.216.34"
        if host in {"127.0.0.1", "localhost"}:
            return None
        return "1.2.3.4"

    monkeypatch.setattr("nexusearch.url_safety.resolve_public_ip", fake_resolve)

    class FakeResp:
        def __init__(self, status, location=None, body=b""):
            self.status = status
            self._location = location
            self._body = body

        def getheader(self, name, default=None):
            if name.lower() == "location":
                return self._location
            if name.lower() == "content-type":
                return "text/html"
            return default

        def read(self):
            return self._body

    class FakeConn:
        def __init__(self, host, *, pinned_ip, port=None, timeout=8.0):
            self.host = host

        def request(self, method, path, headers=None):
            calls["n"] += 1

        def getresponse(self):
            if calls["n"] == 1:
                return FakeResp(302, location="https://127.0.0.1/secret")
            return FakeResp(200, body=b"should not reach")

        def close(self):
            pass

    monkeypatch.setattr("nexusearch.url_safety._PinnedHTTPSConnection", FakeConn)
    status, final, body = pinned_https_get(
        "https://vendor.com/",
        expected_domain="vendor.com",
    )
    assert status == 0
    assert body == ""
    assert "127.0.0.1" in final
