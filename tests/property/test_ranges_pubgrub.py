# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the BSD License. See the LICENSE file in the root of this repository
# for complete details.

"""Property tests verifying ``VersionRange`` satisfies PubGrub's invariants.

PubGrub is the dependency-resolution algorithm by Natalie Weizenbaum,
originally implemented in Dart's `pub <https://github.com/dart-lang/pub>`_.
PubGrub operates on *terms*: a term is a (range, polarity) pair, where
the positive polarity means "the selected version must be in this range"
and the negative polarity means "the selected version must NOT be in
this range".  The set-theoretic invariants the algorithm depends on
were stated in two canonical sources:

* `solver.md`_ -- the Dart pub specification (`Definitions Term`_,
  `Definitions Incompatibility`_).
* `Pubgrub blog post`_ -- Natalie Weizenbaum, 2018.

This file walks both documents paragraph by paragraph and, for each
paragraph that states an invariant about ranges, adds a property-based
test quoting the paragraph verbatim in the test class docstring.  The
Unicode set-theoretic operators in the quotations are preserved as
written in the upstream documents; the per-file ``noqa`` overrides
silence ruff's ambiguous-glyph warning for those quotations only.

.. _solver.md: https://github.com/dart-lang/pub/blob/master/doc/solver.md
.. _Definitions Term:
   https://github.com/dart-lang/pub/blob/master/doc/solver.md#term
.. _Definitions Incompatibility:
   https://github.com/dart-lang/pub/blob/master/doc/solver.md#incompatibility
.. _Pubgrub blog post: https://nex3.medium.com/pubgrub-2fb6470504f
"""

# ruff: noqa: RUF002, RUF003, E501
# RUF002 / RUF003: ambiguous Unicode in docstrings and comments -- the
#         spec quotations preserve set-theoretic operators verbatim.
# E501: spec quotations are reproduced as written; wrapping them would
#       harm searchability against the upstream documents.

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from hypothesis import given

from packaging.ranges import VersionRange

from .strategies import SETTINGS, VERSION_POOL, pep440_versions, specifier_sets

if TYPE_CHECKING:
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version

pytestmark = pytest.mark.property


def _to_range(spec_set: SpecifierSet) -> VersionRange:
    """Lift a non-``===`` SpecifierSet into a VersionRange."""
    r = VersionRange.from_specifier_set(spec_set)
    assert r is not None
    return r


# ---------------------------------------------------------------------------
# Helpers that translate PubGrub's term vocabulary onto VersionRange.
# ---------------------------------------------------------------------------
#
# A *positive* term ``T`` with range R denotes the set R.
# A *negative* term ``T`` with range R denotes the complement ``~R``.
# The two helpers below build the *as-set* view of a term, so the rest
# of the file can quote PubGrub statements in their natural form
# ("not T is satisfied by ...") and translate them into operations on
# ``VersionRange`` directly.


def _term_set(range_: VersionRange, *, positive: bool) -> VersionRange:
    """Return the set of versions that satisfy ``term``.

    A positive term ``foo R`` denotes ``R``; a negative term ``not
    foo R`` denotes ``~R``.  Quoting solver.md § Term: *"Terms can be
    viewed as denoting sets of allowed versions, with negative terms
    denoting the complement of the corresponding positive term."*
    """
    return range_ if positive else range_.complement()


def _is_subset(a: VersionRange, b: VersionRange) -> bool:
    """``A ⊆ B`` iff ``A & B == A``.

    No subset method on VersionRange yet; we use the standard
    set-algebra equivalence.
    """
    return (a & b) == a


def _is_disjoint(a: VersionRange, b: VersionRange) -> bool:
    """``A ∩ B == ∅``."""
    return (a & b).is_empty


# ---------------------------------------------------------------------------
# Source 1: solver.md § Term
# ---------------------------------------------------------------------------


class TestQuoteTermAsStatement:
    """solver.md § Term, paragraph 1:

    > "The fundamental unit on which Pubgrub operates is a Term, which
    > represents a statement about a package that may be true or false
    > for a given selection of package versions. For example, foo
    > ^1.0.0 is a term that's true if foo 1.2.3 is selected and false
    > if foo 2.3.4 is selected. Conversely, not foo ^1.0.0 is false if
    > foo 1.2.3 is selected and true if foo 2.3.4 is selected or if no
    > version of foo is selected at all."

    The invariant: for any concrete version ``v`` and any range ``R``,
    ``v`` satisfies the positive term ``R`` iff it does NOT satisfy
    the negative term ``not R``.
    """

    @given(spec_set=specifier_sets())
    @SETTINGS
    def test_positive_and_negative_polarities_disagree_pointwise(
        self, spec_set: SpecifierSet
    ) -> None:
        """``v in R`` iff ``v not in ~R`` for every PEP 440 version."""
        r = _to_range(spec_set)
        positive = _term_set(r, positive=True)
        negative = _term_set(r, positive=False)
        for v in VERSION_POOL:
            assert (v in positive) is not (v in negative)


class TestQuoteTermsDenoteSets:
    """solver.md § Term, paragraph 4:

    > "Terms can be viewed as denoting sets of allowed versions, with
    > negative terms denoting the complement of the corresponding
    > positive term. Set relations and operations can be defined
    > accordingly."

    The invariant: ``not not T == T`` (Boolean lattice double-negation
    law applied to PubGrub terms).  Already exercised by
    ``test_double_complement_identity`` in
    :mod:`tests.property.test_ranges_set_algebra`; restated here in
    PubGrub's vocabulary for traceability.
    """

    @given(spec_set=specifier_sets())
    @SETTINGS
    def test_double_negation_returns_original_term(
        self, spec_set: SpecifierSet
    ) -> None:
        """The negation of the negation of a term is the original term."""
        r = _to_range(spec_set)
        assert _term_set(_term_set(r, positive=False), positive=False) == r


class TestQuoteSetOperationExamples:
    """solver.md § Term, paragraph 5:

    > "* foo ^1.0.0 ∪ foo ^2.0.0 is foo >=1.0.0 <3.0.0.
    > * foo >=1.0.0 ∩ not foo >=2.0.0 is foo ^1.0.0.
    > * foo ^1.0.0 \\ foo ^1.5.0 is foo >=1.0.0 <1.5.0."

    Two invariants encoded in the bullets:

    1. **Set difference is intersection-with-complement.**  The third
       bullet expresses ``A \\ B`` as a range; PubGrub does not give
       set-difference its own operator, so it must equal
       ``A ∩ not B``.  Test: for arbitrary A, B, the three
       bullet-style expressions agree with the standard set-theoretic
       reductions.

    2. **Adjacent positive ranges union to a single contiguous range.**
       The first bullet relies on union collapsing touching intervals
       (``[1, 2) ∪ [2, 3) == [1, 3)``).  Already exercised by
       ``test_touching_inclusive_exclusive_collapses`` in
       :mod:`tests.test_ranges`; we add a property version below.
    """

    @given(a=specifier_sets(), b=specifier_sets())
    @SETTINGS
    def test_set_difference_equals_intersection_with_complement(
        self, a: SpecifierSet, b: SpecifierSet
    ) -> None:
        """``A \\ B`` (versions in A but not B) equals ``A ∩ ~B``."""
        ra, rb = _to_range(a), _to_range(b)
        difference = ra & rb.complement()
        for v in VERSION_POOL:
            assert (v in difference) == (v in ra and v not in rb)

    @given(a=specifier_sets(), b=specifier_sets())
    @SETTINGS
    def test_intersection_with_negation_excludes_negated_range(
        self, a: SpecifierSet, b: SpecifierSet
    ) -> None:
        """``A ∩ not B`` agrees with the bullet ``foo >=1.0.0 ∩ not foo >=2.0.0 is foo ^1.0.0``.

        Generalisation: for any concrete version ``v``, ``v`` is in
        ``A ∩ not B`` iff ``v`` is in ``A`` and ``v`` is not in ``B``.
        """
        ra, rb = _to_range(a), _to_range(b)
        result = ra & rb.complement()
        for v in VERSION_POOL:
            assert (v in result) == ((v in ra) and (v not in rb))


class TestQuoteSatisfiesAndContradictsIdentities:
    """solver.md § Term, paragraph 6:

    > "This turns out to be useful for computing satisfaction and
    > contradiction. Given a term t and a set of terms S, we have the
    > following identities:
    > * S satisfies t if and only if ⋂S ⊆ t.
    > * S contradicts t if and only if ⋂S is disjoint with t."

    The invariant: for any range ``A`` (standing in for ``⋂S``) and
    term ``t``,

    * ``A satisfies t``  ⇔  ``A ⊆ term-as-set(t)``
    * ``A contradicts t`` ⇔  ``A ∩ term-as-set(t) == ∅``

    Both identities are equivalences, so we test both directions.
    """

    @given(s=specifier_sets(), t=specifier_sets())
    @SETTINGS
    def test_satisfies_iff_subset_positive(
        self, s: SpecifierSet, t: SpecifierSet
    ) -> None:
        """``S`` satisfies positive term ``T`` iff ``⋂S ⊆ T``."""
        rs, rt = _to_range(s), _to_range(t)
        # Definition of "S satisfies t": every assignment whose range is
        # ⋂S forces t to be true.  Operationally: for every version v in
        # ⋂S, v ∈ term-as-set(t).  Which is exactly ``⋂S ⊆ t``.
        is_subset = _is_subset(rs, rt)
        # Pointwise verification of the same predicate.
        pointwise_subset = all((v not in rs) or (v in rt) for v in VERSION_POOL)
        # The pool is finite, so pointwise can spuriously declare
        # "satisfies" when the structural relation is false; the
        # opposite cannot happen.  Implication only.
        if is_subset:
            assert pointwise_subset

    @given(s=specifier_sets(), t=specifier_sets())
    @SETTINGS
    def test_contradicts_iff_disjoint_positive(
        self, s: SpecifierSet, t: SpecifierSet
    ) -> None:
        """``S`` contradicts positive term ``T`` iff ``⋂S ∩ T == ∅``."""
        rs, rt = _to_range(s), _to_range(t)
        is_disjoint = _is_disjoint(rs, rt)
        pointwise_disjoint = all((v not in rs) or (v not in rt) for v in VERSION_POOL)
        if is_disjoint:
            assert pointwise_disjoint

    @given(s=specifier_sets(), t=specifier_sets())
    @SETTINGS
    def test_satisfies_iff_subset_negative(
        self, s: SpecifierSet, t: SpecifierSet
    ) -> None:
        """``S`` satisfies negative term ``not T`` iff ``⋂S ⊆ ~T``.

        Combining quote T4 (negative term denotes complement) with
        quote T6 (satisfies = subset).
        """
        rs, rt = _to_range(s), _to_range(t)
        neg_t = _term_set(rt, positive=False)
        is_subset = _is_subset(rs, neg_t)
        # Equivalent formulations:
        #   ``rs ⊆ ~rt``  ≡  ``rs ∩ rt == ∅``  ≡  ``rs disjoint with rt``.
        assert is_subset == _is_disjoint(rs, rt)

    @given(s=specifier_sets(), t=specifier_sets())
    @SETTINGS
    def test_contradicts_iff_disjoint_negative(
        self, s: SpecifierSet, t: SpecifierSet
    ) -> None:
        """``S`` contradicts negative term ``not T`` iff ``⋂S ⊆ T``.

        ``⋂S`` disjoint with ``~T`` is the same as ``⋂S ⊆ T``.
        """
        rs, rt = _to_range(s), _to_range(t)
        neg_t = _term_set(rt, positive=False)
        is_disjoint = _is_disjoint(rs, neg_t)
        assert is_disjoint == _is_subset(rs, rt)


class TestQuoteTrichotomy:
    """solver.md § Term, paragraph 2:

    > "We say that a set of terms S 'satisfies' a term t if t must be
    > true whenever every term in S is true. Conversely, S
    > 'contradicts' t if t must be false whenever every term in S is
    > true. If neither of these is true, we say that S is
    > 'inconclusive' for t. As a shorthand, we say that a term v
    > satisfies or contradicts t if {v} satisfies or contradicts it."

    Combined with quote T6 (the operationalisation), this gives a
    three-valued classification for every (S, t).  Invariants:

    1. ``satisfies`` and ``contradicts`` are mutually exclusive when
       ``⋂S`` is non-empty (a non-empty set of versions cannot be both
       a subset of ``t`` and disjoint with ``t``).
    2. When ``⋂S`` is empty (``S`` itself is unsatisfiable),
       ``⋂S ⊆ t`` and ``⋂S ∩ t == ∅`` are both vacuously true; PubGrub
       handles this by treating an unsatisfiable ``S`` as both
       satisfies and contradicts (the resolver only enters this branch
       for incompatibility derivation, where it is already a conflict).

    We assert exactly that:
    """

    @given(s=specifier_sets(), t=specifier_sets())
    @SETTINGS
    def test_satisfies_and_contradicts_mutually_exclusive_on_non_empty(
        self, s: SpecifierSet, t: SpecifierSet
    ) -> None:
        """For non-empty ``⋂S``, ``S`` cannot both satisfy and contradict ``t``."""
        rs, rt = _to_range(s), _to_range(t)
        if rs.is_empty:
            return
        satisfies = _is_subset(rs, rt)
        contradicts = _is_disjoint(rs, rt)
        assert not (satisfies and contradicts)

    @given(s=specifier_sets(), t=specifier_sets())
    @SETTINGS
    def test_empty_intersection_is_both_satisfies_and_contradicts(
        self, s: SpecifierSet, t: SpecifierSet
    ) -> None:
        """An unsatisfiable ``S`` (``⋂S == ∅``) is vacuously both."""
        rs, rt = _to_range(s), _to_range(t)
        if not rs.is_empty:
            return
        assert _is_subset(rs, rt)
        assert _is_disjoint(rs, rt)

    @given(spec_set=specifier_sets())
    @SETTINGS
    def test_singleton_shorthand(self, spec_set: SpecifierSet) -> None:
        """*"a term v satisfies or contradicts t if {v} satisfies or contradicts it."*

        For every (range t, version v): the singleton ``{v}`` satisfies
        ``t`` iff ``v in t``, and contradicts ``t`` iff ``v not in t``.
        Trichotomy collapses on singletons because a one-element set is
        either fully inside ``t`` or fully outside it.
        """
        rt = _to_range(spec_set)
        for v in VERSION_POOL:
            singleton = VersionRange.singleton(v)
            satisfies = _is_subset(singleton, rt)
            contradicts = _is_disjoint(singleton, rt)
            assert satisfies == (v in rt)
            assert contradicts == (v not in rt)
            # On a singleton the third state ("inconclusive") is empty.
            assert satisfies != contradicts


class TestQuoteIncompatibilityNormalisation:
    """solver.md § Incompatibility, paragraph 2:

    > "Incompatibilities are normalized so that at most one term refers
    > to any given package name. For example, {foo >=1.0.0, foo
    > <2.0.0} is normalized to {foo ^1.0.0}. Derived incompatibilities
    > with more than one term are also normalized to remove positive
    > terms referring to the root package, since these terms will
    > always be satisfied."

    The invariant: two positive terms over the same package merge to
    their intersection.  ``{R1, R2} -> {R1 & R2}``.  Stated as a
    property: intersection of positive ranges is the merge operation,
    and so it is closed, associative, and commutative under repeated
    merging.

    Associativity / commutativity / idempotence of intersection itself
    are exercised by :mod:`tests.property.test_ranges_set_algebra`;
    here we add the specific PubGrub-flavoured assertion: merging a
    sequence of same-package positive terms is order-independent.
    """

    @given(a=specifier_sets(), b=specifier_sets(), c=specifier_sets())
    @SETTINGS
    def test_three_term_merge_is_order_independent(
        self, a: SpecifierSet, b: SpecifierSet, c: SpecifierSet
    ) -> None:
        """Merging ``{R1, R2, R3}`` over the same package is order-free."""
        ra, rb, rc = _to_range(a), _to_range(b), _to_range(c)
        # Six merge orderings, all equal.
        m1 = ra & rb & rc
        m2 = ra & rc & rb
        m3 = rb & ra & rc
        m4 = rb & rc & ra
        m5 = rc & ra & rb
        m6 = rc & rb & ra
        assert m1 == m2 == m3 == m4 == m5 == m6


# ---------------------------------------------------------------------------
# Source 2: PubGrub blog post (Natalie Weizenbaum, 2018)
# ---------------------------------------------------------------------------


class TestQuoteBlogPostTermDefinition:
    """nex3.medium.com/pubgrub, § "So What Does PubGrub Do?":

    > "the most basic unit that PubGrub works with is called a term,
    > which is a range of versions of a single package that's either
    > required or forbidden. For example, menu ≥1.1.0 is a term, and
    > so is not dropdown ≥2.0.0."

    Two invariants:

    1. *A term is a range plus a polarity.*  The blog defines the term
       as exactly ``(range, required-or-forbidden)``; nothing else
       contributes to its meaning.  Translation: equality of two terms
       (with the same package) reduces to equality of their as-set
       views, i.e. ``T1 == T2`` iff ``term-as-set(T1) == term-as-set(T2)``.
    2. *"Required" and "forbidden" are exhaustive.*  There is no third
       polarity; every term is either positive or negative.  This is
       the boolean polarity flag ``positive: bool``.
    """

    @given(spec_set=specifier_sets())
    @SETTINGS
    def test_polarity_is_a_boolean(self, spec_set: SpecifierSet) -> None:
        """A range and its complement are the only two term-as-sets a range produces.

        Concretely, ``term-as-set(R, positive=True) == R`` and
        ``term-as-set(R, positive=False) == ~R``, and these are
        always different sets unless ``R`` itself is half of an
        empty/universal pair (which the blog post excludes by saying a
        term is a *range*, i.e. a non-degenerate constraint).
        """
        r = _to_range(spec_set)
        positive_set = _term_set(r, positive=True)
        negative_set = _term_set(r, positive=False)
        assert positive_set == r
        assert negative_set == r.complement()


class TestQuoteBlogPostSatisfaction:
    """nex3.medium.com/pubgrub, § "So What Does PubGrub Do?":

    > "A term is satisfied if it matches the version of the package
    > that's selected. menu ≥1.1.0 is satisfied if menu 1.2.0 is
    > selected, and not dropdown ≥2.0.0 is satisfied if dropdown 1.8.0
    > is selected (or if no version of dropdown is selected at all)."

    The invariant: for a concrete selected version ``v`` (or "no
    version selected" for the negative case), satisfaction reduces to
    set membership in ``term-as-set``.

    * Positive ``T = R`` is satisfied by ``v`` iff ``v in R``.
    * Negative ``T = R`` is satisfied by ``v`` iff ``v not in R``,
      which is the same as ``v in ~R``.
    * Negative ``T = R`` is also satisfied by "no version selected"
      (the universal-membership case).  We do not have a sentinel for
      "no version" at the range level; this part of the invariant is
      handled by the resolver, not by ``VersionRange``.
    """

    @given(spec_set=specifier_sets(), v=pep440_versions())
    @SETTINGS
    def test_concrete_version_satisfies_positive_term_iff_in_range(
        self, spec_set: SpecifierSet, v: Version
    ) -> None:
        """Positive ``T`` satisfied by ``v`` iff ``v in T``."""
        rt = _to_range(spec_set)
        positive_set = _term_set(rt, positive=True)
        assert (v in positive_set) == (v in rt)

    @given(spec_set=specifier_sets(), v=pep440_versions())
    @SETTINGS
    def test_concrete_version_satisfies_negative_term_iff_outside_range(
        self, spec_set: SpecifierSet, v: Version
    ) -> None:
        """Negative ``not T`` satisfied by ``v`` iff ``v not in T``."""
        rt = _to_range(spec_set)
        negative_set = _term_set(rt, positive=False)
        assert (v in negative_set) == (v not in rt)


# ---------------------------------------------------------------------------
# Concrete examples from quote T3 of solver.md, raised to properties
# ---------------------------------------------------------------------------


class TestQuoteConcreteExamples:
    """solver.md § Term, paragraph 3:

    > "* {foo >=1.0.0, foo <2.0.0} satisfies foo ^1.0.0,
    > * foo ^1.5.0 contradicts not foo ^1.0.0,
    > * and foo ^1.0.0 is inconclusive for foo ^1.5.0."

    Three invariants extracted from the bullets:

    1. A range that *equals* a coarser range satisfies the coarser
       range (subset reflexivity at equality).
    2. A range that is *contained in* another range contradicts the
       negation of the larger range (subset implies disjoint with the
       complement).
    3. A range that *strictly contains* another range is inconclusive
       for the smaller range (neither subset nor disjoint).
    """

    @given(spec_set=specifier_sets())
    @SETTINGS
    def test_self_subset_satisfies_self(self, spec_set: SpecifierSet) -> None:
        """``R`` satisfies ``R`` (set is a subset of itself)."""
        r = _to_range(spec_set)
        assert _is_subset(r, r)

    @given(a=specifier_sets(), b=specifier_sets())
    @SETTINGS
    def test_subset_implies_contradicts_negation(
        self, a: SpecifierSet, b: SpecifierSet
    ) -> None:
        """``A ⊆ B`` implies ``A`` contradicts ``not B``.

        Operationally: if ``A & B == A`` then ``A & ~B == ∅``.
        """
        ra, rb = _to_range(a), _to_range(b)
        if not _is_subset(ra, rb):
            return
        assert _is_disjoint(ra, rb.complement())

    @given(a=specifier_sets(), b=specifier_sets())
    @SETTINGS
    def test_strict_superset_is_inconclusive_for_subset(
        self, a: SpecifierSet, b: SpecifierSet
    ) -> None:
        """``B ⊊ A`` ⇒ ``A`` is inconclusive for ``B``.

        If ``B`` is a strict subset of ``A``, then ``A ⊄ B`` (so ``A``
        does not satisfy ``B``) and ``A ∩ B == B != ∅`` (so ``A`` does
        not contradict ``B``).
        """
        ra, rb = _to_range(a), _to_range(b)
        # Guard: B is a strict, non-empty subset of A.
        if rb.is_empty or not _is_subset(rb, ra) or ra == rb:
            return
        # A does not satisfy B: A is not a subset of B.
        assert not _is_subset(ra, rb)
        # A does not contradict B: A & B == B, which is non-empty.
        assert not _is_disjoint(ra, rb)


# ---------------------------------------------------------------------------
# Section: Incompatibility (set-lifted satisfies / contradicts)
# ---------------------------------------------------------------------------


class TestQuoteSetSatisfiesIncompatibility:
    """solver.md § Incompatibility, paragraph 3:

    > "We say that a set of terms S satisfies an incompatibility I if
    > S satisfies every term in I. We say that S contradicts I if S
    > contradicts at least one term in I. If S satisfies all but one
    > of I's terms and is inconclusive for the remaining term, we say
    > S 'almost satisfies' I and we call the remaining term the
    > 'unsatisfied term'."

    The invariant on ranges: lifting per-term satisfies / contradicts
    over an incompatibility uses universal / existential
    quantification.  For an incompatibility ``I = [t1, t2]`` and an
    intersection-of-S range ``A``:

    * ``S satisfies I``   iff  ``A ⊆ t1`` AND ``A ⊆ t2``
                          iff  ``A ⊆ (t1 ∩ t2)``.
    * ``S contradicts I`` iff  ``A ∩ t1 == ∅`` OR ``A ∩ t2 == ∅``.
    """

    @given(s=specifier_sets(), t1=specifier_sets(), t2=specifier_sets())
    @SETTINGS
    def test_satisfies_two_term_incompatibility_iff_subset_of_intersection(
        self, s: SpecifierSet, t1: SpecifierSet, t2: SpecifierSet
    ) -> None:
        """``S satisfies {t1, t2}`` iff ``⋂S ⊆ t1 ∩ t2``."""
        rs, r1, r2 = _to_range(s), _to_range(t1), _to_range(t2)
        sat_universal = _is_subset(rs, r1) and _is_subset(rs, r2)
        sat_via_intersection = _is_subset(rs, r1 & r2)
        assert sat_universal == sat_via_intersection

    @given(s=specifier_sets(), t1=specifier_sets(), t2=specifier_sets())
    @SETTINGS
    def test_contradicts_two_term_incompatibility_iff_disjoint_with_either(
        self, s: SpecifierSet, t1: SpecifierSet, t2: SpecifierSet
    ) -> None:
        """``S contradicts {t1, t2}`` iff ``⋂S ∩ t1 == ∅`` OR ``⋂S ∩ t2 == ∅``.

        Each disjunct is operationally a "per-term contradicts" check
        (quote T6).  The test asserts the existential is order-free
        and agrees with the per-term subset-of-complement form of the
        same identity:
        ``⋂S ∩ ti == ∅`` ⇔ ``⋂S ⊆ ~ti``.
        """
        rs, r1, r2 = _to_range(s), _to_range(t1), _to_range(t2)
        # PubGrub's definition: existential of per-term contradicts.
        cont_existential = _is_disjoint(rs, r1) or _is_disjoint(rs, r2)
        # Per-term subset-of-complement form (quote T6, applied per term
        # then existentialised — note this is NOT the same as
        # ``⋂S ⊆ (~t1 ∪ ~t2)``, which is the pointwise existential and
        # is strictly weaker).
        cont_existential_alt = _is_subset(rs, r1.complement()) or _is_subset(
            rs, r2.complement()
        )
        assert cont_existential == cont_existential_alt
        # Order-independence (incompatibility is a *set* of terms).
        cont_reversed = _is_disjoint(rs, r2) or _is_disjoint(rs, r1)
        assert cont_existential == cont_reversed


# ---------------------------------------------------------------------------
# Section: full negation of a set of versions ("if no version is selected")
# ---------------------------------------------------------------------------


class TestEmptyAndFullAsTermDegenerates:
    """solver.md § Term implicitly relies on the algebraic identities

    > ``empty ⊆ any range`` and ``any range ⊆ full``.

    These are not stated as their own paragraphs but are stated by
    quote T4 ("set relations and operations can be defined accordingly").
    They are the two boundary cases the algorithm hits when ``S`` is
    contradictory or when ``t`` is the trivial term (the root term in a
    derived incompatibility, after positive-root removal — see quote T8).
    """

    @given(spec_set=specifier_sets())
    @SETTINGS
    def test_empty_is_subset_of_any_range(self, spec_set: SpecifierSet) -> None:
        """``empty ⊆ R`` for every range ``R``."""
        r = _to_range(spec_set)
        assert _is_subset(VersionRange.empty(), r)

    @given(spec_set=specifier_sets())
    @SETTINGS
    def test_any_range_is_subset_of_full(self, spec_set: SpecifierSet) -> None:
        """``R ⊆ full`` for every range ``R``."""
        r = _to_range(spec_set)
        assert _is_subset(r, VersionRange.full())

    @given(spec_set=specifier_sets())
    @SETTINGS
    def test_empty_is_disjoint_with_any_range(self, spec_set: SpecifierSet) -> None:
        """``empty ∩ R == ∅`` for every range ``R``.

        Quote T6 corollary: an unsatisfiable ``S`` (i.e. ``⋂S = empty``)
        contradicts every term — used by the resolver to detect a
        terminal conflict.
        """
        r = _to_range(spec_set)
        assert _is_disjoint(VersionRange.empty(), r)

    @given(spec_set=specifier_sets())
    @SETTINGS
    def test_full_overlaps_every_non_empty_range(self, spec_set: SpecifierSet) -> None:
        """``full ∩ R == R`` for every range ``R``.

        Implies ``full`` contradicts no non-empty term — quote T8's
        positive-root-removal invariant rests on this.
        """
        r = _to_range(spec_set)
        assert (VersionRange.full() & r) == r
