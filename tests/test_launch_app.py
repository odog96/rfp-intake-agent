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


def run_launcher(monkeypatch, tmp_path: Path, port: str | None) -> dict:
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
    """Streamlit is given the project's app.py and inherits the project root.

    Settings resolves config/ and runs/ against the working directory, so
    launching from anywhere else makes the application read the wrong config and
    write run folders the CML Job never looks in.
    """
    result = run_launcher(monkeypatch, tmp_path, "8100")
    root = find_project_root()
    assert result["command"][4] == str(root / "app.py")
    assert result["cwd_at_call"] == root


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
