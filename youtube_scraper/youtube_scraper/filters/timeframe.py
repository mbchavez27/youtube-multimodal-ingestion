from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date

    def includes(self, value: datetime | None) -> bool:
        if value is None:
            return False
        target = value.date()
        return self.start <= target <= self.end


def build_date_range(start: str, end: str) -> DateRange:
    return DateRange(
        start=_parse_date(start),
        end=_parse_date(end),
    )


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()
