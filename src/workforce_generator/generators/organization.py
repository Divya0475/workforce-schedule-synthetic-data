"""Generates Teams, Skills, Employees, and EmployeeSkill bridge records with department maps."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List, Tuple
import numpy as np

from workforce_generator.config import GeneratorConfig
from workforce_generator.models.entities import Employee, EmployeeSkill, Skill, Team
from workforce_generator.models.identifiers import (
    format_employee_id,
    format_employee_skill_id,
    format_skill_id,
    format_team_id,
)

FIRST_NAMES = [
    "James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael", "Linda",
    "David", "Elizabeth", "William", "Barbara", "Richard", "Susan", "Joseph", "Jessica",
    "Thomas", "Sarah", "Charles", "Karen", "Christopher", "Nancy", "Daniel", "Lisa",
    "Matthew", "Betty", "Anthony", "Margaret", "Mark", "Sandra", "Donald", "Ashley",
    "Steven", "Kimberly", "Paul", "Emily", "Andrew", "Donna", "Joshua", "Michelle",
    "Kenneth", "Carol", "Kevin", "Amanda", "Brian", "Dorothy", "George", "Melissa",
    "Edward", "Deborah", "Ronald", "Stephanie", "Timothy", "Rebecca", "Jason", "Sharon",
    "Jeffrey", "Laura", "Ryan", "Cynthia", "Jacob", "Kathleen", "Gary", "Amy",
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
    "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
    "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson",
    "White", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson", "Walker",
    "Young", "Allen", "King", "Wright", "Scott", "Torres", "Nguyen", "Hill", "Flores",
    "Green", "Adams", "Nelson", "Baker", "Hall", "Rivera", "Campbell", "Mitchell",
    "Carter", "Roberts", "Gomez", "Phillips", "Evans", "Turner", "Diaz", "Parker",
]

ALL_SKILL_DEFINITIONS = [
    Skill("SKL-TRIAGE", "TRIAGE", "Emergency Triage & Assessment", "Clinical", "Rapid patient condition classification"),
    Skill("SKL-ICU_CARE", "ICU_CARE", "Intensive Care Operations", "Clinical", "Critical patient monitoring and care"),
    Skill("SKL-PHARMACY", "PHARMACY", "Clinical Pharmacology", "Clinical", "Medication dispensary and verification"),
    Skill("SKL-DISPATCH", "DISPATCH", "Resource & Unit Dispatch", "Logistical", "Emergency communications and unit routing"),
    Skill("SKL-SUPPLY_CHAIN", "SUPPLY_CHAIN", "Medical Supply Chain", "Logistical", "Medical inventory and supply routing"),
    Skill("SKL-INTAKE", "INTAKE", "Patient Intake & Admission", "Administrative", "Patient registration and preliminary intake"),
    Skill("SKL-BILINGUAL_ES", "BILINGUAL_ES", "Bilingual Spanish", "Linguistic", "Certified fluent Spanish clinical communication"),
    # Rare specialized skills
    Skill("SKL-TOXICOLOGY", "TOXICOLOGY", "Clinical Toxicology", "Clinical", "Rare poisoning and overdose interventions"),
    Skill("SKL-ONCOLOGY", "ONCOLOGY", "Clinical Oncology Protocols", "Clinical", "Specialized cancer therapy protocols"),
    Skill("SKL-HAZMAT", "HAZMAT", "Hazardous Material Logistics", "Logistical", "Biohazard handling and hazardous waste dispatch"),
    Skill("SKL-CRISIS_COUNSEL", "CRISIS_COUNSEL", "Crisis Counseling", "Linguistic", "De-escalation and behavioral crisis intervention"),
]


def generate_organization(
    config: GeneratorConfig,
    rng: np.random.Generator,
) -> Tuple[List[Team], List[Skill], List[Employee], List[EmployeeSkill]]:
    # 1. Build Department dictionary from config
    departments = config.departments
    if not departments:
        # Fallback to standard 4 departments
        from workforce_generator.config import DepartmentConfig
        departments = [
            DepartmentConfig("EMERGENCY", "Emergency", ["TRIAGE", "ICU_CARE"], ["PHARMACY", "BILINGUAL_ES"], ["TOXICOLOGY"]),
            DepartmentConfig("INPATIENT", "Inpatient", ["ICU_CARE", "PHARMACY"], ["TRIAGE", "BILINGUAL_ES"], ["ONCOLOGY"]),
            DepartmentConfig("LOGISTICS", "Logistics", ["DISPATCH", "SUPPLY_CHAIN"], ["BILINGUAL_ES"], ["HAZMAT"]),
            DepartmentConfig("CUSTOMER_SUPPORT", "Customer Support", ["BILINGUAL_ES", "INTAKE"], ["DISPATCH"], ["CRISIS_COUNSEL"]),
        ]

    dept_by_name = {d.name: d for d in departments}
    dept_names = [d.name for d in departments]

    # 2. Teams
    teams: List[Team] = []
    num_teams = config.workforce.num_teams
    for t_idx in range(1, num_teams + 1):
        team_id = format_team_id(t_idx)
        dept_name = dept_names[(t_idx - 1) % len(dept_names)]
        team_name = f"{dept_name} Operations Team {t_idx:03d}"
        teams.append(
            Team(
                team_id=team_id,
                name=team_name,
                department=dept_name,
                timezone=config.horizon.timezone,
                is_active=True,
            )
        )

    skills = ALL_SKILL_DEFINITIONS.copy()
    skills_by_code = {s.code: s for s in skills}

    # 3. Roles and FTEs
    fte_levels = list(config.workforce.fte_distribution.keys())
    fte_weights = np.array(list(config.workforce.fte_distribution.values()), dtype=float)
    fte_weights /= fte_weights.sum()

    non_manager_roles = [r for r in config.workforce.roles if r.code != "MANAGER"]
    role_codes = [r.code for r in non_manager_roles]
    role_weights = np.array([r.weight for r in non_manager_roles], dtype=float)
    role_weights /= role_weights.sum()

    start_horizon = config.horizon.start_date
    end_horizon = config.horizon.end_date
    horizon_days = config.horizon.total_days

    total_emp_target = config.workforce.total_employees
    # Boundary case allocation: guarantee at least 1 mid-horizon hire and 1 mid-horizon termination
    num_mid_hires = max(1, int(round(total_emp_target * config.workforce.boundary_cases.mid_horizon_hires_ratio)))
    num_mid_terms = max(1, int(round(total_emp_target * config.workforce.boundary_cases.mid_horizon_terminations_ratio)))

    # Track boundary assignments
    mid_hires_assigned = 0
    mid_terms_assigned = 0

    employees: List[Employee] = []
    employee_skills: List[EmployeeSkill] = []

    emp_seq = 0
    esk_seq = 0
    prior_cert_ratio = config.workforce.prior_certification_ratio

    for t_idx, team in enumerate(teams):
        dept_cfg = dept_by_name.get(team.department, departments[0])
        core_skills = dept_cfg.core_skills
        secondary_skills = dept_cfg.secondary_skills
        rare_skills = dept_cfg.rare_skills

        num_emp = config.workforce.employees_per_team
        # Employee 0 in team is MANAGER
        team_roles: List[str] = ["MANAGER"]
        if num_emp > 1:
            drawn_roles = list(rng.choice(role_codes, size=num_emp - 1, p=role_weights))
            team_roles.extend(drawn_roles)

        for r_idx, role in enumerate(team_roles):
            emp_seq += 1
            emp_id = format_employee_id(emp_seq)

            first_name = str(rng.choice(FIRST_NAMES))
            last_name = str(rng.choice(LAST_NAMES))
            email = f"{first_name.lower()}.{last_name.lower()}.{emp_seq:04d}@workforce.example.com"

            # FTE
            if role == "MANAGER":
                fte = 1.00
            else:
                fte = float(rng.choice(fte_levels, p=fte_weights))
            contract_hours = round(fte * config.labor_constraints.regular_weekly_hours_standard, 1)

            # Determine hire date & termination date (including boundary cases)
            is_mid_hire = False
            is_mid_term = False

            # Assign boundary cases to non-managers
            if role != "MANAGER":
                if mid_hires_assigned < num_mid_hires and (emp_seq % 25 == 3 or emp_seq == 2):
                    is_mid_hire = True
                    mid_hires_assigned += 1
                elif mid_terms_assigned < num_mid_terms and (emp_seq % 25 == 7 or emp_seq == 4):
                    is_mid_term = True
                    mid_terms_assigned += 1

            if is_mid_hire:
                # Hired inside the horizon: e.g. day 3 to day 10
                offset = int(rng.integers(2, max(3, min(10, horizon_days - 2))))
                hire_date = start_horizon + timedelta(days=offset)
                termination_date = None
                is_active = True
            elif is_mid_term:
                # Hired in past, terminated inside the horizon
                hire_date = start_horizon - timedelta(days=int(rng.integers(100, 1000)))
                term_offset = int(rng.integers(max(2, min(5, horizon_days - 4)), horizon_days - 1))
                termination_date = start_horizon + timedelta(days=term_offset)
                is_active = False # Terminated during horizon
            else:
                hire_date = start_horizon - timedelta(days=int(rng.integers(60, 1800)))
                termination_date = None
                is_active = True

            emp = Employee(
                employee_id=emp_id,
                team_id=team.team_id,
                first_name=first_name,
                last_name=last_name,
                email=email,
                role=role,
                weekly_contract_hours=contract_hours,
                fte=fte,
                hire_date=hire_date,
                termination_date=termination_date,
                is_active=is_active,
            )
            employees.append(emp)

            # Skill assignment according to department mapping
            # Core skill guaranteed for early staff members to guarantee coverage feasibility
            functional_core = [c for c in core_skills if c != "BILINGUAL_ES"]
            functional_secondary = [c for c in secondary_skills if c != "BILINGUAL_ES"]

            if r_idx == 0:
                # Manager has a core skill
                primary_skill = functional_core[0] if functional_core else "TRIAGE"
            elif r_idx <= len(functional_core):
                # Guaranteed coverage of department core skills
                primary_skill = functional_core[(r_idx - 1) % len(functional_core)]
            else:
                # Drawn from functional core and secondary skills
                dept_pool = functional_core + functional_secondary
                primary_skill = str(rng.choice(dept_pool))

            # Secondary skills determination:
            # 1. BILINGUAL_ES: Secondary-only certification held by roughly 20-25% of employees
            has_bilingual = bool(rng.random() < 0.18)

            # 2. ONCOLOGY: Specialized oncology protocol skill guaranteed on Inpatient team non-managers
            has_oncology = bool(dept_cfg.code == "INPATIENT" and r_idx in (2, 3))

            chosen_secondary: List[str] = []
            if has_oncology:
                chosen_secondary.append("ONCOLOGY")
            if has_bilingual:
                chosen_secondary.append("BILINGUAL_ES")

            # Determine remaining capacity for secondary skills (total skills between 1 and 3)
            functional_candidates = [c for c in (functional_core + functional_secondary) if c != primary_skill]
            max_secondary = config.workforce.skills_per_employee_max - 1  # 3 - 1 = 2
            available_slots = max_secondary - len(chosen_secondary)

            if available_slots > 0 and functional_candidates:
                num_func = int(rng.integers(0, available_slots + 1))
                if num_func > 0:
                    take_k = min(num_func, len(functional_candidates))
                    additional = list(rng.choice(functional_candidates, size=take_k, replace=False))
                    for sk in additional:
                        if sk not in chosen_secondary:
                            chosen_secondary.append(sk)

            emp_skill_codes = [primary_skill] + chosen_secondary

            # Certifications generation with Requirement 4:
            # Documented minority (e.g. 15%) prior to hire_date, otherwise certified_date >= hire_date
            for s_code in emp_skill_codes:
                esk_seq += 1
                esk_id = format_employee_skill_id(esk_seq)
                prof = int(rng.integers(2, 6)) if s_code == primary_skill else int(rng.integers(1, 5))

                # Decide if prior to hire_date
                is_prior = bool(rng.random() < prior_cert_ratio)
                if is_prior:
                    # Acquired before hire_date (e.g. college or previous job certification)
                    prior_days = int(rng.integers(30, 1000))
                    cert_date = hire_date - timedelta(days=prior_days)
                else:
                    # In-house certification: certified_date >= hire_date
                    if hire_date <= start_horizon:
                        # Hired before or on horizon start: certified between hire_date and start_horizon
                        days_between = max(0, (start_horizon - hire_date).days)
                        offset = int(rng.integers(0, max(1, days_between + 1)))
                        cert_date = hire_date + timedelta(days=offset)
                    else:
                        # Mid-horizon hire: certified on hire_date
                        cert_date = hire_date

                # Expiry date is always strictly > certified_date (2 to 4 years validity)
                expiry_offset = int(rng.integers(700, 1500))
                expiry_date = cert_date + timedelta(days=expiry_offset)

                esk = EmployeeSkill(
                    employee_skill_id=esk_id,
                    employee_id=emp_id,
                    skill_id=format_skill_id(s_code),
                    proficiency_level=prof,
                    certified_date=cert_date,
                    expiry_date=expiry_date,
                    is_primary=(s_code == primary_skill and s_code != "BILINGUAL_ES"),
                )
                employee_skills.append(esk)

    return teams, skills, employees, employee_skills
