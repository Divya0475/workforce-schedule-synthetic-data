"""Shared pytest fixtures across all test suites."""

from __future__ import annotations

import pytest

from workforce_generator.config import load_config
from workforce_generator.pipeline import run_pipeline


@pytest.fixture(scope="session")
def starter_config():
    """Returns the AppConfig for the starter profile."""
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
    return load_config(overrides=overrides)


@pytest.fixture(scope="session")
def starter_pipeline_data(starter_config):
    """Executes the complete generation pipeline once for the starter profile and caches it."""
    return run_pipeline(starter_config)


@pytest.fixture
def starter_data(starter_pipeline_data):
    """Legacy fixture for test_organization.py."""
    data = starter_pipeline_data
    return data["config"], data["teams"], data["skills"], data["employees"], data["employee_skills"]


@pytest.fixture
def stage_a_artifacts(starter_pipeline_data):
    """Legacy fixture for test_stage_a_invariants.py."""
    return starter_pipeline_data


@pytest.fixture
def starter_leave_pipeline(starter_pipeline_data):
    """Legacy fixture for test_leave_requests.py."""
    return starter_pipeline_data
