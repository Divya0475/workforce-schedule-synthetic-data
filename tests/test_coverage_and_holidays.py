"""Tests for holiday calendar and coverage requirement generators."""

from datetime import date
import numpy as np

from workforce_generator.config import load_config
from workforce_generator.generators.coverage import generate_coverage_requirements
from workforce_generator.generators.holidays import generate_holidays
from workforce_generator.generators.organization import generate_organization


def test_holidays_in_q1():
    cfg = load_config()
    holidays = generate_holidays(cfg)
    assert len(holidays) == 3
    dates = {h.calendar_date for h in holidays}
    assert date(2026, 1, 1) in dates
    assert date(2026, 1, 19) in dates
    assert date(2026, 2, 16) in dates


def test_starter_coverage_requirements():
    overrides = {
        "seed": 20261022,
        "workforce": {
            "num_teams": 5,
            "employees_per_team": 10,
        },
        "horizon": {
            "start_date": "2026-01-01",
            "end_date": "2026-01-14", # 14 days
        },
    }
    cfg = load_config(overrides=overrides)
    rng = np.random.default_rng(cfg.seed)
    teams, skills, employees, _ = generate_organization(cfg, rng)
    holidays = generate_holidays(cfg)

    cov_reqs = generate_coverage_requirements(cfg, teams, holidays)

    # In 14 days:
    # 2026-01-01 is Thursday (New Year's Day, statutory holiday -> weekend tier: A:1 skill, B:1 skill, C:1 skill = 3 rows/team)
    # 4 weekend days (Jan 3, 4, 10, 11) -> weekend tier: 3 rows/team
    # 9 weekday days (Jan 2, 5, 6, 7, 8, 9, 12, 13, 14) -> weekday tier: A:2 skills, B:2 skills, C:1 skill = 5 rows/team
    # Total rows per team = 1*3 + 4*3 + 9*5 = 3 + 12 + 45 = 60 rows
    # Total rows for 5 teams = 5 * 60 = 300 rows
    assert len(cov_reqs) == 300
    for req in cov_reqs:
        assert req.min_headcount >= 1
        assert req.target_headcount >= req.min_headcount
        assert req.effective_date >= cfg.horizon.start_date
        assert req.effective_date <= cfg.horizon.end_date

