"""Locate the project root without hardcoding where the repository was cloned.

Every relative path in `Settings` — `config/fields.yaml`, `config/models.yaml`,
`runs/` — is resolved against the current working directory, so the entry points
have to change into the project root before anything else happens.

Until 2026-08-28 they did that by hardcoding `/home/cdsw/rfp-intake-agent`, which
is only true because this repository happens to have been cloned into a
subfolder by hand. A Cloudera AI project deployed from `.project-metadata.yaml`
clones the repository into the project root itself, so the files land in
`/home/cdsw` and every hardcoded path is wrong. Discovery keeps both layouts
working, and any other one the customer chooses.

Pure functions, no LLM, no side effects (CLAUDE.md rule 7).
"""

from __future__ import annotations

from pathlib import Path

# CML always mounts the project at this path. It is the fallback, not the answer:
# the answer is whichever candidate directory actually holds the repository.
CML_PROJECT_DIR = Path("/home/cdsw")

# Files that together identify this repository and nothing else. Both must be
# present: `run_job.py` alone would match a sibling checkout of a fork, and
# `config/` alone matches half the projects on any machine.
_MARKERS = (Path("run_job.py"), Path("config") / "fields.yaml")


class ProjectRootNotFoundError(RuntimeError):
    """The repository was not found in any of the directories searched."""


def looks_like_project_root(candidate: Path) -> bool:
    """Whether `candidate` holds this repository."""
    return all((candidate / marker).is_file() for marker in _MARKERS)


def find_project_root(
    start: Path | None = None,
    home: Path = CML_PROJECT_DIR,
) -> Path:
    """Return the directory holding this repository.

    Searched in order, first match wins:

    1. `start` (the current working directory by default) and each of its
       parents — covers running from anywhere inside a normal checkout.
    2. `home` — the Cloudera AI project directory, which is the root itself when
       the project was deployed from `.project-metadata.yaml`.
    3. Each immediate subdirectory of `home` — covers a repository cloned into a
       subfolder by hand, which is how this project was first set up.

    Raises ProjectRootNotFoundError naming everything it looked at, rather than
    returning a guess that would make Settings read the wrong config file.
    """
    searched: list[Path] = []

    for candidate in _candidates(start, home):
        if candidate in searched:
            continue
        searched.append(candidate)
        if looks_like_project_root(candidate):
            return candidate

    markers = ", ".join(str(marker) for marker in _MARKERS)
    looked = "\n  ".join(str(path) for path in searched)
    raise ProjectRootNotFoundError(
        f"Could not find the rfp-intake project root. Looked for a directory "
        f"containing both {markers} in:\n  {looked}"
    )


def _candidates(start: Path | None, home: Path) -> list[Path]:
    """Directories to test, in priority order."""
    begin = (start or Path.cwd()).resolve()
    candidates = [begin, *begin.parents]

    home = home.resolve() if home.exists() else home
    candidates.append(home)
    if home.is_dir():
        # Sorted so the choice is the same on every machine when, unusually,
        # two subfolders both hold a checkout.
        candidates.extend(sorted(child for child in home.iterdir() if child.is_dir()))

    return candidates
