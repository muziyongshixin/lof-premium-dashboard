#!/usr/bin/env python3
"""Incrementally collect completed exchange daily volume via current TDX protocol.

The benchmark supplies exchange dates, not a weekday calendar. Missing symbol
bars stay missing (not zero); no suspension status is inferred from absence.
Only a fully fetched/validated fund pool replaces the previous atomic snapshot.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from tdx_v2 import MAX_BARS_PER_REQUEST, TDX_V2_SERVERS, TdxV2Client

CHINA_TZ = ZoneInfo("Asia/Shanghai")


def completed_cutoff(now: datetime) -> str:
    local = now.astimezone(CHINA_TZ)
    return (local.date() if local.time() >= time(16) else local.date() - timedelta(days=1)).isoformat()


def market_for(code: str) -> int:
    if len(code) != 6 or not code.isdigit() or code[0] not in "15":
        raise ValueError(f"Unsupported exchange fund: {code}")
    return int(code.startswith("5"))


def normalize_bars(bars, cutoff: str) -> list[list]:
    if not bars:
        raise ValueError("TDX returned no daily bars")
    result = {}
    for bar in bars:
        day = datetime.strptime(str(bar.date), "%Y%m%d").date().isoformat()
        if day > cutoff:
            continue  # Never persist an unfinished session, including manual runs.
        prices = [bar.open, bar.high, bar.low, bar.close]
        if not all(math.isfinite(v) and v > 0 for v in prices):
            raise ValueError(f"Invalid OHLC on {day}")
        if not all(math.isfinite(v) and v >= 0 for v in [bar.volume, bar.amount]):
            raise ValueError(f"Invalid volume/amount on {day}")
        if (bar.volume == 0) != (bar.amount == 0):
            raise ValueError(f"Inconsistent zero volume/amount on {day}")
        # Detect unit/record decoding errors (e.g. mistaking lots for shares).
        if bar.volume and not bar.low * .95 <= bar.amount / bar.volume <= bar.high * 1.05:
            raise ValueError(f"Amount/volume outside price range on {day}")
        if day in result:
            raise ValueError(f"Duplicate daily bar on {day}")
        result[day] = [day, round(bar.volume), round(bar.amount, 2)]
    return [result[day] for day in sorted(result)]


def request_count(previous: dict | None, target: str, backfill_days: int) -> int:
    if not previous:
        return backfill_days
    checked = date.fromisoformat(previous["checked_through"])
    gap = (date.fromisoformat(target) - checked).days
    count = max(5, gap + 5)  # Small overlap repairs recent upstream revisions.
    if count > MAX_BARS_PER_REQUEST:
        raise ValueError("History gap exceeds 700 days; run with --refresh-history")
    return count


def merge_points(previous: list, incoming: list, start_date: str) -> list:
    by_date = {row[0]: row for row in previous}
    by_date.update({row[0]: row for row in incoming if row[0] >= start_date})
    return [by_date[day] for day in sorted(by_date)]


def collect(latest: dict, cached: dict, now: datetime, backfill_days: int = 60,
            refresh: bool = False, client_factory=TdxV2Client) -> dict:
    cutoff = completed_cutoff(now)
    expected = latest.get("quote_date", "")
    if expected > cutoff:
        expected = ""  # Intraday premium dates do not prove a completed daily bar.
    expected = max(expected, cached.get("checked_through", ""))
    clients, errors = [], []
    try:
        for name, host, port in TDX_V2_SERVERS:
            client = client_factory(host, port, timeout=6)
            try:
                client.connect()
                calendar = normalize_bars(client.get_daily_bars(1, "510300", 700), cutoff)
                if not calendar or calendar[-1][0] < expected:
                    raise ValueError(f"Benchmark is behind premium date {expected}")
                clients.append((client, calendar))
            except Exception as error:
                client.disconnect()
                errors.append(f"{name}: {error}")
        if not clients:
            raise RuntimeError("No healthy TDX node: " + "; ".join(errors))
        target = max(calendar[-1][0] for _, calendar in clients)
        clients = sorted(clients, key=lambda item: item[1][-1][0], reverse=True)
        fresh_clients = [client for client, cal in clients if cal[-1][0] == target]
        calendar = [row[0] for row in clients[0][1]]
        pool = {row["code"]: row for row in latest["rows"]}
        if not pool or len(pool) != len(latest["rows"]):
            raise ValueError("Empty or duplicate fund pool")
        old_funds = cached.get("funds", {})
        if not refresh and cached.get("checked_through") == target and set(old_funds) == set(pool):
            print(json.dumps({"status": "unchanged", "checked_through": target, "fund_count": len(pool)}))
            return cached
        funds = {}
        for index, (code, row) in enumerate(sorted(pool.items())):
            market = market_for(code)
            previous = old_funds.get(code)
            if previous and previous.get("checked_through") == target and not refresh:
                funds[code] = previous
                continue
            count = request_count(None if refresh else previous, target, backfill_days)
            failures = []
            best_incoming = None
            for client in fresh_clients:
                try:
                    incoming = normalize_bars(client.get_daily_bars(market, code, count), cutoff)
                    if not incoming:
                        raise ValueError("No completed daily bars")
                    # Incremental overlap must reach the last verified session.
                    if previous and not refresh and incoming[0][0] > previous["checked_through"]:
                        raise ValueError("Incremental response does not reach cached history")
                    if best_incoming is None or incoming[-1][0] > best_incoming[-1][0]:
                        best_incoming = incoming
                    if incoming[-1][0] == target:
                        break
                except Exception as error:
                    failures.append(f"{client.host}: {error}")
            if best_incoming is None:
                raise RuntimeError(f"{code} failed on every healthy node; previous snapshot preserved: {failures}")
            start_date = previous["start_date"] if previous else calendar[-min(backfill_days, len(calendar))]
            points = merge_points(previous["points"] if previous else [], best_incoming, start_date)
            funds[code] = {"name": row["name"], "start_date": start_date,
                           "checked_through": target, "points": points}
            if (index + 1) % 50 == 0:
                print(f"Validated {index + 1}/{len(pool)} funds", flush=True)
        exchange_dates = sorted(set(cached.get("trading_dates", [])) | set(calendar))
        first = min(item["start_date"] for item in funds.values())
        exchange_dates = [day for day in exchange_dates if day >= first]
        result = {"schema_version": 1, "source": "tdx-v2", "volume_unit": "fund_units",
                  "amount_unit": "CNY", "checked_through": target,
                  "trading_dates": exchange_dates, "fund_count": len(funds), "funds": funds}
        print(json.dumps({"status": "updated", "checked_through": target, "fund_count": len(funds),
                          "missing_latest_bar": [code for code, fund in funds.items()
                                                 if not fund["points"] or fund["points"][-1][0] != target]}))
        return result
    finally:
        for client, _ in clients:
            client.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--backfill-days", type=int, default=60)
    parser.add_argument("--refresh-history", action="store_true", help="Re-fetch recent history even if already checked")
    args = parser.parse_args()
    if not 7 <= args.backfill_days <= 700:
        parser.error("--backfill-days must be between 7 and 700")
    path = args.data_dir / "volume" / "latest.json"
    cached = json.loads(path.read_text()) if path.exists() else {}
    latest = json.loads((args.data_dir / "latest.json").read_text())
    result = collect(latest, cached, datetime.now(CHINA_TZ), args.backfill_days, args.refresh_history)
    if result != cached:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
        os.replace(temporary, path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
