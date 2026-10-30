"""Unit and integration tests for Stage B1: LeaveRequest and LeaveRequestEvent generation."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
import math
import subprocess
import sys
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
            "leave_coverage_buffer": 1.25,
        },
    }
    cfg = load_config(overrides=overrides)
    ss = np.random.SeedSequence(cfg.seed)
    rng_org, rng_leave = [np.random.default_rng(s) for s in ss.spawn(2)]

    teams, skills, employees, employee_skills = generate_organization(cfg, rng_org)
    holidays = generate_holidays(cfg)
    coverage_reqs = generate_coverage_requirements(cfg, teams, holidays)
    leave_requests, leave_events = generate_leave_requests_and_events(
        cfg, rng_leave, employees, employee_skills, coverage_reqs, holidays
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


def test_submitted_at_clamped_to_hire_date(starter_leave_pipeline):
    """Verify that submitted_at is strictly >= employee hire_date."""
    reqs = starter_leave_pipeline["leave_requests"]
    emps = {e.employee_id: e for e in starter_leave_pipeline["employees"]}

    for r in reqs:
        emp = emps[r.employee_id]
        sub_d = datetime.fromisoformat(r.submitted_at.rstrip("Z")).date()
        assert sub_d >= emp.hire_date, (
            f"Request {r.leave_request_id}: submitted_at ({sub_d}) is prior to hire_date ({emp.hire_date})"
        )


def test_no_overlapping_approved_leave_for_same_employee(starter_leave_pipeline):
    """Verify that no employee has overlapping approved leave requests."""
    reqs = starter_leave_pipeline["leave_requests"]
    # Check all leaves that ended in APPROVED status
    approved_reqs = [r for r in reqs if r.current_status == "APPROVED"]
    emp_approved_ranges = defaultdict(list)

    for r in approved_reqs:
        for ex_start, ex_end in emp_approved_ranges[r.employee_id]:
            overlap = not (r.end_date < ex_start or r.start_date > ex_end)
            assert not overlap, (
                f"Employee {r.employee_id} has overlapping approved leave: "
                f"[{r.start_date}, {r.end_date}] overlaps with [{ex_start}, {ex_end}]"
            )
        emp_approved_ranges[r.employee_id].append((r.start_date, r.end_date))


def test_rejection_reasons_and_approval_rate(starter_leave_pipeline):
    """Verify all rejected requests have non-empty reason, and approval rate is within 60-95%."""
    reqs = starter_leave_pipeline["leave_requests"]

    rejected = [r for r in reqs if r.current_status == "REJECTED"]
    for r in rejected:
        assert r.decision_reason and len(r.decision_reason.strip()) > 0

    # Decided requests: evaluated requests that received an approval or rejection decision
    decided_requests = [r for r in reqs if r.decided_at is not None]
    approved_count = sum(1 for r in decided_requests if r.current_status in ("APPROVED", "CANCELLED"))
    approval_rate = (approved_count / len(decided_requests)) * 100.0

    assert 60.0 <= approval_rate <= 95.0, f"Expected approval rate 60-95%, got {approval_rate:.1f}%"


def test_cancelled_before_decision_fields(starter_leave_pipeline):
    """Verify cancelled-before-decision requests have decided_at=None and decision_reason=None."""
    reqs = starter_leave_pipeline["leave_requests"]
    events = starter_leave_pipeline["leave_events"]

    evts_by_req = defaultdict(list)
    for ev in events:
        evts_by_req[ev.leave_request_id].append(ev)

    pre_decision_cancelled = [
        r for r in reqs
        if r.current_status == "CANCELLED" and len(evts_by_req[r.leave_request_id]) == 2 and evts_by_req[r.leave_request_id][1].from_status == "SUBMITTED"
    ]
    assert len(pre_decision_cancelled) >= 1
    for r in pre_decision_cancelled:
        assert r.decided_at is None
        assert r.decision_reason is None


def test_submission_and_cancellation_temporal_bounds(starter_leave_pipeline):
    """Verify submitted_at < start_date (except SICK) and cancellations happen before end_date."""
    reqs = starter_leave_pipeline["leave_requests"]
    events = starter_leave_pipeline["leave_events"]

    req_map = {r.leave_request_id: r for r in reqs}

    for r in reqs:
        sub_dt = datetime.fromisoformat(r.submitted_at.rstrip("Z"))
        start_midnight = datetime.combine(r.start_date, datetime.min.time())

        if r.leave_type != "SICK":
            assert sub_dt < start_midnight, (
                f"Planned leave {r.leave_request_id} ({r.leave_type}) submitted_at ({sub_dt}) >= start_date ({start_midnight})"
            )
        else:
            # SICK leave can be submitted same day
            assert sub_dt <= datetime.combine(r.start_date, datetime.max.time())

    for ev in events:
        if ev.to_status == "CANCELLED":
            r = req_map[ev.leave_request_id]
            cancel_dt = datetime.fromisoformat(ev.event_timestamp.rstrip("Z"))
            end_boundary = datetime.combine(r.end_date, datetime.max.time())
            assert cancel_dt < end_boundary, (
                f"Cancellation {ev.leave_event_id} ({cancel_dt}) is not before end_date ({end_boundary})"
            )


def test_counterfactual_rejections_violate_threshold(starter_leave_pipeline):
    """Counterfactual test: every REJECTED request with a coverage reason would have violated the threshold."""
    cfg = starter_leave_pipeline["config"]
    reqs = starter_leave_pipeline["leave_requests"]
    cov_reqs = starter_leave_pipeline["coverage_requirements"]
    emp_skills = starter_leave_pipeline["employee_skills"]
    sched_emps = [e for e in starter_leave_pipeline["employees"] if e.role != "MANAGER"]

    buffer_multiplier = cfg.leave_rules.leave_coverage_buffer

    demand_by_team_date = defaultdict(lambda: defaultdict(int))
    for cov in cov_reqs:
        demand_by_team_date[(cov.team_id, cov.effective_date)][cov.skill_id] += cov.min_headcount

    emp_skills_map = defaultdict(set)
    for es in emp_skills:
        emp_skills_map[es.employee_id].add(es.skill_id)

    team_skill_holders = defaultdict(list)
    for staff in sched_emps:
        for sk_id in emp_skills_map[staff.employee_id]:
            team_skill_holders[(staff.team_id, sk_id)].append(staff)

    # Replay sequential decisions
    approved_on_leave = defaultdict(set)

    for r in reqs:
        if r.current_status == "REJECTED" and r.decision_reason and "Insufficient coverage" in r.decision_reason:
            # Counterfactual check: must fail on at least one date and skill
            emp_holds = emp_skills_map[r.employee_id]
            emp_team = [e.team_id for e in sched_emps if e.employee_id == r.employee_id][0]

            violated = False
            cur_d = r.start_date
            while cur_d <= r.end_date:
                demands = demand_by_team_date.get((emp_team, cur_d), {})
                current_leave = approved_on_leave[(emp_team, cur_d)]

                for sk_id, min_cnt in demands.items():
                    if min_cnt <= 0 or sk_id not in emp_holds:
                        continue
                    thresh = math.ceil(min_cnt * buffer_multiplier)
                    holders = team_skill_holders.get((emp_team, sk_id), [])

                    active_avail = sum(
                        1 for s in holders
                        if s.employee_id not in current_leave
                        and s.hire_date <= cur_d
                        and (s.termination_date is None or s.termination_date >= cur_d)
                    )
                    rem = active_avail - 1
                    if rem < thresh:
                        violated = True
                        break

                if violated:
                    break
                cur_d += timedelta(days=1)

            assert violated, f"Rejected request {r.leave_request_id} did not actually violate threshold counterfactually!"

        elif r.decided_at is not None and r.decision_reason and r.decision_reason.startswith("Approved"):
            # Update approved on leave calendar for replay
            emp_team = [e.team_id for e in starter_leave_pipeline["employees"] if e.employee_id == r.employee_id][0]
            cur_d = r.start_date
            while cur_d <= r.end_date:
                approved_on_leave[(emp_team, cur_d)].add(r.employee_id)
                cur_d += timedelta(days=1)


def test_coverage_invariant_planned_non_manager_leave_only(starter_leave_pipeline):
    """Verify coverage invariant for planned non-manager leave, robust to baseline shortfalls."""
    cfg = starter_leave_pipeline["config"]
    reqs = starter_leave_pipeline["leave_requests"]
    emps = {e.employee_id: e for e in starter_leave_pipeline["employees"]}
    cov_reqs = starter_leave_pipeline["coverage_requirements"]
    emp_skills = starter_leave_pipeline["employee_skills"]

    buffer_multiplier = cfg.leave_rules.leave_coverage_buffer

    demand_by_team_date = defaultdict(lambda: defaultdict(int))
    for cov in cov_reqs:
        demand_by_team_date[(cov.team_id, cov.effective_date)][cov.skill_id] += cov.min_headcount

    emp_skills_map = defaultdict(set)
    for es in emp_skills:
        emp_skills_map[es.employee_id].add(es.skill_id)

    # Reconstruct approved on-leave calendar for planned non-manager leaves
    planned_non_mgr_approved = [
        r for r in reqs
        if r.current_status == "APPROVED" and r.leave_type != "SICK" and emps[r.employee_id].role != "MANAGER"
    ]
    approved_by_team_date = defaultdict(set)
    for r in planned_non_mgr_approved:
        cur_d = r.start_date
        while cur_d <= r.end_date:
            approved_by_team_date[(emps[r.employee_id].team_id, cur_d)].add(r.employee_id)
            cur_d += timedelta(days=1)

    teams = starter_leave_pipeline["teams"]
    sched_emps = [e for e in starter_leave_pipeline["employees"] if e.role != "MANAGER"]

    for t in teams:
        cur_d = cfg.horizon.start_date
        while cur_d <= cfg.horizon.end_date:
            demands = demand_by_team_date.get((t.team_id, cur_d), {})
            on_leave = approved_by_team_date[(t.team_id, cur_d)]

            for sk_id, min_sum in demands.items():
                if min_sum <= 0:
                    continue
                threshold = math.ceil(min_sum * buffer_multiplier)

                # Total baseline holders active on cur_d
                baseline_holders = [
                    e for e in sched_emps
                    if e.team_id == t.team_id
                    and e.hire_date <= cur_d
                    and (e.termination_date is None or e.termination_date >= cur_d)
                    and sk_id in emp_skills_map[e.employee_id]
                ]
                baseline_count = len(baseline_holders)

                # Active non-leave holders
                qualified_non_leave = sum(1 for e in baseline_holders if e.employee_id not in on_leave)

                if baseline_count >= threshold:
                    # If baseline met threshold, approved planned leave must preserve it
                    assert qualified_non_leave >= threshold, (
                        f"Coverage Invariant Broken on {cur_d} Team {t.team_id} Skill {sk_id}: "
                        f"qualified_non_leave ({qualified_non_leave}) < threshold ({threshold})"
                    )
                else:
                    # Baseline shortfall: zero planned leaves for holders of this skill can be approved
                    holders_on_leave = [e.employee_id for e in baseline_holders if e.employee_id in on_leave]
                    assert len(holders_on_leave) == 0, (
                        f"Planned leave approved despite baseline shortfall on {cur_d} Team {t.team_id} Skill {sk_id}: "
                        f"Holders on leave: {holders_on_leave}"
                    )

            cur_d += timedelta(days=1)


def test_statutory_holidays_exemption_non_vacuous_ground_truth(starter_leave_pipeline):
    """Non-vacuous holiday test: assert at least one annual leave spans Jan 1 and verify hand-computed hours."""
    reqs = starter_leave_pipeline["leave_requests"]
    emps = {e.employee_id: e for e in starter_leave_pipeline["employees"]}

    # Non-vacuous check: at least one annual leave spans 2026-01-01
    spanning = [
        r for r in reqs
        if r.leave_type == "ANNUAL" and r.start_date <= date(2026, 1, 1) <= r.end_date
    ]
    assert len(spanning) >= 1, "Expected at least one annual leave spanning statutory holiday 2026-01-01!"

    # Ground truth verification for request #1 spanning 2026-01-01 to 2026-01-02
    req1 = spanning[0]
    emp = emps[req1.employee_id]
    daily_hours = round(emp.weekly_contract_hours / 5.0, 1)

    # 2026-01-01 is statutory holiday (0h); 2026-01-02 is Friday (daily_hours)
    # Hand-computed ground truth:
    cur = req1.start_date
    expected = 0.0
    while cur <= req1.end_date:
        if cur.weekday() < 5 and cur != date(2026, 1, 1):
            expected += daily_hours
        cur += timedelta(days=1)
    if expected == 0.0:
        expected = daily_hours

    assert round(req1.requested_hours, 1) == round(expected, 1)


def test_decisions_actor_governance(starter_leave_pipeline):
    """Verify actor_id != requester, and manager requests are decided by SYSTEM or another team manager."""
    reqs = {r.leave_request_id: r for r in starter_leave_pipeline["leave_requests"]}
    events = starter_leave_pipeline["leave_events"]
    emps = {e.employee_id: e for e in starter_leave_pipeline["employees"]}

    for ev in events:
        if ev.from_status == "SUBMITTED" and ev.to_status in ("APPROVED", "REJECTED"):
            r = req_map = reqs[ev.leave_request_id]
            req_emp = emps[r.employee_id]

            # In all cases, actor_id != requester
            if ev.actor_id is not None:
                assert ev.actor_id != r.employee_id, f"Self-decision detected on request {r.leave_request_id}!"

            if req_emp.role == "MANAGER":
                # Manager requests must be decided by SYSTEM or a manager of another team
                if ev.actor_type == "EMPLOYEE":
                    actor = emps[ev.actor_id]
                    assert actor.role == "MANAGER"
                    assert actor.team_id != req_emp.team_id, (
                        f"Manager {r.employee_id} had leave decided by same team manager {actor.employee_id}!"
                    )
                else:
                    assert ev.actor_type == "SYSTEM"
                    assert ev.actor_id is None
            else:
                # Schedulable non-manager staff
                if ev.actor_type == "EMPLOYEE":
                    actor = emps[ev.actor_id]
                    assert actor.role == "MANAGER"
                    assert actor.team_id == req_emp.team_id
                else:
                    assert ev.actor_type == "SYSTEM"
                    assert ev.actor_id is None


def test_timezone_independence():
    """Verify leave generator produces bitwise identical output regardless of environment TZ."""
    import os

    script = (
        "import sys, json; "
        "sys.path.insert(0, 'src'); "
        "import numpy as np, pandas as pd; "
        "from workforce_generator.config import load_config; "
        "from workforce_generator.generators.organization import generate_organization; "
        "from workforce_generator.generators.holidays import generate_holidays; "
        "from workforce_generator.generators.coverage import generate_coverage_requirements; "
        "from workforce_generator.generators.leave import generate_leave_requests_and_events; "
        "cfg = load_config(overrides={'seed': 20261022, 'workforce': {'num_teams': 5, 'employees_per_team': 10}, 'horizon': {'start_date': '2026-01-01', 'end_date': '2026-01-14'}, 'leave_rules': {'target_total_requests': 100, 'leave_coverage_buffer': 1.25}}); "
        "ss = np.random.SeedSequence(cfg.seed); "
        "rng_org, rng_leave = [np.random.default_rng(s) for s in ss.spawn(2)]; "
        "t, sk, e, esk = generate_organization(cfg, rng_org); "
        "h = generate_holidays(cfg); "
        "cov = generate_coverage_requirements(cfg, t, h); "
        "reqs, evts = generate_leave_requests_and_events(cfg, rng_leave, e, esk, cov, h); "
        "print(json.dumps([r.to_dict() for r in reqs]))"
    )

    env_kolkata = os.environ.copy()
    env_kolkata["TZ"] = "Asia/Kolkata"

    env_utc = os.environ.copy()
    env_utc["TZ"] = "UTC"

    out_kolkata = subprocess.check_output(
        [sys.executable, "-c", script],
        env=env_kolkata,
        text=True,
    )
    out_utc = subprocess.check_output(
        [sys.executable, "-c", script],
        env=env_utc,
        text=True,
    )
    assert out_kolkata == out_utc


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
            "leave_coverage_buffer": 1.25,
        },
    }

    def run():
        cfg = load_config(overrides=overrides)
        ss = np.random.SeedSequence(cfg.seed)
        rng_org, rng_leave = [np.random.default_rng(s) for s in ss.spawn(2)]
        t, sk, e, esk = generate_organization(cfg, rng_org)
        h = generate_holidays(cfg)
        cov = generate_coverage_requirements(cfg, t, h)
        reqs, evts = generate_leave_requests_and_events(cfg, rng_leave, e, esk, cov, h)
        return pd.DataFrame([r.to_dict() for r in reqs]), pd.DataFrame([ev.to_dict() for ev in evts])

    reqs1, evts1 = run()
    reqs2, evts2 = run()

    pd.testing.assert_frame_equal(reqs1, reqs2, check_exact=True)
    pd.testing.assert_frame_equal(evts1, evts2, check_exact=True)
