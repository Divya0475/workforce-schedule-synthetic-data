"""Tests for organization generation: teams, skills, employees, and employee skills."""

import numpy as np
from workforce_generator.config import load_config
from workforce_generator.generators.organization import generate_organization


def test_starter_organization_generation():
    overrides = {
        "seed": 20261022,
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
    rng = np.random.default_rng(cfg.seed)

    teams, skills, employees, employee_skills = generate_organization(cfg, rng)

    assert len(teams) == 5
    assert len(skills) >= 5
    assert len(employees) == 50

    # Verify exactly 1 manager per team
    managers = [e for e in employees if e.role == "MANAGER"]
    assert len(managers) == 5
    for t in teams:
        team_mgrs = [e for e in employees if e.team_id == t.team_id and e.role == "MANAGER"]
        assert len(team_mgrs) == 1

    # Verify contract hours arithmetic
    for e in employees:
        assert round(e.fte * 40.0, 1) == e.weekly_contract_hours
        assert e.hire_date < cfg.horizon.start_date

    # Verify employee skills bridge
    emp_ids = {e.employee_id for e in employees}
    esk_emp_ids = {esk.employee_id for esk in employee_skills}
    assert emp_ids == esk_emp_ids

    # Each employee has 1 to 3 skills, and exactly 1 primary skill
    for emp_id in emp_ids:
        emp_sk = [esk for esk in employee_skills if esk.employee_id == emp_id]
        assert 1 <= len(emp_sk) <= 3
        primary_sk = [esk for esk in emp_sk if esk.is_primary]
        assert len(primary_sk) == 1

