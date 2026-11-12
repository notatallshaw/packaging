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
import tomli_w

from packaging.version import Version
from tasks.update_downstream_groups import (
    MANIFEST,
    check_sources,
    project_text,
    read_inputs,
)

if TYPE_CHECKING:
    from collections.abc import Generator

ROOT = Path(__file__).resolve().parents[1]


def _build_project_text() -> str:
    """Derive the build environment and backend requirements from pyproject.toml."""
    metadata = nox.project.load_toml(ROOT / "pyproject.toml")
    settings = metadata["tool"]["nab"]
    environment = metadata["tool"]["packaging"]["build-lock"]

    # The build lock serves one Python minor and platform.
    python = environment["python"]
    major, minor = Version(python).release

    dependencies = [
        *nox.project.dependency_groups(metadata, "build"),
        *metadata["build-system"]["requires"],
    ]

    project = {
        "name": "packaging-build-tools",
        "version": "0",
        "requires-python": f">={python},<{major}.{minor + 1}",
        "dependencies": dependencies,
    }
    policy = {
        "mode": "specific",
        "decision-order": settings["decision-order"],
        "uploaded-prior-to": settings["uploaded-prior-to"],
        "dist-policy": "wheel-only",
        "environment": environment,
    }
    return tomli_w.dumps({"project": project, "tool": {"nab": policy}})


@contextmanager
def generated_project(*, build: bool = False) -> Generator[Path, None, None]:
    """Keep compiled inputs beside the project with reproducible provenance."""
    text = (
        _build_project_text()
        if build
        else project_text(read_inputs(json.loads(MANIFEST.read_text())))
    )
    path = ROOT / ".pyproject-lock.toml"
    temporary = path.open("x", encoding="utf-8")
    try:
        with temporary:
            temporary.write(text)
        # Close the file before Nab opens it on Windows.
        yield path
    finally:
        path.unlink(missing_ok=True)


def main() -> None:
    check_sources()
    for build, output in ((False, "pylock.dev.toml"), (True, "pylock.build.toml")):
        with generated_project(build=build) as path:
            groups = nox.project.load_toml(path).get("dependency-groups", {})
            contexts = [name for name in groups if name != "editable-build"]
            selection = ("--groups", *contexts) if contexts else ()
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "nab",
                    "lock",
                    str(path),
                    *selection,
                    "--output",
                    output,
                    *sys.argv[1:],
                ],
                cwd=ROOT,
                check=True,
            )


if __name__ == "__main__":
    main()
