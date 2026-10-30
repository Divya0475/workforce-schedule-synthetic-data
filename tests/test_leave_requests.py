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
from workforce_generator.pipeline import run_pipeline


def test_leave_counts_and_types(starter_leave_pipeline):
    """Verify configured target count and distribution of leave types, including still-SUBMITTED requests."""
    reqs = starter_leave_pipeline["leave_requests"]
    assert len(reqs) == 100

    # Assert at least one still-SUBMITTED request exists
    submitted_reqs = [r for r in reqs if r.current_status == "SUBMITTED"]
    assert len(submitted_reqs) >= 1, "Expected at least one still-SUBMITTED request in starter profile!"


def test_lifecycle_events_transitions_and_actor_rules(starter_leave_pipeline):
    """Restore lifecycle tests: event counts (1, 2, or 3), valid transitions, non-decreasing timestamps, current_status mirrors latest event, and actor governance."""
    reqs = starter_leave_pipeline["leave_requests"]
    events = starter_leave_pipeline["leave_events"]
    emps = {e.employee_id: e for e in starter_leave_pipeline["employees"]}
    req_map = {r.leave_request_id: r for r in reqs}

    evts_by_req = defaultdict(list)
    for ev in events:
        evts_by_req[ev.leave_request_id].append(ev)

    assert len(reqs) == 100
    assert len(events) > 100

    VALID_TRANSITIONS = {
        (None, "SUBMITTED"),
        ("SUBMITTED", "APPROVED"),
        ("SUBMITTED", "REJECTED"),
        ("SUBMITTED", "CANCELLED"),
        ("APPROVED", "CANCELLED"),
    }

    found_1_event = False
    found_2_events = False
    found_3_events = False

    for r in reqs:
        r_events = evts_by_req[r.leave_request_id]
        n_events = len(r_events)

        # 1 (pending), 2 (decided or cancelled before decision) or 3 (post-approval cancel)
        assert n_events in (1, 2, 3), (
            f"Request {r.leave_request_id} has invalid event count {n_events}!"
        )

        if n_events == 1:
            found_1_event = True
            assert r.current_status == "SUBMITTED"
            assert r_events[0].from_status is None
            assert r_events[0].to_status == "SUBMITTED"
        elif n_events == 2:
            found_2_events = True
            assert r.current_status in ("APPROVED", "REJECTED", "CANCELLED")
            if r.current_status == "CANCELLED":
                # Cancelled before decision
                assert r_events[1].from_status == "SUBMITTED"
                assert r_events[1].to_status == "CANCELLED"
            else:
                assert r_events[1].from_status == "SUBMITTED"
                assert r_events[1].to_status in ("APPROVED", "REJECTED")
        elif n_events == 3:
            found_3_events = True
            assert r.current_status == "CANCELLED"
            assert r_events[1].from_status == "SUBMITTED"
            assert r_events[1].to_status == "APPROVED"
            assert r_events[2].from_status == "APPROVED"
            assert r_events[2].to_status == "CANCELLED"

        # current_status == latest event to_status
        latest_event = r_events[-1]
        assert r.current_status == latest_event.to_status, (
            f"Request {r.leave_request_id} current_status {r.current_status} != latest event {latest_event.to_status}"
        )

        # Timestamps non-decreasing and valid transitions
        prev_dt = None
        for ev in r_events:
            transition = (ev.from_status, ev.to_status)
            assert transition in VALID_TRANSITIONS, (
                f"Invalid transition {transition} on request {r.leave_request_id}"
            )

            dt = datetime.fromisoformat(ev.event_timestamp.rstrip("Z"))
            if prev_dt is not None:
                assert dt >= prev_dt, (
                    f"Timestamp regression on {r.leave_request_id}: {prev_dt} -> {dt}"
                )
            prev_dt = dt

            # Actor rules for every event type:
            # - Creation (SUBMITTED): actor must be the requester employee
            # - Pre-decision or post-approval CANCELLED: actor must be the requester employee
            # - Decision (APPROVED / REJECTED):
            #     * Requester cannot decide their own leave (actor_id != requester)
            #     * If requester is MANAGER: decided by SYSTEM (actor_id is None) or another team's MANAGER
            #     * If requester is non-manager: decided by SYSTEM (actor_id is None) or their team's MANAGER
            req_emp = emps[r.employee_id]

            if ev.to_status == "SUBMITTED":
                assert ev.actor_type == "EMPLOYEE"
                assert ev.actor_id == r.employee_id
            elif ev.to_status == "CANCELLED":
                assert ev.actor_type == "EMPLOYEE"
                assert ev.actor_id == r.employee_id
            elif ev.to_status in ("APPROVED", "REJECTED"):
                assert ev.actor_id != r.employee_id
                if ev.actor_type == "SYSTEM":
                    assert ev.actor_id is None
                elif ev.actor_type == "EMPLOYEE":
                    actor_emp = emps[ev.actor_id]
                    assert actor_emp.role == "MANAGER"
                    if req_emp.role == "MANAGER":
                        assert actor_emp.team_id != req_emp.team_id
                    else:
                        assert actor_emp.team_id == req_emp.team_id

    # Assert all event patterns were observed in the starter profile
    assert found_1_event, "Expected at least one pending request with 1 event!"
    assert found_2_events, "Expected requests with 2 events!"
    assert found_3_events, "Expected post-approval cancelled requests with 3 events!"

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


def test_counterfactual_rejections_violate_threshold_and_replay_overlaps(starter_leave_pipeline):
    """Counterfactual test: assert at least 3 coverage rejections exist and replay both coverage and overlap rejections."""
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

    coverage_rejections = [
        r for r in reqs
        if r.current_status == "REJECTED" and r.decision_reason and "Insufficient coverage" in r.decision_reason
    ]
    overlap_rejections = [
        r for r in reqs
        if r.current_status == "REJECTED" and r.decision_reason and "Overlapping approved leave" in r.decision_reason
    ]

    # Assert at least 3 coverage rejections exist
    assert len(coverage_rejections) >= 3, f"Expected >= 3 coverage rejections, got {len(coverage_rejections)}"
    assert len(overlap_rejections) >= 1, f"Expected >= 1 overlap rejection, got {len(overlap_rejections)}"

    # Replay sequential decisions
    approved_on_leave = defaultdict(set)
    emp_approved_intervals = defaultdict(list)

    for r in reqs:
        if r.current_status == "REJECTED":
            if r.decision_reason and "Insufficient coverage" in r.decision_reason:
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

            elif r.decision_reason and "Overlapping approved leave" in r.decision_reason:
                # Replay overlap check: employee must already have an approved range overlapping [r.start_date, r.end_date]
                prior_approved = emp_approved_intervals[r.employee_id]
                has_overlap = any(
                    not (r.end_date < ex_s or r.start_date > ex_e)
                    for ex_s, ex_e in prior_approved
                )
                assert has_overlap, (
                    f"Overlap rejected request {r.leave_request_id} for {r.employee_id} "
                    f"({r.start_date} to {r.end_date}) had no prior approved overlap in {prior_approved}!"
                )

        elif r.decided_at is not None and r.decision_reason and r.decision_reason.startswith("Approved"):
            # Update approved on leave calendar for replay
            emp_team = [e.team_id for e in starter_leave_pipeline["employees"] if e.employee_id == r.employee_id][0]
            cur_d = r.start_date
            while cur_d <= r.end_date:
                approved_on_leave[(emp_team, cur_d)].add(r.employee_id)
                cur_d += timedelta(days=1)
            emp_approved_intervals[r.employee_id].append((r.start_date, r.end_date))


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
    """Non-vacuous holiday test: assert literal 8.0 hours for a 40h employee spanning Jan 1-2 2026."""
    reqs = starter_leave_pipeline["leave_requests"]
    emps = {e.employee_id: e for e in starter_leave_pipeline["employees"]}

    # Non-vacuous check: find 40h full-time employee with ANNUAL leave spanning 2026-01-01 to 2026-01-02
    target_reqs = [
        r for r in reqs
        if r.leave_type == "ANNUAL"
        and r.start_date == date(2026, 1, 1)
        and r.end_date == date(2026, 1, 2)
        and emps[r.employee_id].weekly_contract_hours == 40.0
    ]
    assert len(target_reqs) >= 1, (
        "Expected at least one ANNUAL leave request for a 40h employee spanning Jan 1-2 2026!"
    )

    # Jan 1 is statutory holiday (0.0h); Jan 2 is working Friday (8.0h). Total must literally equal 8.0 hours.
    for r in target_reqs:
        assert r.requested_hours == 8.0, (
            f"Expected literal 8.0 requested_hours for 40h employee {r.employee_id} spanning Jan 1-2, got {r.requested_hours}"
        )


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
    """Verify leave generator produces bitwise identical hashes under TZ='IST-5:30' and TZ='UTC0'."""
    import os

    script = (
        "import sys, json, hashlib; "
        "sys.path.insert(0, 'src'); "
        "from workforce_generator.pipeline import run_pipeline; "
        "d = run_pipeline(overrides={'seed': 20261022, 'workforce': {'num_teams': 5, 'employees_per_team': 10}, 'horizon': {'start_date': '2026-01-01', 'end_date': '2026-01-14'}, 'leave_rules': {'target_total_requests': 100, 'leave_coverage_buffer': 1.25}}); "
        "reqs_json = json.dumps([r.to_dict() for r in d['leave_requests']], sort_keys=True); "
        "evts_json = json.dumps([ev.to_dict() for ev in d['leave_events']], sort_keys=True); "
        "h_reqs = hashlib.sha256(reqs_json.encode('utf-8')).hexdigest(); "
        "h_evts = hashlib.sha256(evts_json.encode('utf-8')).hexdigest(); "
        "print(f'{h_reqs}:{h_evts}')"
    )

    env_ist = os.environ.copy()
    env_ist["TZ"] = "IST-5:30"

    env_utc = os.environ.copy()
    env_utc["TZ"] = "UTC0"

    out_ist = subprocess.check_output(
        [sys.executable, "-c", script],
        env=env_ist,
        text=True,
    ).strip()
    out_utc = subprocess.check_output(
        [sys.executable, "-c", script],
        env=env_utc,
        text=True,
    ).strip()
    assert out_ist == out_utc
    assert len(out_ist.split(":")) == 2


def test_static_timezone_safety_no_disallowed_functions():
    """Static code test: fails if src/ contains .astimezone(, datetime.now(, utcnow(, fromtimestamp(, or time.localtime."""
    import glob
    from pathlib import Path

    repo_src = Path(__file__).resolve().parent.parent / "src"
    disallowed = [".astimezone(", "datetime.now(", "utcnow(", "fromtimestamp(", "time.localtime"]

    violations = []
    for py_file in repo_src.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        for token in disallowed:
            if token in text:
                violations.append(f"{py_file.name}: contains {token}")

    assert not violations, f"Disallowed timezone-dependent functions found in src/: {violations}"


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
