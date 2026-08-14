"""Tests for the demo rate limiter.

Three defects motivated this design:

1. The quota keyed on a device fingerprint whose dominant component is a random
   UUID the browser stores in localStorage. Clearing site data minted a new
   device and a fresh allowance, so the limit enforced nothing.
2. `check_rate_limit` incremented as a side effect, at the top of the upload
   handler — so a file rejected by size, type or threat validation still cost
   the caller a scan.
3. The UI counted in localStorage under a 24-hour expiry while the server
   remembered for a year, so the two disagreed.

The IP counter is now primary (a user cannot reset it) with a ceiling set high
enough not to lock out a shared institutional address; the fingerprint is a
secondary per-device courtesy limit.
"""

import pytest

from src.api import demo_routes
from src.api.demo_routes import DEMO_LIMITS, DemoRateLimiter

IP = "203.0.113.7"
FP_A = "a" * 64
FP_B = "b" * 64


@pytest.fixture(autouse=True)
def isolated_memory_store(monkeypatch):
    """Run against the in-memory backend with a clean store per test."""
    monkeypatch.setattr(demo_routes, "_demo_rate_limits", {})
    monkeypatch.setattr(demo_routes, "get_redis_client", lambda: None)
    monkeypatch.setattr(demo_routes, "DEMO_WHITELIST_IPS", set())
    yield


def _remaining(headers) -> int:
    return int(headers["X-RateLimit-Remaining"])


def test_check_does_not_consume():
    """Regression: checking the quota must not spend it.

    The check used to run before file validation, so an oversized or malformed
    upload burned a scan it never used.
    """
    for _ in range(10):
        allowed, headers = DemoRateLimiter.check_rate_limit(IP, FP_A, "full")
        assert allowed
        assert _remaining(headers) == DEMO_LIMITS["max_scans_total"]


def test_consume_decrements_and_check_reflects_it():
    DemoRateLimiter.consume_scan(IP, FP_A, "full")
    _, headers = DemoRateLimiter.check_rate_limit(IP, FP_A, "full")
    assert _remaining(headers) == DEMO_LIMITS["max_scans_total"] - 1


def test_device_limit_blocks_after_its_allowance():
    for _ in range(DEMO_LIMITS["max_scans_total"]):
        DemoRateLimiter.consume_scan(IP, FP_A, "full")

    allowed, headers = DemoRateLimiter.check_rate_limit(IP, FP_A, "full")
    assert not allowed
    assert _remaining(headers) == 0
    assert headers["X-RateLimit-Method"] == "fingerprint"


def test_a_new_device_cannot_exceed_what_the_ip_has_left(monkeypatch):
    """Regression: a fresh fingerprint must not resurrect a spent IP allowance.

    The fingerprint comes from a random localStorage UUID, so a user can mint a
    new one at will; only the IP counter constrains that. Squeeze the ceiling to
    just above the device limit so the interaction is visible — at the shipped
    ratio a second device legitimately still has room.
    """
    device_limit = DEMO_LIMITS["max_scans_total"]
    monkeypatch.setitem(DEMO_LIMITS, "max_scans_per_ip", device_limit + 1)

    for _ in range(device_limit):
        DemoRateLimiter.consume_scan(IP, FP_A, "full")

    allowed, headers = DemoRateLimiter.check_rate_limit(IP, FP_B, "full")
    assert allowed, "the IP still has one scan left, so the new device may use it"
    assert (
        _remaining(headers) == 1
    ), "the new device must inherit what the IP has left, not a full allowance"

    DemoRateLimiter.consume_scan(IP, FP_B, "full")
    allowed, headers = DemoRateLimiter.check_rate_limit(IP, "c" * 64, "full")
    assert not allowed, "rotating devices must not get past the IP ceiling"
    assert headers["X-RateLimit-Method"] == "ip"


def test_ip_ceiling_stops_unlimited_device_rotation():
    """However many devices are presented, the IP ceiling is the hard stop."""
    ip_limit = DEMO_LIMITS["max_scans_per_ip"]

    for i in range(ip_limit):
        fp = f"{i:064x}"
        allowed, _ = DemoRateLimiter.check_rate_limit(IP, fp, "full")
        assert allowed, f"blocked early at scan {i}"
        DemoRateLimiter.consume_scan(IP, fp, "full")

    allowed, headers = DemoRateLimiter.check_rate_limit(IP, f"{ip_limit:064x}", "full")
    assert not allowed
    assert headers["X-RateLimit-Method"] == "ip"


def test_ip_ceiling_is_above_the_device_limit():
    """A shared institutional address must not be spent by one evaluator.

    The demo's target market sits behind university NAT, where a per-IP ceiling
    equal to the per-device limit would lock out everyone after the first user.
    """
    assert DEMO_LIMITS["max_scans_per_ip"] > DEMO_LIMITS["max_scans_total"]


def test_separate_ips_are_independent():
    for _ in range(DEMO_LIMITS["max_scans_total"]):
        DemoRateLimiter.consume_scan(IP, FP_A, "full")

    allowed, headers = DemoRateLimiter.check_rate_limit("198.51.100.4", FP_B, "full")
    assert allowed
    assert _remaining(headers) == DEMO_LIMITS["max_scans_total"]


def test_remaining_is_the_smaller_of_the_two_counters():
    """Never advertise more scans than the caller can actually use."""
    ip_limit = DEMO_LIMITS["max_scans_per_ip"]

    # Spend the IP down to one, using throwaway devices.
    for i in range(ip_limit - 1):
        DemoRateLimiter.consume_scan(IP, f"{i:064x}", "full")

    _, headers = DemoRateLimiter.check_rate_limit(IP, FP_B, "full")
    assert _remaining(headers) == 1, "device allowance must not mask the IP ceiling"
    assert headers["X-RateLimit-Method"] == "ip"


@pytest.mark.parametrize("quality", ["minimal", "none", "junk"])
def test_low_quality_fingerprints_fall_back_to_the_ip_counter(quality):
    """An untrustworthy fingerprint must not create a private allowance."""
    DemoRateLimiter.consume_scan(IP, FP_A, quality)
    _, headers = DemoRateLimiter.check_rate_limit(IP, FP_B, quality)
    assert _remaining(headers) == DEMO_LIMITS["max_scans_per_ip"] - 1
    assert headers["X-RateLimit-Method"] == "ip"


def test_whitelisted_ip_is_unlimited_and_never_consumes(monkeypatch):
    monkeypatch.setattr(demo_routes, "DEMO_WHITELIST_IPS", {IP})

    for _ in range(DEMO_LIMITS["max_scans_per_ip"] + 5):
        DemoRateLimiter.consume_scan(IP, FP_A, "full")

    allowed, headers = DemoRateLimiter.check_rate_limit(IP, FP_A, "full")
    assert allowed
    assert headers["X-RateLimit-Remaining"] == "unlimited"


def test_remaining_never_goes_negative():
    for _ in range(DEMO_LIMITS["max_scans_per_ip"] + 3):
        headers = DemoRateLimiter.consume_scan(IP, FP_A, "full")
        assert _remaining(headers) >= 0
