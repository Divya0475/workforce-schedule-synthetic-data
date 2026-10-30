"""Generates Teams, Skills, Employees, and EmployeeSkill bridge records."""

from __future__ import annotations

from datetime import date, timedelta
from typing import List, Tuple
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

DEFAULT_SKILLS = [
    Skill(skill_id="SKL-TRIAGE", code="TRIAGE", name="Emergency Triage & Assessment", category="Clinical", description="Rapid patient condition classification"),
    Skill(skill_id="SKL-DISPATCH", code="DISPATCH", name="Resource & Unit Dispatch", category="Logistical", description="Emergency communications and unit routing"),
    Skill(skill_id="SKL-ICU_CARE", code="ICU_CARE", name="Intensive Care Operations", category="Clinical", description="Critical patient monitoring and care"),
    Skill(skill_id="SKL-PHARMACY", code="PHARMACY", name="Clinical Pharmacology", category="Clinical", description="Medication dispensary and verification"),
    Skill(skill_id="SKL-BILINGUAL_ES", code="BILINGUAL_ES", name="Bilingual Spanish", category="Linguistic", description="Certified fluent Spanish clinical communication"),
]

DEPARTMENTS = ["Emergency", "Inpatient", "Logistics", "Customer Support"]


def generate_organization(
    config: GeneratorConfig,
    rng: np.random.Generator,
) -> Tuple[List[Team], List[Skill], List[Employee], List[EmployeeSkill]]:
    teams: List[Team] = []
    num_teams = config.workforce.num_teams
    for t_idx in range(1, num_teams + 1):
        team_id = format_team_id(t_idx)
        dept = DEPARTMENTS[(t_idx - 1) % len(DEPARTMENTS)]
        team_name = f"{dept} Operations Team {t_idx:03d}"
        teams.append(
            Team(
                team_id=team_id,
                name=team_name,
                department=dept,
                timezone=config.horizon.timezone,
                is_active=True,
            )
        )

    skills = DEFAULT_SKILLS.copy()

    # FTE options and weights
    fte_levels = list(config.workforce.fte_distribution.keys())
    fte_weights = np.array(list(config.workforce.fte_distribution.values()), dtype=float)
    fte_weights /= fte_weights.sum()

    # Non-manager role options and weights
    non_manager_roles = [r for r in config.workforce.roles if r.code != "MANAGER"]
    role_codes = [r.code for r in non_manager_roles]
    role_weights = np.array([r.weight for r in non_manager_roles], dtype=float)
    role_weights /= role_weights.sum()

    employees: List[Employee] = []
    employee_skills: List[EmployeeSkill] = []

    emp_seq = 0
    esk_seq = 0
    start_horizon = config.horizon.start_date

    for team in teams:
        # For each team, employee 0 is designated MANAGER
        # Remaining (employees_per_team - 1) are STAFF, SENIOR, LEAD
        num_emp = config.workforce.employees_per_team

        # Generate roles for this team
        team_roles: List[str] = ["MANAGER"]
        if num_emp > 1:
            drawn_roles = rng.choice(role_codes, size=num_emp - 1, p=role_weights)
            team_roles.extend(drawn_roles)

        for r_idx, role in enumerate(team_roles):
            emp_seq += 1
            emp_id = format_employee_id(emp_seq)

            first_name = str(rng.choice(FIRST_NAMES))
            last_name = str(rng.choice(LAST_NAMES))
            clean_first = first_name.lower()
            clean_last = last_name.lower()
            email = f"{clean_first}.{clean_last}.{emp_seq:04d}@workforce.example.com"

            # FTE assignment: managers are 1.0 FTE; staff drawn from distribution
            if role == "MANAGER":
                fte = 1.00
            else:
                fte = float(rng.choice(fte_levels, p=fte_weights))
            contract_hours = round(fte * config.labor_constraints.regular_weekly_hours_standard, 1)

            # Hire date between 1 and 5 years prior to schedule start
            hire_offset_days = int(rng.integers(30, 1800))
            hire_date = start_horizon - timedelta(days=hire_offset_days)

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
                termination_date=None,
                is_active=True,
            )
            employees.append(emp)

            # Skill assignment:
            # Schedulable employees get 1 to 3 skills
            # To ensure coverage feasibility, ensure mandatory skills (TRIAGE, DISPATCH) are present
            all_skill_codes = [s.code for s in skills]
            if r_idx == 1 or r_idx == 2 or r_idx == 3:
                # Guaranteed TRIAGE
                primary_skill = "TRIAGE"
            elif r_idx == 4 or r_idx == 5:
                # Guaranteed DISPATCH
                primary_skill = "DISPATCH"
            else:
                primary_skill = str(rng.choice(all_skill_codes))

            num_skills = int(rng.integers(
                config.workforce.skills_per_employee_min,
                config.workforce.skills_per_employee_max + 1,
            ))
            # Pick secondary skills
            other_skills = [c for c in all_skill_codes if c != primary_skill]
            chosen_secondary = list(rng.choice(other_skills, size=min(num_skills - 1, len(other_skills)), replace=False))
            emp_skill_codes = [primary_skill] + chosen_secondary

            for s_code in emp_skill_codes:
                esk_seq += 1
                esk_id = format_employee_skill_id(esk_seq)
                prof = int(rng.integers(2, 6)) if s_code == primary_skill else int(rng.integers(1, 5))
                cert_offset = int(rng.integers(10, 1000))
                cert_date = start_horizon - timedelta(days=cert_offset)
                # Valid for 2-4 years
                expiry_offset = int(rng.integers(700, 1500))
                expiry_date = cert_date + timedelta(days=expiry_offset)

                esk = EmployeeSkill(
                    employee_skill_id=esk_id,
                    employee_id=emp_id,
                    skill_id=format_skill_id(s_code),
                    proficiency_level=prof,
                    certified_date=cert_date,
                    expiry_date=expiry_date,
                    is_primary=(s_code == primary_skill),
                )
                employee_skills.append(esk)

    return teams, skills, employees, employee_skills

