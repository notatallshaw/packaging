# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the BSD License. See the LICENSE file in the root of this repository
# for complete details.

"""Property tests for ``VersionRange.to_specifier_set`` /
``to_specifier_sets`` round-tripping.

The conversions are partial: not every ``VersionRange`` has a
``SpecifierSet`` representation, because PEP 440 specifiers cannot
express bound shapes such as "less than V but include V's
pre-releases" or strict singleton ``[V, V]``.  These tests assert the
contract:

* If ``to_specifier_set(r)`` returns a SpecifierSet ``s``, then
  ``from_specifier_set(s) == r`` (round-trip exactness).
* If ``to_specifier_sets(r)`` returns a tuple ``T``, then the union
  of ``from_specifier_set(s) for s in T`` equals ``r``.
* For specifier-derived ranges, the conversion always succeeds.
* Returning ``None`` is allowed; silent semantic drift is not.
"""

from __future__ import annotations

from functools import reduce
from typing import TYPE_CHECKING

import pytest
from hypothesis import given

from packaging.ranges import VersionRange

from .strategies import SETTINGS, specifier_sets

if TYPE_CHECKING:
    from packaging.specifiers import SpecifierSet

pytestmark = pytest.mark.property


@given(spec_set=specifier_sets())
@SETTINGS
def test_to_specifier_set_round_trips_when_not_none(
    spec_set: SpecifierSet,
) -> None:
    """If ``to_specifier_set`` succeeds, the result must round-trip exactly.

    This is the *exactness* guarantee.  Silent semantic drift would
    make the conversion useless for serialising lock files; we forbid
    it by asserting equality, not approximate inclusion.
    """
    r = VersionRange.from_specifier_set(spec_set)
    assert r is not None
    converted = r.to_specifier_set()
    if converted is None:
        return
    assert VersionRange.from_specifier_set(converted) == r


@given(spec_set=specifier_sets())
@SETTINGS
def test_specifier_derived_ranges_always_have_a_specifier_set(
    spec_set: SpecifierSet,
) -> None:
    """Ranges produced by ``from_specifier_set`` are always re-encodable.

    The conversion can return ``None`` only for ranges that arose from
    operations producing bound shapes no specifier can express (e.g.,
    :meth:`singleton` for a local-less version).  Anything that came
    directly from a specifier -- including unsatisfiable ones, which
    map to the empty range and re-encode as ``<0`` -- must round-trip.
    """
    r = VersionRange.from_specifier_set(spec_set)
    assert r is not None
    converted = r.to_specifier_set()
    assert converted is not None, (
        f"specifier-derived range {r!r} should always re-encode "
        f"(input was {spec_set!r})"
    )
    assert VersionRange.from_specifier_set(converted) == r


@given(spec_set=specifier_sets())
@SETTINGS
def test_to_specifier_sets_round_trips_when_not_none(
    spec_set: SpecifierSet,
) -> None:
    """If ``to_specifier_sets`` succeeds, the union of its elements equals ``r``."""
    r = VersionRange.from_specifier_set(spec_set)
    assert r is not None
    converted = r.to_specifier_sets()
    if converted is None:
        return
    assert converted, "to_specifier_sets must return a non-empty tuple"
    union = reduce(
        VersionRange.union,
        (VersionRange.from_specifier_set(s) for s in converted),
    )
    assert union == r


@given(a=specifier_sets(), b=specifier_sets())
@SETTINGS
def test_intersection_round_trips_when_not_none(
    a: SpecifierSet, b: SpecifierSet
) -> None:
    """Intersection preserves specifier-encodability.

    SpecifierSet is closed under intersection (just concatenate
    specifiers with commas), so the intersection of two
    specifier-derived ranges always re-encodes -- including the empty
    intersection, which canonically encodes as ``<0``.
    """
    ra = VersionRange.from_specifier_set(a)
    rb = VersionRange.from_specifier_set(b)
    assert ra is not None
    assert rb is not None
    inter = ra & rb
    converted = inter.to_specifier_set()
    assert converted is not None
    assert VersionRange.from_specifier_set(converted) == inter


@given(a=specifier_sets(), b=specifier_sets())
@SETTINGS
def test_to_specifier_sets_handles_union_when_intervals_are_specifier_shaped(
    a: SpecifierSet, b: SpecifierSet
) -> None:
    """Union of specifier-derived ranges has each interval specifier-shaped.

    Every interval in either input came from a specifier and so has a
    specifier-shaped bound.  Union may produce a multi-interval range;
    each interval is still specifier-shaped, so ``to_specifier_sets``
    succeeds even though ``to_specifier_set`` may not.
    """
    ra = VersionRange.from_specifier_set(a)
    rb = VersionRange.from_specifier_set(b)
    assert ra is not None
    assert rb is not None
    u = ra | rb
    converted = u.to_specifier_sets()
    assert converted is not None
    union = reduce(
        VersionRange.union,
        (VersionRange.from_specifier_set(s) for s in converted),
    )
    assert union == u


@given(spec_set=specifier_sets())
@SETTINGS
def test_to_specifier_set_implies_to_specifier_sets(
    spec_set: SpecifierSet,
) -> None:
    """If the single-set form succeeds, the tuple form must also succeed.

    Both encode the *same* range; the tuple form is strictly more
    permissive (it can split disjoint intervals).  So
    ``to_specifier_set is not None`` ⇒ ``to_specifier_sets is not None``.
    """
    r = VersionRange.from_specifier_set(spec_set)
    assert r is not None
    if r.to_specifier_set() is not None:
        assert r.to_specifier_sets() is not None


@given(spec_set=specifier_sets())
@SETTINGS
def test_to_specifier_sets_size_is_one_or_one_per_interval(
    spec_set: SpecifierSet,
) -> None:
    """The tuple length is either 1 (single-set form succeeded) or
    equal to the number of intervals (per-interval fallback)."""
    r = VersionRange.from_specifier_set(spec_set)
    assert r is not None
    converted = r.to_specifier_sets()
    if converted is None:
        return
    assert len(converted) in (1, len(r._bounds))


@given(spec_set=specifier_sets())
@SETTINGS
def test_empty_range_round_trips_via_lt_zero(spec_set: SpecifierSet) -> None:
    """The empty range encodes as ``<0`` and the form round-trips."""
    from packaging.specifiers import SpecifierSet as _SpecifierSet  # noqa: PLC0415

    r = VersionRange.from_specifier_set(spec_set)
    assert r is not None
    if r.is_empty:
        assert r.to_specifier_set() == _SpecifierSet("<0")
        assert r.to_specifier_sets() == (_SpecifierSet("<0"),)
