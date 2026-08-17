# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the BSD License. See the LICENSE file in the root of this repository
# for complete details.

"""Property tests for the ordering of ``LowerBound`` and ``UpperBound``.

Two oracles. The first is the definition of a total order: over a random pool
of bounds, every pair is comparable; exactly one of ``a < b``, ``a == b`` and
``a > b`` holds; ``<=`` and ``>=`` agree with that; and ``<`` is transitive.
``sorted`` is checked too, since an incomparable pair makes it depend on input
order.

The second is the same ordering written against the version operators alone,
with ``>``, ``<=`` and ``>=`` derived from it the way ``functools.total_ordering``
derives them. The classes order on the versions' cached comparison keys where
both sides have one, and this holds the two spellings to the same answers.

Bounds are drawn over the inner values the range engine builds: the unbounded
end in both inclusivity spellings, a plain version, and a boundary of either
kind over the versions that kind is built on.
"""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING, TypeVar

import pytest
from hypothesis import given
from hypothesis import strategies as st

from packaging._ranges import (
    BoundaryKind,
    BoundaryVersion,
    LowerBound,
    UpperBound,
)

from .strategies import SETTINGS, pep440_versions

if TYPE_CHECKING:
    from collections.abc import Callable

    from packaging.version import Version

pytestmark = pytest.mark.property

_BoundT = TypeVar("_BoundT", LowerBound, UpperBound)


@st.composite
def _inner_values(draw: st.DrawFn) -> Version | BoundaryVersion | None:
    """One inner value for a bound: unbounded, a version, or a boundary.

    ``AFTER_POSTS`` is drawn only on versions with no ``dev`` or ``post``
    segment, the only ones the engine builds it on. Wider than that the order
    is not transitive: ``AFTER_POSTS(1.0.dev0)`` sorts below ``1.0a1`` and
    ``1.0a1`` sorts below ``1.0.post0``, but the boundary counts ``1.0.post0``
    as family and so does not sort below it.
    """
    shape = draw(st.sampled_from(["unbounded", "version", "boundary"]))
    if shape == "unbounded":
        return None

    version = draw(pep440_versions())
    if shape == "version":
        return version

    kinds = [BoundaryKind.AFTER_LOCALS]
    if version.dev is None and version.post is None:
        kinds.append(BoundaryKind.AFTER_POSTS)

    return BoundaryVersion(version, draw(st.sampled_from(kinds)))


@st.composite
def _lower_bounds(draw: st.DrawFn) -> LowerBound:
    """Generate a lower bound over one inner value and either inclusivity."""
    return LowerBound(draw(_inner_values()), draw(st.booleans()))


@st.composite
def _upper_bounds(draw: st.DrawFn) -> UpperBound:
    """Generate an upper bound over one inner value and either inclusivity."""
    return UpperBound(draw(_inner_values()), draw(st.booleans()))


def _assert_totally_ordered(pool: list[_BoundT]) -> None:
    """Assert the total-order laws over *pool*.

    Comparability, trichotomy and the non-strict operators on every pair,
    transitivity on every triple, and a sort that does not depend on the
    order the pool came in.
    """
    for a, b in itertools.product(pool, repeat=2):
        assert a <= b or b <= a
        assert [a < b, a == b, a > b].count(True) == 1
        assert (a <= b) == (a < b or a == b)
        assert (a >= b) == (a > b or a == b)

    for a, b, c in itertools.permutations(pool, 3):
        if a < b < c:
            assert a < c

    ascending = sorted(pool)
    assert all(x <= y for x, y in itertools.pairwise(ascending))

    reversed_pool = list(reversed(pool))
    assert sorted(reversed_pool) == ascending


def _reference_lower_lt(left: LowerBound, right: LowerBound) -> bool:
    """``LowerBound.__lt__`` written against the version operators alone."""
    if left.version is None:
        return right.version is not None
    if right.version is None:
        return False
    if left.version != right.version:
        return left.version < right.version
    return left.inclusive and not right.inclusive


def _reference_upper_lt(left: UpperBound, right: UpperBound) -> bool:
    """``UpperBound.__lt__`` written against the version operators alone."""
    if left.version is None:
        return False
    if right.version is None:
        return True
    if left.version != right.version:
        return left.version < right.version
    return not left.inclusive and right.inclusive


def _assert_matches_reference(
    pool: list[_BoundT], reference: Callable[[_BoundT, _BoundT], bool]
) -> None:
    """Assert every operator agrees with *reference* and what it derives.

    The four operators are evaluated before *reference*, because *reference*
    compares the two versions and so builds both their comparison keys. Two
    passes: the first meets pairs whose keys the pool has not built yet, the
    second meets every pair with both keys built.
    """
    for _ in range(2):
        for a, b in itertools.product(pool, repeat=2):
            answers = (a < b, a > b, a <= b, a >= b)
            equal = a == b
            expected_lt = reference(a, b)

            assert answers == (
                expected_lt,
                not expected_lt and not equal,
                expected_lt or equal,
                not expected_lt,
            )


@given(pool=st.lists(_lower_bounds(), min_size=2, max_size=5))
@SETTINGS
def test_lower_bounds_are_totally_ordered(pool: list[LowerBound]) -> None:
    _assert_totally_ordered(pool)


@given(pool=st.lists(_upper_bounds(), min_size=2, max_size=5))
@SETTINGS
def test_upper_bounds_are_totally_ordered(pool: list[UpperBound]) -> None:
    _assert_totally_ordered(pool)


@given(pool=st.lists(_lower_bounds(), min_size=2, max_size=5))
@SETTINGS
def test_lower_bounds_order_as_the_version_operators_do(
    pool: list[LowerBound],
) -> None:
    _assert_matches_reference(pool, _reference_lower_lt)


@given(pool=st.lists(_upper_bounds(), min_size=2, max_size=5))
@SETTINGS
def test_upper_bounds_order_as_the_version_operators_do(
    pool: list[UpperBound],
) -> None:
    _assert_matches_reference(pool, _reference_upper_lt)
