# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the BSD License. See the LICENSE file in the root of this repository
# for complete details.

"""Version subtraction across gaps, endpoints, and PEP 440 boundaries."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from packaging import ranges
from packaging._ranges import (
    BoundaryKind,
    BoundaryVersion,
    LowerBound,
    UpperBound,
    intersect_ranges,
)
from packaging.specifiers import SpecifierSet
from packaging.version import Version

if TYPE_CHECKING:
    from collections.abc import Sequence

    from packaging._ranges import Interval


@pytest.mark.parametrize("prereleases", [None, False, True])
@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("<0", "!=2"),
        (">=3", "<2"),
        ("<2", ">=3"),
        (">=1,<=5", ">=2,<4"),
        (">=1,<=5", ">=2,<=5"),
        (">=1,<=5", ">=2"),
        (">=1,<=5", "<0"),
        ("!=1,!=3", "==2"),
        ("!=1,!=3", "!=2,!=4"),
        ("", ""),
        (">=1", ">1"),
        ("<=1", "<1"),
        (">1a1", "<1a2.dev0"),
        ("<=1.post0.dev0", "<=1"),
        ("==1.*", "==1.1.*"),
        ("==1+local", "==1"),
        ("!=0.dev0", "<1.dev0"),
        (">=2!1", "<2!1.post1"),
    ],
)
def test_difference_matches_complement_intersection(
    left: str, right: str, prereleases: bool | None
) -> None:
    first = SpecifierSet(left, prereleases=prereleases).to_range()
    second = SpecifierSet(right, prereleases=prereleases).to_range()

    assert first - second == first & ~second


def test_difference_preserves_arbitrary_equality() -> None:
    literal = SpecifierSet("===legacy").to_range()
    numeric = SpecifierSet(">=1").to_range()

    assert literal - numeric == literal
    assert (literal - literal).is_empty
    assert "legacy" in (literal | numeric) - numeric


def test_difference_does_not_construct_a_complement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = SpecifierSet(">=1,<5").to_range()
    second = SpecifierSet("!=2,!=4").to_range()
    expected = first & ~second

    def refuse_complement(_bounds: Sequence[Interval]) -> list[Interval]:
        raise AssertionError("subtraction constructed a complement")

    monkeypatch.setattr(ranges, "_complement_ranges", refuse_complement)
    assert first - second == expected


@pytest.mark.parametrize("right_is_empty", [False, True])
def test_empty_interval_contributes_no_difference(right_is_empty: bool) -> None:
    left = ((LowerBound(Version("1"), False), UpperBound(Version("1"), True)),)
    right = (
        ()
        if right_is_empty
        else ((LowerBound(Version("2"), True), UpperBound(Version("3"), True)),)
    )

    assert ranges._difference_ranges(left, right) == ()


def test_subtraction_drops_a_gap_below_a_boundary_successor() -> None:
    boundary = BoundaryVersion(Version("1a1"), BoundaryKind.AFTER_POSTS)
    left = ((LowerBound(boundary, False), UpperBound(Version("2"), True)),)
    right = (
        (LowerBound(Version("1a2.dev0"), True), UpperBound(Version("1a3.dev0"), False)),
    )
    expected = tuple(intersect_ranges(left, ranges._complement_ranges(right)))

    assert ranges._difference_ranges(left, right) == expected
    assert len(expected) == 1
