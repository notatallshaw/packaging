from __future__ import annotations

from collections import UserDict
from typing import TYPE_CHECKING

import pytest

import packaging.dependency_sources
from packaging.dependency_sources import (
    DependencySource,
    InvalidDependencySource,
    parse_dependency_sources,
    select_dependency_source,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


@pytest.mark.parametrize(
    "table",
    [
        None,
        [],
        {1: "x"},
        {"not a name": "x"},
        {"Foo_Bar": "x", "foo-bar": "y"},
        {"core": []},
        {"core": ""},
        {"core": 1},
        {"core": {"path": "x", "url": "https://example.org/x.zip"}},
        {"core": {"path": "x", "unknown": True}},
        {"core": {"path": "x", 1: True, "unknown": True}},
        {"core": {"path": "x", "extra": "test", "group": "test"}},
        {"core": {"git": "https://example.org/core", "rev": "x", "tag": "y"}},
        {"core": {"hg": "https://example.org/core", "branch": "main"}},
        {"core": {"git": "git+https://example.org/core"}},
        {"core": {"path": "x", "editable": "yes"}},
        {"core": {"path": "x", "marker": "invalid marker"}},
        {"core": {"path": "x", "extra": ""}},
        {"core": {"path": "x", "group": 1}},
        {"core": {"path": "x", "subdirectory": "/outside"}},
        {"core": {"path": "x", "subdirectory": "../outside"}},
        {"core": {"path": "x", "subdirectory": "a\\b"}},
        {"core": {"path": "core.whl", "subdirectory": "inside"}},
        {"core": {"url": "https://example.org/core.whl?x=1", "subdirectory": "inside"}},
        {"core": {"url": "relative.zip"}},
        {"core": {"url": "https://[bad"}},
        {"core": {"url": "https://example.org/x.zip", "hashes": {}}},
        {"core": {"url": "https://example.org/x.zip", "hashes": []}},
        {"core": {"url": "https://example.org/x.zip", "hashes": {"SHA256": "abcd"}}},
        {"core": {"url": "https://example.org/x.zip", "hashes": {"sha256": "xyz"}}},
        {"core": {"url": "https://example.org/x.zip", "hashes": {1: "abcd"}}},
        {"core": {"url": "https://example.org/x.zip", "hashes": {"sha256": 1}}},
    ],
)
def test_invalid_tables(table: object) -> None:
    with pytest.raises(InvalidDependencySource):
        parse_dependency_sources(table)


@pytest.mark.parametrize("kind", ["path", "url", "index", "git", "hg", "bzr", "svn"])
def test_source_kinds(kind: str) -> None:
    source = parse_dependency_sources({"Core_Lib": {kind: "https://example.org/core"}})
    assert source["core-lib"][0] == DependencySource(kind, "https://example.org/core")


@pytest.mark.parametrize(
    ("ref", "revision"),
    [("rev", "stable"), ("tag", "refs/tags/stable"), ("branch", "refs/heads/stable")],
)
def test_git_references(ref: str, revision: str) -> None:
    source = parse_dependency_sources(
        {"core": {"git": "https://example.org/core", ref: "stable"}}
    )
    assert source["core"][0].revision == revision


def test_paths_and_credentials_remain_uninterpreted() -> None:
    table = UserDict(
        {
            "Core": "../core",
            "remote": UserDict({"index": "https://${TOKEN}@example.org/simple"}),
        }
    )
    sources = parse_dependency_sources(table)
    assert sources["core"][0].location == "../core"
    assert sources["remote"][0].location == "https://${TOKEN}@example.org/simple"


def test_hashes_preserve_all_algorithms_without_runtime_support_checks() -> None:
    hashes = {"unknown_algorithm": "ABCD", "sha256": "EF01"}
    source = parse_dependency_sources(
        {"core": {"url": "https://example.org/core.zip", "hashes": hashes}}
    )["core"][0]
    assert source.hashes == (("sha256", "ef01"), ("unknown_algorithm", "abcd"))
    assert hashes == {"unknown_algorithm": "ABCD", "sha256": "EF01"}


@pytest.mark.parametrize(
    ("extra", "group", "environment", "expected"),
    [
        ("Fast_Mode", "", {"python_version": "3.14"}, "fast"),
        ("fast-mode", "", {"python_version": "3.10"}, "default"),
        ("", "Test_Only", {}, "test"),
        ("", "", {}, "default"),
    ],
)
def test_order_and_context(
    extra: str, group: str, environment: Mapping[str, str], expected: str
) -> None:
    entries = parse_dependency_sources(
        {
            "core": [
                {
                    "path": "fast",
                    "extra": "Fast_Mode",
                    "marker": "python_version >= '3.12' and extra == 'fast-mode'",
                },
                {"path": "test", "group": "Test_Only"},
                {"path": "default"},
            ]
        }
    )["core"]
    selected = select_dependency_source(
        entries, extra=extra, group=group, environment=environment
    )
    assert selected is not None
    assert selected.location == expected
    assert selected in entries


def test_no_matching_source() -> None:
    entries = parse_dependency_sources(
        {"core": {"path": "local", "marker": "python_version < '1'"}}
    )["core"]
    assert select_dependency_source(entries) is None
    assert select_dependency_source(()) is None


def test_defaults_and_optional_fields() -> None:
    entry = parse_dependency_sources(
        {"core": {"path": "local", "editable": True, "subdirectory": "packages/core"}}
    )["core"][0]
    assert entry.editable
    assert entry.subdirectory == "packages/core"
    assert entry.matches()


def test_unreachable_entries_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, ...]] = []

    def warn(*args: object) -> None:
        calls.append(args)

    monkeypatch.setattr(packaging.dependency_sources.logger, "warning", warn)
    entries = parse_dependency_sources(
        {"core": ["first", "unreachable", "also-unreachable"]}
    )["core"]
    assert select_dependency_source(entries) is entries[0]
    assert len(calls) == 1


def test_selection_keeps_callers_environment() -> None:
    entries = parse_dependency_sources(
        {"core": {"path": "local", "marker": "extra == 'fast'"}}
    )["core"]
    environment = {"extra": "original"}
    assert (
        select_dependency_source(entries, environment=environment, extra="fast")
        is entries[0]
    )
    assert environment == {"extra": "original"}
