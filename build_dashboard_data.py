#!/usr/bin/env python3
"""Build compact static data files consumed by the GitHub Pages dashboard."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


CHINA_TZ = ZoneInfo("Asia/Shanghai")


def write_compact(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--site-data-dir", type=Path, default=Path("site/data"))
    args = parser.parse_args()

    latest_path = args.data_dir / "latest.json"
    index_path = args.data_dir / "index.json"
    if not latest_path.exists() or not index_path.exists():
        raise SystemExit("data/latest.json and data/index.json are required")

    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    index = json.loads(index_path.read_text(encoding="utf-8"))
    source_accuracy_path = args.data_dir / "source_accuracy" / "latest.json"
    source_accuracy = (
        json.loads(source_accuracy_path.read_text(encoding="utf-8"))
        if source_accuracy_path.exists()
        else {"display_date": None, "coverage": 0, "funds": {}}
    )
    all_dates = index.get("dates", [])
    intraday_dates = index.get("intraday_dates", all_dates[-60:])
    funds: dict[str, dict[str, object]] = {}

    for archive_path in sorted((args.data_dir / "archive").glob("????.json")):
        archive = json.loads(archive_path.read_text(encoding="utf-8"))
        for code, archived_fund in (archive.get("funds") or {}).items():
            fund = funds.setdefault(
                code,
                {"name": archived_fund.get("name", code), "category": archived_fund.get("category", "other"), "points": []},
            )
            fund["name"] = archived_fund.get("name", fund["name"])
            fund["category"] = archived_fund.get("category", fund["category"])
            fund["points"].extend([[f"{date}T15:00:00+08:00", premium] for date, premium in archived_fund.get("points") or []])

    for date in intraday_dates:
        daily_path = args.data_dir / "daily" / f"{date}.json"
        if not daily_path.exists():
            continue
        daily = json.loads(daily_path.read_text(encoding="utf-8"))
        for snapshot in daily.get("snapshots", []):
            captured_at = snapshot.get("captured_at")
            for row in snapshot.get("rows", []):
                premium = row.get("premium_rate_pct")
                if premium is None:
                    continue
                code = row["code"]
                fund = funds.setdefault(code, {"name": row["name"], "category": row.get("category", "other"), "points": []})
                fund["name"] = row["name"]
                fund["category"] = row.get("category", "other")
                fund["points"].append([captured_at, premium])

    generated_at = datetime.now(CHINA_TZ).isoformat(timespec="seconds")
    args.site_data_dir.mkdir(parents=True, exist_ok=True)
    trends_dir = args.site_data_dir / "trends"
    if trends_dir.exists():
        shutil.rmtree(trends_dir)
    trends_dir.mkdir(parents=True)
    write_compact(args.site_data_dir / "latest.json", latest)
    write_compact(args.site_data_dir / "source-accuracy.json", source_accuracy)
    legacy_trends_path = args.site_data_dir / "trends.json"
    if legacy_trends_path.exists():
        legacy_trends_path.unlink()
    for code, fund in funds.items():
        fund["points"].sort(key=lambda point: point[0])
        write_compact(
            trends_dir / f"{code}.json",
            {
                "generated_at": generated_at,
                "code": code,
                "name": fund["name"],
                "category": fund["category"],
                "points": fund["points"],
                "intraday_retention_days": index.get("intraday_retention_days", 60),
                "archive_granularity": index.get("archive_granularity", "daily_close"),
            },
        )
    write_compact(
        args.site_data_dir / "index.json",
        {
            "generated_at": generated_at,
            "latest_date": index.get("latest_date"),
            "latest_slot": index.get("latest_slot"),
            "available_dates": all_dates,
            "intraday_dates": intraday_dates,
            "intraday_retention_days": index.get("intraday_retention_days", 60),
            "archive_granularity": index.get("archive_granularity", "daily_close"),
            "fund_count": len(funds),
            "source_accuracy_coverage": source_accuracy.get("coverage", 0),
        },
    )
    shutil.copyfile(index_path, args.site_data_dir / "archive-index.json")
    print(json.dumps({"latest_rows": len(latest.get("rows", [])), "trend_funds": len(funds), "history_dates": len(all_dates)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
