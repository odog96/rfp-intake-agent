"""Checks that .project-metadata.yaml stays in step with the code it deploys.

Cloudera AI reads .project-metadata.yaml to build the project: it installs the
dependencies, creates the CML Job and starts the Cloudera AI Application. Nothing
in that file is imported by Python, so a rename anywhere else in the repository
breaks the deployment silently and only for a customer installing it fresh.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from rfp_intake.config.paths import find_project_root
from rfp_intake.config.settings import Settings


@pytest.fixture(scope="module")
def project_root() -> Path:
    return find_project_root(start=Path(__file__).parent)


@pytest.fixture(scope="module")
def metadata(project_root: Path) -> dict[str, Any]:
    text = (project_root / ".project-metadata.yaml").read_text()
    loaded = yaml.safe_load(text)
    assert isinstance(loaded, dict)
    return loaded


def tasks_of_type(metadata: dict[str, Any], task_type: str) -> list[dict[str, Any]]:
    return [t for t in metadata["tasks"] if t["type"] == task_type]


class TestScriptsExist:
    def test_every_script_named_by_a_task_is_present(
        self, metadata: dict[str, Any], project_root: Path
    ) -> None:
        for task in metadata["tasks"]:
            script = task.get("script")
            assert script, f"task {task['name']} names no script"
            assert (project_root / script).is_file(), f"{script} does not exist"


class TestJobName:
    def test_the_created_job_is_the_one_the_application_looks_for(
        self, metadata: dict[str, Any]
    ) -> None:
        # app.py finds the job by name (find_job_id). If .project-metadata.yaml
        # creates a job called anything else, the application raises
        # "CML Job '...' not found" on the first upload.
        created = tasks_of_type(metadata, "create_job")
        assert len(created) == 1
        assert created[0]["name"] == Settings().job_name

    def test_the_job_runs_the_pipeline_entry_point(
        self, metadata: dict[str, Any]
    ) -> None:
        assert tasks_of_type(metadata, "create_job")[0]["script"] == "run_job.py"


class TestApplication:
    def test_the_application_runs_the_launcher(self, metadata: dict[str, Any]) -> None:
        started = tasks_of_type(metadata, "start_application")
        assert len(started) == 1
        assert started[0]["script"] == "launch_app.py"


class TestTaskOrder:
    def test_dependencies_are_installed_before_anything_uses_them(
        self, metadata: dict[str, Any]
    ) -> None:
        types = [task["type"] for task in metadata["tasks"]]
        assert types[0] == "run_session"
        assert types.index("create_job") < types.index("start_application")


class TestEnvironmentVariables:
    def test_every_prompted_variable_is_one_something_actually_reads(
        self, metadata: dict[str, Any]
    ) -> None:
        # An RFP_INTAKE_ variable that does not correspond to a Settings field is
        # a variable the customer fills in and the pipeline ignores.
        settings_fields = set(Settings.model_fields)
        for name in metadata["environment_variables"]:
            if not name.startswith("RFP_INTAKE_"):
                continue  # AWS_BEARER_TOKEN_BEDROCK is read by boto3, not by us
            field = name.removeprefix("RFP_INTAKE_").lower()
            assert field in settings_fields, f"{name} matches no Settings field"

    def test_the_backend_default_is_a_value_settings_accepts(
        self, metadata: dict[str, Any]
    ) -> None:
        default = metadata["environment_variables"]["RFP_INTAKE_LLM_BACKEND"]["default"]
        assert Settings(llm_backend=default).llm_backend == default

    def test_the_bedrock_credential_is_offered(
        self, metadata: dict[str, Any]
    ) -> None:
        # config/models.yaml ships routed to Bedrock, so a customer who is not
        # prompted for this key installs a project that cannot call any model.
        assert "AWS_BEARER_TOKEN_BEDROCK" in metadata["environment_variables"]
