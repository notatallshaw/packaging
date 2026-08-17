# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the BSD License. See the LICENSE file in the root of this repository
# for complete details.

"""Property tests for ``VersionRange.filter(assume_sorted=...)``.

The oracle is ``filter`` itself without the keyword: the bisecting walk is only
a faster route to the same answer, so on a genuinely ordered sequence the two
must yield the same items in the same order. The plain strategy exercises the
bisecting path; the ``===`` strategy makes the range fall back to a test per
entry, so the same oracle guards both. Sequences are sorted here with
``Version``'s own ordering, which is the precondition the keyword declares.
"""

from __future__ import annotations

import operator
from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st

from .strategies import (
    SETTINGS,
    VERSION_POOL,
    rich_specifier_sets,
    specifier_sets,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from packaging.ranges import VersionRange
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version

pytestmark = pytest.mark.property

version_lists = st.lists(st.sampled_from(VERSION_POOL), max_size=12)


def _union(a: VersionRange, b: VersionRange) -> VersionRange:
    return a | b


def _intersection(a: VersionRange, b: VersionRange) -> VersionRange:
    return a & b


def _difference(a: VersionRange, b: VersionRange) -> VersionRange:
    return a - b


def _complement_of_union(a: VersionRange, b: VersionRange) -> VersionRange:
    return ~(a | b)


def _complement_minus(a: VersionRange, b: VersionRange) -> VersionRange:
    return ~a - b


# Ways to build a range no single specifier set can spell. The complements
# reach the ``AFTER_POSTS`` bound shape and the unions the multi-interval one,
# the two shapes that stress the bisection's assumption that a bound predicate
# changes value at most once along the sequence.
range_operations: st.SearchStrategy[
    Callable[[VersionRange, VersionRange], VersionRange]
]
range_operations = st.sampled_from(
    [_union, _intersection, _difference, _complement_of_union, _complement_minus]
)


@given(spec=specifier_sets(vary_prereleases=True), versions=version_lists)
@SETTINGS
def test_ascending_matches_the_per_entry_walk(
    spec: SpecifierSet, versions: list[Version]
) -> None:
    r = spec.to_range()
    ordered = sorted(versions)
    assert list(r.filter(ordered, assume_sorted="ascending")) == list(r.filter(ordered))


@given(spec=specifier_sets(vary_prereleases=True), versions=version_lists)
@SETTINGS
def test_descending_matches_the_per_entry_walk(
    spec: SpecifierSet, versions: list[Version]
) -> None:
    r = spec.to_range()
    ordered = sorted(versions, reverse=True)
    assert list(r.filter(ordered, assume_sorted="descending")) == list(
        r.filter(ordered)
    )


@given(
    spec=rich_specifier_sets(include_arbitrary=True, vary_prereleases=True),
    versions=version_lists,
)
@SETTINGS
def test_rich_ranges_match_the_per_entry_walk(
    spec: SpecifierSet, versions: list[Version]
) -> None:
    """``===`` ranges ignore the keyword; the answer must not move either way."""
    r = spec.to_range()
    ordered = sorted(versions)
    assert list(r.filter(ordered, assume_sorted="ascending")) == list(r.filter(ordered))


@given(spec=specifier_sets(vary_prereleases=True), versions=version_lists)
@SETTINGS
def test_strings_match_version_objects(
    spec: SpecifierSet, versions: list[Version]
) -> None:
    """The projection reaches the same answer through a string entry."""
    r = spec.to_range()
    as_strings = [str(v) for v in sorted(versions)]
    assert list(r.filter(as_strings, assume_sorted="ascending")) == list(
        r.filter(as_strings)
    )


@given(
    left=specifier_sets(),
    right=specifier_sets(),
    operation=range_operations,
    prereleases=st.sampled_from([None, True, False]),
    versions=version_lists,
)
@SETTINGS
def test_algebra_built_ranges_match_the_per_entry_walk(
    left: SpecifierSet,
    right: SpecifierSet,
    operation: Callable[[VersionRange, VersionRange], VersionRange],
    prereleases: bool | None,
    versions: list[Version],
) -> None:
    """Bound shapes only set algebra reaches, filtered both ways.

    The operands share the autodetect policy because combining two ranges
    under different ones is refused; the policy varies at the call instead.
    """
    r = operation(left.to_range(), right.to_range())
    ordered = sorted(versions)
    assert list(r.filter(ordered, prereleases, assume_sorted="ascending")) == list(
        r.filter(ordered, prereleases)
    )

    reverse = ordered[::-1]
    assert list(r.filter(reverse, prereleases, assume_sorted="descending")) == list(
        r.filter(reverse, prereleases)
    )


@given(spec=specifier_sets(vary_prereleases=True), versions=version_lists)
@SETTINGS
def test_key_reaches_the_same_answer(
    spec: SpecifierSet, versions: list[Version]
) -> None:
    """``key`` reaches the version inside a richer entry on the bisecting path."""
    r = spec.to_range()
    ordered = sorted(versions)
    wrapped = [(v, "payload") for v in ordered]
    kept = r.filter(wrapped, key=operator.itemgetter(0), assume_sorted="ascending")
    assert [pair[0] for pair in kept] == list(r.filter(ordered))
