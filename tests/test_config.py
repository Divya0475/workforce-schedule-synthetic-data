"""Tests for configuration loading and overrides."""

from datetime import date
from workforce_generator.config import load_config


def test_load_default_config():
    cfg = load_config()
    assert cfg.seed == 20261022
    assert cfg.horizon.start_date == date(2026, 1, 1)
    assert cfg.horizon.end_date == date(2026, 3, 31)
    assert cfg.horizon.total_days == 90
    assert cfg.workforce.num_teams == 100
    assert cfg.workforce.employees_per_team == 20
    assert cfg.workforce.total_employees == 2000
    assert cfg.leave_rules.target_total_requests == 5000
    assert len(cfg.shifts) == 3
    assert "MANAGER" in [r.code for r in cfg.workforce.roles]


def test_load_config_with_overrides():
    overrides = {
        "seed": 9999,
        "workforce": {
            "num_teams": 5,
            "employees_per_team": 10,
        },
        "horizon": {
            "start_date": "2026-01-01",
            "end_date": "2026-01-14",
        },
    }
    cfg = load_config(overrides=overrides)
    assert cfg.seed == 9999
    assert cfg.workforce.num_teams == 5
    assert cfg.workforce.employees_per_team == 10
    assert cfg.workforce.total_employees == 50
    assert cfg.horizon.total_days == 14

