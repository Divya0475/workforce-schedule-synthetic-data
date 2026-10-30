"""Unit and integration tests for Stage B1: LeaveRequest and LeaveRequestEvent generation."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
import math
import numpy as np
import pandas as pd
import pytest

from workforce_generator.config import load_config
from workforce_generator.generators.coverage import generate_coverage_requirements
from workforce_generator.generators.holidays import generate_holidays
from workforce_generator.generators.leave import generate_leave_requests_and_events
from workforce_generator.generators.organization import generate_organization


@pytest.fixture
def starter_leave_pipeline():
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
        "leave_rules": {
            "target_total_requests": 100,
            "leave_coverage_buffer": 1.4,
        },
    }
    cfg = load_config(overrides=overrides)
    rng = np.random.default_rng(cfg.seed)

    teams, skills, employees, employee_skills = generate_organization(cfg, rng)
    holidays = generate_holidays(cfg)
    coverage_reqs = generate_coverage_requirements(cfg, teams, holidays)
    leave_requests, leave_events = generate_leave_requests_and_events(
        cfg, rng, employees, employee_skills, coverage_reqs, holidays
    )

    return {
        "config": cfg,
        "teams": teams,
        "skills": skills,
        "employees": employees,
        "employee_skills": employee_skills,
        "holidays": holidays,
        "coverage_requirements": coverage_reqs,
        "leave_requests": leave_requests,
        "leave_events": leave_events,
    }


def test_leave_counts_and_types(starter_leave_pipeline):
    """Verify configured target count and distribution of leave types."""
    reqs = starter_leave_pipeline["leave_requests"]
    assert len(reqs) == 100

    types = {r.leave_type for r in reqs}
    assert "ANNUAL" in types
    assert "SICK" in types
    assert len(types) >= 3


def test_leave_only_for_active_employees_on_requested_dates(starter_leave_pipeline):
    """Verify employees are active on all requested leave dates (hire_date <= start and term_date >= end)."""
    reqs = starter_leave_pipeline["leave_requests"]
    emps = {e.employee_id: e for e in starter_leave_pipeline["employees"]}

    for r in reqs:
        emp = emps[r.employee_id]
        assert emp.hire_date <= r.start_date, f"Employee {emp.employee_id} requested leave before hire_date!"
        if emp.termination_date:
            assert emp.termination_date >= r.end_date, (
                f"Employee {emp.employee_id} requested leave past termination_date!"
            )


def test_approval_never_breaks_daily_coverage_rule(starter_leave_pipeline):
    """Verify that approval strictly guarantees: qualified non-leave staff >= ceil(min_headcount_sum * buffer)."""
    cfg = starter_leave_pipeline["config"]
    reqs = starter_leave_pipeline["leave_requests"]
    emps = {e.employee_id: e for e in starter_leave_pipeline["employees"]}
    cov_reqs = starter_leave_pipeline["coverage_requirements"]
    emp_skills = starter_leave_pipeline["employee_skills"]

    buffer_multiplier = cfg.leave_rules.leave_coverage_buffer

    # Map daily min headcount sum: (team_id, date, skill_id) -> int
    daily_min_sum = defaultdict(int)
    for cov in cov_reqs:
        daily_min_sum[(cov.team_id, cov.effective_date, cov.skill_id)] += cov.min_headcount

    # Map employee skills
    emp_skills_map = defaultdict(set)
    for es in emp_skills:
        emp_skills_map[es.employee_id].add(es.skill_id)

    # Reconstruct approved on-leave calendar
    # A request is active on-leave if its status was APPROVED (even if later cancelled, during evaluation it was approved)
    # Check at final state: for all APPROVED requests
    approved_reqs = [r for r in reqs if r.current_status == "APPROVED"]
    approved_by_team_date = defaultdict(set)
    for r in approved_reqs:
        cur_d = r.start_date
        while cur_d <= r.end_date:
            approved_by_team_date[(emps[r.employee_id].team_id, cur_d)].add(r.employee_id)
            cur_d += timedelta(days=1)

    # For every team and every date, verify remaining qualified staff meets the threshold
    teams = starter_leave_pipeline["teams"]
    sched_emps = [e for e in starter_leave_pipeline["employees"] if e.role != "MANAGER"]

    for t in teams:
        cur_d = cfg.horizon.start_date
        while cur_d <= cfg.horizon.end_date:
            # Demanded skills on cur_d
            demanded = [sk for (tm, dt, sk), m in daily_min_sum.items() if tm == t.team_id and dt == cur_d and m > 0]
            on_leave = approved_by_team_date[(t.team_id, cur_d)]

            for sk_id in demanded:
                min_sum = daily_min_sum[(t.team_id, cur_d, sk_id)]
                threshold = math.ceil(min_sum * buffer_multiplier)

                # Count active non-leave staff holding sk_id
                qualified_non_leave = sum(
                    1 for e in sched_emps
                    if e.team_id == t.team_id
                    and e.employee_id not in on_leave
                    and e.hire_date <= cur_d
                    and (e.termination_date is None or e.termination_date >= cur_d)
                    and sk_id in emp_skills_map[e.employee_id]
                )

                assert qualified_non_leave >= threshold, (
                    f"Coverage Violation on {cur_d} for Team {t.team_id}, Skill {sk_id}: "
                    f"Qualified active non-leave staff ({qualified_non_leave}) < required threshold ({threshold})"
                )

            cur_d += timedelta(days=1)


def test_leave_events_lifecycle_and_actor_rules(starter_leave_pipeline):
    """Verify event sequencing, status transitions, and manager/system actor rules."""
    reqs = {r.leave_request_id: r for r in starter_leave_pipeline["leave_requests"]}
    events = starter_leave_pipeline["leave_events"]
    emps = {e.employee_id: e for e in starter_leave_pipeline["employees"]}

    # Group events by leave_request_id
    events_by_req = defaultdict(list)
    for ev in events:
        events_by_req[ev.leave_request_id].append(ev)

    for req_id, r in reqs.items():
        req_events = events_by_req[req_id]
        assert len(req_events) in (2, 3), f"Request {req_id} has unexpected event count: {len(req_events)}"

        # Event 1: SUBMISSION
        ev0 = req_events[0]
        assert ev0.from_status is None
        assert ev0.to_status == "SUBMITTED"
        assert ev0.actor_type == "EMPLOYEE"
        assert ev0.actor_id == r.employee_id
        assert ev0.event_timestamp == r.submitted_at

        # Event 2: DECISION
        ev1 = req_events[1]
        assert ev1.from_status == "SUBMITTED"
        assert ev1.to_status in ("APPROVED", "REJECTED")
        assert ev1.event_timestamp >= ev0.event_timestamp
        assert ev1.event_timestamp == r.decided_at

        req_emp = emps[r.employee_id]
        if ev1.actor_type == "EMPLOYEE":
            # Must be a manager of the requester's team
            actor = emps[ev1.actor_id]
            assert actor.role == "MANAGER"
            assert actor.team_id == req_emp.team_id
        else:
            assert ev1.actor_type == "SYSTEM"
            assert ev1.actor_id is None

        # Event 3 (if cancelled): CANCELLATION
        if len(req_events) == 3:
            ev2 = req_events[2]
            assert ev2.from_status == "APPROVED"
            assert ev2.to_status == "CANCELLED"
            assert ev2.event_timestamp >= ev1.event_timestamp


def test_current_status_mirrors_latest_event_to_status(starter_leave_pipeline):
    """Verify invariant: LeaveRequest.current_status == latest LeaveRequestEvent.to_status."""
    reqs = starter_leave_pipeline["leave_requests"]
    events = starter_leave_pipeline["leave_events"]

    events_by_req = defaultdict(list)
    for ev in events:
        events_by_req[ev.leave_request_id].append(ev)

    for r in reqs:
        req_events = events_by_req[r.leave_request_id]
        latest_event = req_events[-1]
        assert r.current_status == latest_event.to_status, (
            f"Status mismatch for {r.leave_request_id}: "
            f"Request status is '{r.current_status}', but latest event to_status is '{latest_event.to_status}'"
        )


def test_statutory_holidays_exemption_for_annual_leave(starter_leave_pipeline):
    """Verify that statutory holidays do not deduct annual leave hours."""
    reqs = starter_leave_pipeline["leave_requests"]
    holidays = starter_leave_pipeline["holidays"]
    emps = {e.employee_id: e for e in starter_leave_pipeline["employees"]}

    statutory_dates = {h.calendar_date for h in holidays if h.is_statutory}

    # Find an annual leave that spans a statutory holiday (e.g. 2026-01-01)
    annual_leaves_spanning_holiday = [
        r for r in reqs
        if r.leave_type == "ANNUAL" and any(r.start_date <= h_d <= r.end_date for h_d in statutory_dates)
    ]

    for r in annual_leaves_spanning_holiday:
        emp = emps[r.employee_id]
        daily_hours = round(emp.weekly_contract_hours / 5.0, 1)

        # Count weekdays in range
        cur = r.start_date
        expected_hours = 0.0
        while cur <= r.end_date:
            if cur.weekday() not in (5, 6) and cur not in statutory_dates:
                expected_hours += daily_hours
            cur += timedelta(days=1)

        if expected_hours == 0.0:
            expected_hours = daily_hours

        assert round(r.requested_hours, 1) == round(expected_hours, 1), (
            f"Annual leave {r.leave_request_id} did not properly exempt statutory holiday!"
        )


def test_leave_generation_determinism():
    """Verify identical seed produces bitwise identical LeaveRequest and LeaveRequestEvent DataFrames."""
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
        "leave_rules": {
            "target_total_requests": 100,
            "leave_coverage_buffer": 1.4,
        },
    }

    def run():
        cfg = load_config(overrides=overrides)
        rng = np.random.default_rng(cfg.seed)
        t, sk, e, esk = generate_organization(cfg, rng)
        h = generate_holidays(cfg)
        cov = generate_coverage_requirements(cfg, t, h)
        reqs, evts = generate_leave_requests_and_events(cfg, rng, e, esk, cov, h)
        return pd.DataFrame([r.to_dict() for r in reqs]), pd.DataFrame([ev.to_dict() for ev in evts])

    reqs1, evts1 = run()
    reqs2, evts2 = run()

    pd.testing.assert_frame_equal(reqs1, reqs2, check_exact=True)
    pd.testing.assert_frame_equal(evts1, evts2, check_exact=True)
