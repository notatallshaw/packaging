# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the BSD License. See the LICENSE file in the root of this repository
# for complete details.

from __future__ import annotations

import pickle

import pytest

from packaging.ranges import (
    VersionRange,
    _BoundaryKind,
    _BoundaryVersion,
)
from packaging.specifiers import Specifier, SpecifierSet
from packaging.version import Version


class TestDirectConstructionForbidden:
    def test_call_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="cannot create 'VersionRange' instances"):
            VersionRange()

    def test_call_with_args_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            VersionRange("anything")

    def test_call_with_kwargs_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            VersionRange(bounds=())

    def test_subclass_call_raises_too(self) -> None:
        # __new__ raises before any subclass __init__ runs.
        class Sub(VersionRange):
            pass

        with pytest.raises(TypeError):
            Sub()


def test_ranges_module_reexports_specifiers_class() -> None:
    # ``packaging.ranges`` re-exports the class defined in
    # ``packaging.specifiers``; both imports must resolve to the same
    # object so ``isinstance`` checks and pickling stay consistent.
    from packaging.ranges import VersionRange as ranges_VR  # noqa: PLC0415
    from packaging.specifiers import VersionRange as specifiers_VR  # noqa: PLC0415

    assert ranges_VR is specifiers_VR


class TestToRangeMethods:
    """``Specifier.to_range`` and ``SpecifierSet.to_range`` are
    convenience methods that delegate to the corresponding
    :class:`VersionRange` classmethod factories.  They must produce the
    same result as the factories."""

    def test_specifier_to_range(self) -> None:
        spec = Specifier(">=1.0")
        method_result = spec.to_range()
        factory_result = VersionRange.from_specifier(spec)
        assert method_result == factory_result
        assert method_result is not None
        assert "1.5" in method_result

    def test_specifier_to_range_arbitrary(self) -> None:
        assert Specifier("===wat").to_range() is None

    def test_specifier_set_to_range(self) -> None:
        ss = SpecifierSet(">=1.0,<2.0")
        method_result = ss.to_range()
        factory_result = VersionRange.from_specifier_set(ss)
        # The factories share a per-SpecifierSet cache; both must
        # return the *same object*.
        assert method_result is factory_result
        assert method_result is not None
        assert "1.5" in method_result

    def test_specifier_set_to_range_arbitrary(self) -> None:
        assert SpecifierSet("===wat").to_range() is None

    def test_specifier_set_to_range_empty(self) -> None:
        r = SpecifierSet("").to_range()
        assert r is not None
        assert "0.1" in r

    def test_specifier_set_to_range_unsatisfiable(self) -> None:
        r = SpecifierSet(">=2,<1").to_range()
        assert r is not None
        assert r.is_empty


class TestFromSpecifier:
    def test_returns_version_range(self) -> None:
        r = VersionRange.from_specifier(Specifier(">=1.0"))
        assert isinstance(r, VersionRange)
        assert "1.0" in r
        assert "0.5" not in r

    def test_arbitrary_returns_none(self) -> None:
        assert VersionRange.from_specifier(Specifier("===wat")) is None
        assert VersionRange.from_specifier(Specifier("===1.0")) is None

    def test_unsatisfiable_returns_empty_range(self) -> None:
        # ``<V`` for V at the smallest possible version yields an empty
        # range, not None.
        r = VersionRange.from_specifier(Specifier("<0"))
        assert isinstance(r, VersionRange)
        assert r.is_empty

    def test_wildcard(self) -> None:
        r = VersionRange.from_specifier(Specifier("==1.2.*"))
        assert isinstance(r, VersionRange)
        assert "1.2" in r
        assert "1.2.3" in r
        assert "1.3" not in r
        assert "1.1" not in r

    def test_compatible_release(self) -> None:
        r = VersionRange.from_specifier(Specifier("~=1.2.3"))
        assert isinstance(r, VersionRange)
        assert "1.2.3" in r
        assert "1.2.99" in r
        assert "1.3" not in r

    def test_not_equal_disjoint(self) -> None:
        r = VersionRange.from_specifier(Specifier("!=1.5"))
        assert isinstance(r, VersionRange)
        assert "1.4" in r
        assert "1.5" not in r
        assert "1.6" in r


class TestFromSpecifierSet:
    def test_simple(self) -> None:
        r = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
        assert isinstance(r, VersionRange)
        assert "1.5" in r
        assert "2.0" not in r

    def test_arbitrary_returns_none(self) -> None:
        assert VersionRange.from_specifier_set(SpecifierSet("===wat")) is None
        assert VersionRange.from_specifier_set(SpecifierSet("===wat,>=1")) is None
        # Order does not matter.
        assert VersionRange.from_specifier_set(SpecifierSet(">=1,===wat")) is None

    def test_empty_specifier_set_is_full_range(self) -> None:
        r = VersionRange.from_specifier_set(SpecifierSet(""))
        assert isinstance(r, VersionRange)
        assert "0.1" in r
        assert "999.0" in r

    def test_unsatisfiable_returns_empty_range(self) -> None:
        r = VersionRange.from_specifier_set(SpecifierSet(">=2,<1"))
        assert isinstance(r, VersionRange)
        assert r.is_empty

    def test_intersection_via_combine(self) -> None:
        ss = SpecifierSet(">=1.0,<3.0") & SpecifierSet(">=2.0,<4.0")
        r = VersionRange.from_specifier_set(ss)
        assert isinstance(r, VersionRange)
        assert "2.5" in r
        assert "1.5" not in r
        assert "3.5" not in r

    def test_caching_returns_same_object(self) -> None:
        ss = SpecifierSet(">=1.0,<2.0")
        first = VersionRange.from_specifier_set(ss)
        second = VersionRange.from_specifier_set(ss)
        assert first is second

    def test_caching_for_arbitrary_returns_same_none(self) -> None:
        ss = SpecifierSet("===wat")
        first = VersionRange.from_specifier_set(ss)
        second = VersionRange.from_specifier_set(ss)
        assert first is None
        assert second is None

    def test_cache_invalidates_on_canonicalize(self) -> None:
        # Building a SpecifierSet from an iterable leaves it
        # un-canonicalized; iterating triggers canonicalization, which
        # must invalidate the from_specifier_set cache.
        ss = SpecifierSet([Specifier(">=1.0"), Specifier("<2.0"), Specifier(">=1.0")])
        first = VersionRange.from_specifier_set(ss)
        # Force canonicalization (deduplicates the spec list).
        str(ss)
        second = VersionRange.from_specifier_set(ss)
        # The cache is cleared and re-populated; equality must hold.
        assert first == second

    def test_cache_invalidates_on_prereleases_setter(self) -> None:
        ss = SpecifierSet(">=1.0")
        first = VersionRange.from_specifier_set(ss)
        ss.prereleases = True
        second = VersionRange.from_specifier_set(ss)
        # The range itself does not depend on prereleases, so the
        # recomputed result is structurally equal.
        assert first == second

    def test_combination_with_arbitrary_returns_none(self) -> None:
        a = SpecifierSet(">=1.0")
        b = SpecifierSet("===wat")
        assert VersionRange.from_specifier_set(a & b) is None


class TestContains:
    def test_simple_lower_inclusive(self) -> None:
        r = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
        assert r is not None
        assert "1.0" in r
        assert "1.5" in r
        assert "2.0" not in r
        assert "0.9" not in r

    def test_simple_upper_inclusive(self) -> None:
        r = VersionRange.from_specifier_set(SpecifierSet(">1.0,<=2.0"))
        assert r is not None
        assert "1.0" not in r
        assert "1.5" in r
        assert "2.0" in r
        assert "2.0.1" not in r

    def test_disjoint_excluded(self) -> None:
        r = VersionRange.from_specifier_set(SpecifierSet(">=1.0,!=1.5"))
        assert r is not None
        assert "1.0" in r
        assert "1.4" in r
        assert "1.5" not in r
        assert "1.6" in r

    def test_empty_range_contains_nothing(self) -> None:
        r = VersionRange.from_specifier_set(SpecifierSet(">=2.0,<1.0"))
        assert r is not None
        assert "0.5" not in r
        assert "1.5" not in r
        assert "2.5" not in r

    def test_full_range_contains_anything_parseable(self) -> None:
        r = VersionRange.from_specifier_set(SpecifierSet(""))
        assert r is not None
        assert "0.1" in r
        assert "999.0" in r
        assert "1.0a1" in r

    def test_unparsable_string_not_contained(self) -> None:
        r = VersionRange.from_specifier_set(SpecifierSet(">=1.0"))
        assert r is not None
        assert "not-a-version" not in r
        assert "" not in r

    def test_version_object(self) -> None:
        r = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
        assert r is not None
        assert Version("1.5") in r
        assert Version("2.0") not in r

    def test_local_segment_handling(self) -> None:
        # PEP 440: <=1.0 includes 1.0+local
        r = VersionRange.from_specifier_set(SpecifierSet("<=1.0"))
        assert r is not None
        assert "1.0" in r
        assert "1.0+local" in r

    def test_post_release_excluded_by_gt(self) -> None:
        # PEP 440: >1.0 excludes 1.0.postN
        r = VersionRange.from_specifier_set(SpecifierSet(">1.0"))
        assert r is not None
        assert "1.0" not in r
        assert "1.0.post1" not in r
        assert "1.1" in r


class TestEmpty:
    def test_unsatisfiable_is_empty(self) -> None:
        r = VersionRange.from_specifier_set(SpecifierSet(">=2,<1"))
        assert r is not None
        assert r.is_empty
        assert not bool(r)

    def test_normal_is_not_empty(self) -> None:
        r = VersionRange.from_specifier_set(SpecifierSet(">=1,<2"))
        assert r is not None
        assert not r.is_empty
        assert bool(r)

    def test_empty_specifier_set_is_not_empty(self) -> None:
        r = VersionRange.from_specifier_set(SpecifierSet(""))
        assert r is not None
        assert not r.is_empty
        assert bool(r)


class TestEquality:
    def test_same_range_equal(self) -> None:
        r1 = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
        r2 = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
        assert r1 == r2

    def test_equivalent_specifiers_equal(self) -> None:
        # Two SpecifierSets that intersect to the same range should
        # produce equal VersionRanges.
        r1 = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
        r2 = VersionRange.from_specifier_set(
            SpecifierSet(">=1.0") & SpecifierSet("<2.0")
        )
        assert r1 == r2

    def test_different_ranges_unequal(self) -> None:
        r1 = VersionRange.from_specifier_set(SpecifierSet(">=1.0"))
        r2 = VersionRange.from_specifier_set(SpecifierSet(">=2.0"))
        assert r1 != r2

    def test_compare_to_other_types(self) -> None:
        r = VersionRange.from_specifier_set(SpecifierSet(">=1.0"))
        assert r != "VersionRange"
        assert r != 42
        assert r != None  # noqa: E711

    def test_hash_matches_equality(self) -> None:
        r1 = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
        r2 = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
        assert hash(r1) == hash(r2)

    def test_hashable_in_set(self) -> None:
        r1 = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
        r2 = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
        r3 = VersionRange.from_specifier_set(SpecifierSet(">=3.0"))
        assert len({r1, r2, r3}) == 2


class TestRepr:
    def test_repr_normal(self) -> None:
        r = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
        assert repr(r) == "<VersionRange '[1.0, 2.0.dev0)'>"

    def test_repr_empty(self) -> None:
        r = VersionRange.from_specifier_set(SpecifierSet(">=2.0,<1.0"))
        assert repr(r) == "<VersionRange '(empty)'>"

    def test_repr_full(self) -> None:
        r = VersionRange.from_specifier_set(SpecifierSet(""))
        assert repr(r) == "<VersionRange '(-inf, +inf)'>"

    def test_repr_disjoint(self) -> None:
        r = VersionRange.from_specifier_set(SpecifierSet("!=1.0"))
        # Two disjoint ranges separated by " | ".
        assert " | " in repr(r)
        assert "(-inf" in repr(r)
        assert "+inf)" in repr(r)

    def test_repr_with_boundary(self) -> None:
        # AFTER_LOCALS / AFTER_POSTS still produce a valid repr.
        r = VersionRange.from_specifier_set(SpecifierSet("<=1.0"))
        text = repr(r)
        assert text.startswith("<VersionRange")
        assert "1.0" in text


class TestPickle:
    def test_pickle_round_trip(self) -> None:
        r = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
        restored = pickle.loads(pickle.dumps(r))
        assert restored == r
        assert "1.5" in restored
        assert "2.5" not in restored

    def test_pickle_empty_range(self) -> None:
        r = VersionRange.from_specifier_set(SpecifierSet(">=2.0,<1.0"))
        restored = pickle.loads(pickle.dumps(r))
        assert restored == r
        assert restored.is_empty

    def test_pickle_full_range(self) -> None:
        r = VersionRange.from_specifier_set(SpecifierSet(""))
        restored = pickle.loads(pickle.dumps(r))
        assert restored == r
        assert "1.0" in restored

    def test_pickle_disjoint(self) -> None:
        r = VersionRange.from_specifier_set(SpecifierSet("!=1.5"))
        restored = pickle.loads(pickle.dumps(r))
        assert restored == r
        assert "1.5" not in restored
        assert "1.6" in restored

    def test_pickle_at_all_protocols(self) -> None:
        r = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
        for protocol in range(pickle.HIGHEST_PROTOCOL + 1):
            restored = pickle.loads(pickle.dumps(r, protocol=protocol))
            assert restored == r


class TestBoundaryClosureEdgeCases:
    """Edge cases for the closure-based boundary checks.

    These exercise branches that fire when the parsed version's
    release tuple is shorter than the bound version's trimmed
    release: uncommon in real specifiers but reachable.
    """

    def test_after_posts_short_release_above(self) -> None:
        # ``>1.0.0.5`` against a version with a shorter release than V.
        # ``2`` has cmpkey > V but len(release)=1 < len(v_trimmed)=4.
        r = VersionRange.from_specifier_set(SpecifierSet(">1.0.0.5"))
        assert r is not None
        assert "2" in r  # above the boundary
        assert "1" not in r  # below the boundary
        assert "1.0.0.5" not in r  # the boundary excludes V itself

    def test_after_locals_short_release_below(self) -> None:
        # ``<=1.0.0.5`` against a parsed version with a shorter release.
        # ``2`` has cmpkey > V but len(release)=1 < len(v_trimmed)=4,
        # so it cannot be in V's local family.
        r = VersionRange.from_specifier_set(SpecifierSet("<=1.0.0.5"))
        assert r is not None
        assert "1" in r  # below
        assert "1.0.0.5" in r  # equal
        assert "1.0.0.5+local" in r  # local family
        assert "2" not in r  # above + not in local family

    def test_after_locals_lower_short_release(self) -> None:
        # ``!=1.0.0.5`` produces an AFTER_LOCALS lower bound on the
        # second range.  Ensure short-release versions resolve correctly.
        r = VersionRange.from_specifier_set(SpecifierSet("!=1.0.0.5"))
        assert r is not None
        assert "2" in r  # above the AFTER_LOCALS boundary
        assert "1" in r  # below the lower of the second range
        assert "1.0.0.5" not in r  # the excluded V


class TestBoundaryVersionCompare:
    """The :class:`_BoundaryVersion` class is private but its comparison
    operators must stay correct for the bound-sorting machinery."""

    def test_lt_same_version_different_kind(self) -> None:
        v = Version("1.0")
        a = _BoundaryVersion(v, _BoundaryKind.AFTER_LOCALS)
        b = _BoundaryVersion(v, _BoundaryKind.AFTER_POSTS)
        # AFTER_LOCALS sorts before AFTER_POSTS for the same V.
        assert a < b
        assert not (b < a)

    def test_gt_same_version_different_kind(self) -> None:
        v = Version("1.0")
        a = _BoundaryVersion(v, _BoundaryKind.AFTER_LOCALS)
        b = _BoundaryVersion(v, _BoundaryKind.AFTER_POSTS)
        assert b > a
        assert not (a > b)

    def test_lt_different_versions(self) -> None:
        a = _BoundaryVersion(Version("1.0"), _BoundaryKind.AFTER_POSTS)
        b = _BoundaryVersion(Version("2.0"), _BoundaryKind.AFTER_POSTS)
        assert a < b


class TestDoctests:
    def test_doctest_examples(self) -> None:
        # Mirror the module-level doctest examples.
        r = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
        assert r is not None
        assert "1.5" in r
        assert "2.0" not in r
        assert bool(r) is True
        assert (
            bool(VersionRange.from_specifier_set(SpecifierSet(">=2.0,<1.0"))) is False
        )


class TestEmptyFactory:
    """``VersionRange.empty`` builds the additive identity for union."""

    def test_returns_empty_range(self) -> None:
        r = VersionRange.empty()
        assert isinstance(r, VersionRange)
        assert r.is_empty
        assert not bool(r)

    def test_contains_nothing(self) -> None:
        r = VersionRange.empty()
        assert "1.0" not in r
        assert Version("1.0") not in r
        assert "0" not in r

    def test_intersect_with_empty_is_empty(self) -> None:
        any_r = VersionRange.unbounded()
        e = VersionRange.empty()
        assert any_r.intersect(e).is_empty
        assert e.intersect(any_r).is_empty

    def test_union_with_empty_is_self(self) -> None:
        a = VersionRange.from_specifier(Specifier(">=1.0"))
        assert a is not None
        e = VersionRange.empty()
        assert a.union(e) == a
        assert e.union(a) == a

    def test_complement_of_empty_is_unbounded(self) -> None:
        assert VersionRange.empty().complement() == VersionRange.unbounded()

    def test_equal_across_constructions(self) -> None:
        a = VersionRange.empty()
        b = VersionRange.from_specifier_set(SpecifierSet(">=2,<1"))
        assert b is not None
        assert a == b
        assert hash(a) == hash(b)


class TestUnboundedFactory:
    """``VersionRange.unbounded`` builds the multiplicative identity for intersect."""

    def test_returns_full_range(self) -> None:
        r = VersionRange.unbounded()
        assert isinstance(r, VersionRange)
        assert not r.is_empty
        assert bool(r)

    def test_contains_anything_parseable(self) -> None:
        r = VersionRange.unbounded()
        assert "0" in r
        assert "999.999.999" in r
        assert "1.0a1" in r
        assert "not-a-version" not in r

    def test_intersect_with_unbounded_is_self(self) -> None:
        a = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
        assert a is not None
        u = VersionRange.unbounded()
        assert a.intersect(u) == a
        assert u.intersect(a) == a

    def test_union_with_unbounded_is_unbounded(self) -> None:
        a = VersionRange.from_specifier(Specifier(">=1.0"))
        assert a is not None
        u = VersionRange.unbounded()
        assert a.union(u) == u
        assert u.union(a) == u

    def test_complement_of_unbounded_is_empty(self) -> None:
        assert VersionRange.unbounded().complement().is_empty

    def test_equal_to_empty_specifier_set(self) -> None:
        assert VersionRange.unbounded() == VersionRange.from_specifier_set(
            SpecifierSet("")
        )


class TestExactFactory:
    """``VersionRange.exact`` builds the singleton range."""

    def test_from_string(self) -> None:
        r = VersionRange.exact("1.2.3")
        assert "1.2.3" in r
        assert "1.2.4" not in r
        assert "1.2.2" not in r

    def test_from_version_object(self) -> None:
        r = VersionRange.exact(Version("1.2.3"))
        assert "1.2.3" in r
        assert "1.2.4" not in r

    def test_invalid_string_raises(self) -> None:
        from packaging.version import InvalidVersion  # noqa: PLC0415

        with pytest.raises(InvalidVersion):
            VersionRange.exact("not-a-version")

    def test_equal_to_eq_specifier(self) -> None:
        # ``==1.2.3`` matches ``1.2.3`` and ``1.2.3+local``; ``exact``
        # is the strict singleton — not the same range.
        exact = VersionRange.exact("1.2.3")
        eq_spec = VersionRange.from_specifier(Specifier("==1.2.3"))
        assert eq_spec is not None
        assert "1.2.3+local" in eq_spec
        assert "1.2.3+local" not in exact

    def test_intersect_disjoint_exacts_is_empty(self) -> None:
        a = VersionRange.exact("1.0")
        b = VersionRange.exact("2.0")
        assert a.intersect(b).is_empty

    def test_intersect_equal_exacts_is_self(self) -> None:
        a = VersionRange.exact("1.0")
        b = VersionRange.exact("1.0")
        assert a.intersect(b) == a

    def test_hashable(self) -> None:
        a = VersionRange.exact("1.0")
        b = VersionRange.exact("1.0")
        assert hash(a) == hash(b)
        assert len({a, b, VersionRange.exact("2.0")}) == 2


class TestUnion:
    def test_disjoint_exacts(self) -> None:
        a = VersionRange.exact("1.0")
        b = VersionRange.exact("2.0")
        u = a.union(b)
        assert "1.0" in u
        assert "2.0" in u
        assert "1.5" not in u

    def test_overlapping_intervals_collapse(self) -> None:
        a = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
        b = VersionRange.from_specifier_set(SpecifierSet(">=1.5,<3.0"))
        assert a is not None
        assert b is not None
        u = a.union(b)
        assert "1.0" in u
        assert "2.5" in u
        assert "3.0" not in u
        assert "0.5" not in u

    def test_union_with_self_is_self(self) -> None:
        a = VersionRange.from_specifier(Specifier(">=1.0"))
        assert a is not None
        assert a.union(a) == a

    def test_union_is_commutative(self) -> None:
        a = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
        b = VersionRange.from_specifier_set(SpecifierSet(">=3.0,<4.0"))
        assert a is not None
        assert b is not None
        assert a.union(b) == b.union(a)

    def test_union_is_associative(self) -> None:
        a = VersionRange.exact("1.0")
        b = VersionRange.exact("2.0")
        c = VersionRange.exact("3.0")
        assert a.union(b).union(c) == a.union(b.union(c))

    def test_union_of_neg_complementary_ranges_covers_all(self) -> None:
        # ``<1.0`` and ``>=1.0`` partition the version line.
        lower = VersionRange.from_specifier(Specifier("<1.0"))
        upper = VersionRange.from_specifier(Specifier(">=1.0"))
        assert lower is not None
        assert upper is not None
        u = lower.union(upper)
        assert "0.5" in u
        assert "1.0" in u
        assert "999" in u

    def test_union_preserves_disjoint_repr_count(self) -> None:
        # Two non-adjacent ranges keep both intervals.
        a = VersionRange.exact("1.0")
        b = VersionRange.exact("3.0")
        u = a.union(b)
        assert " | " in repr(u)

    def test_union_of_two_unbounded_lower_collapses(self) -> None:
        # ``<2`` already covers ``<1``; the union is just ``<2``.
        a = VersionRange.from_specifier(Specifier("<1"))
        b = VersionRange.from_specifier(Specifier("<2"))
        assert a is not None
        assert b is not None
        assert a.union(b) == b

    def test_union_of_two_unbounded_upper_collapses(self) -> None:
        a = VersionRange.from_specifier(Specifier(">=1"))
        b = VersionRange.from_specifier(Specifier(">=2"))
        assert a is not None
        assert b is not None
        assert a.union(b) == a

    def test_touching_inclusive_exclusive_collapses(self) -> None:
        # ``[1.0, 2.0)`` U ``[2.0, 3.0)`` == ``[1.0, 3.0)``.
        a = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
        b = VersionRange.from_specifier_set(SpecifierSet(">=2.0,<3.0"))
        assert a is not None
        assert b is not None
        u = a.union(b)
        assert "1.5" in u
        assert "2.0" in u
        assert "2.999" in u
        assert "3.0" not in u

    def test_touching_exclusive_exclusive_does_not_collapse(self) -> None:
        # ``[1.0, 2.0)`` U ``(2.0, 3.0)`` excludes ``2.0`` itself.
        a = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
        b = VersionRange.from_specifier_set(SpecifierSet(">2.0,<3.0"))
        assert a is not None
        assert b is not None
        u = a.union(b)
        assert "2.0" not in u
        assert "1.5" in u
        assert "2.5" in u


class TestComplement:
    def test_complement_of_unbounded(self) -> None:
        assert VersionRange.unbounded().complement().is_empty

    def test_complement_of_empty(self) -> None:
        assert VersionRange.empty().complement() == VersionRange.unbounded()

    def test_double_complement_is_identity(self) -> None:
        for spec_str in [">=1.0", "<2.0", ">=1.0,<2.0", "!=1.5", "==1.0", ">1.0,<=2.0"]:
            r = VersionRange.from_specifier_set(SpecifierSet(spec_str))
            assert r is not None
            assert r.complement().complement() == r

    def test_union_with_complement_is_unbounded(self) -> None:
        for spec in [">=1.0", "<2.0", ">=1.0,<2.0", "!=1.5"]:
            r = VersionRange.from_specifier_set(SpecifierSet(spec))
            assert r is not None
            assert r.union(r.complement()) == VersionRange.unbounded()

    def test_intersect_with_complement_is_empty(self) -> None:
        for spec in [">=1.0", "<2.0", ">=1.0,<2.0", "!=1.5"]:
            r = VersionRange.from_specifier_set(SpecifierSet(spec))
            assert r is not None
            assert r.intersect(r.complement()).is_empty

    def test_complement_of_lower_bound(self) -> None:
        r = VersionRange.from_specifier(Specifier(">=2.0"))
        assert r is not None
        c = r.complement()
        assert "1.0" in c
        assert "2.0" not in c
        assert "3.0" not in c

    def test_complement_of_upper_bound(self) -> None:
        r = VersionRange.from_specifier(Specifier("<2.0"))
        assert r is not None
        c = r.complement()
        assert "1.0" not in c
        assert "2.0" in c
        assert "3.0" in c

    def test_complement_of_disjoint_ranges(self) -> None:
        # Complement of a !=V range is the {V} singleton.
        r = VersionRange.from_specifier(Specifier("!=1.5"))
        assert r is not None
        c = r.complement()
        assert "1.5" in c
        assert "1.4" not in c
        assert "1.6" not in c

    def test_complement_creates_after_posts_upper_bound(self) -> None:
        # ``>1.0,<=2.0`` has an AFTER_POSTS lower bound; complementing
        # moves it to an upper bound, exercising :func:`_make_below_after_posts`.
        # The leading interval ``(-inf, AFTER_POSTS(1.0)]`` is what hits
        # the new predicate; pick versions whose membership in that
        # interval is determined by the post-family check rather than
        # by a fall-through to the second interval.
        r = VersionRange.from_specifier_set(SpecifierSet(">1.0,<=2.0"))
        assert r is not None
        c = r.complement()
        # 1.0 itself: at v cmpkey-wise, below the boundary.
        assert "1.0" in c
        # 1.0+local: in v's local family, below v cmpkey-wise.
        assert "1.0+local" in c
        # 1.0.post0: in v's post family, above v cmpkey-wise — only
        # the leading interval can include this (cmpkey < 2.0).
        assert "1.0.post0" in c
        # 1.0.post5+local: in v's post family with a local segment.
        assert "1.0.post5+local" in c
        # 1.5: above the AFTER_POSTS upper bound, in original range.
        assert "1.5" not in c
        # Shorter release than v's trimmed: parsed_release shorter path.
        # ``>1.0.0.5`` produces v=1.0.0.5.  ``1`` is below v cmpkey-wise
        # so v_ge handles it; ``1.0.0`` is also below v cmpkey-wise.
        # The shorter-release path fires when parsed > v cmpkey but
        # release is shorter than n_trimmed; that requires a release
        # like ``2`` after the upper bound includes 1.0.0.5.  Use a
        # different fixture: an isolated complement whose only interval
        # is the AFTER_POSTS one.
        r2 = VersionRange.from_specifier_set(SpecifierSet(">1.0.0.5"))
        assert r2 is not None
        c2 = r2.complement()  # (-inf, AFTER_POSTS(1.0.0.5)]
        assert "1.0.0.5" in c2  # at v
        assert "1.0.0.5.post0" in c2  # in post family
        # ``2`` has release=(2,), shorter than v's trimmed (1,0,0,5);
        # not in family, above v cmpkey-wise.
        assert "2" not in c2
        # ``1.0.0.6`` has same length but different prefix.
        assert "1.0.0.6" not in c2
        # Tail-zero release that DOES match the family.
        r3 = VersionRange.from_specifier_set(SpecifierSet(">1.0"))
        assert r3 is not None
        c3 = r3.complement()  # (-inf, AFTER_POSTS(1.0)]
        # ``1.0.0`` parses with release=(1,0,0); v=1.0 trims to (1,);
        # extra components are zero, so in family.
        assert "1.0.0" in c3
        assert "1.0.0.post0" in c3
        # Different release tail with non-zero component.
        assert "1.0.1" not in c3
        # Different pre-release.
        # ``>1.0a1`` is the only spec form that gives v with a pre.
        # Build a complement covering parsed.pre != v_pre.
        r4 = VersionRange.from_specifier_set(SpecifierSet(">1.0a1"))
        assert r4 is not None
        c4 = r4.complement()  # (-inf, AFTER_POSTS(1.0a1)]
        # ``1.0a2`` is above v_a1 cmpkey-wise but has a different pre.
        assert "1.0a2" not in c4
        # Different epoch: parsed.epoch != v_epoch path.  ``2!1.0`` has
        # cmpkey > v=1.0, so v_ge is False; epoch differs, so the
        # boundary check returns False (not in family).
        assert "2!1.0" not in c3


class TestOperatorAliases:
    def test_and_aliases_intersect(self) -> None:
        a = VersionRange.from_specifier(Specifier(">=1.0"))
        b = VersionRange.from_specifier(Specifier("<2.0"))
        assert a is not None
        assert b is not None
        assert (a & b) == a.intersect(b)

    def test_or_aliases_union(self) -> None:
        a = VersionRange.exact("1.0")
        b = VersionRange.exact("2.0")
        assert (a | b) == a.union(b)

    def test_invert_aliases_complement(self) -> None:
        r = VersionRange.from_specifier(Specifier(">=1.0"))
        assert r is not None
        assert (~r) == r.complement()

    def test_and_with_non_range_returns_notimplemented(self) -> None:
        a = VersionRange.from_specifier(Specifier(">=1.0"))
        assert a is not None
        with pytest.raises(TypeError):
            a & "not a range"  # type: ignore[operator]
        with pytest.raises(TypeError):
            a & 42  # type: ignore[operator]

    def test_or_with_non_range_returns_notimplemented(self) -> None:
        a = VersionRange.from_specifier(Specifier(">=1.0"))
        assert a is not None
        with pytest.raises(TypeError):
            a | "not a range"  # type: ignore[operator]
        with pytest.raises(TypeError):
            a | 42  # type: ignore[operator]

    def test_chained_operations(self) -> None:
        # ``(>=1) & (<2) | (==3)``
        ge1 = VersionRange.from_specifier(Specifier(">=1.0"))
        lt2 = VersionRange.from_specifier(Specifier("<2.0"))
        eq3 = VersionRange.exact("3.0")
        assert ge1 is not None
        assert lt2 is not None
        result = (ge1 & lt2) | eq3
        assert "1.5" in result
        assert "3.0" in result
        assert "2.5" not in result


class TestSetAlgebra:
    """De Morgan and basic set-theoretic identities."""

    def test_de_morgan_intersect(self) -> None:
        # ~(A & B) == ~A U ~B
        a = VersionRange.from_specifier(Specifier(">=1.0"))
        b = VersionRange.from_specifier(Specifier("<2.0"))
        assert a is not None
        assert b is not None
        assert ~(a & b) == (~a) | (~b)

    def test_de_morgan_union(self) -> None:
        # ~(A U B) == ~A & ~B
        a = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
        b = VersionRange.from_specifier_set(SpecifierSet(">=3.0,<4.0"))
        assert a is not None
        assert b is not None
        assert ~(a | b) == (~a) & (~b)

    def test_distributivity_intersect_over_union(self) -> None:
        # A & (B U C) == (A & B) U (A & C)
        a = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<5.0"))
        b = VersionRange.from_specifier_set(SpecifierSet(">=2.0,<3.0"))
        c = VersionRange.from_specifier_set(SpecifierSet(">=4.0,<5.0"))
        assert a is not None
        assert b is not None
        assert c is not None
        assert a & (b | c) == (a & b) | (a & c)

    def test_distributivity_union_over_intersect(self) -> None:
        # A U (B & C) == (A U B) & (A U C)
        a = VersionRange.from_specifier_set(SpecifierSet(">=10.0"))
        b = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<3.0"))
        c = VersionRange.from_specifier_set(SpecifierSet(">=2.0,<4.0"))
        assert a is not None
        assert b is not None
        assert c is not None
        assert a | (b & c) == (a | b) & (a | c)
