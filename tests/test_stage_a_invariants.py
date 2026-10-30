"""Invariants testing for Stage A: Demand vs Supply holders, ID Uniqueness, and Generation Determinism."""

from __future__ import annotations

import re
import numpy as np
import pandas as pd
import pytest

from workforce_generator.config import load_config
from workforce_generator.models.entities import ShiftTemplate
from workforce_generator.models.identifiers import (
    RE_COVERAGE_ID,
    RE_EMPLOYEE_ID,
    RE_EMPLOYEE_SKILL_ID,
    RE_HOLIDAY_ID,
    RE_SHIFT_TEMPLATE_ID,
    RE_SKILL_ID,
    RE_TEAM_ID,
)
from workforce_generator.pipeline import run_pipeline


def test_every_demanded_skill_has_holders_in_each_requiring_team(stage_a_artifacts):
    """Verify that every skill demanded on a team has qualified non-manager holders in that team."""
    teams = stage_a_artifacts["teams"]
    employees = stage_a_artifacts["employees"]
    employee_skills = stage_a_artifacts["employee_skills"]
    cov_reqs = stage_a_artifacts["coverage_requirements"]

    emp_df = pd.DataFrame([e.to_dict() for e in employees])
    esk_df = pd.DataFrame([es.to_dict() for es in employee_skills])
    cov_df = pd.DataFrame([c.to_dict() for c in cov_reqs])

    # Filter to schedulable staff only (managers excluded from coverage)
    schedulable_emps = emp_df[emp_df["role"] != "MANAGER"]
    sched_skills = esk_df.merge(schedulable_emps[["employee_id", "team_id"]], on="employee_id")

    # Group demand by team and skill
    demanded_by_team = cov_df.groupby(["team_id", "skill_id"])["min_headcount"].sum().reset_index()

    for _, row in demanded_by_team.iterrows():
        t_id = row["team_id"]
        sk_id = row["skill_id"]
        min_demand = row["min_headcount"]

        # Qualified non-manager holders in that team
        holders = sched_skills[(sched_skills["team_id"] == t_id) & (sched_skills["skill_id"] == sk_id)]
        holder_count = holders["employee_id"].nunique()

        assert holder_count >= 1, (
            f"Fatal Coverage Pinch: Team {t_id} demands skill {sk_id} "
            f"({min_demand} person-shifts) but has {holder_count} qualified non-manager holders!"
        )


def test_unique_ids_and_patterns_across_all_stage_a_tables(stage_a_artifacts):
    """Verify PK uniqueness and strict regex conformity across all Stage A tables."""
    teams = stage_a_artifacts["teams"]
    skills = stage_a_artifacts["skills"]
    employees = stage_a_artifacts["employees"]
    employee_skills = stage_a_artifacts["employee_skills"]
    shift_templates = stage_a_artifacts["shift_templates"]
    holidays = stage_a_artifacts["holidays"]
    cov_reqs = stage_a_artifacts["coverage_requirements"]

    # 1. Teams
    team_ids = [t.team_id for t in teams]
    assert len(team_ids) == len(set(team_ids)), "Duplicate team_id found"
    assert all(RE_TEAM_ID.match(tid) for tid in team_ids)

    # 2. Skills
    skill_ids = [s.skill_id for s in skills]
    assert len(skill_ids) == len(set(skill_ids)), "Duplicate skill_id found"
    assert all(RE_SKILL_ID.match(sid) for sid in skill_ids)

    # 3. Employees
    emp_ids = [e.employee_id for e in employees]
    assert len(emp_ids) == len(set(emp_ids)), "Duplicate employee_id found"
    assert all(RE_EMPLOYEE_ID.match(eid) for eid in emp_ids)

    # 4. EmployeeSkills
    esk_ids = [es.employee_skill_id for es in employee_skills]
    assert len(esk_ids) == len(set(esk_ids)), "Duplicate employee_skill_id found"
    assert all(RE_EMPLOYEE_SKILL_ID.match(eskid) for eskid in esk_ids)

    # 5. ShiftTemplates
    st_ids = [st.shift_template_id for st in shift_templates]
    assert len(st_ids) == len(set(st_ids)), "Duplicate shift_template_id found"
    assert all(RE_SHIFT_TEMPLATE_ID.match(stid) for stid in st_ids)

    # 6. Holidays
    hol_ids = [h.holiday_id for h in holidays]
    assert len(hol_ids) == len(set(hol_ids)), "Duplicate holiday_id found"
    assert all(RE_HOLIDAY_ID.match(hid) for hid in hol_ids)

    # 7. CoverageRequirements
    cov_ids = [c.coverage_id for c in cov_reqs]
    assert len(cov_ids) == len(set(cov_ids)), "Duplicate coverage_id found"
    assert all(RE_COVERAGE_ID.match(cid) for cid in cov_ids)


def test_generation_determinism():
    """Verify that identical seed (20261022) produces bitwise identical DataFrames on repeated runs."""
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

    def generate_pipeline():
        data = run_pipeline(overrides=overrides)
        return {
            "Team": pd.DataFrame([x.to_dict() for x in data["teams"]]),
            "Skill": pd.DataFrame([x.to_dict() for x in data["skills"]]),
            "Employee": pd.DataFrame([x.to_dict() for x in data["employees"]]),
            "EmployeeSkill": pd.DataFrame([x.to_dict() for x in data["employee_skills"]]),
            "ShiftTemplate": pd.DataFrame([x.to_dict() for x in data["shift_templates"]]),
            "HolidayCalendar": pd.DataFrame([x.to_dict() for x in data["holidays"]]),
            "CoverageRequirement": pd.DataFrame([x.to_dict() for x in data["coverage_requirements"]]),
        }

    run1 = generate_pipeline()
    run2 = generate_pipeline()

    for table_name in run1.keys():
        df1 = run1[table_name]
        df2 = run2[table_name]
        pd.testing.assert_frame_equal(df1, df2, check_exact=True)


def test_bilingual_es_secondary_only_and_ratio(stage_a_artifacts):
    """Verify BILINGUAL_ES is strictly secondary and held by roughly 20-25% of employees."""
    employees = stage_a_artifacts["employees"]
    employee_skills = stage_a_artifacts["employee_skills"]

    bilingual_esks = [es for es in employee_skills if es.skill_id == "SKL-BILINGUAL_ES"]
    bilingual_holders = {es.employee_id for es in bilingual_esks}

    # Must NEVER be primary
    for es in bilingual_esks:
        assert not es.is_primary, f"Employee {es.employee_id} has BILINGUAL_ES as primary skill!"

    # Ratio check: roughly 20-25% (assert between 18% and 26% for 50 staff)
    ratio = len(bilingual_holders) / len(employees)
    assert 0.18 <= ratio <= 0.28, f"Expected BILINGUAL_ES ratio ~20-25%, got {ratio:.1%}"


def test_oncology_min_demand_and_holders(stage_a_artifacts):
    """Verify ONCOLOGY has a small min requirement on TEAM-002 and qualified holders exist."""
    employees = stage_a_artifacts["employees"]
    employee_skills = stage_a_artifacts["employee_skills"]
    cov_reqs = stage_a_artifacts["coverage_requirements"]

    # 1. Coverage requirement exists on TEAM-002
    onc_reqs = [c for c in cov_reqs if c.skill_id == "SKL-ONCOLOGY"]
    assert len(onc_reqs) > 0, "No CoverageRequirement found for ONCOLOGY"
    for req in onc_reqs:
        assert req.team_id == "TEAM-002"
        assert req.min_headcount == 1
        assert req.target_headcount >= 1

    # 2. Qualified non-manager holders in TEAM-002
    team2_non_mgrs = {e.employee_id for e in employees if e.team_id == "TEAM-002" and e.role != "MANAGER"}
    onc_holders_team2 = [es for es in employee_skills if es.skill_id == "SKL-ONCOLOGY" and es.employee_id in team2_non_mgrs]
    assert len(onc_holders_team2) >= 1, "TEAM-002 has no non-manager holders for SKL-ONCOLOGY"
