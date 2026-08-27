"""Table-driven tests for working out which run the CML Job should process."""

from __future__ import annotations

import pytest

from rfp_intake.job.args import RUN_ID_ENV_VAR, MissingRunIdError, resolve_run_id


class TestResolveRunId:
    def test_environment_variable_wins(self) -> None:
        got = resolve_run_id(["run_job.py", "from-argv"], {RUN_ID_ENV_VAR: "from-env"})
        assert got == "from-env"

    def test_plain_command_line_still_works(self) -> None:
        assert resolve_run_id(["run_job.py", "r-123"], {}) == "r-123"

    def test_skips_the_ipython_connection_file(self) -> None:
        # The exact shape that produced "No inputs directory: runs/-f/inputs"
        # on 2026-08-27.
        argv = ["run_job.py", "-f", "/root/.local/share/jupyter/kernel-9f2.json"]
        with pytest.raises(MissingRunIdError):
            resolve_run_id(argv, {})

    def test_finds_the_run_id_after_ipython_arguments(self) -> None:
        argv = ["run_job.py", "-f", "/tmp/kernel-9f2.json", "r-456"]
        assert resolve_run_id(argv, {}) == "r-456"

    def test_blank_environment_variable_falls_through_to_argv(self) -> None:
        assert resolve_run_id(["run_job.py", "r-789"], {RUN_ID_ENV_VAR: "   "}) == "r-789"

    def test_no_run_id_anywhere_names_both_channels(self) -> None:
        with pytest.raises(MissingRunIdError) as exc:
            resolve_run_id(["run_job.py"], {})
        assert RUN_ID_ENV_VAR in str(exc.value)
        assert "run_job.py <run_id>" in str(exc.value)

    def test_a_run_id_is_never_a_flag(self) -> None:
        # Guards the original bug directly: "-f" must never be returned as a run id.
        argv = ["run_job.py", "-f", "/tmp/kernel.json"]
        with pytest.raises(MissingRunIdError):
            resolve_run_id(argv, {})
