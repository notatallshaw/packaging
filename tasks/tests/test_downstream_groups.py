# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the BSD License. See the LICENSE file in the root of this repository
# for complete details.

"""Check the dependency declarations and source identities used by downstream locks."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import tarfile
import urllib.request
from contextlib import nullcontext
from typing import TYPE_CHECKING

import nox
import pytest
import tomli_w

import noxfile
from noxfile import Project
from packaging.requirements import Requirement
from tasks import refresh_locks as refresh
from tasks import update_downstream_groups as groups

if TYPE_CHECKING:
    from pathlib import Path


def _project(path: Path, text: str) -> None:
    """Write an actual local project declaration for the input reader."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "pyproject.toml").write_text(text)


def test_groups_local_extras_and_build_requirements(tmp_path: Path) -> None:
    _project(
        tmp_path,
        """
[project]
name = "Example_Source"
version = "1"
dependencies = ["packaging>=24"]
[project.optional-dependencies]
test = ["pytest>=8", "pytest-mock"]
[dependency-groups]
test = ["example-source[test]", {include-group="coverage"}]
coverage = ["coverage[toml]"]
[build-system]
requires = ["hatchling"]
""",
    )
    config = Project(
        "https://github.com/example/source", install=("-e.", "--group=test", "filelock")
    )
    requirements = {Requirement(item) for item in groups.requirements(tmp_path, config)}
    assert requirements == {
        Requirement(item)
        for item in (
            "packaging>=24",
            "pytest>=8",
            "pytest-mock",
            "coverage[toml]",
            "hatchling",
            "filelock",
        )
    }


def test_monorepo_sources_and_requirements_file(tmp_path: Path) -> None:
    _project(
        tmp_path,
        """
[project]
name="frontend"
version="1"
dependencies=["backend>=1", "requests"]
[build-system]
requires=["hatchling"]
""",
    )
    _project(
        tmp_path / "backend",
        """
[project]
name="backend"
version="1"
dependencies=["pluggy"]
[project.optional-dependencies]
test=["pytest-mock"]
[build-system]
requires=[]
""",
    )
    (tmp_path / "requirements.txt").write_text("# Test tools\n\npytest>=8\n")
    config = Project(
        "https://github.com/example/source",
        install=("-e.", "-e./backend[test]", "-r", "requirements.txt"),
    )
    assert set(groups.requirements(tmp_path, config)) == {
        "requests",
        "hatchling",
        "pluggy",
        "pytest-mock",
        "pytest>=8",
    }


@pytest.mark.parametrize(
    "declaration",
    [
        'dynamic=["dependencies"]',
        "dependencies=[\"source[test]; python_version < '3.12'\"]\n"
        '[project.optional-dependencies]\ntest=["pytest"]',
    ],
)
def test_unprepared_dynamic_or_conditional_source_metadata_is_refused(
    tmp_path: Path,
    declaration: str,
) -> None:
    _project(tmp_path, '[project]\nname="source"\nversion="1"\n' + declaration)
    config = Project("https://github.com/example/source", install=("-e.",))
    with pytest.raises(ValueError, match=r"dependencies|metadata"):
        groups.requirements(tmp_path, config)


@pytest.mark.parametrize(
    ("python", "platform", "machine", "expected"),
    [
        ("3.14", "linux", "x86_64", True),
        ("3.14", "win32", "AMD64", False),
        ("3.10", "linux", "x86_64", False),
        ("3.14", "linux", "aarch64", False),
    ],
)
def test_downstream_roots_keep_their_original_markers(
    python: str,
    platform: str,
    machine: str,
    expected: bool,
) -> None:
    requirement = Requirement(
        groups.target_requirement('pytest; python_version >= "3.12"')
    )
    assert requirement.marker is not None
    assert (
        requirement.marker.evaluate(
            {
                "python_version": python,
                "sys_platform": platform,
                "platform_machine": machine,
            }
        )
        is expected
    )


def test_import_records_archive_hashes_and_common_wheel_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"""[project]
name="pip"
version="1"
[dependency-groups]
test=["pytest"]
test-common-wheels=["setuptools<80"]
"""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        entry = tarfile.TarInfo("source/pyproject.toml")
        entry.size = len(content)
        archive.addfile(entry, io.BytesIO(content))
    data = buffer.getvalue()
    config = Project("https://github.com/example/source", pytest_before_9_1=True)
    monkeypatch.setattr(groups, "PROJECTS", {"pip": config})

    def download(configuration: Project) -> bytes:
        assert configuration is config
        return data

    monkeypatch.setattr(groups, "download_project", download)
    inputs = groups.import_sources()
    assert inputs["projects"]["pip"]["sha256"] == hashlib.sha256(data).hexdigest()
    assert set(inputs["projects"]["pip"]["requirements"]) == {"pytest", "pytest<9.1"}
    assert inputs["pip_common_wheels"] == ["setuptools<80"]


@pytest.fixture
def snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> groups.LockInputs:
    """Use a real project table and a matching persisted source snapshot."""
    config = Project("https://github.com/example/source")
    inputs: groups.LockInputs = {
        "projects": {
            "tox": {
                "url": config.url,
                "sha256": "a" * 64,
                "install": list(config.install),
                "pytest_before_9_1": False,
                "editable_requires": [],
                "requirements": ["setuptools>=81,<82"],
            }
        },
        "pip_common_wheels": ["setuptools<80"],
    }
    declarations = {
        "test": ["pytest"],
        "docs": ["sphinx"],
        "benchmark": ["asv"],
        "lint": ["twine"],
        "nox": ["nox"],
        "locks": ["nab"],
        "licenses": ["httpx"],
    }
    metadata = {
        "project": {"name": "example", "version": "1", "dependencies": ["requests"]},
        "build-system": {"requires": ["flit_core>=3.12"]},
        "dependency-groups": {
            **declarations,
            "dev": [{"include-group": name} for name in declarations],
            "build": ["build", "twine", "nox"],
        },
        "tool": {
            "example": {"value": 1},
            "nab": {
                "default-groups": ["dev"],
                "decision-order": "stable",
                "uploaded-prior-to": "P7D",
            },
            "packaging": {"build-lock": {"python": "3.14", "platform": "linux_x86_64"}},
        },
    }
    _project(tmp_path, tomli_w.dumps(metadata))
    monkeypatch.setattr(groups, "ROOT", tmp_path)
    monkeypatch.setattr(groups, "MANIFEST", tmp_path / "inputs.json")
    monkeypatch.setattr(groups, "PROJECTS", {"tox": config})
    monkeypatch.setattr(refresh, "ROOT", tmp_path)
    monkeypatch.setattr(refresh, "MANIFEST", groups.MANIFEST)
    groups.MANIFEST.write_text(json.dumps(inputs))
    return inputs


@pytest.mark.usefixtures("snapshot")
def test_generated_groups_preserve_project_metadata_and_close_build_inputs() -> None:
    source = (groups.ROOT / "pyproject.toml").read_bytes()
    with refresh.generated_project() as path:
        metadata = nox.project.load_toml(path)
    assert metadata["project"]["dependencies"] == ["requests"]
    assert metadata["tool"]["example"]["value"] == 1
    assert set(metadata["dependency-groups"]["dev"]) == {
        "pytest",
        "sphinx",
        "asv",
        "twine",
        "nox",
        "nab",
        "httpx",
    }
    assert "build" not in metadata["dependency-groups"]
    assert {member["group"] for member in metadata["tool"]["nab"]["conflicts"][0]} == {
        "test",
        "docs",
        "benchmark",
        "lint",
        "nox",
        "locks",
        "licenses",
        "dev",
        "downstream-tox",
        "downstream-pip-wheels",
    }
    downstream = metadata["dependency-groups"]["downstream-tox"]
    assert {Requirement(req).name for req in downstream} == {"setuptools", "flit_core"}
    assert metadata["tool"]["nab"]["default-groups"] == ["dev"]
    assert (groups.ROOT / "pyproject.toml").read_bytes() == source
    groups.check_sources()


@pytest.mark.parametrize("change", ["source", "install", "pytest"])
def test_stale_source_selections_are_refused(
    snapshot: groups.LockInputs,
    change: str,
) -> None:
    inputs = copy.deepcopy(snapshot)
    source = inputs["projects"]["tox"]
    if change == "source":
        source["url"] += "/different"
    elif change == "install":
        source["install"].append("pytest")
    elif change == "pytest":
        source["pytest_before_9_1"] = True
    groups.MANIFEST.write_text(json.dumps(inputs))
    with pytest.raises(ValueError, match="changed"):
        groups.check_sources()


@pytest.mark.parametrize("change", ["requirements", "wheels", "dev"])
def test_changed_inputs_reach_the_generated_project(
    snapshot: groups.LockInputs, change: str
) -> None:
    inputs = copy.deepcopy(snapshot)
    if change == "requirements":
        inputs["projects"]["tox"]["requirements"].append("wheel")
        group, expected = "downstream-tox", "wheel"
    elif change == "wheels":
        inputs["pip_common_wheels"].append("wheel")
        group, expected = "downstream-pip-wheels", "wheel"
    else:
        path = groups.ROOT / "pyproject.toml"
        metadata = nox.project.load_toml(path)
        metadata["dependency-groups"]["test"] = ["pytest>=9"]
        path.write_text(tomli_w.dumps(metadata))
        group, expected = "dev", "pytest>=9"
    groups.MANIFEST.write_text(json.dumps(inputs))
    groups.check_sources()
    with refresh.generated_project() as path:
        selected = nox.project.load_toml(path)["dependency-groups"][group]
    assert any(str(Requirement(req)).split(";")[0] == expected for req in selected)


@pytest.mark.usefixtures("snapshot")
def test_build_inputs_use_the_declared_tools_backend_and_environment() -> None:
    path = groups.ROOT / "pyproject.toml"
    metadata = nox.project.load_toml(path)
    metadata["build-system"]["requires"] = ["hatchling>=1.30"]
    metadata["tool"]["packaging"]["build-lock"]["python"] = "3.15"
    path.write_text(tomli_w.dumps(metadata))

    with refresh.generated_project(build=True) as compiled:
        result = nox.project.load_toml(compiled)

    assert result["project"]["requires-python"] == ">=3.15,<3.16"
    assert result["project"]["dependencies"] == [
        "build",
        "twine",
        "nox",
        "hatchling>=1.30",
    ]
    assert result["tool"]["nab"]["environment"] == {
        "python": "3.15",
        "platform": "linux_x86_64",
    }
    assert result["tool"]["nab"]["dist-policy"] == "wheel-only"
    assert "matrix" not in result["tool"]["nab"]


@pytest.mark.usefixtures("snapshot")
def test_new_contexts_are_isolated_without_a_second_configuration() -> None:
    path = groups.ROOT / "pyproject.toml"
    metadata = nox.project.load_toml(path)
    metadata["dependency-groups"]["extra-check"] = ["wheel"]
    path.write_text(tomli_w.dumps(metadata))

    with refresh.generated_project() as compiled:
        result = nox.project.load_toml(compiled)

    assert {"group": "extra-check"} in result["tool"]["nab"]["conflicts"][0]


@pytest.mark.usefixtures("snapshot")
@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("build", [False, True])
def test_compiled_inputs_are_removed_after_the_lock_operation(
    fail: bool, build: bool
) -> None:
    context = pytest.raises(RuntimeError) if fail else nullcontext()
    generated: Path | None = None
    with context, refresh.generated_project(build=build) as path:
        generated = path
        assert path.parent == groups.ROOT
        assert path.is_file()
        if fail:
            raise RuntimeError("lock failed")
    assert generated is not None
    assert not generated.exists()


@pytest.mark.usefixtures("snapshot")
def test_compiled_input_path_is_stable_between_refreshes() -> None:
    with refresh.generated_project() as first:
        contents = first.read_bytes()
    with refresh.generated_project() as second:
        assert second == first
        assert second.read_bytes() == contents


@pytest.mark.usefixtures("snapshot")
def test_compilation_does_not_overwrite_an_existing_file() -> None:
    with refresh.generated_project() as path:
        pass
    path.write_text("existing file")
    with pytest.raises(FileExistsError), refresh.generated_project():
        pass
    assert path.read_text() == "existing file"


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {},
        {"projects": []},
        {
            "projects": {"tox": {"url": "https://example.invalid", "sha256": "bad"}},
            "pip_common_wheels": [],
        },
    ],
)
def test_invalid_source_snapshots_are_refused(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        groups.read_inputs(value)


@pytest.mark.parametrize("mismatch", [None, "archive", "url"])
def test_runtime_archive_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str | None,
) -> None:
    data = b"a source archive"
    config = Project("https://github.com/example/source")
    path = tmp_path / "tasks/downstream-lock-inputs.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "projects": {
                    "example": {
                        "url": config.url,
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                }
            }
        )
    )
    monkeypatch.setattr(noxfile, "ROOT", tmp_path)
    if mismatch == "archive":
        data += b"changed"
    elif mismatch == "url":
        config = Project("https://github.com/example/different")
    if mismatch is None:
        noxfile.verify_project_source("example", config, data)
    else:
        with pytest.raises(ValueError, match="source changed"):
            noxfile.verify_project_source("example", config, data)


@pytest.mark.parametrize("binary", [True, False])
def test_archive_download_requires_a_byte_stream(
    monkeypatch: pytest.MonkeyPatch,
    binary: bool,
) -> None:
    config = Project("https://github.com/example/source")

    def open_source(url: str) -> io.BytesIO | io.StringIO:
        assert url == config.url
        return io.BytesIO(b"archive") if binary else io.StringIO("archive")

    monkeypatch.setattr(urllib.request, "urlopen", open_source)
    if binary:
        assert noxfile.download_project(config) == b"archive"
    else:
        with pytest.raises(TypeError, match="archive bytes"):
            noxfile.download_project(config)
