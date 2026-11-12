# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the BSD License. See the LICENSE file in the root of this repository
# for complete details.

"""Refresh the shared development lock and the single-environment build lock."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import nox

from packaging.requirements import Requirement
from tasks.update_downstream_groups import check_groups

ROOT = Path(__file__).resolve().parents[1]
BUILD_INPUT = ROOT / ".github/requirements/build.toml"


def main() -> None:
    check_groups()
    project = nox.project.load_toml(ROOT / "pyproject.toml")
    build = nox.project.load_toml(BUILD_INPUT)
    tools = {Requirement(req) for req in build["project"]["dependencies"]}
    if not all(
        Requirement(req) in tools for req in project["build-system"]["requires"]
    ):
        raise ValueError("update build.toml to include packaging's build requirements")
    contexts = [
        name for name in project["dependency-groups"] if name != "editable-build"
    ]
    for arguments in (
        ("pyproject.toml", "--groups", *contexts, "--output", "pylock.dev.toml"),
        (str(BUILD_INPUT), "--output", ".github/requirements/pylock.build.toml"),
    ):
        subprocess.run(
            [sys.executable, "-m", "nab", "lock", *arguments, *sys.argv[1:]],
            cwd=ROOT,
            check=True,
        )


if __name__ == "__main__":
    main()
