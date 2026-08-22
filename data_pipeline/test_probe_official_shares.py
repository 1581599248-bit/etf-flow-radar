import socket
import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

import probe_official_shares as probe
import update_daily_resilient as resilient


class OfficialShareProbeTests(unittest.TestCase):
    def test_ready_requires_both_exchanges_and_exact_date(self):
        day = date(2026, 8, 21)
        frame = pd.DataFrame(
            [
                {"code": "510001", "name": "A", "trade_date": day, "shares": 10, "exchange": "SSE"},
                {"code": "510002", "name": "B", "trade_date": day, "shares": 20, "exchange": "SSE"},
                {"code": "159001", "name": "C", "trade_date": day, "shares": 30, "exchange": "SZSE"},
                {"code": "159002", "name": "D", "trade_date": day, "shares": 40, "exchange": "SZSE"},
            ]
        )
        payload, code = probe.probe_official_shares(day, lambda _: frame, min_rows=4)
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["exchangeRows"], {"SSE": 2, "SZSE": 2})

    def test_not_published_is_retryable(self):
        day = date(2026, 8, 21)
        payload, code = probe.probe_official_shares(
            day,
            lambda _: (_ for _ in ()).throw(ValueError("closing shares have not been published")),
        )
        self.assertEqual(code, probe.RETRYABLE_EXIT)
        self.assertEqual(payload["category"], "not_published")

    def test_network_unreachable_is_retryable(self):
        category, retryable = probe.classify_probe_error(
            RuntimeError("[Errno 101] Network is unreachable")
        )
        self.assertEqual(category, "network")
        self.assertTrue(retryable)

    def test_schema_failure_blocks_publication(self):
        day = date(2026, 8, 21)
        frame = pd.DataFrame([{"code": "510001"}])
        payload, code = probe.probe_official_shares(day, lambda _: frame, min_rows=1)
        self.assertEqual(code, probe.QUALITY_EXIT)
        self.assertEqual(payload["status"], "quality_failed")

    def test_ipv4_addresses_are_tried_before_ipv6_without_dropping_fallback(self):
        ipv6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0))
        ipv4 = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        with patch.object(resilient, "_ORIGINAL_GETADDRINFO", return_value=[ipv6, ipv4]):
            ordered = resilient._ipv4_first_getaddrinfo("example.com", 443)
        self.assertEqual(ordered, [ipv4, ipv6])


if __name__ == "__main__":
    unittest.main()
