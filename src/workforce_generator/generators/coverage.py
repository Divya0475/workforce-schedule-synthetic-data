"""Generates daily CoverageRequirement records across teams, dates, shifts, and skills using department mapping."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List, Set

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

    # Department lookup
    dept_map = {d.name: d for d in config.departments}

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
            dept_cfg = dept_map.get(team.department)

            for sft in config.shifts:
                sft_code = sft.code
                rule = tier_rules.get(sft_code)
                if not rule:
                    continue

                # Determine required skills for this team's department
                if dept_cfg and dept_cfg.core_skills:
                    if is_weekend or is_holiday or sft_code == "C":
                        # 1 skill requirement: primary core skill
                        skills_to_demand = [dept_cfg.core_skills[0]]
                    else:
                        # Shift A or B on weekday: 2 skill requirements (first 2 core skills)
                        skills_to_demand = dept_cfg.core_skills[:2]
                else:
                    skills_to_demand = [sk.skill_code for sk in rule.skill_requirements]

                for sk_code in skills_to_demand:
                    seq += 1
                    cov_id = format_coverage_id(cur_date.year, seq)
                    min_heads = 1
                    target_heads = 2

                    cov = CoverageRequirement(
                        coverage_id=cov_id,
                        effective_date=cur_date,
                        team_id=team.team_id,
                        shift_template_id=format_shift_template_id(sft_code),
                        skill_id=format_skill_id(sk_code),
                        min_headcount=min_heads,
                        target_headcount=target_heads,
                    )
                    coverage_records.append(cov)

        cur_date += timedelta(days=1)

    return coverage_records
