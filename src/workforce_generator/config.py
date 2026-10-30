"""Configuration loader and typed dataclass representations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml


@dataclass
class HorizonConfig:
    start_date: date
    end_date: date
    timezone: str = "UTC"

    @property
    def total_days(self) -> int:
        return (self.end_date - self.start_date).days + 1


@dataclass
class RoleConfig:
    code: str
    name: str
    weight: float


@dataclass
class BoundaryCasesConfig:
    mid_horizon_hires_ratio: float = 0.02
    mid_horizon_terminations_ratio: float = 0.02


@dataclass
class DepartmentConfig:
    code: str
    name: str
    core_skills: List[str]
    secondary_skills: List[str] = field(default_factory=list)
    rare_skills: List[str] = field(default_factory=list)


@dataclass
class WorkforceConfig:
    num_teams: int
    employees_per_team: int
    roles: List[RoleConfig]
    fte_distribution: Dict[float, float]
    skills_per_employee_min: int = 1
    skills_per_employee_max: int = 3
    prior_certification_ratio: float = 0.15
    boundary_cases: BoundaryCasesConfig = field(default_factory=BoundaryCasesConfig)

    @property
    def total_employees(self) -> int:
        return self.num_teams * self.employees_per_team


@dataclass
class ShiftTemplateItem:
    id: str
    code: str
    name: str
    start_time: str
    end_time: str
    duration_hours: float
    unpaid_break_minutes: int
    is_overnight: bool


@dataclass
class LaborConstraintsConfig:
    regular_weekly_hours_standard: float = 40.0
    overtime_cap_weekly_hours: float = 48.0
    min_rest_hours_between_shifts: float = 11.0
    max_consecutive_working_days: int = 6
    max_daily_working_hours: float = 12.0
    unpaid_break_hours_per_shift: float = 0.5
    paid_hours_per_shift: float = 7.5


@dataclass
class SkillRequirementConfig:
    skill_code: str
    min_headcount: int


@dataclass
class ShiftCoverageRule:
    min_total_headcount: int
    skill_requirements: List[SkillRequirementConfig]


@dataclass
class CoverageConfig:
    weekday: Dict[str, ShiftCoverageRule]
    weekend: Dict[str, ShiftCoverageRule]


@dataclass
class LeaveTypeConfig:
    code: str
    name: str
    paid: bool
    advance_notice_days_required: int


@dataclass
class LeaveRulesConfig:
    target_total_requests: int
    types: List[LeaveTypeConfig]
    enforce_coverage_minimum: bool = True
    allow_retroactive_sick_leave: bool = True
    max_annual_leave_consecutive_days: int = 14
    request_rate_per_employee_month: float = 0.833


@dataclass
class SwapRulesConfig:
    initial_state: str = "REQUESTED"
    terminal_states: List[str] = field(default_factory=lambda: ["REJECTED", "APPLIED"])
    require_matching_skill: bool = True
    require_rest_period_compliance: bool = True
    require_weekly_hour_cap: bool = True
    require_manager_approval: bool = True
    request_rate_per_assignment: float = 0.04
    history_preservation_mode: str = "NON_DESTRUCTIVE_INSERT"
    active_status: str = "ACTIVE"
    swapped_out_status: str = "SWAPPED_OUT"


@dataclass
class AttendanceSimulationConfig:
    punctuality_probabilities: Dict[str, float]
    clock_out_variance: Dict[str, float]
    exception_thresholds: Dict[str, int]
    exception_types: List[str] = field(
        default_factory=lambda: ["LATE", "EARLY_LEAVE", "NO_SHOW", "OVERTIME"]
    )


@dataclass
class AnomalyItemConfig:
    id: str
    name: str
    description: str
    target_count: int = 5


@dataclass
class AnomaliesConfig:
    enabled: bool = True
    output_dirty_dataset: bool = True
    ground_truth_file: str = "ground_truth"
    injected_cases: List[AnomalyItemConfig] = field(default_factory=list)


@dataclass
class ExportsConfig:
    formats: List[str] = field(default_factory=lambda: ["csv", "json", "sql"])
    include_clean: bool = True
    include_dirty: bool = True
    compression: Optional[str] = None


@dataclass
class GeneratorConfig:
    seed: int
    horizon: HorizonConfig
    workforce: WorkforceConfig
    departments: List[DepartmentConfig]
    shifts: List[ShiftTemplateItem]
    labor_constraints: LaborConstraintsConfig
    coverage: CoverageConfig
    leave_rules: LeaveRulesConfig
    swap_rules: SwapRulesConfig
    attendance: AttendanceSimulationConfig
    anomalies: AnomaliesConfig
    exports: ExportsConfig
    raw_dict: Dict[str, Any] = field(default_factory=dict)


def load_config(
    config_path: str | Path | None = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> GeneratorConfig:
    if config_path is None:
        # Default config in repo root / config / default.yaml
        config_path = Path(__file__).resolve().parent.parent.parent / "config" / "default.yaml"
    else:
        config_path = Path(config_path)

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if overrides:
        # Apply shallow/nested overrides
        for k, v in overrides.items():
            if isinstance(v, dict) and k in data and isinstance(data[k], dict):
                data[k].update(v)
            else:
                data[k] = v

    # Parse Horizon
    h_data = data["horizon"]
    start_d = date.fromisoformat(str(h_data["start_date"]))
    end_d = date.fromisoformat(str(h_data["end_date"]))
    horizon_cfg = HorizonConfig(start_date=start_d, end_date=end_d, timezone=h_data.get("timezone", "UTC"))

    # Parse Workforce
    wf_data = data["workforce"]
    roles = [RoleConfig(code=r["code"], name=r["name"], weight=float(r["weight"])) for r in wf_data["roles"]]
    fte_dist = {float(k): float(v) for k, v in wf_data["fte_distribution"].items()}
    bc_data = wf_data.get("boundary_cases", {})
    boundary_cases_cfg = BoundaryCasesConfig(
        mid_horizon_hires_ratio=float(bc_data.get("mid_horizon_hires_ratio", 0.02)),
        mid_horizon_terminations_ratio=float(bc_data.get("mid_horizon_terminations_ratio", 0.02)),
    )
    workforce_cfg = WorkforceConfig(
        num_teams=int(wf_data["num_teams"]),
        employees_per_team=int(wf_data["employees_per_team"]),
        roles=roles,
        fte_distribution=fte_dist,
        skills_per_employee_min=int(wf_data.get("skills_per_employee", {}).get("min", 1)),
        skills_per_employee_max=int(wf_data.get("skills_per_employee", {}).get("max", 3)),
        prior_certification_ratio=float(wf_data.get("prior_certification_ratio", 0.15)),
        boundary_cases=boundary_cases_cfg,
    )

    # Parse Departments
    dept_list: List[DepartmentConfig] = []
    for d_item in data.get("departments", []):
        dept_list.append(
            DepartmentConfig(
                code=d_item["code"],
                name=d_item["name"],
                core_skills=d_item["core_skills"],
                secondary_skills=d_item.get("secondary_skills", []),
                rare_skills=d_item.get("rare_skills", []),
            )
        )

    # Parse Shifts
    shift_items = []
    for s in data["shifts"]["templates"]:
        shift_items.append(
            ShiftTemplateItem(
                id=s["id"],
                code=s["code"],
                name=s["name"],
                start_time=s["start_time"],
                end_time=s["end_time"],
                duration_hours=float(s["duration_hours"]),
                unpaid_break_minutes=int(s["unpaid_break_minutes"]),
                is_overnight=bool(s["is_overnight"]),
            )
        )

    # Parse Labor Constraints
    lc = data.get("labor_constraints", {})
    labor_cfg = LaborConstraintsConfig(
        regular_weekly_hours_standard=float(lc.get("regular_weekly_hours_standard", 40.0)),
        overtime_cap_weekly_hours=float(lc.get("overtime_cap_weekly_hours", 48.0)),
        min_rest_hours_between_shifts=float(lc.get("min_rest_hours_between_shifts", 11.0)),
        max_consecutive_working_days=int(lc.get("max_consecutive_working_days", 6)),
        max_daily_working_hours=float(lc.get("max_daily_working_hours", 12.0)),
        unpaid_break_hours_per_shift=float(lc.get("unpaid_break_hours_per_shift", 0.5)),
        paid_hours_per_shift=float(lc.get("paid_hours_per_shift", 7.5)),
    )

    # Parse Coverage
    cov_data = data["coverage"]["default_requirements"]

    def parse_shift_rules(rules_dict: Dict[str, Any]) -> Dict[str, ShiftCoverageRule]:
        res = {}
        for s_code, s_rule in rules_dict.items():
            skills = [
                SkillRequirementConfig(skill_code=sk["skill_code"], min_headcount=int(sk["min_headcount"]))
                for sk in s_rule.get("skill_requirements", [])
            ]
            res[s_code] = ShiftCoverageRule(
                min_total_headcount=int(s_rule["min_total_headcount"]),
                skill_requirements=skills,
            )
        return res

    coverage_cfg = CoverageConfig(
        weekday=parse_shift_rules(cov_data["weekday"]),
        weekend=parse_shift_rules(cov_data["weekend"]),
    )

    # Parse Leave Rules
    lr_data = data["leave_rules"]
    leave_types = [
        LeaveTypeConfig(
            code=lt["code"],
            name=lt["name"],
            paid=bool(lt["paid"]),
            advance_notice_days_required=int(lt["advance_notice_days_required"]),
        )
        for lt in lr_data["types"]
    ]
    leave_cfg = LeaveRulesConfig(
        target_total_requests=int(lr_data.get("target_total_requests", 5000)),
        types=leave_types,
        enforce_coverage_minimum=bool(lr_data.get("approval_policy", {}).get("enforce_coverage_minimum", True)),
        allow_retroactive_sick_leave=bool(lr_data.get("approval_policy", {}).get("allow_retroactive_sick_leave", True)),
        max_annual_leave_consecutive_days=int(
            lr_data.get("approval_policy", {}).get("max_annual_leave_consecutive_days", 14)
        ),
        request_rate_per_employee_month=float(lr_data.get("request_rate_per_employee_month", 0.833)),
    )

    # Parse Swap Rules
    sw_data = data["swap_rules"]
    swap_cfg = SwapRulesConfig(
        initial_state=sw_data.get("lifecycle", {}).get("initial_state", "REQUESTED"),
        terminal_states=sw_data.get("lifecycle", {}).get("terminal_states", ["REJECTED", "APPLIED"]),
        require_matching_skill=bool(sw_data.get("eligibility", {}).get("require_matching_skill", True)),
        require_rest_period_compliance=bool(
            sw_data.get("eligibility", {}).get("require_rest_period_compliance", True)
        ),
        require_weekly_hour_cap=bool(sw_data.get("eligibility", {}).get("require_weekly_hour_cap", True)),
        require_manager_approval=bool(sw_data.get("eligibility", {}).get("require_manager_approval", True)),
        request_rate_per_assignment=float(sw_data.get("request_rate_per_assignment", 0.04)),
        history_preservation_mode=sw_data.get("history_preservation", {}).get(
            "mode", "NON_DESTRUCTIVE_INSERT"
        ),
        active_status=sw_data.get("history_preservation", {}).get("active_status", "ACTIVE"),
        swapped_out_status=sw_data.get("history_preservation", {}).get("swapped_out_status", "SWAPPED_OUT"),
    )

    # Parse Attendance Simulation
    att_data = data["attendance_simulation"]
    attendance_cfg = AttendanceSimulationConfig(
        punctuality_probabilities={k: float(v) for k, v in att_data["punctuality_probabilities"].items()},
        clock_out_variance={k: float(v) for k, v in att_data["clock_out_variance"].items()},
        exception_thresholds={k: int(v) for k, v in att_data["exception_thresholds"].items()},
        exception_types=att_data.get("exception_types", ["LATE", "EARLY_LEAVE", "NO_SHOW", "OVERTIME"]),
    )

    # Parse Anomalies
    anom_data = data.get("anomalies", {})
    injected_cases = [
        AnomalyItemConfig(
            id=item["id"],
            name=item["name"],
            description=item["description"],
            target_count=int(item.get("target_count", 5)),
        )
        for item in anom_data.get("injected_cases", [])
    ]
    anomalies_cfg = AnomaliesConfig(
        enabled=bool(anom_data.get("enabled", True)),
        output_dirty_dataset=bool(anom_data.get("output_dirty_dataset", True)),
        ground_truth_file=anom_data.get("ground_truth_file", "ground_truth"),
        injected_cases=injected_cases,
    )

    # Parse Exports
    exp_data = data.get("exports", {})
    exports_cfg = ExportsConfig(
        formats=exp_data.get("formats", ["csv", "json", "sql"]),
        include_clean=bool(exp_data.get("include_clean", True)),
        include_dirty=bool(exp_data.get("include_dirty", True)),
        compression=exp_data.get("compression"),
    )

    return GeneratorConfig(
        seed=int(data["seed"]),
        horizon=horizon_cfg,
        workforce=workforce_cfg,
        departments=dept_list,
        shifts=shift_items,
        labor_constraints=labor_cfg,
        coverage=coverage_cfg,
        leave_rules=leave_cfg,
        swap_rules=swap_cfg,
        attendance=attendance_cfg,
        anomalies=anomalies_cfg,
        exports=exports_cfg,
        raw_dict=data,
    )

