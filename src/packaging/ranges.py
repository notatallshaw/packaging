# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the BSD License. See the LICENSE file in the root of this repository
# for complete details.
"""Public :class:`VersionRange` API.

A set-algebra view of the versions accepted by a
:class:`~packaging.specifiers.SpecifierSet`. Ranges support intersection,
union, and complement; membership and filtering match the originating
specifier set; and conversion back to a
:class:`~packaging.specifiers.SpecifierSet` is available where a PEP 440 form
exists.

.. testsetup::

    from packaging.ranges import VersionRange
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version
"""

from __future__ import annotations

import typing
from typing import (
    TYPE_CHECKING,
    Any,
    TypeVar,
    Union,
)

from ._ranges import (
    FULL_RANGE,
    MIN_VERSION,
    NEG_INF,
    POS_INF,
    BoundaryKind,
    BoundaryVersion,
    LowerBound,
    UpperBound,
    coerce_version,
    filter_by_ranges,
    intersect_ranges,
    matches_bounds_only,
    trim_release,
)
from .version import Version

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Sequence

    from .specifiers import SpecifierSet

    #: A single contiguous interval as a (lower, upper) bound pair.
    _Interval = tuple[LowerBound, UpperBound]


__all__ = ["VersionRange"]

T = TypeVar("T")
UnparsedVersion = Union[Version, str]
UnparsedVersionVar = TypeVar("UnparsedVersionVar", bound=UnparsedVersion)


def __dir__() -> list[str]:
    return __all__


# Range algebra: intersection lives in the engine (``intersect_ranges``);
# union and complement are only needed here, so they live in this module.


def _after_locals_successor(version: Version) -> Version:
    """Smallest real version strictly above ``AFTER_LOCALS(version)``.

    When V has a dev segment, the next real version is V with ``dev + 1``.
    Otherwise AFTER_LOCALS(V) sits just below V.post0, so its successor is
    V.post0.dev0 (V.post(N+1).dev0 when V is itself a post-release).
    """
    if version.dev is not None:
        return version.__replace__(dev=version.dev + 1, local=None)

    next_post = (version.post + 1) if version.post is not None else 0
    return version.__replace__(post=next_post, dev=0, local=None)


def _range_is_empty(lower: LowerBound, upper: UpperBound) -> bool:
    """True when the union gap ``(lower, upper)`` holds no version.

    Union calls this only for a strictly-ordered gap between two intervals,
    so an ordinary version gap always holds a version. The one empty case is
    a flipped ``AFTER_LOCALS(V)`` lower whose smallest real successor,
    ``V.post0.dev0``, sits at or above the upper.
    """
    lower_version = lower.version
    upper_version = upper.version

    if (
        isinstance(lower_version, BoundaryVersion)
        and lower_version.kind == BoundaryKind.AFTER_LOCALS
        and isinstance(upper_version, Version)
    ):
        successor = _after_locals_successor(lower_version.version)
        if upper_version == successor:
            return not upper.inclusive
        return upper_version < successor
    return False


def _union_ranges(
    left: Sequence[_Interval],
    right: Sequence[_Interval],
) -> list[_Interval]:
    """Union two sorted, non-overlapping interval lists.

    A linear merge over the two pre-sorted inputs followed by a single
    coalescing pass: adjacent or overlapping intervals collapse so the result
    is itself sorted and non-overlapping.
    """
    if not left:
        return list(right)
    if not right:
        return list(left)

    merged_input: list[_Interval] = []
    left_index = right_index = 0
    while left_index < len(left) and right_index < len(right):
        if left[left_index][0] <= right[right_index][0]:
            merged_input.append(left[left_index])
            left_index += 1
        else:
            merged_input.append(right[right_index])
            right_index += 1
    merged_input.extend(left[left_index:])
    merged_input.extend(right[right_index:])

    merged: list[_Interval] = [merged_input[0]]
    for lower, upper in merged_input[1:]:
        prev_lower, prev_upper = merged[-1]

        if (
            prev_upper.version is None
            or lower.version is None
            or prev_upper.version > lower.version
        ):
            overlaps = True
        elif prev_upper.version == lower.version:
            overlaps = prev_upper.inclusive or lower.inclusive
        else:
            # An ordering gap may still hold no version when the two bounds
            # straddle a synthetic boundary; merge across an empty gap to
            # stay canonical.
            gap_lower = LowerBound(prev_upper.version, not prev_upper.inclusive)
            gap_upper = UpperBound(lower.version, not lower.inclusive)
            overlaps = _range_is_empty(gap_lower, gap_upper)

        if overlaps:
            merged[-1] = (prev_lower, max(prev_upper, upper))
        else:
            merged.append((lower, upper))

    return merged


def _complement_ranges(ranges: Sequence[_Interval]) -> list[_Interval]:
    """Complement a sorted, non-overlapping interval list.

    Yields the gaps between intervals plus a leading gap before the first and
    a trailing gap after the last. Bound inclusivity flips so that
    complement-of-complement round-trips back to the input.
    """
    if not ranges:
        return list(FULL_RANGE)

    result: list[_Interval] = []
    prev_upper: UpperBound | None = None

    for lower, upper in ranges:
        if prev_upper is None:
            # A leading gap that ends at or below the PEP 440 floor holds no
            # version, so it is dropped to keep ``is_empty`` correct. The live
            # trigger is ``~singleton("0.dev0")`` (a strict ``[0.dev0, 0.dev0]``
            # built outside ``_canonical_floor``); specifier-derived floor
            # ranges are already collapsed to ``-inf`` before complement.
            if lower.version is not None and not (
                lower.inclusive
                and isinstance(lower.version, Version)
                and lower.version <= MIN_VERSION
            ):
                gap_upper = UpperBound(lower.version, not lower.inclusive)
                result.append((NEG_INF, gap_upper))
        else:
            gap_lower = LowerBound(prev_upper.version, not prev_upper.inclusive)
            gap_upper = UpperBound(lower.version, not lower.inclusive)
            # Input intervals are canonical (sorted, disjoint, non-touching),
            # so the gap between two of them always holds at least one version.
            result.append((gap_lower, gap_upper))
        prev_upper = upper

    # The empty-input early return guarantees the loop ran.
    assert prev_upper is not None
    if prev_upper.version is not None:
        gap_lower = LowerBound(prev_upper.version, not prev_upper.inclusive)
        result.append((gap_lower, POS_INF))

    return result


def _canonical_floor(bounds: tuple[_Interval, ...]) -> tuple[_Interval, ...]:
    """Collapse the PEP 440 floor in a sorted interval list.

    Only the first interval can touch ``0.dev0`` (the minimum version). An
    inclusive lower at or below it admits everything below, the same as
    ``-inf``, so ``>=0.dev0`` becomes the one canonical full range. An
    exclusive upper at or below it leaves the interval empty, so it is dropped.
    """
    if not bounds:
        return bounds

    lower, upper = bounds[0]
    if (
        not upper.inclusive
        and isinstance(upper.version, Version)
        and upper.version <= MIN_VERSION
    ):
        return bounds[1:]

    if (
        lower.inclusive
        and isinstance(lower.version, Version)
        and lower.version <= MIN_VERSION
    ):
        return ((NEG_INF, upper), *bounds[1:])

    return bounds


def _struct_admits(
    bounds: tuple[_Interval, ...], admit_arbitrary: bool, literal: str
) -> bool:
    """True when the bounds (plus arbitrary admission) admit ``literal``.

    Skips the explicit admit/reject sets, which the caller layers on top. A
    non-version string matches via ``admit_arbitrary`` only on full bounds;
    on narrower bounds the flag is metadata only.
    """
    parsed = coerce_version(literal)
    if parsed is None:
        return admit_arbitrary and bounds == FULL_RANGE

    return matches_bounds_only(bounds, parsed)


# Repr helpers:


def _bound_version_str(value: BoundaryVersion | Version) -> str:
    """Printout for a bound's inner value, kind-tagged for boundaries."""
    if isinstance(value, BoundaryVersion):
        return f"{value.version}[{value.kind.name}]"
    return str(value)


def _format_lower(bound: LowerBound) -> str:
    if bound.version is None:
        return "(-inf"
    bracket = "[" if bound.inclusive else "("
    return f"{bracket}{_bound_version_str(bound.version)}"


def _format_upper(bound: UpperBound) -> str:
    if bound.version is None:
        return "+inf)"
    bracket = "]" if bound.inclusive else ")"
    return f"{_bound_version_str(bound.version)}{bracket}"


# ``to_specifier_set`` recovery: encode a range's interval list back into
# specifier fragments. Each helper returns ``None`` when its shape has no
# PEP 440 form.


def _is_dev0_version(version: Version) -> bool:
    """True when version is exactly ``X[.Y]*.dev0`` (the shape ``<X`` makes)."""
    return (
        version.dev == 0
        and version.pre is None
        and version.post is None
        and version.local is None
    )


def _clean_lower(version: Version) -> list[str] | None:
    """A prerelease-free spelling for an inclusive ``[version`` lower, or ``None``.

    Several ``[V`` lowers come from an operator whose own spelling carries no
    synthetic ``.dev0``. Recovering that spelling keeps the round trip from
    flipping the pre-release autodetect, so it is used only when the resolved
    policy is ``None`` (see :meth:`VersionRange.to_specifier_set`).
    """
    if version.dev != 0 or version.pre is not None or version.local is not None:
        return None

    # ``[B.post(k).dev0`` is the lower ``>B.post(k-1)`` builds (k >= 1).
    if version.post is not None:
        if version.post < 1:
            return None
        return [f">{version.__replace__(post=version.post - 1, dev=None)}"]

    # ``[F.dev0`` is family F's base. The prefix P just below F has
    # ``==P.* == [P.dev0, F.dev0)``, so ``>=P,!=P.*`` lands exactly on ``[F.dev0``.
    family = trim_release(version.release)
    last = family[-1]
    if last < 1:
        return None

    below_release = (*family[:-1], last - 1)
    below = Version.from_parts(epoch=version.epoch, release=below_release)

    # At the epoch-0 floor ``==P.*`` already reaches ``0.dev0``, so the ``>=P``
    # half is redundant: ``[1.dev0, +inf)`` is plain ``!=0.*``.
    if version.epoch == 0 and not any(below_release):
        return [f"!={below}.*"]

    return [f">={below}", f"!={below}.*"]


def _epoch_floor_lower(
    lower: LowerBound, upper: UpperBound
) -> tuple[Version, bool, bool] | None:
    """The ``E!0`` family of a lower sitting on an epoch>0 zero-family floor.

    An epoch>0 zero-family base such as ``1!0.dev0`` has no ``>=P,!=P.*`` spelling
    since no version sorts below ``E!0`` within the epoch. While the interval
    stays within ``==E!0.*`` it is that wildcard, trimmed by the upper and (for an
    ``AFTER_LOCALS(E!0.dev0)`` lower) with ``E!0.dev0`` excluded. Returns the
    ``E!0`` family, whether to exclude ``E!0.dev0``, and whether the upper already
    sits at the family cap (so ``==E!0.*`` needs no extra upper), or ``None``.
    """
    # An AFTER_LOCALS(E!0.dev0) lower drops E!0.dev0; a plain inclusive lower
    # keeps it.
    version = lower.version
    exclude_dev0 = False
    if isinstance(version, BoundaryVersion):
        if version.kind != BoundaryKind.AFTER_LOCALS:
            return None
        version, exclude_dev0 = version.version, True
    elif not isinstance(version, Version) or not lower.inclusive:
        return None

    # Only the bare ``E!0.dev0`` floor of a non-zero epoch qualifies.
    if version.epoch == 0 or version.dev != 0:
        return None
    if version.pre is not None or version.post is not None or version.local is not None:
        return None
    if any(trim_release(version.release)):
        return None

    # ``==E!0.*`` spans ``[E!0.dev0, E!1.dev0)``; it fits only below that cap.
    next_family = Version.from_parts(epoch=version.epoch, release=(1,), dev=0)
    cap = UpperBound(next_family, False)
    if upper > cap:
        return None

    family = Version.from_parts(epoch=version.epoch, release=(0,))
    return family, exclude_dev0, upper == cap


def _encode_lower(lower: LowerBound, prereleases: bool | None) -> list[str] | None:
    """Encode a lower bound as specifier fragments, or ``None``.

    ``[]`` for ``-inf``. AFTER_LOCALS lower bounds emit ``>=V,!=V`` since the
    boundary excludes V and every V+local. ``prereleases`` is the range's
    resolved pre-release policy: when it is ``None`` an inclusive ``.dev0`` lower
    prefers a spelling that carries no synthetic pre-release (:func:`_clean_lower`)
    so the recovered set keeps autodetecting ``None``.
    """
    lower_version = lower.version
    if lower_version is None:
        return []

    if isinstance(lower_version, BoundaryVersion):
        if lower_version.kind == BoundaryKind.AFTER_POSTS:
            # AFTER_POSTS only ever appears as an exclusive ``>V`` lower.
            return [f">{lower_version.version}"]
        inner = lower_version.version
        if inner <= MIN_VERSION:
            # The ``(-inf, V)`` side was dropped at the floor, so the lone
            # ``(AFTER_LOCALS(0.dev0), +inf)`` interval is exactly ``!=0.dev0``.
            return [f"!={inner}"]
        # ``(AFTER_LOCALS(V), ..)`` is ``[V, ..)`` minus V's local family, i.e.
        # ``>=V,!=V``, using V's prerelease-free family spelling where it has one.
        head = _clean_lower(inner) if prereleases is None else None
        if head is None:
            head = [f">={inner}"]
        return [*head, f"!={inner}"]

    if not lower.inclusive:
        return None

    # Under autodetect a ``.dev0`` lower prefers its prerelease-free spelling.
    if prereleases is None:
        clean = _clean_lower(lower_version)
        if clean is not None:
            return clean
    return [f">={lower_version}"]


def _encode_upper(upper: UpperBound, prereleases: bool | None) -> list[str] | None:
    """Encode an upper bound as specifier fragments, or ``None``.

    ``[]`` for ``+inf``. When the resolved ``prereleases`` policy is ``None`` the
    ``<X`` spelling is used for the ``X.dev0`` upper that ``<X`` builds; otherwise
    the synthetic ``.dev0`` is kept so the recovered set autodetects pre-releases.
    """
    upper_version = upper.version
    if upper_version is None:
        return []

    if isinstance(upper_version, BoundaryVersion):
        if upper_version.kind == BoundaryKind.AFTER_LOCALS and upper.inclusive:
            return [f"<={upper_version.version}"]
        return None

    if not upper.inclusive:
        # ``<X`` builds an exclusive ``X.dev0`` upper (X final or post-release;
        # never pre/local). ``<X`` and ``<X.dev0`` define the same bound but
        # differ in autodetect, so pick by the resolved policy.
        if (
            upper_version.dev == 0
            and upper_version.pre is None
            and upper_version.local is None
        ):
            if prereleases is None:
                return [f"<{upper_version.__replace__(dev=None)}"]
            return [f"<{upper_version}"]
        # ``V`` (exclusive) upper, including V's pre-releases.
        return [f"<={upper_version}", f"!={upper_version}"]
    return None


def _detect_equal_wildcard(lower: LowerBound, upper: UpperBound) -> Version | None:
    """If ``[lower, upper)`` is the ``==V.*`` shape, return ``V``."""
    if isinstance(lower.version, BoundaryVersion) or isinstance(
        upper.version, BoundaryVersion
    ):
        return None
    if lower.version is None or upper.version is None:
        return None
    if not lower.inclusive or upper.inclusive:
        return None
    if not (_is_dev0_version(lower.version) and _is_dev0_version(upper.version)):
        return None
    if lower.version.epoch != upper.version.epoch:
        return None

    lower_release = trim_release(lower.version.release)
    upper_release = trim_release(upper.version.release)
    padded_length = max(len(lower_release), len(upper_release))
    assert padded_length > 0
    lower_release += (0,) * (padded_length - len(lower_release))
    upper_release += (0,) * (padded_length - len(upper_release))

    if lower_release[:-1] != upper_release[:-1]:
        return None

    # A genuine ``==V.*`` spans one family: the upper is exactly the next prefix.
    # A wider span like ``[3.8.dev0, 3.14.dev0)`` shares the prefix but is not a
    # single wildcard, so it falls through to the generic ``>=...,<...`` form.
    if upper_release[-1] != lower_release[-1] + 1:
        return None

    return lower.version.__replace__(release=lower_release, dev=None)


def _encode_interval(
    lower: LowerBound, upper: UpperBound, prereleases: bool | None
) -> list[str] | None:
    """Encode one interval as specifier fragments, or ``None``.

    Special-cases ``[V, V]`` with a local segment (``==V+local``) and the
    ``==V.*`` shape so the fragment carries no synthetic ``.dev0`` literal.
    """
    if (
        lower.version is not None
        and upper.version is not None
        and not isinstance(lower.version, BoundaryVersion)
        and not isinstance(upper.version, BoundaryVersion)
        and lower.inclusive
        and upper.inclusive
        and lower.version == upper.version
        and lower.version.local is not None
    ):
        return [f"=={lower.version}"]

    wildcard = _detect_equal_wildcard(lower, upper)
    if wildcard is not None:
        return [f"=={wildcard}.*"]

    # A ``[E!0.dev0`` lower has no prerelease-free ``>=`` spelling; within its own
    # family it is ``==E!0.*`` trimmed by the upper.
    floor = _epoch_floor_lower(lower, upper) if prereleases is None else None
    if floor is not None:
        family, exclude_dev0, upper_at_cap = floor
        parts = [f"=={family}.*"]
        if exclude_dev0:
            parts.append(f"!={family.__replace__(dev=0)}")

        # ``==E!0.*`` already caps at the next family; add the upper only if tighter.
        if not upper_at_cap:
            upper_parts = _encode_upper(upper, prereleases)
            if upper_parts is None:
                return None
            parts.extend(upper_parts)

        return parts

    lower_parts = _encode_lower(lower, prereleases)
    if lower_parts is None:
        return None

    upper_parts = _encode_upper(upper, prereleases)
    if upper_parts is None:
        return None

    return lower_parts + upper_parts


def _detect_not_equal(
    left_upper: UpperBound, right_lower: LowerBound
) -> Version | None:
    """If the gap between two intervals is an ``!=V`` exclusion, return V."""
    if isinstance(left_upper.version, BoundaryVersion):
        return None
    if left_upper.version is None or left_upper.inclusive:
        return None

    if not isinstance(right_lower.version, BoundaryVersion):
        if (
            right_lower.version is not None
            and not right_lower.inclusive
            and right_lower.version == left_upper.version
            and left_upper.version.local is not None
        ):
            return left_upper.version
        return None

    if right_lower.version.kind != BoundaryKind.AFTER_LOCALS:
        return None

    # A ``!=V`` exclusion drops a single point: the left interval ends just
    # below V and the right interval resumes just above V and its locals, so
    # both bounds must name the same V. When they differ the gap spans a whole
    # interval instead. Complementing ``>=2.3,<=2.7``, for example, leaves a
    # left upper of ``2.3`` and a right lower of ``AFTER_LOCALS(2.7)``; the gap
    # from 2.3 to 2.7 is an interval, not a ``!=`` exclusion.
    if right_lower.version.version != left_upper.version:
        return None
    return left_upper.version


def _decompose_dev0_gap(
    lower_trim: tuple[int, ...],
    upper_trim: tuple[int, ...],
    epoch: int,
) -> list[Version] | None:
    """Decompose the gap ``[L.dev0, U.dev0)`` into wildcard prefixes.

    ``lower_trim``/``upper_trim`` are trimmed release tuples with
    ``lower_trim < upper_trim`` lexicographically. The chain sweeps at the
    first differing level. The gap is undecomposable when L has trailing
    components below that level (the chain cannot escape L's subtree).
    """
    diff = 0
    while (
        diff < len(lower_trim)
        and diff < len(upper_trim)
        and lower_trim[diff] == upper_trim[diff]
    ):
        diff += 1

    if len(lower_trim) > diff + 1:
        return None

    common = lower_trim[:diff]
    lower_val = lower_trim[diff] if len(lower_trim) > diff else 0
    upper_val = upper_trim[diff]

    fragments = [
        Version.from_parts(epoch=epoch, release=(*common, segment))
        for segment in range(lower_val, upper_val)
    ]

    if len(upper_trim) == diff + 1:
        return fragments

    tail = _decompose_dev0_gap((*common, upper_val), upper_trim, epoch)
    assert tail is not None
    return fragments + tail


def _detect_not_equal_wildcards(
    left_upper: UpperBound, right_lower: LowerBound
) -> list[Version] | None:
    """Decompose a ``[L.dev0, U.dev0)`` gap into a chain of ``!=P.*`` prefixes."""
    left_upper_v = left_upper.version
    right_lower_v = right_lower.version

    if not isinstance(left_upper_v, Version) or not isinstance(right_lower_v, Version):
        return None
    if left_upper.inclusive or not right_lower.inclusive:
        return None
    if not (_is_dev0_version(left_upper_v) and _is_dev0_version(right_lower_v)):
        return None
    if left_upper_v.epoch != right_lower_v.epoch:
        return None

    return _decompose_dev0_gap(
        trim_release(left_upper_v.release),
        trim_release(right_lower_v.release),
        left_upper_v.epoch,
    )


def _detect_wildcards_then_dev0(
    left_upper: UpperBound, right_lower: LowerBound
) -> tuple[list[Version], Version] | None:
    """Split a ``[L.dev0, AFTER_LOCALS(U.dev0)]`` gap into ``!=P.*`` + ``!=U.dev0``.

    A ``!=U.dev0`` point exclusion sitting just above an excluded ``!=family.*``
    chain leaves the right interval at ``AFTER_LOCALS(U.dev0)``: the gap covers
    the wildcard families ``[L.dev0, U.dev0)`` and then U.dev0's own family.
    Returns the ``!=P.*`` chain prefixes and ``U.dev0``, or ``None``.
    """
    left_upper_v = left_upper.version
    right_lower_v = right_lower.version

    # The gap runs from an exclusive ``L.dev0`` up to ``AFTER_LOCALS(U.dev0)``.
    if not isinstance(left_upper_v, Version):
        return None
    if not isinstance(right_lower_v, BoundaryVersion):
        return None
    if right_lower_v.kind != BoundaryKind.AFTER_LOCALS:
        return None
    if left_upper.inclusive or right_lower.inclusive:
        return None

    # Both ends must be dev0 family bases in the same epoch, with L below U.
    upper_dev0 = right_lower_v.version
    if not (_is_dev0_version(left_upper_v) and _is_dev0_version(upper_dev0)):
        return None
    if left_upper_v.epoch != upper_dev0.epoch or left_upper_v >= upper_dev0:
        return None

    # The chain ``[L.dev0, U.dev0)`` decomposes into the ``==P.*`` prefixes.
    prefixes = _decompose_dev0_gap(
        trim_release(left_upper_v.release),
        trim_release(upper_dev0.release),
        left_upper_v.epoch,
    )
    if prefixes is None:
        return None

    return prefixes, upper_dev0


def _close_group(
    group_lower: LowerBound,
    group_upper: UpperBound,
    exclusions: list[str],
    prereleases: bool | None,
) -> list[str] | None:
    """Encode one accumulated group as specifier fragments, or ``None``.

    A group is a single contiguous interval (its members joined through ``!=``
    gaps), never a disjoint union: a multi-family dev0 span such as
    ``[3.8.dev0, 3.14.dev0)`` is the contiguous ``>=3.8.dev0,<3.14``. Genuinely
    disjoint families land in separate groups, which
    :meth:`VersionRange.to_specifier_set` rejects by group count. The outer span
    itself may still have no PEP 440 form, in which case this returns ``None``.
    """
    outer = _encode_interval(group_lower, group_upper, prereleases)
    if outer is None:
        return None

    return outer + exclusions


def _encode_grouped(
    bounds: list[_Interval], prereleases: bool | None
) -> list[list[str]] | None:
    """Split bounds into disjoint groups, encoding each as fragments.

    Consecutive intervals whose gap is an ``!=V`` / ``!=V+local`` / ``!=V.*``
    exclusion stay in one group; any other gap starts a new group. Returns one
    fragment list per group, or ``None`` if any group has no PEP 440 form.
    """
    groups: list[list[str]] = []
    group_lower, group_upper = bounds[0]
    exclusions: list[str] = []
    for next_lower, next_upper in bounds[1:]:
        not_equal = _detect_not_equal(group_upper, next_lower)
        not_equal_wildcards = _detect_not_equal_wildcards(group_upper, next_lower)
        wildcards_then_dev0 = _detect_wildcards_then_dev0(group_upper, next_lower)
        if not_equal is not None:
            exclusions.append(f"!={not_equal}")
        elif not_equal_wildcards is not None:
            exclusions.extend(f"!={prefix}.*" for prefix in not_equal_wildcards)
        elif wildcards_then_dev0 is not None:
            chain, point = wildcards_then_dev0
            exclusions.extend(f"!={prefix}.*" for prefix in chain)
            exclusions.append(f"!={point}")
        else:
            closed = _close_group(group_lower, group_upper, exclusions, prereleases)
            if closed is None:
                return None
            groups.append(closed)
            group_lower, exclusions = next_lower, []

        group_upper = next_upper

    closed = _close_group(group_lower, group_upper, exclusions, prereleases)
    if closed is None:
        return None
    groups.append(closed)

    return groups


class VersionRange:
    """A set of :class:`~packaging.version.Version` values accepted by a
    :class:`~packaging.specifiers.SpecifierSet`.

    Construct via :meth:`~packaging.specifiers.SpecifierSet.to_range`, or with
    the :meth:`full`, :meth:`empty`, and :meth:`singleton` class methods.
    Compose with :meth:`intersection`, :meth:`union`, and :meth:`complement`
    (or the ``&`` / ``|`` / ``~`` operators). Test membership with ``in`` or
    :meth:`contains`, filter an iterable with :meth:`filter`, and convert back
    to a :class:`~packaging.specifiers.SpecifierSet` with
    :meth:`to_specifier_set`.

    The configured pre-release policy of the originating specifier set carries
    onto the range and controls whether pre-releases are admitted under ``in``,
    :meth:`contains`, and :meth:`filter`. :meth:`intersection` and
    :meth:`union` require both operands to share the same policy.

    >>> r = SpecifierSet(">=1.0,<2.0").to_range()
    >>> "1.5" in r
    True
    >>> "2.0" in r
    False
    >>> SpecifierSet(">=2.0,<1.0").to_range().is_empty
    True

    PEP 440's ``===`` operator matches a candidate string verbatim
    (case-insensitive) rather than a set of versions. Ranges built from
    ``===`` specifiers still support membership, set operations, and
    conversion back to a :class:`~packaging.specifiers.SpecifierSet`; matching
    follows the literal-equality rule.
    """

    __slots__ = (
        "_admit",
        "_admit_arbitrary",
        "_bounds",
        "_prereleases",
        "_prereleases_configured",
        "_reject",
    )

    #: The disjoint, sorted, non-overlapping interval list.
    _bounds: tuple[_Interval, ...]

    #: Whether this range matches non-version strings as well as versions.
    #: True only by construction on ``SpecifierSet("")`` / :meth:`full`. Set
    #: algebra ANDs on intersection, ORs on union, and preserves on complement.
    #: Part of equality, since membership reads it.
    _admit_arbitrary: bool

    #: Case-folded strings the range admits in addition to its bounds.
    #: ``===wat`` produces ``_admit = {"wat"}``.
    _admit: frozenset[str]

    #: Case-folded strings the range rejects (overrides ``_admit`` and the
    #: bounds). Populated only by :meth:`complement` of an admit-bearing range.
    _reject: frozenset[str]

    #: Resolved pre-release policy used by :meth:`filter` and :meth:`contains`
    #: when their ``prereleases`` argument is ``None``. Part of equality, so
    #: two ranges that compare equal always filter the same versions.
    _prereleases: bool | None

    #: Raw configured pre-release override of the originating specifier set.
    #: Distinguishes autodetect-True from explicit-True. :meth:`intersection`
    #: and :meth:`union` require it to match on both operands. Part of equality.
    _prereleases_configured: bool | None

    def __new__(cls, *args: object, **kwargs: object) -> VersionRange:  # noqa: PYI034
        raise TypeError(
            "cannot create 'VersionRange' instances directly; use "
            "SpecifierSet.to_range(), VersionRange.full(), "
            "VersionRange.empty(), or VersionRange.singleton() instead"
        )

    @classmethod
    def _build(
        cls,
        bounds: tuple[_Interval, ...],
        admit: frozenset[str] = frozenset(),
        reject: frozenset[str] = frozenset(),
        admit_arbitrary: bool = False,
    ) -> VersionRange:
        """Internal factory; bypasses :meth:`__new__`.

        Drops admit literals the bounds already admit, and reject literals the
        bounds do not match anyway. Reject wins over admit on overlap.
        """
        if admit and reject:
            admit = admit - reject
        if admit:
            admit = frozenset(
                literal
                for literal in admit
                if not _struct_admits(bounds, admit_arbitrary, literal)
            )
        if reject:
            reject = frozenset(
                literal
                for literal in reject
                if _struct_admits(bounds, admit_arbitrary, literal)
            )

        instance = object.__new__(cls)
        instance._bounds = bounds
        instance._admit = admit
        instance._reject = reject
        instance._admit_arbitrary = admit_arbitrary
        instance._prereleases = None
        instance._prereleases_configured = None

        return instance

    def _has_literals(self) -> bool:
        return bool(self._admit) or bool(self._reject)

    def _arbitrary_active(self) -> bool:
        """True when ``_admit_arbitrary`` actually admits non-version strings.

        The flag rides through set algebra but only fires admission on full
        bounds; on narrower bounds it is metadata awaiting a later widening.
        """
        return self._admit_arbitrary and self._bounds == FULL_RANGE

    def _check_policy_compat(self, other: VersionRange) -> None:
        """Refuse combining ranges with different pre-release policies."""
        if not isinstance(other, VersionRange):
            raise TypeError(f"expected VersionRange, got {type(other).__name__}")
        if self._prereleases_configured != other._prereleases_configured:
            raise ValueError(
                "Cannot combine VersionRange operands with different "
                f"pre-release policies: {self._prereleases_configured!r} "
                f"and {other._prereleases_configured!r}"
            )

    def _propagate_prereleases(self, other: VersionRange, result: VersionRange) -> None:
        """Carry the shared pre-release policy onto a freshly built result."""
        result._prereleases_configured = self._prereleases_configured
        if self._prereleases_configured is not None:
            result._prereleases = self._prereleases_configured
        elif self._prereleases is True or other._prereleases is True:
            result._prereleases = True
        else:
            result._prereleases = None

    def _restamp(self, *, resolved: bool | None, configured: bool | None) -> None:
        """Set the pre-release policy slots on a freshly built range."""
        self._prereleases = resolved
        self._prereleases_configured = configured

    @classmethod
    def empty(cls, *, prereleases: bool | None = None) -> VersionRange:
        """Return the empty range. No version satisfies it.

        >>> VersionRange.empty().is_empty
        True
        >>> "1.0" in VersionRange.empty()
        False
        """
        result = cls._build(())
        if prereleases is not None:
            result._restamp(resolved=prereleases, configured=prereleases)

        return result

    @classmethod
    def full(
        cls, *, admit_arbitrary: bool = True, prereleases: bool | None = None
    ) -> VersionRange:
        """Return the full range. Every PEP 440 version satisfies it.

        ``admit_arbitrary=False`` restricts the range to PEP 440 versions only
        (matching the same versions as ``SpecifierSet(">=0.dev0").to_range()``);
        its complement is :meth:`empty`. The flag propagates through set algebra
        and is part of equality. Default ``True`` so that ``r & full()``
        preserves ``r``'s own flag structurally.

        >>> "1.0" in VersionRange.full()
        True
        >>> "garbage" in VersionRange.full()
        True
        >>> "garbage" in VersionRange.full(admit_arbitrary=False)
        False
        """
        result = cls._build(FULL_RANGE, admit_arbitrary=admit_arbitrary)
        if prereleases is not None:
            result._restamp(resolved=prereleases, configured=prereleases)

        return result

    @classmethod
    def singleton(
        cls, version: Version | str, *, prereleases: bool | None = None
    ) -> VersionRange:
        """Return the strict singleton range ``{version}``.

        Built as the closed interval ``[version, version]`` with strict
        equality. ``Specifier("==V")`` matches ``V+local`` too, so the strict
        singleton is narrower:

        >>> "1.0+local" in VersionRange.singleton("1.0")
        False
        >>> "1.0+local" in SpecifierSet("==1.0").to_range()
        True

        :raises packaging.version.InvalidVersion: if version is a string that
            does not parse as a PEP 440 version.
        """
        if not isinstance(version, Version):
            version = Version(version)

        lower = LowerBound(version, True)
        upper = UpperBound(version, True)

        # Collapse the floor: nothing sorts below ``MIN_VERSION``, so the
        # ``0.dev0`` singleton is ``(-inf, 0.dev0]`` in canonical form.
        result = cls._build(_canonical_floor(((lower, upper),)))
        if prereleases is not None:
            result._restamp(resolved=prereleases, configured=prereleases)

        return result

    def intersection(self, other: VersionRange) -> VersionRange:
        """Range containing exactly the versions in both self and other.

        Both operands must share the same configured pre-release policy;
        otherwise :exc:`ValueError` is raised.

        >>> a = SpecifierSet(">=1.0").to_range()
        >>> b = SpecifierSet("<2.0").to_range()
        >>> a.intersection(b) == SpecifierSet(">=1.0,<2.0").to_range()
        True
        """
        self._check_policy_compat(other)

        new_bounds = tuple(intersect_ranges(self._bounds, other._bounds))
        combined_arb = self._admit_arbitrary and other._admit_arbitrary
        if not self._has_literals() and not other._has_literals():
            result = self._build(new_bounds, admit_arbitrary=combined_arb)
        else:
            result = self._combine_literals(
                other, new_bounds, intersect=True, admit_arbitrary=combined_arb
            )

        self._propagate_prereleases(other, result)
        return result

    def union(self, other: VersionRange) -> VersionRange:
        """Range containing every version in self or other.

        Both operands must share the same configured pre-release policy;
        otherwise :exc:`ValueError` is raised.

        >>> a = VersionRange.singleton("1.0")
        >>> b = VersionRange.singleton("2.0")
        >>> "1.0" in a.union(b) and "2.0" in a.union(b)
        True
        >>> "1.5" in a.union(b)
        False
        """
        self._check_policy_compat(other)

        new_bounds = tuple(_union_ranges(self._bounds, other._bounds))
        combined_arb = self._admit_arbitrary or other._admit_arbitrary
        if not self._has_literals() and not other._has_literals():
            result = self._build(new_bounds, admit_arbitrary=combined_arb)
        else:
            result = self._combine_literals(
                other, new_bounds, intersect=False, admit_arbitrary=combined_arb
            )

        # ``r | full()`` collapses to the canonical universal range only when
        # both sides carry the autodetect default; an explicit policy survives.
        if (
            result._bounds == FULL_RANGE
            and result._admit_arbitrary
            and not result._has_literals()
            and self._prereleases_configured is None
        ):
            result._prereleases_configured = None
            result._prereleases = None
            return result

        self._propagate_prereleases(other, result)
        return result

    def complement(self) -> VersionRange:
        """Range containing every version not in self.

        Preserves the configured pre-release policy. Within the PEP 440
        universe (no ``===`` literals and no arbitrary admission) double
        negation holds; for ``===`` ranges complement is one-way.

        >>> r = SpecifierSet(">=1.0").to_range()
        >>> "0.5" in r.complement()
        True
        >>> "1.5" in r.complement()
        False
        >>> r.complement().complement() == r
        True
        """
        if not self._has_literals():
            result = self._build(
                tuple(_complement_ranges(self._bounds)),
                admit_arbitrary=self._admit_arbitrary,
            )
        else:
            result = self._build(
                tuple(_complement_ranges(self._bounds)),
                admit=self._reject,
                reject=self._admit,
                admit_arbitrary=self._admit_arbitrary,
            )

        result._prereleases = self._prereleases
        result._prereleases_configured = self._prereleases_configured
        return result

    def _combine_literals(
        self,
        other: VersionRange,
        new_bounds: tuple[_Interval, ...],
        *,
        intersect: bool,
        admit_arbitrary: bool,
    ) -> VersionRange:
        """Resolve admit/reject for ``self & other`` or ``self | other``."""
        admits: set[str] = set()
        rejects: set[str] = set()
        for literal in self._admit | self._reject | other._admit | other._reject:
            self_in = self._matches_literal(literal)
            other_in = other._matches_literal(literal)
            want = (self_in and other_in) if intersect else (self_in or other_in)
            if want:
                admits.add(literal)
            else:
                rejects.add(literal)

        return self._build(
            new_bounds,
            admit=frozenset(admits),
            reject=frozenset(rejects),
            admit_arbitrary=admit_arbitrary,
        )

    def _matches_literal(self, literal: str) -> bool:
        """Whether literal (case-folded) matches this range's predicate."""
        if literal in self._reject:
            return False
        if literal in self._admit:
            return True

        parsed = coerce_version(literal)
        if parsed is None:
            return self._arbitrary_active()
        return matches_bounds_only(self._bounds, parsed)

    def __and__(self, other: object) -> VersionRange:
        """Operator alias for :meth:`intersection`."""
        if not isinstance(other, VersionRange):
            return NotImplemented
        return self.intersection(other)

    def __or__(self, other: object) -> VersionRange:
        """Operator alias for :meth:`union`."""
        if not isinstance(other, VersionRange):
            return NotImplemented
        return self.union(other)

    def __invert__(self) -> VersionRange:
        """Operator alias for :meth:`complement`."""
        return self.complement()

    @typing.overload
    def filter(
        self,
        iterable: Iterable[UnparsedVersionVar],
        prereleases: bool | None = None,
        key: None = ...,
    ) -> Iterator[UnparsedVersionVar]: ...

    @typing.overload
    def filter(
        self,
        iterable: Iterable[T],
        prereleases: bool | None = None,
        key: Callable[[T], UnparsedVersion] = ...,
    ) -> Iterator[T]: ...

    def filter(
        self,
        iterable: Iterable[Any],
        prereleases: bool | None = None,
        key: Callable[[Any], Version | str] | None = None,
    ) -> Iterator[Any]:
        """Yield items from iterable whose version falls inside the range.

        With prereleases ``None`` the PEP 440 default applies: pre-releases are
        buffered and only emitted if no final release in iterable is in range.

        The signature mirrors
        :meth:`~packaging.specifiers.SpecifierSet.filter`.

        >>> r = SpecifierSet(">=1.0,<2.0").to_range()
        >>> list(r.filter(["0.9", "1.5", "2.0"]))
        ['1.5']
        """
        if prereleases is None:
            prereleases = self._prereleases

        arbitrary_active = self._arbitrary_active()
        if not self._admit and not self._reject and not arbitrary_active:
            return filter_by_ranges(self._bounds, iterable, key, prereleases)
        return self._filter_with_admission(iterable, key, prereleases, arbitrary_active)

    def _filter_with_admission(
        self,
        iterable: Iterable[Any],
        key: Callable[[Any], Version | str] | None,
        prereleases: bool | None,
        arbitrary_active: bool,
    ) -> Iterator[Any]:
        """Filter for ranges with admit/reject literals or live arbitrary
        admission (including the universal ``SpecifierSet("")`` range)."""
        admit_set = self._admit
        reject_set = self._reject

        def admit(item: Any) -> tuple[bool, Version | None]:  # noqa: ANN401
            raw: Version | str = item if key is None else key(item)
            raw_lower = str(raw).lower()

            if reject_set and raw_lower in reject_set:
                return False, None
            if admit_set and raw_lower in admit_set:
                return True, coerce_version(raw)

            parsed = coerce_version(raw)
            if parsed is None:
                return arbitrary_active, None
            if not matches_bounds_only(self._bounds, parsed):
                return False, None
            return True, parsed

        if prereleases is True:
            for item in iterable:
                ok, _ = admit(item)
                if ok:
                    yield item
            return

        if prereleases is False:
            for item in iterable:
                ok, parsed = admit(item)
                if not ok:
                    continue
                if parsed is not None and parsed.is_prerelease:
                    continue
                yield item
            return

        all_nonfinal: list[Any] = []
        arbitrary_strings: list[Any] = []
        found_final = False
        for item in iterable:
            ok, parsed = admit(item)
            if not ok:
                continue

            if parsed is None:
                if found_final:
                    yield item
                else:
                    arbitrary_strings.append(item)
                    all_nonfinal.append(item)
                continue

            if not parsed.is_prerelease:
                if not found_final:
                    yield from arbitrary_strings
                    arbitrary_strings.clear()
                    found_final = True
                yield item
                continue

            if not found_final:
                all_nonfinal.append(item)

        if not found_final:
            yield from all_nonfinal

    @classmethod
    def _from_specifier_set(cls, specifier_set: SpecifierSet) -> VersionRange:
        """Build the range accepted by ``specifier_set``.

        Friend constructor for :meth:`~packaging.specifiers.SpecifierSet.to_range`.
        The intersection of every specifier in the set: an empty set yields the
        full range, an unsatisfiable set yields the empty range, and ``===``
        specifiers contribute literal-string admission.
        """
        if not specifier_set:
            result = cls.full()
        elif not specifier_set._has_arbitrary:
            result = cls._build(
                bounds=_canonical_floor(tuple(specifier_set._get_ranges()))
            )
        else:
            result = cls.full()
            for spec in specifier_set:
                if spec.operator == "===":
                    operand = cls._build(
                        bounds=(), admit=frozenset({spec.version.lower()})
                    )
                else:
                    operand = cls._build(
                        bounds=_canonical_floor(tuple(spec._to_ranges()))
                    )
                result = result.intersection(operand)

        result._restamp(
            resolved=specifier_set.prereleases,
            configured=specifier_set._prereleases,
        )

        return result

    def to_specifier_set(self) -> SpecifierSet | None:
        """Return a :class:`~packaging.specifiers.SpecifierSet` that matches the
        same versions as self, or ``None`` if no single set expresses it.

        PEP 440 has no syntax for the strict singleton ``{V}``, the bounds
        produced by complementing ``>V``, or a disjoint union of two or more
        intervals; those return ``None``. The empty range maps to
        ``SpecifierSet("<0")`` and the full range to ``SpecifierSet("")``.

        Every range built from a :class:`~packaging.specifiers.SpecifierSet`
        round-trips: feeding the result back through
        :meth:`~packaging.specifiers.SpecifierSet.to_range` reproduces self.
        The encoder picks a spelling whose pre-release autodetect matches self's
        resolved policy: a prerelease-free spelling such as ``>3.8.post1`` or
        ``<3.14`` when that policy is ``None``, the ``.dev0``-bearing spelling
        otherwise. A no-op ``>=0.dev0`` floor is appended where a ``True`` policy
        rode on a floor the clean encoding dropped. ``None`` is reserved for
        ranges with no single-set form, such as those produced by set algebra.

        >>> str(SpecifierSet(">=1.0,<2.0").to_range().to_specifier_set())
        '<2.0,>=1.0'
        >>> VersionRange.singleton("1.5").to_specifier_set() is None
        True
        """
        from .specifiers import SpecifierSet  # noqa: PLC0415

        if self._reject:
            return None
        if self._admit_arbitrary and self._bounds != FULL_RANGE:
            return None
        if self.is_empty:
            # Both ``<0`` and ``<0.dev0`` are empty, but only ``<0.dev0``
            # autodetects pre-releases, so an autodetected-True empty range
            # (e.g. ``>=1.0,<=1.0a1``) round-trips its policy through it.
            empty_spec = "<0.dev0" if self._prereleases is True else "<0"
            return SpecifierSet(empty_spec, prereleases=self._prereleases_configured)

        admit_pieces = [f"==={literal}" for literal in sorted(self._admit)]

        if not self._bounds:
            # Pure ``===`` literals; only a single literal has a single-set form.
            if len(admit_pieces) != 1:
                return None
            return self._recover_with_floor(admit_pieces)

        if admit_pieces:
            # Bounds plus literals cannot be one set.
            return None

        if self._bounds == FULL_RANGE:
            # ``>=0.dev0`` keeps the synthetic ``.dev0`` floor so the recovered
            # set still admits ``0.dev0`` under a ``prereleases=True`` override,
            # matching the range. ``>=0`` would drop it.
            full_spec = "" if self._admit_arbitrary else ">=0.dev0"
            return self._guard_drift(
                SpecifierSet(full_spec, prereleases=self._prereleases_configured)
            )

        groups = _encode_grouped(list(self._bounds), self._prereleases)
        if groups is None or len(groups) != 1:
            return None
        return self._recover_with_floor(groups[0])

    def _guard_drift(self, recovered: SpecifierSet) -> SpecifierSet | None:
        """Return ``recovered`` unless its autodetect drifts from self.

        Under an explicit configured policy there is no drift concern. Under
        autodetect, a recovered set whose pre-release autodetect differs from
        self's resolved policy would silently filter differently, so report it
        as ``None``.
        """
        if self._prereleases_configured is not None:
            return recovered

        recovered_pre = True if recovered.prereleases else None
        if recovered_pre != self._prereleases:
            return None
        return recovered

    def _recover_with_floor(self, fragments: list[str]) -> SpecifierSet | None:
        """Build a recovered set from ``fragments``, restoring a lost policy.

        Wraps :meth:`_guard_drift`. When the clean encoding drifts to autodetect
        ``None`` but the range resolved to ``True`` (the pre-release intent rode
        on a ``.dev0`` floor that canonicalized to ``-inf``, e.g.
        ``>=0.dev0,!=1.0``), append the no-op ``>=0.dev0`` floor: it admits every
        version, so the accepted set is unchanged, but it autodetects ``True``
        and restores the policy. The opposite drift has no prerelease-free
        spelling, so :meth:`_guard_drift` leaves it ``None``.
        """
        from .specifiers import SpecifierSet  # noqa: PLC0415

        spec_str = ",".join(fragments)
        configured = self._prereleases_configured
        recovered = self._guard_drift(SpecifierSet(spec_str, prereleases=configured))
        if recovered is not None or self._prereleases is not True:
            return recovered

        # The policy is autodetect-True but the clean encoding lost it. An
        # arbitrary ``===`` string blocks recovery: ``>=0.dev0`` rejects
        # non-versions, so no floored set keeps the string and autodetects True.
        if any(coerce_version(literal) is None for literal in self._admit):
            return None

        return SpecifierSet(f"{spec_str},>=0.dev0", prereleases=None)

    @property
    def is_empty(self) -> bool:
        """``True`` if no version or string satisfies this range.

        >>> SpecifierSet(">=2,<1").to_range().is_empty
        True
        >>> SpecifierSet(">=1,<2").to_range().is_empty
        False
        """
        return not self._bounds and not self._admit

    def contains(
        self,
        item: Version | str,
        prereleases: bool | None = None,
        installed: bool | None = None,
    ) -> bool:
        """Return whether item is contained in this range.

        :param item: a version string or :class:`~packaging.version.Version`.
        :param prereleases: whether to match pre-releases. ``None`` (default)
            uses the range's own policy.
        :param installed: when ``True``, accept a pre-release item even if the
            range would not otherwise allow it.

        Unparsable strings do not match, except where the full
        ``SpecifierSet`` would also match: the full range admits any string,
        and a ``===`` range admits items equal to the literal
        case-insensitively.

        >>> r = SpecifierSet(">=1.0,<2.0").to_range()
        >>> r.contains("1.5")
        True
        >>> r.contains("2.0")
        False

        :raises TypeError: if item is not a str or Version.
        """
        if not isinstance(item, (str, Version)):
            raise TypeError(
                f"VersionRange.contains() expected str or Version, "
                f"got {type(item).__name__}"
            )

        parsed: Version | None = item if isinstance(item, Version) else None
        if installed and parsed is None:
            parsed = coerce_version(item)
        if installed and parsed is not None and parsed.is_prerelease:
            prereleases = True

        effective_pre = (
            self._prereleases_configured if prereleases is None else prereleases
        )

        if self._admit or self._reject:
            item_str = str(item).lower()
            if item_str in self._reject:
                return False
            if item_str in self._admit:
                if effective_pre is False:
                    literal_parsed = coerce_version(item_str)
                    if literal_parsed is not None and literal_parsed.is_prerelease:
                        return False
                return True

        if not isinstance(item, Version):
            if parsed is None:
                parsed = coerce_version(item)
            if parsed is None:
                return self._arbitrary_active()
            item = parsed

        if effective_pre is False and item.is_prerelease:
            return False
        return matches_bounds_only(self._bounds, item)

    def __contains__(self, item: Version | str) -> bool:
        """Return whether item is contained in this range.

        Forwards to :meth:`contains` with default arguments.

        >>> "1.5" in SpecifierSet(">=1.0,<2.0").to_range()
        True
        """
        return self.contains(item)

    def __eq__(self, other: object) -> bool:
        """Structural equality.

        Two ranges compare equal when every input to :meth:`contains` and
        :meth:`filter` agrees: the bounds, the ``===`` admit/reject literals,
        the arbitrary-string flag, and both the configured and resolved
        pre-release policies. Equal ranges therefore filter identically.

        >>> r = SpecifierSet(">=1.0,<2.0").to_range()
        >>> r == SpecifierSet(">=1.0,<2.0").to_range()
        True
        """
        if not isinstance(other, VersionRange):
            return NotImplemented
        return (
            self._bounds == other._bounds
            and self._admit == other._admit
            and self._reject == other._reject
            and self._admit_arbitrary == other._admit_arbitrary
            and self._prereleases_configured == other._prereleases_configured
            and self._prereleases == other._prereleases
        )

    def __hash__(self) -> int:
        return hash(
            (
                self._bounds,
                self._admit,
                self._reject,
                self._admit_arbitrary,
                self._prereleases_configured,
                self._prereleases,
            )
        )

    def __repr__(self) -> str:
        """Human-readable representation for debugging.

        >>> SpecifierSet(">=1.0,<2.0").to_range()
        <VersionRange '[1.0, 2.0.dev0)'>
        >>> SpecifierSet("").to_range()
        <VersionRange '(-inf, +inf)' arbitrary>
        >>> SpecifierSet(">=2.0,<1.0").to_range()
        <VersionRange '(empty)'>
        """
        parts: list[str] = []
        if self._bounds:
            parts.append(
                " | ".join(
                    f"{_format_lower(lower)}, {_format_upper(upper)}"
                    for lower, upper in self._bounds
                )
            )
        if self._admit:
            parts.append("{" + ", ".join(sorted(self._admit)) + "}")

        body = " | ".join(parts) if parts else "(empty)"
        if self._reject:
            body = f"{body} \\ {{{', '.join(sorted(self._reject))}}}"

        tail = ""
        if self._admit_arbitrary:
            tail += " arbitrary"
        if self._prereleases_configured is not None:
            tail += f" pre={self._prereleases_configured}"
        return f"<{self.__class__.__name__} {body!r}{tail}>"
