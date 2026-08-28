"""Table-driven tests for project-root discovery.

The bug being guarded: run_job.py, app.py and launch_app.py hardcoded
/home/cdsw/rfp-intake-agent, which is true only because this repository was
cloned into a subfolder by hand. A Cloudera AI project deployed from
.project-metadata.yaml clones the repository into /home/cdsw itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rfp_intake.config.paths import (
    ProjectRootNotFoundError,
    find_project_root,
    looks_like_project_root,
)


def make_project(root: Path) -> Path:
    """Create the two marker files that identify this repository."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "run_job.py").write_text("")
    (root / "config").mkdir(exist_ok=True)
    (root / "config" / "fields.yaml").write_text("")
    return root


class TestLooksLikeProjectRoot:
    def test_both_markers_present(self, tmp_path: Path) -> None:
        assert looks_like_project_root(make_project(tmp_path / "repo"))

    def test_one_marker_is_not_enough(self, tmp_path: Path) -> None:
        # A sibling checkout of a fork would have run_job.py and nothing else.
        half = tmp_path / "half"
        half.mkdir()
        (half / "run_job.py").write_text("")
        assert not looks_like_project_root(half)

    def test_empty_directory(self, tmp_path: Path) -> None:
        assert not looks_like_project_root(tmp_path)


class TestFindProjectRoot:
    def test_repository_cloned_into_the_project_directory(self, tmp_path: Path) -> None:
        # The layout an AMP deployment produces: the repository IS /home/cdsw.
        home = make_project(tmp_path / "cdsw")
        assert find_project_root(start=home, home=home) == home

    def test_repository_cloned_into_a_subfolder(self, tmp_path: Path) -> None:
        # The layout this project was first set up with, and the one the
        # hardcoded paths assumed: /home/cdsw/rfp-intake-agent.
        home = tmp_path / "cdsw"
        home.mkdir()
        repo = make_project(home / "rfp-intake-agent")
        assert find_project_root(start=home, home=home) == repo

    def test_started_from_a_subdirectory_of_the_repository(self, tmp_path: Path) -> None:
        # Walking up from wherever the process happens to be.
        repo = make_project(tmp_path / "repo")
        deep = repo / "src" / "rfp_intake" / "render"
        deep.mkdir(parents=True)
        assert find_project_root(start=deep, home=tmp_path / "nonexistent") == repo

    def test_the_starting_directory_wins_over_the_project_directory(
        self, tmp_path: Path
    ) -> None:
        # Two checkouts on one machine must not make the answer arbitrary: the
        # one the process is standing in is the one it means.
        home = tmp_path / "cdsw"
        home.mkdir()
        other = make_project(home / "a-different-checkout")
        here = make_project(tmp_path / "here")
        assert find_project_root(start=here, home=home) == here
        assert other != here

    def test_subfolders_are_searched_in_a_stable_order(self, tmp_path: Path) -> None:
        home = tmp_path / "cdsw"
        home.mkdir()
        make_project(home / "zeta")
        first = make_project(home / "alpha")
        assert find_project_root(start=home, home=home) == first

    def test_not_found_names_what_it_looked_for(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(ProjectRootNotFoundError) as excinfo:
            find_project_root(start=empty, home=empty)
        message = str(excinfo.value)
        assert "run_job.py" in message
        assert "config/fields.yaml" in message
        assert str(empty) in message

    def test_a_missing_project_directory_is_not_an_error(self, tmp_path: Path) -> None:
        # An ordinary laptop has no /home/cdsw. Discovery must still work from
        # the checkout the process is standing in.
        repo = make_project(tmp_path / "repo")
        assert find_project_root(start=repo, home=Path("/no/such/directory")) == repo


class TestThisRepository:
    def test_finds_the_real_project_root(self) -> None:
        root = find_project_root(start=Path(__file__).parent)
        assert (root / "run_job.py").is_file()
        assert (root / "config" / "fields.yaml").is_file()
        assert (root / ".project-metadata.yaml").is_file()
