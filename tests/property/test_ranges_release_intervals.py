# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the BSD License. See the LICENSE file in the root of this repository
# for complete details.

"""Property tests for ``VersionRange.release_intervals``.

The projection has one defining property: a release of the requested shape
falls in one of the returned intervals exactly when the range contains it. It
is checked against a dense grid of such releases, so a bound that rounds the
wrong way by one shows up as a disagreement on the release it rounds onto.
Ranges carrying ``===`` literals are drawn too, since those decide membership
ahead of the bounds.

The rest are structural. The intervals come back ascending, and they are
maximal: none is empty and no two are adjacent, since a gap between two of them
must hold at least one release the range excludes. Emptiness is measured
against the bottom of the grid, which an interval unbounded below starts at.

One law relates two grids: every release a coarse projection covers, the next
finer one covers too, for a range carrying no ``===`` literal.
"""

from __future__ import annotations

import itertools

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from packaging.ranges import VersionRange
from packaging.version import Version

from .strategies import SETTINGS, rich_specifier_sets

pytestmark = pytest.mark.property

_PARTS = st.integers(min_value=1, max_value=3)


def _grid(parts: int) -> list[Version]:
    """Every release of ``parts`` components under 5, in the first two epochs.

    Dense rather than wide: the strategies draw their own components from a
    small range too, so a bound that rounds the wrong way by one usually has a
    grid release beside it to land on.
    """
    return [
        Version.from_parts(epoch=epoch, release=release)
        for epoch in (0, 1)
        for release in itertools.product(range(5), repeat=parts)
    ]


def _covers(
    intervals: tuple[tuple[Version | None, Version | None], ...], version: Version
) -> bool:
    """Whether ``version`` falls in one of the half-open intervals."""
    return any(
        (lower is None or version >= lower) and (upper is None or version < upper)
        for lower, upper in intervals
    )


@st.composite
def from_bounds_ranges(draw: st.DrawFn) -> VersionRange:
    """A single raw order interval, reaching bounds no specifier set spells."""
    endpoints = st.none() | st.sampled_from(
        ["0.dev0", "1.0", "1.0.post1", "1.0+local", "2.3.0.1", "3.4rc1", "4", "1!2.0"]
    )
    return VersionRange.from_bounds(
        draw(endpoints),
        draw(endpoints),
        include_lower=draw(st.booleans()),
        include_upper=draw(st.booleans()),
    )


@st.composite
def projectable_ranges(draw: st.DrawFn) -> VersionRange:
    """A range built from specifier sets, from raw bounds, or from algebra.

    Set algebra refuses to mix configured pre-release policies, so only the
    specifier-set source varies the policy; the two that go on to combine
    ranges leave it unset.
    """
    source = draw(st.sampled_from(["specifiers", "bounds", "algebra"]))
    if source == "specifiers":
        return draw(
            rich_specifier_sets(include_arbitrary=True, vary_prereleases=True)
        ).to_range()

    result = (
        draw(from_bounds_ranges())
        if source == "bounds"
        else draw(rich_specifier_sets(include_arbitrary=True)).to_range()
    )
    for _ in range(draw(st.integers(min_value=0, max_value=2))):
        op = draw(st.sampled_from(["and", "or", "sub", "invert"]))
        if op == "invert":
            result = ~result
            continue

        other = draw(
            st.one_of(
                from_bounds_ranges(),
                rich_specifier_sets(include_arbitrary=True).map(
                    lambda spec_set: spec_set.to_range()
                ),
            )
        )
        if op == "and":
            result = result & other
        elif op == "or":
            result = result | other
        else:
            result = result - other
    return result


@given(source=projectable_ranges(), parts=_PARTS)
@SETTINGS
def test_intervals_agree_with_contains(source: VersionRange, parts: int) -> None:
    """A release is covered exactly when the range contains it."""
    intervals = source.release_intervals(parts)
    for version in _grid(parts):
        assert _covers(intervals, version) == source.contains(version)


@given(source=projectable_ranges(), parts=_PARTS)
@SETTINGS
def test_intervals_are_ascending_and_maximal(source: VersionRange, parts: int) -> None:
    """Ascending, non-empty, and separated by at least one excluded release."""
    floor = Version.from_parts(release=(0,) * parts)
    intervals = source.release_intervals(parts)
    for index, (lower, upper) in enumerate(intervals):
        if index:
            previous_upper = intervals[index - 1][1]
            assert previous_upper is not None
            assert lower is not None
            assert previous_upper < lower
        if upper is not None:
            assert upper > (floor if lower is None else lower)


@given(source=projectable_ranges(), parts=_PARTS)
@SETTINGS
def test_only_the_ends_are_unbounded(source: VersionRange, parts: int) -> None:
    """An unbounded side can only appear on the first or the last interval."""
    intervals = source.release_intervals(parts)
    for index, (lower, upper) in enumerate(intervals):
        if lower is None:
            assert index == 0
        if upper is None:
            assert index == len(intervals) - 1


@given(source=projectable_ranges(), parts=_PARTS)
@SETTINGS
def test_a_finer_grid_refines_the_coarser_one(source: VersionRange, parts: int) -> None:
    """Every release the coarse projection covers, the fine one covers too.

    A ``parts``-component release is also a ``parts + 1``-component release
    with a trailing zero, so widening the grid can only add releases. That
    holds of the bounds and not of a ``===`` literal, which matches one
    spelling where ``3.11`` and ``3.11.0`` are one version written two ways.
    """
    assume(not source._admit and not source._reject)
    coarse = source.release_intervals(parts)
    fine = source.release_intervals(parts + 1)
    for version in _grid(parts):
        if _covers(coarse, version):
            assert _covers(fine, version)
