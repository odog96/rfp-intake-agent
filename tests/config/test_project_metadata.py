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


class TestSpecConformance:
    """Field names Cloudera AI accepts, and the two mistakes that cost a deployment.

    Ground truth is the AMP project specification plus two AMPs published by
    Cloudera that are known to deploy: CML_AMP_Anomaly_Detection and
    CML_Community_AMP_Template. A field the specification does not define is not
    ignored — it stops the deployment.
    """

    ALLOWED = {
        "run_session": {
            "type", "name", "entity_label", "script", "code", "kernel",
            "cpu", "memory", "gpu", "short_summary", "environment_variables",
        },
        "create_job": {
            "type", "name", "entity_label", "script", "arguments", "kernel",
            "cpu", "memory", "gpu", "timeout", "short_summary", "long_summary",
            "environment",
        },
        "start_application": {
            "type", "name", "entity_label", "subdomain", "script", "kernel",
            "cpu", "memory", "gpu", "short_summary", "long_summary",
            "bypass_authentication", "static_subdomain", "environment_variables",
        },
    }

    def test_no_task_carries_a_field_the_specification_does_not_define(
        self, metadata: dict[str, Any]
    ) -> None:
        for task in metadata["tasks"]:
            allowed = self.ALLOWED[task["type"]]
            unknown = set(task) - allowed
            assert not unknown, f"{task['type']} carries unknown fields: {sorted(unknown)}"

    def test_run_session_has_no_long_summary(self, metadata: dict[str, Any]) -> None:
        # The specification defines long_summary for create_job and
        # start_application but not for run_session. One was present on
        # 2026-08-28 when the deployment into project test-rfp-deploy failed.
        for task in tasks_of_type(metadata, "run_session"):
            assert "long_summary" not in task

    def test_the_runtime_block_does_not_pin_a_version(
        self, metadata: dict[str, Any]
    ) -> None:
        # The other half of the 2026-08-28 failure: version: "2026.04" matched
        # nothing, because the workspace's runtime catalog listed only
        # 2026.04.1-b7. A runtime block that matches nothing stops the AMP before
        # a session exists, so there is no log to read — the worst way to fail.
        # version is optional; leaving it out lets the workspace supply it, and
        # is what Cloudera's own published AMPs do.
        for runtime in metadata["runtimes"]:
            assert "version" not in runtime, (
                "pinning a runtime version makes this AMP deploy only into "
                "workspaces holding that exact build"
            )

    def test_every_runtime_names_the_three_required_fields(
        self, metadata: dict[str, Any]
    ) -> None:
        for runtime in metadata["runtimes"]:
            assert set(runtime) >= {"editor", "kernel", "edition"}

    def test_the_runtime_kernel_satisfies_requires_python(
        self, metadata: dict[str, Any], project_root: Path
    ) -> None:
        # pyproject.toml sets requires-python = ">=3.11". A runtime below that
        # installs fine and then fails at `pip install -e .`.
        pyproject = (project_root / "pyproject.toml").read_text()
        assert 'requires-python = ">=3.11"' in pyproject
        for runtime in metadata["runtimes"]:
            major, minor = runtime["kernel"].removeprefix("Python ").split(".")[:2]
            assert (int(major), int(minor)) >= (3, 11), runtime["kernel"]
