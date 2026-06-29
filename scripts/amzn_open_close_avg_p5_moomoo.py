#!/usr/bin/env python3
"""
Calculate the p5 of AMZN daily open/close averages over a past period of time
using Moomoo OpenD historical daily K-line data.

For each daily K-line:
    open_close_avg = (open + close) / 2

Then p5 is calculated across all daily averages in the requested range.
"""

from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

try:
    from moomoo import AuType, KLType, OpenQuoteContext, RET_OK
    from moomoo import logger as SDK_LOGGER
    from moomoo import logging as SDK_LOGGING
except ModuleNotFoundError:
    from futu import AuType, KLType, OpenQuoteContext, RET_OK
    from futu import logger as SDK_LOGGER
    from futu import logging as SDK_LOGGING


def add_calendar_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, day=28)


TODAY = date.today()
START_DATE = add_calendar_years(TODAY, -2)
END_DATE = TODAY - timedelta(days=1)
TICKER = "AMZN"
MARKET = "US"
OUTPUT_PATH = Path("text") / "data_amzn_open_close_avg_p5.txt"


@dataclass(frozen=True)
class DailyOpenCloseAverage:
    code: str
    trading_date: str
    open_price: float
    close_price: float
    average_price: float


@dataclass(frozen=True)
class OpenCloseAverageStats:
    code: str
    start_date: str
    end_date: str
    p5_average_price: float
    mean_average_price: float
    median_average_price: float
    trading_days: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Use Moomoo OpenD historical daily K-line data to calculate "
            "AMZN p5 of daily (open + close) / 2 over the past 3 years."
        )
    )
    parser.add_argument(
        "--start-date",
        default=START_DATE.isoformat(),
        help=f"Start date, YYYY-MM-DD. Default: {START_DATE.isoformat()}",
    )
    parser.add_argument(
        "--end-date",
        default=END_DATE.isoformat(),
        help=f"End date, YYYY-MM-DD. Default: {END_DATE.isoformat()}",
    )
    parser.add_argument(
        "--autype",
        choices=("qfq", "hfq", "none"),
        default="qfq",
        help="Adjustment type for historical K-line data. Default: qfq",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("MOOMOO_OPEND_HOST", "127.0.0.1"),
        help="OpenD host. Default: MOOMOO_OPEND_HOST or 127.0.0.1",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("MOOMOO_OPEND_PORT", "11111")),
        help="OpenD quote port. Default: MOOMOO_OPEND_PORT or 11111",
    )
    parser.add_argument(
        "--show-sdk-logs",
        action="store_true",
        help="Show Moomoo SDK connection logs. By default, SDK INFO logs are hidden.",
    )
    return parser.parse_args()


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def normalize_code(ticker: str, market: str) -> str:
    ticker = ticker.strip().upper()
    if "." in ticker:
        return ticker
    return f"{market.upper()}.{ticker}"


def display_code(code: str) -> str:
    if code.startswith("US."):
        return code[3:]
    return code


def get_autype(name: str):
    mapping = {
        "qfq": AuType.QFQ,
        "hfq": AuType.HFQ,
        "none": AuType.NONE,
    }
    return mapping[name]


def fetch_daily_open_close_averages(
    quote_ctx: OpenQuoteContext,
    code: str,
    start: str,
    end: str,
    autype,
) -> list[DailyOpenCloseAverage]:
    frames = []
    page_req_key = None

    while True:
        ret, data, page_req_key = quote_ctx.request_history_kline(
            code,
            start=start,
            end=end,
            ktype=KLType.K_DAY,
            autype=autype,
            max_count=1000,
            page_req_key=page_req_key,
        )
        if ret != RET_OK:
            raise RuntimeError(f"{code}: request_history_kline failed: {data}")

        if data is not None and len(data) > 0:
            frames.append(data)

        if page_req_key is None:
            break

    if not frames:
        return []

    bars = pd.concat(frames, ignore_index=True)
    missing_columns = {"time_key", "open", "close"} - set(bars.columns)
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise RuntimeError(f"{code}: historical K-line response is missing columns: {missing}")

    bars = bars.copy()
    bars["date"] = bars["time_key"].astype(str).str.slice(0, 10)
    bars["open"] = pd.to_numeric(bars["open"], errors="coerce")
    bars["close"] = pd.to_numeric(bars["close"], errors="coerce")
    bars = bars.dropna(subset=["date", "open", "close"])
    bars = bars.drop_duplicates(subset=["date"], keep="last")
    bars = bars.sort_values("date")

    rows: list[DailyOpenCloseAverage] = []
    for _, row in bars.iterrows():
        open_price = float(row["open"])
        close_price = float(row["close"])
        rows.append(
            DailyOpenCloseAverage(
                code=code,
                trading_date=str(row["date"]),
                open_price=open_price,
                close_price=close_price,
                average_price=(open_price + close_price) / 2.0,
            )
        )

    return rows


def build_stats(code: str, rows: list[DailyOpenCloseAverage]) -> OpenCloseAverageStats:
    if not rows:
        raise RuntimeError(f"{code}: no valid daily K-line rows found in the requested date range.")

    series = pd.Series([item.average_price for item in rows], dtype="float64")
    return OpenCloseAverageStats(
        code=code,
        start_date=rows[0].trading_date,
        end_date=rows[-1].trading_date,
        p5_average_price=float(series.quantile(0.05)),
        mean_average_price=float(series.mean()),
        median_average_price=float(series.median()),
        trading_days=len(rows),
    )


def write_output(path: Path, stats: OpenCloseAverageStats) -> None:
    if path.parent:
        path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8", newline="\n") as output:
        output.write("code,p5_open_close_avg\n")
        output.write(
            f"{display_code(stats.code)},{stats.p5_average_price:.6f}"
        )


def print_summary(stats: OpenCloseAverageStats) -> None:
    print("code      p5_avg     mean_avg   median_avg  range")
    print("-" * 62)
    print(
        f"{stats.code:<8} {stats.p5_average_price:>10.2f}  "
        f"{stats.mean_average_price:>10.2f}  {stats.median_average_price:>10.2f}  "
        f"{stats.start_date}->{stats.end_date} ({stats.trading_days} days)"
    )


def quiet_sdk_logs() -> None:
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.WARNING)
    for handler in root_logger.handlers:
        handler.setLevel(logging.WARNING)
    SDK_LOGGER.console_level = SDK_LOGGING.WARNING


def main() -> int:
    args = parse_args()
    if not args.show_sdk_logs:
        quiet_sdk_logs()

    start_date = parse_date(args.start_date)
    end_date = parse_date(args.end_date)
    if start_date > end_date:
        raise ValueError("start date must be earlier than or equal to end date.")

    code = normalize_code(TICKER, MARKET)
    autype = get_autype(args.autype)

    quote_ctx = OpenQuoteContext(host=args.host, port=args.port)
    try:
        rows = fetch_daily_open_close_averages(
            quote_ctx=quote_ctx,
            code=code,
            start=start_date.isoformat(),
            end=end_date.isoformat(),
            autype=autype,
        )
    finally:
        quote_ctx.close()

    stats = build_stats(code, rows)
    print_summary(stats)
    write_output(OUTPUT_PATH, stats)
    print(f"\nAMZN open/close average p5 written to: {OUTPUT_PATH}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
