"""Component factory contracts across the native representation boundary."""

from __future__ import annotations

import json
from copy import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from packaging.version import InvalidVersion, Version

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import NoReturn


_ORACLE = json.loads(
    Path(__file__).with_name("version_parts_expected.json").read_text()
)


def tuple_parts(parts: dict[str, object]) -> dict[str, object]:
    """Restore tuple components from the pinned Python oracle outputs."""
    for name in ("release", "pre"):
        component = parts.get(name)
        if isinstance(component, list):
            parts[name] = tuple(component)
    return parts


@pytest.mark.parametrize(
    ("parts", "expected"),
    [(tuple_parts(row["parts"]), row["expected"]) for row in _ORACLE["from_parts"]],
)
def test_from_parts_matches_python_components(
    parts: dict[str, object], expected: str
) -> None:
    actual = Version.from_parts(**parts)
    assert str(actual) == expected
    parsed = Version(expected)
    assert actual == parsed
    assert actual.release == parsed.release
    assert hash(actual) == hash(parsed)


@pytest.mark.parametrize(
    ("text", "changes", "expected"),
    [
        (row["version"], tuple_parts(row["parts"]), row["expected"])
        for row in _ORACLE["replace"]
    ],
)
def test_replace_matches_python_components(
    text: str, changes: dict[str, object], expected: str
) -> None:
    original = Version(text)
    result = replace(original, **changes)
    assert str(result) == expected
    parsed = Version(expected)
    assert result == parsed
    assert result.release == parsed.release
    assert hash(result) == hash(parsed)


@pytest.mark.parametrize(
    "text", ["1.2.3a1.post2.dev3", "0!1.2.3a1.post2.dev3", "1!1.2.3a1.post2.dev3+abc.1"]
)
def test_normalized_noop_preserves_identity(text: str) -> None:
    version = Version(text)
    hash(version)
    assert replace(version, pre=("ALPHA", 1)) is version
    assert replace(version, release=version.release) is version
    if version.local:
        assert replace(version, local="ABC_001") is version


@pytest.mark.parametrize("factory", [Version.from_parts, Version("1.2.3").__replace__])
@pytest.mark.parametrize(
    "invalid",
    [
        {"epoch": -1},
        {"epoch": "1"},
        {"release": ()},
        {"release": [1]},
        {"release": (-1,)},
        {"release": ("1",)},
        {"pre": (1, 1)},
        {"pre": ("a", -1)},
        {"pre": ("a", "1")},
        {"pre": ("a\ud800", 1)},
        {"post": -1},
        {"dev": "1"},
        {"local": ""},
        {"local": "abc..1"},
        {"local": "\ud800"},
        {"local": "\u00e9"},
    ],
)
def test_factories_reject_invalid_components(
    factory: Callable[..., Version], invalid: dict[str, object]
) -> None:
    arguments: dict[str, object] = {"release": (1, 2, 3)}
    arguments.update(invalid)
    with pytest.raises(InvalidVersion):
        factory(**arguments)


def test_factories_preserve_subclass_without_constructing_strings() -> None:
    class DerivedVersion(Version):
        def __new__(cls, _version: str) -> NoReturn:
            raise AssertionError("factory invoked the string constructor")

        def __init__(self, _version: str) -> None:
            raise AssertionError("factory invoked subclass initialization")

    original = DerivedVersion.from_parts(release=(1, 2, 3))
    result = replace(original, epoch=1, local="abc")
    assert type(original) is DerivedVersion
    assert type(result) is DerivedVersion
    assert str(result) == "1!1.2.3+abc"


def test_release_spelling_change_is_not_a_noop() -> None:
    version = Version("1.2.0")
    changed = replace(version, release=(1, 2))
    assert changed == version
    assert changed is not version
    assert changed.release == (1, 2)


def test_from_parts_requires_release_and_rejects_positional_arguments() -> None:
    with pytest.raises(TypeError):
        Version.from_parts()
    with pytest.raises(TypeError):
        Version.from_parts((1, 2, 3))
