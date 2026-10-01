"""The Cloudera AI Application launcher must survive a runtime with no __file__.

The bug being guarded: Cloudera AI starts an Application by exec'ing the script
through a PBJ/IPython kernel, which does not define __file__. On 2026-08-28 the
AMP deployment reached the start_application task and died there with
`NameError: name '__file__' is not defined` / `Engine exited with status 1`,
because launch_app.py derived the project root from __file__ while run_job.py
and app.py already worked around its absence.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rfp_intake.config.paths import find_project_root

LAUNCHER = find_project_root() / "launch_app.py"


def run_launcher(
    monkeypatch, tmp_path: Path, port: str | None, app_file: str | None = None
) -> dict:
    """Exec the launcher the way Cloudera AI does and capture the command.

    The globals deliberately omit __file__, and subprocess.run is replaced so
    Streamlit is never actually started.
    """
    captured: dict = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["cwd_at_call"] = Path.cwd()
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.chdir(tmp_path)
    if port is None:
        monkeypatch.delenv("CDSW_APP_PORT", raising=False)
    else:
        monkeypatch.setenv("CDSW_APP_PORT", port)
    if app_file is None:
        monkeypatch.delenv("RFP_INTAKE_APP_FILE", raising=False)
    else:
        monkeypatch.setenv("RFP_INTAKE_APP_FILE", app_file)

    source = LAUNCHER.read_text()
    namespace: dict = {"__name__": "__main__"}  # no __file__, as the kernel does
    exec(compile(source, str(LAUNCHER), "exec"), namespace)

    captured["namespace"] = namespace
    return captured


def test_launcher_runs_without_dunder_file(monkeypatch, tmp_path):
    """The launcher finds the project root with no __file__ and no NameError."""
    result = run_launcher(monkeypatch, tmp_path, "8100")
    assert result["namespace"]["project_root"] == find_project_root()


def test_launcher_runs_app_from_the_project_root(monkeypatch, tmp_path):
    """Streamlit is given app_v2.py and inherits the project root.

    Settings resolves config/ and runs/ against the working directory, so
    launching from anywhere else makes the application read the wrong config and
    write run folders the CML Job never looks in.

    app_v2.py is the page served by default — the one with the stage cards.
    """
    result = run_launcher(monkeypatch, tmp_path, "8100")
    root = find_project_root()
    assert result["command"][4] == str(root / "app_v2.py")
    assert result["cwd_at_call"] == root


def test_rollback_variable_serves_the_previous_page(monkeypatch, tmp_path):
    """RFP_INTAKE_APP_FILE=app.py returns the page served before the stage cards.

    This is the whole rollback path: a variable in Project Settings and a restart,
    with no code change and no revert. If this breaks, rolling back needs a
    deployment.
    """
    result = run_launcher(monkeypatch, tmp_path, "8100", app_file="app.py")
    assert result["command"][4] == str(find_project_root() / "app.py")


def test_launcher_refuses_a_page_that_does_not_exist(monkeypatch, tmp_path):
    """A typo in RFP_INTAKE_APP_FILE stops the launcher and names the path.

    Letting Streamlit start on a missing file serves an error page that the
    Application's own log does not explain, which is the hardest kind of
    deployment failure to diagnose — see the 2026-08-28 AMP failure in the
    module docstring above.
    """
    with pytest.raises(SystemExit) as caught:
        run_launcher(monkeypatch, tmp_path, "8100", app_file="not_a_page.py")
    message = str(caught.value)
    assert "not_a_page.py" in message
    assert str(find_project_root()) in message


@pytest.mark.parametrize(
    ("port", "expected"),
    [("8100", "--server.port=8100"), ("8090", "--server.port=8090"), (None, "--server.port=8100")],
    ids=["cdsw-port", "other-port", "unset-falls-back"],
)
def test_launcher_binds_the_port_cml_assigned(monkeypatch, tmp_path, port, expected):
    """CML routes the Application to CDSW_APP_PORT; binding elsewhere is a 502."""
    result = run_launcher(monkeypatch, tmp_path, port)
    assert expected in result["command"]


def test_launcher_binds_loopback_only(monkeypatch, tmp_path):
    """CML proxies to 127.0.0.1; Streamlit must not pick its own address."""
    result = run_launcher(monkeypatch, tmp_path, "8100")
    assert "--server.address=127.0.0.1" in result["command"]
    assert "--server.headless=true" in result["command"]
