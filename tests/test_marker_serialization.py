# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the BSD License. See the LICENSE file in the root of this repository
# for complete details.

from __future__ import annotations

import pickle
from unittest.mock import patch

import pytest

import packaging.markers
from packaging.markers import Marker
from packaging.requirements import Requirement


@pytest.mark.parametrize("from_requirement", [False, True])
def test_repeated_serialization_reuses_the_string(from_requirement: bool) -> None:
    text = 'python_version >= "3.10" and sys_platform == "linux"'
    marker = Requirement(f"demo; {text}").marker if from_requirement else Marker(text)
    assert marker is not None
    expected = str(marker)

    with patch.object(packaging.markers, "_format_marker", side_effect=AssertionError):
        assert str(marker) == expected
        assert repr(marker) == f"<Marker({expected!r})>"
        assert hash(marker) == hash(expected)
        assert marker == marker


@pytest.mark.parametrize("operator", ["and", "or"])
def test_combined_markers_have_their_own_serialization(operator: str) -> None:
    left = Marker('python_version >= "3.10"')
    right = Marker('sys_platform == "linux"')
    left_text, right_text = str(left), str(right)
    combined = left & right if operator == "and" else left | right

    assert str(combined) == f"{left_text} {operator} {right_text}"
    assert str(left) == left_text
    assert str(right) == right_text


@pytest.mark.parametrize("format", ["string", "dict", "slots"])
def test_restoring_state_discards_cached_serialization(format: str) -> None:
    marker = Marker('os_name == "posix"')
    assert str(marker) == 'os_name == "posix"'
    replacement = Marker('sys_platform == "win32"')
    state: object = str(replacement)
    if format == "dict":
        state = {"_markers": replacement._markers}
    elif format == "slots":
        state = (None, {"_markers": replacement._markers})

    marker.__setstate__(state)

    assert str(marker) == str(replacement)
    assert hash(marker) == hash(replacement)
    assert marker == replacement


def test_reinitialization_discards_cached_serialization() -> None:
    marker = Marker('os_name == "posix"')
    assert str(marker) == 'os_name == "posix"'

    Marker.__init__(marker, 'sys_platform == "win32"')

    assert str(marker) == 'sys_platform == "win32"'


def test_pickle_contains_only_the_expression() -> None:
    marker = Marker('sys_platform == "linux"')
    expected = str(marker)

    assert marker.__getstate__() == expected
    restored = pickle.loads(pickle.dumps(marker))
    assert str(restored) == expected
    assert restored == marker


def test_mutating_a_requirement_still_changes_its_string_and_hash() -> None:
    requirement = Requirement('demo; sys_platform == "linux"')
    original = str(requirement), hash(requirement)

    requirement.marker = Marker('sys_platform == "win32"')

    expected = Requirement('demo; sys_platform == "win32"')
    assert str(requirement) == str(expected)
    assert hash(requirement) == hash(expected)
    assert (str(requirement), hash(requirement)) != original
