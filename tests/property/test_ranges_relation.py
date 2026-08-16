# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the BSD License. See the LICENSE file in the root of this repository
# for complete details.

"""Property tests for ``VersionRange.relation``.

The oracle is the set algebra ``test_ranges_set_relations.py`` pins the
predicates to: containment is ``(a & ~b).is_empty`` and separation is
``(a & b).is_empty``. One relation must carry both of those answers, and must
report ``EMPTY`` exactly when ``self`` is empty. The plain strategy exercises
the bounds walk; the ``===`` and configured-policy draws reach the predicate
fallback, so the same oracle guards both paths.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from packaging.ranges import RangeRelation, VersionRange
from packaging.specifiers import SpecifierSet

from .strategies import (
    SETTINGS,
    rich_specifier_sets,
    specifier_sets,
)

pytestmark = pytest.mark.property


@given(a=specifier_sets(), b=specifier_sets())
@SETTINGS
def test_matches_the_algebra_plain(a: SpecifierSet, b: SpecifierSet) -> None:
    ra, rb = a.to_range(), b.to_range()
    relation = ra.relation(rb)
    assert relation.is_subset == (ra & ~rb).is_empty
    assert relation.is_disjoint == (ra & rb).is_empty


@given(
    a=rich_specifier_sets(include_arbitrary=True),
    b=rich_specifier_sets(include_arbitrary=True),
)
@SETTINGS
def test_matches_the_algebra_rich(a: SpecifierSet, b: SpecifierSet) -> None:
    """``===`` ranges defer to the predicates; ``_is_plain`` must gate them out."""
    ra, rb = a.to_range(), b.to_range()
    relation = ra.relation(rb)
    assert relation.is_subset == (ra & ~rb).is_empty
    assert relation.is_disjoint == (ra & rb).is_empty


@given(
    a=rich_specifier_sets(include_arbitrary=True),
    b=rich_specifier_sets(include_arbitrary=True),
)
@SETTINGS
def test_matches_the_predicates(a: SpecifierSet, b: SpecifierSet) -> None:
    """The equivalence the docstring promises, checked against the methods.

    On two plain ranges this pits the walk against the bounds fast paths in
    ``is_subset`` and ``is_disjoint``, which reach the same two answers
    through a complement and two intersections.
    """
    ra, rb = a.to_range(), b.to_range()
    relation = ra.relation(rb)
    assert relation.is_subset == ra.is_subset(rb)
    assert relation.is_disjoint == ra.is_disjoint(rb)


@given(
    a=rich_specifier_sets(include_arbitrary=True),
    b=rich_specifier_sets(include_arbitrary=True),
    prereleases=st.sampled_from([None, True, False]),
)
@SETTINGS
def test_matches_the_predicates_under_one_policy(
    a: SpecifierSet, b: SpecifierSet, prereleases: bool | None
) -> None:
    """Both operands take the same drawn policy, the only pairing allowed.

    ``prereleases=False`` withholds members the bounds carry, so it reaches
    the predicate fallback the way ``===`` does.
    """
    ra = SpecifierSet(str(a), prereleases=prereleases).to_range()
    rb = SpecifierSet(str(b), prereleases=prereleases).to_range()
    relation = ra.relation(rb)
    assert relation.is_subset == ra.is_subset(rb)
    assert relation.is_disjoint == ra.is_disjoint(rb)


@given(
    a=rich_specifier_sets(include_arbitrary=True),
    b=rich_specifier_sets(include_arbitrary=True),
)
@SETTINGS
def test_empty_member_means_an_empty_self(a: SpecifierSet, b: SpecifierSet) -> None:
    ra, rb = a.to_range(), b.to_range()
    assert (ra.relation(rb) is RangeRelation.EMPTY) == ra.is_empty


@given(a=specifier_sets(), b=specifier_sets())
@SETTINGS
def test_intersection_is_contained_in_each_operand(
    a: SpecifierSet, b: SpecifierSet
) -> None:
    """Two drawn ranges are rarely a subset pair, so relate the intersection.

    It is contained in each operand by construction, so the containment
    result is driven in every example rather than by chance.
    """
    ra, rb = a.to_range(), b.to_range()
    inter = ra & rb
    contained = {RangeRelation.EMPTY, RangeRelation.SUBSET}
    assert inter.relation(ra) in contained
    assert inter.relation(rb) in contained


@given(a=specifier_sets(), b=specifier_sets())
@SETTINGS
def test_separation_is_symmetric(a: SpecifierSet, b: SpecifierSet) -> None:
    ra, rb = a.to_range(), b.to_range()
    assert ra.relation(rb).is_disjoint == rb.relation(ra).is_disjoint


@given(spec_set=rich_specifier_sets(include_arbitrary=True))
@SETTINGS
def test_reflexive(spec_set: SpecifierSet) -> None:
    r = spec_set.to_range()
    expected = RangeRelation.EMPTY if r.is_empty else RangeRelation.SUBSET
    assert r.relation(r) is expected


@given(spec_set=rich_specifier_sets(include_arbitrary=True))
@SETTINGS
def test_empty_range_against_anything(spec_set: SpecifierSet) -> None:
    assert VersionRange.empty().relation(spec_set.to_range()) is RangeRelation.EMPTY
