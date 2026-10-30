"""Complete end-to-end synthetic data generation pipeline.

Coordinates independent random number generation streams via numpy.random.SeedSequence
to produce deterministic, fully integrated workforce tables across all generation stages.
"""

from __future__ import annotations

from typing import Any, Dict
import numpy as np

from workforce_generator.config import GeneratorConfig, load_config
from workforce_generator.generators.analytics import (
    compute_coverage_gaps,
    compute_weekly_hours_summaries,
)
from workforce_generator.generators.coverage import generate_coverage_requirements
from workforce_generator.generators.holidays import generate_holidays
from workforce_generator.generators.leave import generate_leave_requests_and_events
from workforce_generator.generators.organization import generate_organization
from workforce_generator.generators.scheduler import schedule_shifts
from workforce_generator.models.entities import ShiftTemplate


def run_pipeline(cfg: GeneratorConfig | None = None, overrides: dict | None = None) -> Dict[str, Any]:
    """Execute the workforce synthetic data generation pipeline.

    Args:
        cfg: Optional loaded GeneratorConfig. If None, loaded via load_config(overrides=overrides).
        overrides: Optional dict of overrides if cfg is None.

    Returns:
        Dict mapping table names to their generated dataclass / entity lists:
          - "teams"
          - "skills"
          - "employees"
          - "employee_skills"
          - "shift_templates"
          - "holidays"
          - "coverage_requirements"
          - "leave_requests"
          - "leave_events"
          - "shift_assignments"
          - "coverage_gaps"
          - "weekly_hours_summaries"
          - "config"
    """
    if cfg is None:
        cfg = load_config(overrides=overrides)

    # Spawn 3 independent RNG streams for each generation stage:
    # 0: Organization generator
    # 1: Leave requests and lifecycle events
    # 2: Shift scheduler
    seed_seq = np.random.SeedSequence(cfg.seed)
    rng_org, rng_leave, rng_sched = [np.random.default_rng(s) for s in seed_seq.spawn(3)]

    # Stage A: Organization (Teams, Skills, Employees, EmployeeSkills)
    teams, skills, employees, employee_skills = generate_organization(cfg, rng_org)

    # Stage A: Shift Templates
    shift_templates = [
        ShiftTemplate(
            shift_template_id=s.id,
            template_code=s.code,
            name=s.name,
            start_time=s.start_time,
            end_time=s.end_time,
            duration_hours=s.duration_hours,
            unpaid_break_minutes=s.unpaid_break_minutes,
            is_overnight=s.is_overnight,
        )
        for s in cfg.shifts
    ]

    # Stage A: Holiday Calendar
    holidays = generate_holidays(cfg)

    # Stage A: Coverage Requirements
    coverage_reqs = generate_coverage_requirements(cfg, teams, holidays)

    # Stage B1: Leave Requests and Lifecycle Events
    leave_requests, leave_events = generate_leave_requests_and_events(
        cfg, rng_leave, employees, employee_skills, coverage_reqs, holidays
    )

    # Stage B2: Shift Scheduler
    shift_assignments = schedule_shifts(
        cfg, rng_sched, employees, employee_skills, coverage_reqs, holidays, leave_requests
    )

    # Stage B2: Derived Analytical Tables (computed strictly post-scheduling)
    coverage_gaps = compute_coverage_gaps(
        cfg, coverage_reqs, shift_assignments, employees, employee_skills, leave_requests
    )
    weekly_hours_summaries = compute_weekly_hours_summaries(
        cfg, employees, shift_assignments
    )

    return {
        "teams": teams,
        "skills": skills,
        "employees": employees,
        "employee_skills": employee_skills,
        "shift_templates": shift_templates,
        "holidays": holidays,
        "coverage_requirements": coverage_reqs,
        "leave_requests": leave_requests,
        "leave_events": leave_events,
        "shift_assignments": shift_assignments,
        "coverage_gaps": coverage_gaps,
        "weekly_hours_summaries": weekly_hours_summaries,
        "config": cfg,
    }
