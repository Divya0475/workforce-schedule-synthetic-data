"""Generates daily CoverageRequirement records across teams, dates, shifts, and skills."""

from __future__ import annotations

from datetime import date, timedelta
from typing import List, Set

from workforce_generator.config import GeneratorConfig
from workforce_generator.models.entities import CoverageRequirement, HolidayCalendar, Team
from workforce_generator.models.identifiers import (
    format_coverage_id,
    format_shift_template_id,
    format_skill_id,
)


def generate_coverage_requirements(
    config: GeneratorConfig,
    teams: List[Team],
    holidays: List[HolidayCalendar],
) -> List[CoverageRequirement]:
    statutory_holiday_dates: Set[date] = {
        h.calendar_date for h in holidays if h.is_statutory
    }

    coverage_records: List[CoverageRequirement] = []
    seq = 0
    cur_date = config.horizon.start_date
    end_date = config.horizon.end_date

    while cur_date <= end_date:
        # Determine if weekday or weekend/statutory holiday tier applies
        is_weekend = cur_date.weekday() in (5, 6) # 5=Sat, 6=Sun
        is_holiday = cur_date in statutory_holiday_dates

        if is_weekend or is_holiday:
            tier_rules = config.coverage.weekend
        else:
            tier_rules = config.coverage.weekday

        for team in teams:
            for sft in config.shifts:
                sft_code = sft.code
                rule = tier_rules.get(sft_code)
                if not rule:
                    continue

                for sk_req in rule.skill_requirements:
                    seq += 1
                    cov_id = format_coverage_id(cur_date.year, seq)
                    min_heads = sk_req.min_headcount
                    # Target headcount provides a small buffer above minimum
                    target_heads = min_heads + 1

                    cov = CoverageRequirement(
                        coverage_id=cov_id,
                        effective_date=cur_date,
                        team_id=team.team_id,
                        shift_template_id=format_shift_template_id(sft_code),
                        skill_id=format_skill_id(sk_req.skill_code),
                        min_headcount=min_heads,
                        target_headcount=target_heads,
                    )
                    coverage_records.append(cov)

        cur_date += timedelta(days=1)

    return coverage_records

