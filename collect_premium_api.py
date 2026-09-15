#!/usr/bin/env python3
"""Collect LOF premium data from the upstream public HTTP API."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import time as time_module
import urllib.error
import urllib.request
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


API_URL = "https://api.xiaobeiyangji.com/yangji-api/api/get-arbitrage-list"
SOURCE_ACCURACY_URL = "https://api.xiaobeiyangji.com/yangji-api/api/get-fund-quote-diff"
CHINA_TZ = ZoneInfo("Asia/Shanghai")
TRADING_SLOTS = (
    time(9, 30),
    time(10, 0),
    time(10, 30),
    time(11, 0),
    time(11, 30),
    time(13, 0),
    time(13, 30),
    time(14, 0),
    time(14, 30),
    time(15, 0),
)


def fetch_payload(timeout: int = 30) -> dict[str, Any]:
    body = json.dumps(
        {
            "page": 1,
            "dataResources": "4",
            "version": "3.9.0.0",
            "clientType": "APP",
        },
        separators=(",", ":"),
    ).encode()
    request = urllib.request.Request(
        API_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "xbyj-premium-dashboard/1.0",
            "X-Trace-Id": f"github-dashboard-{int(datetime.now().timestamp())}",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"API returned HTTP {response.status}")
        return json.load(response)


def fetch_source_accuracy_payload(code: str, timeout: int = 20, attempts: int = 3) -> dict[str, Any]:
    body = json.dumps({"code": code}, separators=(",", ":")).encode()
    last_error: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(
            SOURCE_ACCURACY_URL,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "User-Agent": "xbyj-premium-dashboard/1.0",
                "X-Trace-Id": f"source-accuracy-{code}-{int(datetime.now().timestamp())}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if response.status != 200:
                    raise RuntimeError(f"source accuracy API returned HTTP {response.status}")
                return json.load(response)
        except (OSError, RuntimeError, urllib.error.URLError, json.JSONDecodeError) as error:
            last_error = error
            if attempt + 1 < attempts:
                time_module.sleep(0.4 * (attempt + 1))
    raise RuntimeError(f"source accuracy request failed for {code}: {last_error}")


def nearest_slot(now: datetime, max_delay_minutes: int = 50) -> str | None:
    candidates = [datetime.combine(now.date(), slot, tzinfo=CHINA_TZ) for slot in TRADING_SLOTS]
    eligible = [candidate for candidate in candidates if timedelta(0) <= now - candidate <= timedelta(minutes=max_delay_minutes)]
    if not eligible:
        return None
    return max(eligible).strftime("%H:%M")


def latest_configured_slot(now: datetime) -> str:
    """Use the latest normal market slot for a manual run outside the schedule."""
    candidates = [datetime.combine(now.date(), slot, tzinfo=CHINA_TZ) for slot in TRADING_SLOTS]
    elapsed = [candidate for candidate in candidates if candidate <= now]
    return (max(elapsed) if elapsed else min(candidates)).strftime("%H:%M")


def pct(value: Any) -> float | None:
    return None if value is None else round(float(value) * 100, 6)


def number(value: Any) -> float | None:
    return None if value is None else float(value)


def first_number(*values: Any) -> float | None:
    for value in values:
        if value is not None:
            try:
                result = float(value)
            except (TypeError, ValueError):
                continue
            return result
    return None


def resolve_best_source(data: dict[str, Any]) -> str:
    """Match the App's tie-breaking rule: source 1, then source 3, then source 2."""
    deviations = {
        source: first_number(data.get(f"diffNew{source}"), data.get(f"diff{source}"))
        for source in ("1", "2", "3")
    }
    value1, value2, value3 = (deviations[source] for source in ("1", "2", "3"))
    abs1 = abs(value1) if value1 is not None else float("inf")
    abs2 = abs(value2) if value2 is not None else float("inf")
    abs3 = abs(value3) if value3 is not None else float("inf")
    if abs1 <= abs2 and abs1 <= abs3:
        return "1"
    if abs3 <= abs1 and abs3 <= abs2:
        return "3"
    return "2"


def normalize_source_accuracy(row: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("code") != 200 or not isinstance(payload.get("data"), dict):
        raise ValueError(f"unexpected source accuracy response for {row['code']}")
    data = payload["data"]
    best_source = resolve_best_source(data)
    sources = {}
    for source in ("1", "2", "3"):
        sources[source] = {
            "simulated_change_pct": pct(first_number(data.get(f"change{source}"))),
            "yesterday_deviation_pct": pct(first_number(data.get(f"diffNew{source}"), data.get(f"diff{source}"))),
            "month_avg_deviation_pct": pct(first_number(data.get(f"diff{source}Avg"), data.get(f"resDiff{source}"))),
        }
    return {
        "code": row["code"],
        "name": row["name"],
        "source_date": str(data.get("date") or ""),
        "api_today": str(data.get("today") or ""),
        "updated_at": str(data.get("updateTime") or ""),
        "official_nav": number(data.get("nav")),
        "best_source": best_source,
        "sources": sources,
    }


def collect_source_accuracy(data_dir: Path, rows: list[dict[str, Any]], now: datetime, workers: int = 8) -> dict[str, Any]:
    today = now.strftime("%Y-%m-%d")
    daily_path = data_dir / "source_accuracy" / f"{today}.json"
    expected_codes = {row["code"] for row in rows}
    if daily_path.exists():
        existing = json.loads(daily_path.read_text(encoding="utf-8"))
        existing_codes = set((existing.get("funds") or {}).keys())
        if expected_codes.issubset(existing_codes):
            write_json(data_dir / "source_accuracy" / "latest.json", existing)
            return {
                "status": "cached",
                "display_date": today,
                "coverage": len(existing_codes),
                "daily_file": str(daily_path),
            }

    funds: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}

    def fetch_row(row: dict[str, Any]) -> tuple[str, dict[str, Any] | None, str | None]:
        try:
            payload = fetch_source_accuracy_payload(row["code"])
            return row["code"], normalize_source_accuracy(row, payload), None
        except (OSError, ValueError, RuntimeError, urllib.error.URLError, json.JSONDecodeError) as error:
            return row["code"], None, str(error)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        for code, item, error in executor.map(fetch_row, rows):
            if item is not None:
                funds[code] = item
            else:
                errors[code] = error or "unknown error"

    if len(funds) < 250:
        raise RuntimeError(f"source accuracy coverage is incomplete: {len(funds)}/{len(rows)}")

    source_counts = {source: 0 for source in ("1", "2", "3")}
    source_dates: dict[str, int] = {}
    for item in funds.values():
        source_counts[item["best_source"]] += 1
        source_date = item["source_date"] or "unknown"
        source_dates[source_date] = source_dates.get(source_date, 0) + 1

    document = {
        "display_date": today,
        "captured_at": now.isoformat(timespec="seconds"),
        "coverage": len(funds),
        "requested": len(rows),
        "best_source_counts": source_counts,
        "source_dates": source_dates,
        "errors": errors,
        "funds": funds,
    }
    write_json(daily_path, document)
    write_json(data_dir / "source_accuracy" / "latest.json", document)
    return {
        "status": "collected",
        "display_date": today,
        "coverage": len(funds),
        "errors": len(errors),
        "best_source_counts": source_counts,
        "source_dates": source_dates,
        "daily_file": str(daily_path),
    }


def normalize_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        code = str(item.get("code") or "")
        name = str(item.get("name") or "")
        if len(code) != 6 or not code.isdigit() or not name:
            continue
        rows.append(
            {
                "code": code,
                "name": name,
                "category": str(item.get("lofType") or "other"),
                "purchase_info": str(item.get("purchaseInfo") or ""),
                "off_market_value": number(item.get("quotePrice")),
                "off_market_change_pct": pct(item.get("quoteYield")),
                "on_market_price": number(item.get("insidePrice")),
                "on_market_change_pct": pct(item.get("insideYield")),
                "premium_rate_pct": pct(item.get("premiumRate")),
            }
        )
    rows.sort(
        key=lambda row: (
            row["premium_rate_pct"] is not None,
            row["premium_rate_pct"] if row["premium_rate_pct"] is not None else float("-inf"),
        ),
        reverse=True,
    )
    return rows


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_compact_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def archive_daily_close(data_dir: Path, daily_path: Path) -> str:
    daily = json.loads(daily_path.read_text(encoding="utf-8"))
    date = str(daily.get("date") or daily_path.stem)
    snapshots = daily.get("snapshots") or []
    if not snapshots:
        raise ValueError(f"cannot archive an empty daily file: {daily_path}")
    closing_snapshot = max(snapshots, key=lambda item: (str(item.get("slot") or ""), str(item.get("captured_at") or "")))

    archive_path = data_dir / "archive" / f"{date[:4]}.json"
    if archive_path.exists():
        archive = json.loads(archive_path.read_text(encoding="utf-8"))
    else:
        archive = {"year": date[:4], "dates": [], "funds": {}}

    archive["dates"] = sorted(set(archive.get("dates") or []) | {date})
    funds = archive.setdefault("funds", {})
    for row in closing_snapshot.get("rows") or []:
        premium = row.get("premium_rate_pct")
        if premium is None:
            continue
        code = row["code"]
        fund = funds.setdefault(
            code,
            {"name": row["name"], "category": row.get("category", "other"), "points": []},
        )
        fund["name"] = row["name"]
        fund["category"] = row.get("category", "other")
        points_by_date = {point[0]: point[1] for point in fund.get("points") or []}
        points_by_date[date] = premium
        fund["points"] = [[point_date, points_by_date[point_date]] for point_date in sorted(points_by_date)]

    archive["funds"] = {code: funds[code] for code in sorted(funds)}
    write_compact_json(archive_path, archive)
    return date


def compact_history(data_dir: Path, intraday_retention_days: int = 60, source_accuracy_retention_days: int = 60) -> dict[str, Any]:
    """Keep recent intraday files and roll older days into compact daily-close archives."""
    daily_dir = data_dir / "daily"
    daily_paths = sorted(daily_dir.glob("????-??-??.json"))
    retention = max(1, intraday_retention_days)
    archive_candidates = daily_paths[:-retention]
    archived_dates = []
    for daily_path in archive_candidates:
        archived_dates.append(archive_daily_close(data_dir, daily_path))
        daily_path.unlink()

    source_dir = data_dir / "source_accuracy"
    source_paths = sorted(source_dir.glob("????-??-??.json"))
    source_retention = max(1, source_accuracy_retention_days)
    removed_source_dates = []
    for source_path in source_paths[:-source_retention]:
        removed_source_dates.append(source_path.stem)
        source_path.unlink()

    raw_dates = sorted(path.stem for path in daily_dir.glob("????-??-??.json"))
    all_dates = set(raw_dates)
    for archive_path in sorted((data_dir / "archive").glob("????.json")):
        archive = json.loads(archive_path.read_text(encoding="utf-8"))
        all_dates.update(str(date) for date in archive.get("dates") or [])

    latest = json.loads((data_dir / "latest.json").read_text(encoding="utf-8"))
    write_json(
        data_dir / "index.json",
        {
            "updated_at": latest.get("captured_at"),
            "latest_date": latest.get("inside_date"),
            "latest_slot": latest.get("slot"),
            "dates": sorted(all_dates),
            "intraday_dates": raw_dates,
            "intraday_retention_days": retention,
            "archive_granularity": "daily_close",
        },
    )
    return {
        "archived_dates": archived_dates,
        "removed_source_accuracy_dates": removed_source_dates,
        "intraday_dates": len(raw_dates),
        "all_dates": len(all_dates),
    }


def update_history(data_dir: Path, payload: dict[str, Any], now: datetime, slot: str) -> dict[str, Any]:
    if payload.get("code") != 200 or not isinstance(payload.get("data"), dict):
        raise ValueError(f"unexpected API response: code={payload.get('code')!r}")

    api_data = payload["data"]
    today = now.strftime("%Y-%m-%d")
    inside_date = str(api_data.get("insideDate") or "")
    quote_date = str(api_data.get("quoteDate") or "")
    if inside_date != today or quote_date != today:
        return {
            "status": "skipped",
            "reason": "API data is not dated today; likely a non-trading day or data not ready",
            "today": today,
            "inside_date": inside_date,
            "quote_date": quote_date,
        }

    rows = normalize_rows(api_data.get("list") or [])
    valid_rows = sum(row["premium_rate_pct"] is not None for row in rows)
    if len(rows) < 250 or valid_rows < 250:
        raise ValueError(f"incomplete response: rows={len(rows)}, rows_with_premium={valid_rows}")

    snapshot = {
        "slot": slot,
        "captured_at": now.isoformat(timespec="seconds"),
        "inside_date": inside_date,
        "quote_date": quote_date,
        "row_count": len(rows),
        "rows": rows,
    }
    daily_path = data_dir / "daily" / f"{today}.json"
    if daily_path.exists():
        daily = json.loads(daily_path.read_text(encoding="utf-8"))
    else:
        daily = {"date": today, "snapshots": []}

    existing = next((item for item in daily.get("snapshots", []) if item.get("slot") == slot), None)
    if existing and existing.get("rows") == rows and existing.get("inside_date") == inside_date and existing.get("quote_date") == quote_date:
        return {
            "status": "skipped",
            "reason": "configured slot already contains identical data",
            "date": today,
            "slot": slot,
            "rows": len(rows),
            "rows_with_premium": valid_rows,
        }

    snapshots = [item for item in daily.get("snapshots", []) if item.get("slot") != slot]
    snapshots.append(snapshot)
    snapshots.sort(key=lambda item: item["slot"])
    daily["snapshots"] = snapshots
    write_json(daily_path, daily)
    write_json(data_dir / "latest.json", snapshot)

    dates = sorted(path.stem for path in (data_dir / "daily").glob("????-??-??.json"))
    write_json(
        data_dir / "index.json",
        {
            "updated_at": now.isoformat(timespec="seconds"),
            "latest_date": today,
            "latest_slot": slot,
            "dates": dates,
        },
    )
    return {
        "status": "collected",
        "date": today,
        "slot": slot,
        "rows": len(rows),
        "rows_with_premium": valid_rows,
        "daily_file": str(daily_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--force", action="store_true", help="collect outside a scheduled trading slot")
    parser.add_argument("--slot", help="override the HH:MM snapshot slot")
    parser.add_argument("--fixture", type=Path, help="read an API response from a local JSON fixture")
    parser.add_argument("--skip-source-accuracy", action="store_true", help="skip the daily yesterday-best source collection")
    parser.add_argument("--source-workers", type=int, default=8, help="parallel workers for per-fund source accuracy requests")
    parser.add_argument("--intraday-retention-days", type=int, default=60, help="trading days to retain at half-hour resolution")
    parser.add_argument("--source-accuracy-retention-days", type=int, default=60, help="daily source accuracy files to retain")
    args = parser.parse_args()

    now = datetime.now(CHINA_TZ)
    if args.fixture:
        fixture_payload = json.loads(args.fixture.read_text(encoding="utf-8"))
        fixture_date = str(fixture_payload.get("data", {}).get("insideDate") or "")
        if args.force and fixture_date:
            now = datetime.combine(datetime.fromisoformat(fixture_date).date(), time(15, 5), tzinfo=CHINA_TZ)
    else:
        fixture_payload = None
    slot = args.slot or nearest_slot(now)
    if slot is None and not args.force:
        print(json.dumps({"status": "skipped", "reason": "outside configured trading slots"}, ensure_ascii=False))
        return 0
    slot = slot or latest_configured_slot(now)

    try:
        payload = fixture_payload if fixture_payload is not None else fetch_payload()
        result = update_history(args.data_dir, payload, now, slot)
        if not args.skip_source_accuracy and result.get("status") == "collected":
            latest = json.loads((args.data_dir / "latest.json").read_text(encoding="utf-8"))
            result["source_accuracy"] = collect_source_accuracy(args.data_dir, latest.get("rows", []), now, args.source_workers)
        elif not args.skip_source_accuracy and result.get("reason") == "configured slot already contains identical data":
            latest = json.loads((args.data_dir / "latest.json").read_text(encoding="utf-8"))
            result["source_accuracy"] = collect_source_accuracy(args.data_dir, latest.get("rows", []), now, args.source_workers)
        if result.get("status") == "collected" or result.get("reason") == "configured slot already contains identical data":
            result["history_compaction"] = compact_history(
                args.data_dir,
                intraday_retention_days=args.intraday_retention_days,
                source_accuracy_retention_days=args.source_accuracy_retention_days,
            )
    except (OSError, ValueError, RuntimeError, urllib.error.URLError, json.JSONDecodeError) as error:
        print(f"collection failed: {error}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
