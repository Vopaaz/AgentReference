#!/usr/bin/env python3
"""
Calculate rolling one-year return distribution statistics for US ETFs with
Moomoo OpenD.

Formula for each window:
    return = end_date_low / start_date_high - 1

Default date range, calculated at runtime:
    FIRST_START: three years and one day before today
    LAST_END:    one day before today

Windows with no daily K-line data on the start date are skipped. If the target
end date has no daily K-line data, the previous valid trading day is used.
"""

from __future__ import annotations

import argparse
from bisect import bisect_right
import csv
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

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
FIRST_START = add_calendar_years(TODAY, -3) - timedelta(days=1)
LAST_END = TODAY - timedelta(days=1)

DEFAULT_TICKERS = ("VOO", "QQQ", "SMH", "QTUM")
DEFAULT_P5_OUTPUT = Path("text") / "rolling_one_year_p5_returns.txt"


@dataclass(frozen=True)
class WindowReturn:
    code: str
    start_date: str
    requested_end_date: str
    end_date: str
    start_high: float
    end_low: float
    return_rate: float


@dataclass(frozen=True)
class TickerStats:
    code: str
    worst: WindowReturn
    mean_return: float
    median_return: float
    p5_return: float
    p10_return: float
    p25_return: float
    p75_return: float
    valid_windows: int
    skipped_windows: int
    adjusted_end_windows: int
    total_windows: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Use Moomoo OpenD historical daily K-line data to calculate "
            "rolling one-year return distribution statistics based on "
            "start-day high and end-day low."
        )
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        default=list(DEFAULT_TICKERS),
        help="Ticker symbols. Use plain US symbols or full Moomoo codes. Default: VOO QQQ SMH QTUM",
    )
    parser.add_argument("--market", default="US", help="Market prefix for plain symbols. Default: US")
    parser.add_argument(
        "--first-start",
        default=FIRST_START.isoformat(),
        help=(
            "Earliest window start date, YYYY-MM-DD. "
            f"Default: {FIRST_START.isoformat()} (3 years + 1 day before today)"
        ),
    )
    parser.add_argument(
        "--last-end",
        default=LAST_END.isoformat(),
        help=(
            "Latest window end date, YYYY-MM-DD. "
            f"Default: {LAST_END.isoformat()} (1 day before today)"
        ),
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
        "--summary-csv",
        help="Optional path to write one summary row per ticker.",
    )
    parser.add_argument(
        "--all-windows-csv",
        help="Optional path to write every valid rolling window.",
    )
    parser.add_argument(
        "--p5-output",
        default=str(DEFAULT_P5_OUTPUT),
        help=f"Path to write p5 results. Default: {DEFAULT_P5_OUTPUT}",
    )
    parser.add_argument(
        "--show-sdk-logs",
        action="store_true",
        help="Show Moomoo SDK connection logs. By default, SDK INFO logs are hidden.",
    )
    return parser.parse_args()


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def format_date(value: date) -> str:
    return value.strftime("%Y-%m-%d")


def add_one_calendar_year(value: date) -> date:
    return add_calendar_years(value, 1)


def iter_calendar_windows(first_start: date, last_end: date) -> Iterable[tuple[date, date]]:
    current = first_start
    while True:
        end = add_one_calendar_year(current)
        if end > last_end:
            break
        yield current, end
        current += timedelta(days=1)


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


def fetch_daily_bars(
    quote_ctx: OpenQuoteContext,
    code: str,
    start: str,
    end: str,
    autype,
) -> dict[str, dict[str, float]]:
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
        return {}

    bars = pd.concat(frames, ignore_index=True)
    missing_columns = {"time_key", "high", "low"} - set(bars.columns)
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise RuntimeError(f"{code}: historical K-line response is missing columns: {missing}")

    bars = bars.copy()
    bars["date"] = bars["time_key"].astype(str).str.slice(0, 10)
    bars["high"] = pd.to_numeric(bars["high"], errors="coerce")
    bars["low"] = pd.to_numeric(bars["low"], errors="coerce")
    bars = bars.dropna(subset=["date", "high", "low"])
    bars = bars.drop_duplicates(subset=["date"], keep="last")

    return {
        row["date"]: {"high": float(row["high"]), "low": float(row["low"])}
        for _, row in bars.iterrows()
    }


def calculate_returns(
    code: str,
    bars_by_date: dict[str, dict[str, float]],
    windows: list[tuple[date, date]],
) -> tuple[list[WindowReturn], int]:
    returns: list[WindowReturn] = []
    skipped = 0
    trading_dates = sorted(bars_by_date)

    for start_date, end_date in windows:
        start_key = format_date(start_date)
        start_bar = bars_by_date.get(start_key)
        if start_bar is None:
            skipped += 1
            continue

        requested_end_key = format_date(end_date)
        end_date_index = bisect_right(trading_dates, requested_end_key) - 1
        if end_date_index < 0:
            skipped += 1
            continue

        end_key = trading_dates[end_date_index]
        end_bar = bars_by_date[end_key]

        start_high = start_bar["high"]
        end_low = end_bar["low"]
        if start_high <= 0:
            skipped += 1
            continue

        returns.append(
            WindowReturn(
                code=code,
                start_date=start_key,
                requested_end_date=requested_end_key,
                end_date=end_key,
                start_high=start_high,
                end_low=end_low,
                return_rate=end_low / start_high - 1.0,
            )
        )

    return returns, skipped


def build_stats(code: str, window_returns: list[WindowReturn], skipped: int, total_windows: int) -> TickerStats:
    series = pd.Series([item.return_rate for item in window_returns], dtype="float64")
    worst = min(window_returns, key=lambda item: item.return_rate)
    adjusted_end_windows = sum(1 for item in window_returns if item.requested_end_date != item.end_date)

    return TickerStats(
        code=code,
        worst=worst,
        mean_return=float(series.mean()),
        median_return=float(series.median()),
        p5_return=float(series.quantile(0.05)),
        p10_return=float(series.quantile(0.10)),
        p25_return=float(series.quantile(0.25)),
        p75_return=float(series.quantile(0.75)),
        valid_windows=len(window_returns),
        skipped_windows=skipped,
        adjusted_end_windows=adjusted_end_windows,
        total_windows=total_windows,
    )


def write_summary_csv(path: str, stats: list[TickerStats]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "code",
                "min_return",
                "min_return_pct",
                "mean_return",
                "mean_return_pct",
                "median_return",
                "median_return_pct",
                "p5_return",
                "p5_return_pct",
                "p10_return",
                "p10_return_pct",
                "p25_return",
                "p25_return_pct",
                "p75_return",
                "p75_return_pct",
                "start_date",
                "requested_end_date",
                "end_date",
                "start_high",
                "end_low",
                "valid_windows",
                "skipped_windows",
                "adjusted_end_windows",
                "total_windows",
            ],
        )
        writer.writeheader()
        for item in stats:
            writer.writerow(
                {
                    "code": item.code,
                    "min_return": f"{item.worst.return_rate:.10f}",
                    "min_return_pct": f"{item.worst.return_rate:.4%}",
                    "mean_return": f"{item.mean_return:.10f}",
                    "mean_return_pct": f"{item.mean_return:.4%}",
                    "median_return": f"{item.median_return:.10f}",
                    "median_return_pct": f"{item.median_return:.4%}",
                    "p5_return": f"{item.p5_return:.10f}",
                    "p5_return_pct": f"{item.p5_return:.4%}",
                    "p10_return": f"{item.p10_return:.10f}",
                    "p10_return_pct": f"{item.p10_return:.4%}",
                    "p25_return": f"{item.p25_return:.10f}",
                    "p25_return_pct": f"{item.p25_return:.4%}",
                    "p75_return": f"{item.p75_return:.10f}",
                    "p75_return_pct": f"{item.p75_return:.4%}",
                    "start_date": item.worst.start_date,
                    "requested_end_date": item.worst.requested_end_date,
                    "end_date": item.worst.end_date,
                    "start_high": f"{item.worst.start_high:.6f}",
                    "end_low": f"{item.worst.end_low:.6f}",
                    "valid_windows": item.valid_windows,
                    "skipped_windows": item.skipped_windows,
                    "adjusted_end_windows": item.adjusted_end_windows,
                    "total_windows": item.total_windows,
                }
            )


def write_all_windows_csv(path: str, rows: list[WindowReturn]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "code",
                "start_date",
                "requested_end_date",
                "end_date",
                "start_high",
                "end_low",
                "return",
                "return_pct",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "code": row.code,
                    "start_date": row.start_date,
                    "requested_end_date": row.requested_end_date,
                    "end_date": row.end_date,
                    "start_high": f"{row.start_high:.6f}",
                    "end_low": f"{row.end_low:.6f}",
                    "return": f"{row.return_rate:.10f}",
                    "return_pct": f"{row.return_rate:.4%}",
                }
            )


def write_p5_text(path: str, stats: list[TickerStats]) -> None:
    output_path = Path(path)
    if output_path.parent:
        output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8", newline="\n") as output:
        output.write("code,return_pct\n")
        for item in stats:
            output.write(f"{display_code(item.code)},{item.p5_return:.4%}\n")


def print_summary(stats: list[TickerStats]) -> None:
    print(
        "code          min       mean     median        p5       p10       p25       p75  min_window"
    )
    print("-" * 99)
    for item in stats:
        worst = item.worst
        window = f"{worst.start_date}->{worst.end_date}"
        if worst.requested_end_date != worst.end_date:
            window = f"{worst.start_date}->{worst.end_date}*"
        print(
            f"{item.code:<8} {worst.return_rate:>9.2%}  "
            f"{item.mean_return:>9.2%}  {item.median_return:>9.2%}  "
            f"{item.p5_return:>9.2%}  {item.p10_return:>9.2%}  "
            f"{item.p25_return:>9.2%}  {item.p75_return:>9.2%}  "
            f"{window}"
        )
    print("* end date was adjusted to the previous valid trading day")


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

    first_start = parse_date(args.first_start)
    last_end = parse_date(args.last_end)
    windows = list(iter_calendar_windows(first_start, last_end))
    if not windows:
        raise ValueError("No one-year windows exist for the requested date range.")

    codes = [normalize_code(ticker, args.market) for ticker in args.tickers]
    autype = get_autype(args.autype)

    stats: list[TickerStats] = []
    all_window_returns: list[WindowReturn] = []

    quote_ctx = OpenQuoteContext(host=args.host, port=args.port)
    try:
        for code in codes:
            bars_by_date = fetch_daily_bars(
                quote_ctx=quote_ctx,
                code=code,
                start=format_date(first_start),
                end=format_date(last_end),
                autype=autype,
            )
            window_returns, skipped = calculate_returns(code, bars_by_date, windows)
            if not window_returns:
                raise RuntimeError(f"{code}: no valid windows found in the requested date range.")

            stats.append(build_stats(code, window_returns, skipped, len(windows)))
            all_window_returns.extend(window_returns)
    finally:
        quote_ctx.close()

    print_summary(stats)
    write_p5_text(args.p5_output, stats)
    print(f"\nP5 results written to: {args.p5_output}")

    if args.summary_csv:
        write_summary_csv(args.summary_csv, stats)
        print(f"\nSummary CSV written to: {args.summary_csv}")

    if args.all_windows_csv:
        write_all_windows_csv(args.all_windows_csv, all_window_returns)
        print(f"All-window CSV written to: {args.all_windows_csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
