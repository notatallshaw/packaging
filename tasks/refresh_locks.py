# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the BSD License. See the LICENSE file in the root of this repository
# for complete details.

"""Refresh the shared development lock and the single-environment build lock."""

from __future__ import annotations

import json
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import nox

from packaging.requirements import Requirement
from tasks.update_downstream_groups import (
    MANIFEST,
    check_sources,
    project_text,
    read_inputs,
)

if TYPE_CHECKING:
    from collections.abc import Generator

ROOT = Path(__file__).resolve().parents[1]
BUILD_INPUT = ROOT / ".github/requirements/build.toml"


@contextmanager
def generated_project() -> Generator[Path, None, None]:
    """Keep compiled inputs beside the project with reproducible provenance."""
    text = project_text(read_inputs(json.loads(MANIFEST.read_text())))
    path = ROOT / ".pyproject-lock.toml"
    with path.open("x", encoding="utf-8") as temporary:
        try:
            temporary.write(text)
            # Nab must be able to open the file on Windows too.
            temporary.close()
            yield path
        finally:
            temporary.close()
            path.unlink(missing_ok=True)


def main() -> None:
    check_sources()
    project = nox.project.load_toml(ROOT / "pyproject.toml")
    build = nox.project.load_toml(BUILD_INPUT)
    tools = {Requirement(req) for req in build["project"]["dependencies"]}
    if not all(
        Requirement(req) in tools for req in project["build-system"]["requires"]
    ):
        raise ValueError("update build.toml to include packaging's build requirements")
    with generated_project() as path:
        groups = nox.project.load_toml(path)["dependency-groups"]
        contexts = [name for name in groups if name != "editable-build"]
        for arguments in (
            (str(path), "--groups", *contexts, "--output", "pylock.dev.toml"),
            (str(BUILD_INPUT), "--output", ".github/requirements/pylock.build.toml"),
        ):
            subprocess.run(
                [sys.executable, "-m", "nab", "lock", *arguments, *sys.argv[1:]],
                cwd=ROOT,
                check=True,
            )


if __name__ == "__main__":
    main()
