"""Date maths for billing periods (daily, weekly, monthly, yearly)."""
from __future__ import annotations

import calendar
from datetime import date, timedelta


def add_months(day: date, months: int) -> date:
    """Same day-of-month N months later; clamps 31 Jan + 1 month to 28/29 Feb."""
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last_day))


def add_periods(day: date, period: str, count: int = 1) -> date:
    if period == "daily":
        return day + timedelta(days=count)
    if period == "weekly":
        return day + timedelta(weeks=count)
    if period == "monthly":
        return add_months(day, count)
    if period == "yearly":
        return add_months(day, 12 * count)
    raise ValueError(f"Unknown billing period: {period}")


def period_end(start: date, period: str) -> date:
    """Last day covered by a billing period that starts on `start`."""
    return add_periods(start, period, 1) - timedelta(days=1)


def periods_between(start: date, end: date, period: str) -> int:
    """How many whole billing periods fit from start to end (inclusive)."""
    if end is None or end < start:
        return 0
    count, cursor = 0, start
    while cursor <= end and count < 10000:
        cursor = add_periods(cursor, period, 1)
        count += 1
    return count


# How many of each period make up one month (for monthly recurring revenue)
MONTHLY_FACTOR = {"daily": 30, "weekly": 52 / 12, "monthly": 1, "yearly": 1 / 12}

# Default contract length when a subscription is created
DEFAULT_TERM = {"daily": 30, "weekly": 12, "monthly": 12, "yearly": 1}
