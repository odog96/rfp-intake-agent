"""The commit gate's must-hold rule, approved by Oliver on 2026-10-13.

`.claude/hooks/gate_commit.py` denies a commit whose command names a run (an id
with a folder under runs/) where `scripts/check_must_hold.py` exits 1. Exit 2,
any other exit and a check that cannot run do not block. The hook's own
decision (`main`) needs a Claude Code event and a repository named
rfp-intake-agent, so these tests drive the two functions it calls.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def gate(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(ROOT / ".claude" / "hooks"))
    import gate_commit

    # The log is the user's, under ~/.claude; nothing here may write to it.
    monkeypatch.setattr(gate_commit.gate_state, "log", lambda message: None)
    return gate_commit


def _repo(tmp_path: Path, exits: dict[str, int]) -> Path:
    """A repository whose must-hold check exits as `exits` says for each run."""
    for run_id in exits:
        (tmp_path / "runs" / run_id).mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "check_must_hold.py").write_text(
        "import sys\n"
        f"exits = {exits!r}\n"
        "code = exits[sys.argv[1]]\n"
        "if code == 1:\n"
        "    print('PASS  1. a fact')\n"
        "    print('FAIL  4. Number of interim analyses is a disagreement')\n"
        "sys.exit(code)\n"
    )
    return tmp_path


class TestNamedRuns:
    def test_only_ids_with_a_run_folder_count(self, gate: ModuleType, tmp_path: Path) -> None:
        root = _repo(tmp_path, {"r-20261013-prompt-a": 0, "r-20261012-angus2": 1})
        command = ("git commit -q -m 'Shown on r-20261013-prompt-a, against r-20261012-angus2; "
                   "not r-none.'")
        assert gate.named_runs(command, root) == ["r-20261013-prompt-a", "r-20261012-angus2"]

    def test_a_run_named_twice_is_checked_once(self, gate: ModuleType, tmp_path: Path) -> None:
        root = _repo(tmp_path, {"r-1": 0})
        assert gate.named_runs("git commit -m 'r-1 and r-1 again'", root) == ["r-1"]

    def test_a_longer_id_is_not_read_as_a_shorter_one(
        self, gate: ModuleType, tmp_path: Path
    ) -> None:
        root = _repo(tmp_path, {"r-20261012-angus": 0})
        assert gate.named_runs("git commit -m 'r-20261012-angus2'", root) == []


class TestFailingRuns:
    def test_exit_1_blocks_and_names_the_fact(self, gate: ModuleType, tmp_path: Path) -> None:
        root = _repo(tmp_path, {"r-bad": 1})
        assert gate.failing_runs(["r-bad"], root) == [
            ("r-bad", ["FAIL  4. Number of interim analyses is a disagreement"]),
        ]

    @pytest.mark.parametrize("code", [0, 2, 3])
    def test_any_other_exit_does_not_block(
        self, gate: ModuleType, tmp_path: Path, code: int
    ) -> None:
        root = _repo(tmp_path, {"r-x": code})
        assert gate.failing_runs(["r-x"], root) == []

    def test_a_check_that_cannot_run_does_not_block(
        self, gate: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _repo(tmp_path, {"r-x": 1})
        monkeypatch.setattr(gate, "MUST_HOLD_TIMEOUT", 0.0)
        assert gate.failing_runs(["r-x"], root) == []

    def test_the_real_check_on_a_saved_run(self, gate: ModuleType) -> None:
        # r-20261012-angus2 fails fact 4: no disagreement on the interim count.
        if not (ROOT / "runs" / "r-20261012-angus2" / "extraction.json").exists():
            pytest.skip("run r-20261012-angus2 is not saved here")
        [(run_id, fails)] = gate.failing_runs(["r-20261012-angus2"], ROOT)
        assert run_id == "r-20261012-angus2"
        assert any(f.startswith("FAIL  4.") for f in fails)

