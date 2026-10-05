import copy
import struct
import unittest
from datetime import datetime

from collect_daily_volume import CHINA_TZ, collect, completed_cutoff, market_for, merge_points, normalize_bars, request_count
from tdx_v2 import TdxV2Bar, TdxV2Client, TdxV2Error


def bar(day, volume=100, amount=200):
    return TdxV2Bar(day, 2, 2.1, 1.9, 2, amount, volume)


class FakeClient:
    instances = []
    data = {}
    failures = set()

    def __init__(self, host, port, timeout):
        self.host = host
        self.requests = []
        self.closed = False
        self.instances.append(self)

    def connect(self):
        pass

    def disconnect(self):
        self.closed = True

    def get_daily_bars(self, market, code, count):
        self.requests.append((code, count))
        if (self.host, code) in self.failures:
            raise OSError("node failure")
        return self.data[code][-count:]


class VolumeTests(unittest.TestCase):
    def setUp(self):
        FakeClient.instances = []
        FakeClient.failures = set()
        FakeClient.data = {code: [bar(20260928), bar(20260929), bar(20260930)] for code in ["510300", "169201", "501018"]}
        self.latest = {"quote_date": "2026-09-30", "rows": [{"code": "169201", "name": "A"}, {"code": "501018", "name": "B"}]}
        self.now = datetime(2026, 10, 5, 18, tzinfo=CHINA_TZ)

    def test_market_and_units(self):
        self.assertEqual(market_for("169201"), 0)
        self.assertEqual(market_for("501018"), 1)
        with self.assertRaises(ValueError):
            market_for("999999")
        result = normalize_bars([bar(20260930, 28273, 56546)], "2026-09-30")
        self.assertEqual(result, [["2026-09-30", 28273, 56546]])
        with self.assertRaises(ValueError):
            normalize_bars([bar(20260930, 282.73, 56546)], "2026-09-30")

    def test_incomplete_session_and_zero(self):
        self.assertEqual(completed_cutoff(datetime(2026, 9, 30, 15, 59, tzinfo=CHINA_TZ)), "2026-09-29")
        self.assertEqual(completed_cutoff(datetime(2026, 9, 30, 16, tzinfo=CHINA_TZ)), "2026-09-30")
        self.assertEqual(normalize_bars([bar(20260930)], "2026-09-29"), [])
        self.assertEqual(normalize_bars([bar(20260930, 0, 0)], "2026-09-30")[0][1], 0)
        for bad in [bar(20260930, -1), bar(20260930, float("nan")), bar(20260930, 0, 1)]:
            with self.assertRaises(ValueError):
                normalize_bars([bad], "2026-09-30")

    def test_daily_merge_corrects_overlap_keeps_history(self):
        self.assertEqual(merge_points([["2026-09-28", 1, 2], ["2026-09-29", 2, 4]],
                                     [["2026-09-29", 3, 6], ["2026-09-30", 4, 8]], "2026-09-28"),
                         [["2026-09-28", 1, 2], ["2026-09-29", 3, 6], ["2026-09-30", 4, 8]])
        self.assertEqual(request_count({"checked_through": "2026-09-29"}, "2026-09-30", 60), 6)
        self.assertEqual(request_count({"checked_through": "2026-09-01"}, "2026-09-30", 60), 34)

    def test_repeat_is_noop_and_missing_is_not_zero(self):
        FakeClient.data["169201"] = [bar(20260928)]
        result = collect(self.latest, {}, self.now, client_factory=FakeClient)
        self.assertEqual(result["funds"]["169201"]["points"], [["2026-09-28", 100, 200]])
        FakeClient.instances = []
        self.assertIs(collect(self.latest, result, self.now, client_factory=FakeClient), result)
        self.assertTrue(all(all(code == "510300" for code, _ in c.requests) for c in FakeClient.instances))

    def test_new_fund_backfill_even_without_new_exchange_day(self):
        result = collect(self.latest, {}, self.now, client_factory=FakeClient)
        del result["funds"]["501018"]
        FakeClient.instances = []
        new = collect(self.latest, result, self.now, client_factory=FakeClient)
        self.assertEqual(len(new["funds"]), 2)
        symbol_calls = [(code, count) for c in FakeClient.instances for code, count in c.requests if code != "510300"]
        self.assertEqual(symbol_calls, [("501018", 60)])

    def test_incremental_catches_up_missed_days(self):
        result = collect(self.latest, {}, self.now, client_factory=FakeClient)
        for code in FakeClient.data:
            FakeClient.data[code] += [bar(20261008, 200, 400), bar(20261009, 300, 600)]
        FakeClient.instances = []
        new = collect(self.latest, result, datetime(2026, 10, 9, 18, tzinfo=CHINA_TZ), client_factory=FakeClient)
        self.assertEqual(new["funds"]["169201"]["points"][-2:], [["2026-10-08", 200, 400], ["2026-10-09", 300, 600]])
        self.assertEqual(len(new["funds"]["169201"]["points"]), 5)
        self.assertEqual([n for c in FakeClient.instances for code, n in c.requests if code != "510300"], [14, 14])

    def test_failover_and_full_pool_failure_preserves_cache(self):
        FakeClient.failures = {("121.36.248.138", "169201")}
        result = collect(self.latest, {}, self.now, client_factory=FakeClient)
        self.assertEqual(result["fund_count"], 2)
        before = copy.deepcopy(result)
        FakeClient.data["501018"] = []
        with self.assertRaisesRegex(RuntimeError, "previous snapshot preserved"):
            collect(self.latest, result, self.now, refresh=True, client_factory=FakeClient)
        self.assertEqual(result, before)
        self.assertTrue(all(c.closed for c in FakeClient.instances))

    def test_reject_benchmark_behind_premium_date(self):
        FakeClient.data["510300"] = [bar(20260929)]
        with self.assertRaisesRegex(RuntimeError, "No healthy TDX node"):
            collect(self.latest, {}, self.now, client_factory=FakeClient)

    def test_protocol_decode_and_malformed_frame(self):
        header = bytearray(33)
        header[2:8] = b"169201"
        payload = bytes(header) + struct.pack("<II6fI", 20260930, 0, 2, 2.1, 1.9, 2, 56546, 28273, 0) + bytes(120)
        decoded = TdxV2Client._decode_bars(payload, "169201")
        self.assertEqual(decoded[0].volume, 28273)
        for invalid, code in [(payload[:-1], "169201"), (payload, "501018"), (b"", "169201")]:
            with self.assertRaises(TdxV2Error):
                TdxV2Client._decode_bars(invalid, code)


if __name__ == "__main__":
    unittest.main()
