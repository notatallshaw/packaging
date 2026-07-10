# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the BSD License. See the LICENSE file in the root of this repository
# for complete details.

"""Property tests for ``VersionRange.snap_bounds`` and ``from_bounds``.

``snap_bounds`` documents three guarantees for any range and any versions: the
result is a subset of the source (direction), it agrees with the source on the
membership of every given version (agreement), and snapping it again returns an
equal range (idempotence). Each is checked against ranges built from specifier
sets and against ranges assembled only from ``from_bounds`` pieces, since the
two routes reach different bound shapes.

The last test covers the intended pairing of the two methods. Bisecting a
version list around one of its members and calling ``from_bounds`` on the
neighbours widens that member to the largest interval holding no other listed
version; snapping the result back over the same list recovers the member alone.
Recovery is asserted as agreement with the singleton over the list rather than
as equality, since versions outside the list are not part of the contract.
"""

from __future__ import annotations

import bisect
from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st

from packaging.ranges import VersionRange

from .strategies import (
    SETTINGS,
    VERSION_POOL,
    pep440_versions,
    rich_specifier_sets,
)

if TYPE_CHECKING:
    from packaging.version import Version

pytestmark = pytest.mark.property


# Universes mix the fixed pool (finals, pre-releases, posts, devs, locals,
# epochs) with freshly drawn versions covering the same forms.
universes = st.lists(
    st.one_of(st.sampled_from(VERSION_POOL), pep440_versions()),
    min_size=1,
    max_size=10,
    unique=True,
)

_optional_versions = st.none() | pep440_versions()


@st.composite
def from_bounds_ranges(draw: st.DrawFn) -> VersionRange:
    """A range assembled only from :meth:`VersionRange.from_bounds` pieces.

    Raw order cuts reach bound shapes no specifier set spells: an exclusive
    bound at a version whose posts are still members, a closed bound at a
    ``+local``, and multi-segment unions of both.
    """

    def interval() -> VersionRange:
        return VersionRange.from_bounds(
            draw(_optional_versions),
            draw(_optional_versions),
            include_lower=draw(st.booleans()),
            include_upper=draw(st.booleans()),
        )

    result = interval()
    for _ in range(draw(st.integers(min_value=0, max_value=2))):
        other = interval()
        op = draw(st.sampled_from(["intersection", "union", "difference"]))
        if op == "intersection":
            result = result & other
        elif op == "union":
            result = result | other
        else:
            result = result - other
    return result


snappable_ranges = st.one_of(
    rich_specifier_sets(include_arbitrary=True, vary_prereleases=True).map(
        lambda spec_set: spec_set.to_range()
    ),
    from_bounds_ranges(),
)


@given(source=snappable_ranges, versions=universes)
@SETTINGS
def test_snap_bounds_returns_a_subset(
    source: VersionRange, versions: list[Version]
) -> None:
    """Direction: snapping drops versions, it never adds them."""
    assert source.snap_bounds(versions).is_subset(source)


@given(source=snappable_ranges, versions=universes)
@SETTINGS
def test_snap_bounds_agrees_on_the_given_versions(
    source: VersionRange, versions: list[Version]
) -> None:
    """Agreement: the result and the source rule the same way on each version."""
    result = source.snap_bounds(versions)
    for version in versions:
        assert result.contains(version) == source.contains(version)


@given(source=snappable_ranges, versions=universes)
@SETTINGS
def test_snap_bounds_is_idempotent(
    source: VersionRange, versions: list[Version]
) -> None:
    """Idempotence: bounds already sitting on given versions do not move."""
    result = source.snap_bounds(versions)
    assert result.snap_bounds(versions) == result


@given(source=snappable_ranges, versions=universes)
@SETTINGS
def test_snap_bounds_keeps_unbounded_ends(
    source: VersionRange, versions: list[Version]
) -> None:
    """An unbounded end has nothing to land on, so it stays unbounded."""
    result = source.snap_bounds(versions)
    if source._bounds and source._bounds[0][0].version is None:
        assert result._bounds[0][0].version is None
    if source._bounds and source._bounds[-1][1].version is None:
        assert result._bounds[-1][1].version is None


@st.composite
def universe_and_member(draw: st.DrawFn) -> tuple[list[Version], Version]:
    """A version universe together with one of its members."""
    versions = draw(universes)
    return versions, draw(st.sampled_from(versions))


def _widen(version: Version, versions: list[Version]) -> VersionRange:
    """Widen ``version`` to the largest interval holding no other member.

    Two bisects and one :meth:`VersionRange.from_bounds` call: the last listed
    version strictly below and the first strictly above become exclusive bounds.
    """
    ordered = sorted(versions)
    left = bisect.bisect_left(ordered, version)
    right = bisect.bisect_right(ordered, version)
    return VersionRange.from_bounds(
        ordered[left - 1] if left > 0 else None,
        ordered[right] if right < len(ordered) else None,
        include_lower=False,
        include_upper=False,
        prereleases=True,
    )


@given(pair=universe_and_member())
@SETTINGS
def test_widening_round_trips_through_snap_bounds(
    pair: tuple[list[Version], Version],
) -> None:
    """The widened interval snaps back to the version it was widened from."""
    versions, version = pair
    widened = _widen(version, versions)
    assert widened.contains(version)

    snapped = widened.snap_bounds(versions)
    tight = VersionRange.singleton(version, prereleases=True)
    for candidate in versions:
        assert snapped.contains(candidate) == tight.contains(candidate)
    assert snapped.is_subset(widened)
