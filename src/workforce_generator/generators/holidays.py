"""Generates recognized federal and statutory holidays within the scheduling horizon."""

from __future__ import annotations

from datetime import date
from typing import List

from workforce_generator.config import GeneratorConfig
from workforce_generator.models.entities import HolidayCalendar
from workforce_generator.models.identifiers import format_holiday_id

# Canonical US Federal Statutory Holidays in 2026
US_HOLIDAYS_2026 = [
    (date(2026, 1, 1), "New Year's Day", "FEDERAL", True),
    (date(2026, 1, 19), "Martin Luther King Jr. Day", "FEDERAL", True),
    (date(2026, 2, 16), "Washington's Birthday (Presidents' Day)", "FEDERAL", True),
    (date(2026, 5, 25), "Memorial Day", "FEDERAL", True),
    (date(2026, 6, 19), "Juneteenth National Independence Day", "FEDERAL", True),
    (date(2026, 7, 4), "Independence Day", "FEDERAL", True),
    (date(2026, 9, 7), "Labor Day", "FEDERAL", True),
    (date(2026, 10, 12), "Columbus Day", "FEDERAL", True),
    (date(2026, 11, 11), "Veterans Day", "FEDERAL", True),
    (date(2026, 11, 26), "Thanksgiving Day", "FEDERAL", True),
    (date(2026, 12, 25), "Christmas Day", "FEDERAL", True),
]


def generate_holidays(config: GeneratorConfig) -> List[HolidayCalendar]:
    start_d = config.horizon.start_date
    end_d = config.horizon.end_date

    holidays: List[HolidayCalendar] = []
    seq = 0
    for cal_date, name, jurisdiction, is_stat in US_HOLIDAYS_2026:
        if start_d <= cal_date <= end_d:
            seq += 1
            holiday_id = format_holiday_id(cal_date.year, seq)
            holidays.append(
                HolidayCalendar(
                    holiday_id=holiday_id,
                    calendar_date=cal_date,
                    name=name,
                    jurisdiction=jurisdiction,
                    is_statutory=is_stat,
                )
            )

    return holidays

