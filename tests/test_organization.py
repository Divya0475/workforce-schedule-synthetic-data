"""Tests for organization generation: teams, skills, employees, and employee skills."""

from datetime import date
import numpy as np
import pytest

from workforce_generator.config import load_config
from workforce_generator.generators.organization import generate_organization


@pytest.fixture
def starter_data():
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
    return cfg, teams, skills, employees, employee_skills


def test_starter_organization_generation(starter_data):
    cfg, teams, skills, employees, employee_skills = starter_data

    assert len(teams) == 5
    assert len(skills) >= 10
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


def test_certifications_prior_minority_and_ordering(starter_data):
    cfg, _, _, employees, employee_skills = starter_data
    emp_map = {e.employee_id: e for e in employees}

    prior_certs = 0
    in_house_certs = 0

    for esk in employee_skills:
        emp = emp_map[esk.employee_id]
        # Expiry is always strictly greater than certified_date
        assert esk.expiry_date is not None
        assert esk.expiry_date > esk.certified_date

        if esk.certified_date < emp.hire_date:
            prior_certs += 1
        else:
            in_house_certs += 1
            assert esk.certified_date >= emp.hire_date

    total_certs = len(employee_skills)
    prior_ratio = prior_certs / total_certs

    # Documented minority of certifications: between 5% and 30%
    assert 0.05 <= prior_ratio <= 0.30
    assert in_house_certs > prior_certs


def test_boundary_cases_hire_and_termination(starter_data):
    cfg, _, _, employees, _ = starter_data
    start_d = cfg.horizon.start_date
    end_d = cfg.horizon.end_date

    mid_hires = [e for e in employees if start_d <= e.hire_date <= end_d]
    mid_terms = [e for e in employees if e.termination_date and start_d <= e.termination_date <= end_d]

    # Boundary cases must be present
    assert len(mid_hires) >= 1
    assert len(mid_terms) >= 1

    # Terminated employees inside horizon must have is_active = False
    for t_emp in mid_terms:
        assert t_emp.is_active is False
        assert t_emp.termination_date > t_emp.hire_date


def test_department_skills_alignment(starter_data):
    cfg, teams, _, employees, employee_skills = starter_data
    team_map = {t.team_id: t for t in teams}
    dept_map = {d.name: d for d in cfg.departments}
    emp_map = {e.employee_id: e for e in employees}

    # Verify that each employee has skills relevant to their department
    for esk in employee_skills:
        emp = emp_map[esk.employee_id]
        team = team_map[emp.team_id]
        dept_cfg = dept_map.get(team.department)
        if dept_cfg:
            allowed_dept_skills = set(
                [f"SKL-{c}" for c in (dept_cfg.core_skills + dept_cfg.secondary_skills + dept_cfg.rare_skills)]
            )
            assert esk.skill_id in allowed_dept_skills
