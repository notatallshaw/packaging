# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the BSD License. See the LICENSE file in the root of this repository
# for complete details.

"""Import test requirements from the source releases used by downstream sessions."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import tarfile
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

import nox

from noxfile import PROJECTS, Project, download_project
from packaging.markers import Marker
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tasks/downstream-lock-inputs.json"
TARGET = (
    'python_version == "3.14" and sys_platform == "linux"'
    ' and platform_machine == "x86_64" and implementation_name == "cpython"'
)
DEV_GROUPS = ("test", "docs", "benchmark", "lint", "nox", "locks", "licenses")


class SourceSnapshot(TypedDict):
    """The source identity and dependency declarations behind one generated group."""

    url: str
    sha256: str
    install: list[str]
    pytest_before_9_1: bool
    editable_requires: list[str]
    requirements: list[str]


class LockInputs(TypedDict):
    """The pinned sources and wheel fixtures used by the downstream sessions."""

    projects: dict[str, SourceSnapshot]
    pip_common_wheels: list[str]


def _strings(value: object) -> list[str]:
    """Read a requirement or argument list from the stored source manifest."""
    if not isinstance(value, list):
        raise TypeError("expected a list of strings in downstream lock inputs")
    result = []
    for item in value:
        if not isinstance(item, str):
            raise TypeError("expected a string in downstream lock inputs")
        result.append(item)
    return result


def read_inputs(value: object) -> LockInputs:
    """Validate the persisted source identities before using their requirements."""
    if not isinstance(value, dict):
        raise TypeError("expected a downstream lock input table")
    sources = value.get("projects")
    if not isinstance(sources, dict):
        raise TypeError("expected downstream project inputs")
    projects: dict[str, SourceSnapshot] = {}
    for name, source in sources.items():
        if not isinstance(name, str) or not isinstance(source, dict):
            raise TypeError("expected a named downstream project table")
        url, sha256 = source.get("url"), source.get("sha256")
        old_pytest = source.get("pytest_before_9_1")
        if not isinstance(url, str) or not isinstance(sha256, str):
            raise TypeError("expected a source URL and SHA-256 hash")
        if re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
            raise ValueError("invalid downstream source SHA-256 hash")
        if not isinstance(old_pytest, bool):
            raise TypeError("expected a pytest compatibility selection")
        projects[name] = {
            "url": url,
            "sha256": sha256,
            "install": _strings(source.get("install")),
            "pytest_before_9_1": old_pytest,
            "editable_requires": _strings(source.get("editable_requires", [])),
            "requirements": _strings(source.get("requirements")),
        }
    return {
        "projects": projects,
        "pip_common_wheels": _strings(value.get("pip_common_wheels")),
    }


def _local_requirements(
    tree: Path,
    configuration: Project,
) -> tuple[dict[str, dict[str, list[str]]], list[str]]:
    """Read dependencies and extras from each local package installed by a session."""
    projects: dict[str, dict[str, list[str]]] = {}
    selected: list[str] = []
    for argument in configuration.sources:
        path, _, extras = argument[2:].partition("[")
        metadata = nox.project.load_toml(tree / path / "pyproject.toml")
        project = metadata["project"]
        if "dependencies" in project.get("dynamic", []):
            message = f"{project['name']} needs dynamic dependency metadata"
            raise ValueError(message)
        name = canonicalize_name(project["name"])
        projects[name] = project.get("optional-dependencies", {})
        selected.extend(project.get("dependencies", []))
        selected.extend(metadata.get("build-system", {}).get("requires", []))
        for extra in extras.removesuffix("]").split(",") if extras else ():
            selected.extend(project["optional-dependencies"][extra])
    return projects, selected


def _test_requirements(tree: Path, configuration: Project) -> list[str]:
    """Read the groups, requirements files and explicit tools in an install command."""
    selected: list[str] = []
    args = iter(configuration.install)
    metadata = nox.project.load_toml(tree / "pyproject.toml")
    for argument in args:
        if argument.startswith("-e."):
            continue
        if argument.startswith("--group="):
            selected.extend(nox.project.dependency_groups(metadata, argument[8:]))
        elif argument == "-r":
            selected.extend(
                line.strip()
                for line in (tree / next(args)).read_text().splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            )
        else:
            selected.append(argument)
    return selected


def requirements(tree: Path, configuration: Project) -> list[str]:
    """Expand static dependencies, groups and source-local extras for a session."""
    projects, selected = _local_requirements(tree, configuration)
    selected.extend(_test_requirements(tree, configuration))

    result = []
    expanded = set()
    for item in selected:
        requirement = Requirement(item)
        local = projects.get(canonicalize_name(requirement.name))
        if local is None:
            result.append(str(requirement))
            continue
        # A source group can select its own extras, as pdm's test group does.
        if requirement.marker is not None and requirement.extras:
            message = "conditional source-local extras need prepared build metadata"
            raise ValueError(message)
        for extra in requirement.extras:
            key = (canonicalize_name(requirement.name), extra)
            if key not in expanded:
                expanded.add(key)
                selected.extend(local[extra])
    return sorted(set(result))


def target_requirement(text: str) -> str:
    """Limit a downstream root to the environment in which CI runs that suite."""
    requirement = Requirement(text)
    marker = f"({requirement.marker}) and ({TARGET})" if requirement.marker else TARGET
    requirement.marker = Marker(marker)
    return str(requirement)


def group_name(project: str) -> str:
    return "downstream-" + canonicalize_name(project)


def import_sources(names: Sequence[str] | None = None) -> LockInputs:
    """Capture dependencies and hashes from the same archives Nox downloads."""
    previous: LockInputs = {"projects": {}, "pip_common_wheels": []}
    if names is not None:
        previous = read_inputs(json.loads(MANIFEST.read_text()))
    projects = {
        name: entry for name, entry in previous["projects"].items() if name in PROJECTS
    }
    common_wheels = previous["pip_common_wheels"]
    for name in PROJECTS if names is None else names:
        cfg = PROJECTS[name]
        data = download_project(cfg)
        with tempfile.TemporaryDirectory(prefix="packaging-downstream-lock-") as temp:
            destination = Path(temp)
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
                archive.extractall(destination, filter="data")
            (tree,) = destination.iterdir()
            roots = requirements(tree, cfg)
            roots.extend(cfg.editable_requires)
            if cfg.pytest_before_9_1:
                roots.append("pytest<9.1")
            if name == "pip":
                metadata = nox.project.load_toml(tree / "pyproject.toml")
                common_wheels = list(
                    nox.project.dependency_groups(metadata, "test-common-wheels")
                )
        projects[name] = {
            "url": cfg.url,
            "sha256": hashlib.sha256(data).hexdigest(),
            "install": list(cfg.install),
            "pytest_before_9_1": cfg.pytest_before_9_1,
            "editable_requires": list(cfg.editable_requires),
            "requirements": roots,
        }
        print(f"Imported {name}")
    return {"projects": projects, "pip_common_wheels": common_wheels}


def _downstream_groups(manifest: LockInputs, build: list[str]) -> dict[str, list[str]]:
    """Project each source and its current-source build backend onto its CI target."""
    groups = {}
    for name, entry in manifest["projects"].items():
        groups[group_name(name)] = [
            target_requirement(req) for req in [*entry["requirements"], *build]
        ]
    groups["downstream-pip-wheels"] = [
        target_requirement(req) for req in manifest["pip_common_wheels"]
    ]
    return groups


def _development_requirements(groups: Mapping[str, object]) -> list[str]:
    """Combine compatible development contexts without selecting exclusive groups."""
    return sorted(
        set(nox.project.dependency_groups({"dependency-groups": groups}, *DEV_GROUPS))
    )


def _render_groups(groups: Mapping[str, list[str | dict[str, str]]]) -> str:
    """Write the PEP 735 table while leaving other project configuration intact."""
    lines = ["[dependency-groups]"]
    for name, values in groups.items():
        lines.append(f"{name} = [")
        for value in values:
            if isinstance(value, str):
                rendered = json.dumps(value)
            else:
                rendered = (
                    "{ include-group = " + json.dumps(value["include-group"]) + " }"
                )
            lines.append(f"  {rendered},")
        lines.append("]")
    return "\n".join(lines) + "\n\n"


def project_text(manifest: LockInputs) -> str:
    """Combine project groups, source snapshots and target policy for locking."""
    path = ROOT / "pyproject.toml"
    metadata = nox.project.load_toml(path)
    groups = {
        name: values
        for name, values in metadata["dependency-groups"].items()
        if not name.startswith("downstream-")
    }
    groups["dev"] = _development_requirements(groups)
    groups.update(_downstream_groups(manifest, metadata["build-system"]["requires"]))
    text, count = re.subn(
        r"(?ms)^\[dependency-groups\]\n.*?(?=^\[|\Z)",
        lambda _: _render_groups(groups),
        path.read_text(),
        count=1,
    )
    if count != 1:
        raise ValueError("expected an explicit [dependency-groups] table")
    policy = (ROOT / ".github/requirements/dev.toml").read_text()
    return text + policy


def check_sources() -> None:
    """Refuse source snapshots that no longer match the downstream sessions."""
    manifest = read_inputs(json.loads(MANIFEST.read_text()))
    if set(manifest["projects"]) != set(PROJECTS):
        raise ValueError("downstream projects changed; refresh source snapshots")
    for name, cfg in PROJECTS.items():
        entry = manifest["projects"][name]
        if (
            entry["url"] != cfg.url
            or entry["install"] != list(cfg.install)
            or entry["pytest_before_9_1"] != cfg.pytest_before_9_1
            or entry["editable_requires"] != list(cfg.editable_requires)
        ):
            message = f"{name} changed; refresh its source snapshot"
            raise ValueError(message)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--project", action="append", choices=sorted(PROJECTS))
    args = parser.parse_args()
    if args.check:
        check_sources()
    elif args.render:
        check_sources()
        print(project_text(read_inputs(json.loads(MANIFEST.read_text()))), end="")
    else:
        manifest = import_sources(args.project)
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
