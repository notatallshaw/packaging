# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the BSD License. See the LICENSE file in the root of this repository
# for complete details.
"""
.. testsetup::

    from packaging.ranges import VersionRange
    from packaging.specifiers import Specifier, SpecifierSet
    from packaging.version import Version
"""

from __future__ import annotations

import enum
import functools
import typing
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Final,
    Iterable,
    Iterator,
    Optional,
    Sequence,
    Tuple,
    Union,
)

from .version import InvalidVersion, Version

if TYPE_CHECKING:
    from .specifiers import Specifier, SpecifierSet


__all__ = ["VersionRange"]


def __dir__() -> list[str]:
    return __all__


# ---------------------------------------------------------------------------
# Module sentinels and primitive type aliases
# ---------------------------------------------------------------------------

#: The smallest possible PEP 440 version. No valid version is less than this.
_MIN_VERSION: Final[Version] = Version("0.dev0")

#: Packed pickle form of a single bound:
#: ``(version_str_or_None, inclusive, kind_or_None)``.  ``kind`` is
#: ``None`` for a plain :class:`Version` bound, or ``"AFTER_LOCALS"`` /
#: ``"AFTER_POSTS"`` for a boundary bound.  The on-disk format uses
#: only strings, bools, and ``None`` so it does not depend on private
#: class identities and stays stable across packaging releases.
_PackedBound = Tuple[Optional[str], bool, Optional[str]]


# Type aliases ``_VersionOrBoundary`` and ``_VersionRange`` are defined
# after the classes they reference, below.  Placing them post-definition
# (rather than as forward-referenced strings) keeps cross-module
# resolution stable: the names bind to ``packaging.ranges._LowerBound``
# / etc. at type-check time and stay that way when imported elsewhere.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _trim_release(release: tuple[int, ...]) -> tuple[int, ...]:
    """Strip trailing zeros from a release tuple for normalized comparison."""
    end = len(release)
    while end > 1 and release[end - 1] == 0:
        end -= 1
    return release if end == len(release) else release[:end]


def _next_prefix_dev0(version: Version) -> Version:
    """Smallest version in the next prefix: ``1.2 -> 1.3.dev0``."""
    release = (*version.release[:-1], version.release[-1] + 1)
    return Version.from_parts(epoch=version.epoch, release=release, dev=0)


def _base_dev0(version: Version) -> Version:
    """The ``.dev0`` of a version's base release: ``1.2 -> 1.2.dev0``."""
    return Version.from_parts(epoch=version.epoch, release=version.release, dev=0)


def _coerce_version(version: Version | str) -> Version | None:
    """Parse *version* into a :class:`Version`, or return ``None`` on failure."""
    if not isinstance(version, Version):
        try:
            version = Version(version)
        except InvalidVersion:
            return None
    return version


# ---------------------------------------------------------------------------
# Boundary versions
# ---------------------------------------------------------------------------
#
# Some PEP 440 specifier semantics imply boundaries between real
# versions: ``<=1.0`` includes ``1.0+local`` and ``>1.0`` excludes
# ``1.0.post0``.  No real :class:`Version` falls on those boundaries,
# so :class:`_BoundaryVersion` provides synthetic comparison points
# that sort between the real versions on either side.


class _BoundaryKind(enum.Enum):
    """Where a boundary marker sits in the version ordering."""

    AFTER_LOCALS = enum.auto()  # after V+local, before V.post0
    AFTER_POSTS = enum.auto()  # after V.postN, before next release


@functools.total_ordering
class _BoundaryVersion:
    """A point on the version line between two real PEP 440 versions.

    Two kinds exist, shown relative to a base version V::

        V < V+local < AFTER_LOCALS(V) < V.post0 < AFTER_POSTS(V)

    ``AFTER_LOCALS`` sits after V and every V+local but before V.post0.
    Used as the upper bound of ``<=V``, ``==V``, ``!=V`` (no local),
    and as the lower bound of the upper-side range of ``!=V``.

    ``AFTER_POSTS`` sits after every V.postN but before the next
    release segment.  Used as the lower bound of ``>V`` (with V final
    or pre-release) to exclude post-releases per PEP 440.
    """

    __slots__ = (
        "_kind",
        "_trimmed_release",
        "_v_dev",
        "_v_epoch",
        "_v_post",
        "_v_pre",
        "version",
    )

    def __init__(self, version: Version, kind: _BoundaryKind) -> None:
        self.version = version
        self._kind = kind
        self._trimmed_release = _trim_release(version.release)
        # Cache the bound version's identifying fields so the per-call
        # family check below works in straight ``==`` compares against
        # ints/tuples/None — no method dispatch.
        self._v_epoch = version.epoch
        self._v_pre = version.pre
        self._v_post = version.post
        self._v_dev = version.dev

    def _is_family(self, other: Version) -> bool:
        """Is ``other`` a version that this boundary sorts above?"""
        if other.epoch != self._v_epoch:
            return False
        # Inline release-trim comparison: ``other.release`` matches the
        # trimmed release iff its leading slice is equal and any extra
        # components are zero.  Avoids the tuple allocation that calling
        # :func:`_trim_release` on ``other.release`` would incur.
        other_release = other.release
        trimmed = self._trimmed_release
        n = len(trimmed)
        if len(other_release) < n:
            return False
        if other_release[:n] != trimmed:
            return False
        for i in range(n, len(other_release)):
            if other_release[i] != 0:
                return False
        if other.pre != self._v_pre:
            return False
        if self._kind == _BoundaryKind.AFTER_LOCALS:
            # Local family: same public version (any local label).
            return other.post == self._v_post and other.dev == self._v_dev
        # Post family: V itself + any post-release of V.
        return other.dev == self._v_dev or other.post is not None

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _BoundaryVersion):
            return self.version == other.version and self._kind == other._kind
        return NotImplemented

    def __lt__(self, other: _BoundaryVersion | Version) -> bool:
        if isinstance(other, _BoundaryVersion):
            if self.version != other.version:
                return self.version < other.version
            return self._kind.value < other._kind.value
        # ``boundary < other_version`` iff V < other AND other not in family.
        # The cheap V >= other path short-circuits before the family check.
        if not (self.version < other):
            return False
        return not self._is_family(other)

    def __gt__(self, other: _BoundaryVersion | Version) -> bool:
        # Defined directly to avoid the ``functools.total_ordering``
        # wrapper, which would route through ``__lt__`` *and* ``__eq__``
        # and force a ``NotImplemented`` round-trip on every reflected
        # ``Version < boundary`` comparison.
        if isinstance(other, _BoundaryVersion):
            if self.version != other.version:
                return self.version > other.version
            return self._kind.value > other._kind.value
        if self.version >= other:
            return True
        return self._is_family(other)

    def __hash__(self) -> int:
        return hash((self.version, self._kind))

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.version!r}, {self._kind.name})"


if TYPE_CHECKING:
    #: The version slot of a :class:`_LowerBound` / :class:`_UpperBound`.
    _VersionOrBoundary = Union[Version, _BoundaryVersion, None]


# ---------------------------------------------------------------------------
# Closure factories: per-bound "above" / "below" predicates
# ---------------------------------------------------------------------------
#
# Each factory builds a small closure that answers "is *parsed* at or
# above / below this boundary?".  The closures capture the bound
# version's identifying fields at construction time and inline the
# family check, which lets the hot filter / contains loops avoid the
# reflected-dispatch path through :class:`_BoundaryVersion`.
#
# All accesses on *parsed* go through the public :class:`Version`
# attribute API.


def _make_above_after_posts(v: Version) -> Callable[[Version], bool]:
    """Predicate ``parsed > AFTER_POSTS(v)`` for a lower bound.

    Per PEP 440: *"The exclusive ordered comparison ``>V`` MUST NOT
    allow a post-release of the given version unless V itself is a
    post release."*  ``AFTER_POSTS`` therefore sits above V and every
    ``V.postN`` (with or without local), and just below the next
    release.
    """
    v_ge = v.__ge__
    v_epoch = v.epoch
    v_pre = v.pre
    v_dev = v.dev
    v_release_trimmed = _trim_release(v.release)
    n_trimmed = len(v_release_trimmed)

    def above(parsed: Version) -> bool:
        if v_ge(parsed):
            return False
        # parsed > v cmpkey-wise: above the boundary iff NOT in v's
        # post family.
        if parsed.epoch != v_epoch:
            return True
        parsed_release = parsed.release
        if len(parsed_release) < n_trimmed:
            return True
        if parsed_release[:n_trimmed] != v_release_trimmed:
            return True
        for i in range(n_trimmed, len(parsed_release)):
            if parsed_release[i] != 0:
                return True
        if parsed.pre != v_pre:
            return True
        # In post family iff: same dev as v (covers v itself + v+local),
        # or any post-release (covers v.postN + v.postN+local).
        if parsed.dev == v_dev or parsed.post is not None:
            return False
        # Different dev with no post means parsed sorts before v
        # cmpkey-wise, in which case v_ge returned True already.
        return False  # pragma: no cover

    return above


def _make_above_after_locals(v: Version) -> Callable[[Version], bool]:
    """Predicate ``parsed > AFTER_LOCALS(v)`` for a lower bound.

    Used by the upper-side range of ``!=v`` (when *v* has no local
    segment).  ``AFTER_LOCALS`` sits above v and every ``v+local`` but
    just below ``v.post0``.
    """
    v_ge = v.__ge__
    v_epoch = v.epoch
    v_pre = v.pre
    v_post = v.post
    v_dev = v.dev
    v_release_trimmed = _trim_release(v.release)
    n_trimmed = len(v_release_trimmed)

    def above(parsed: Version) -> bool:
        if v_ge(parsed):
            return False
        # parsed > v cmpkey-wise: above the boundary iff NOT in v's
        # local family (same public version, any local segment).
        if parsed.epoch != v_epoch:
            return True
        parsed_release = parsed.release
        if len(parsed_release) < n_trimmed:
            return True
        if parsed_release[:n_trimmed] != v_release_trimmed:
            return True
        for i in range(n_trimmed, len(parsed_release)):
            if parsed_release[i] != 0:
                return True
        if parsed.pre != v_pre:
            return True
        if parsed.post != v_post:
            return True
        return parsed.dev != v_dev

    return above


def _make_below_after_locals(v: Version) -> Callable[[Version], bool]:
    """Predicate ``parsed <= AFTER_LOCALS(v)`` for an upper bound.

    Used by ``<=v``, ``==v``, ``!=v`` (no local).  ``parsed`` is at or
    below the boundary when it is at or below v cmpkey-wise *or* when
    it is in v's local family.
    """
    v_ge = v.__ge__
    v_epoch = v.epoch
    v_pre = v.pre
    v_post = v.post
    v_dev = v.dev
    v_release_trimmed = _trim_release(v.release)
    n_trimmed = len(v_release_trimmed)

    def below(parsed: Version) -> bool:
        if v_ge(parsed):
            return True
        # parsed > v cmpkey-wise: below the boundary iff in v's local
        # family.
        if parsed.epoch != v_epoch:
            return False
        parsed_release = parsed.release
        if len(parsed_release) < n_trimmed:
            return False
        if parsed_release[:n_trimmed] != v_release_trimmed:
            return False
        for i in range(n_trimmed, len(parsed_release)):
            if parsed_release[i] != 0:
                return False
        if parsed.pre != v_pre:
            return False
        if parsed.post != v_post:
            return False
        return parsed.dev == v_dev

    return below


def _make_below_after_posts(v: Version) -> Callable[[Version], bool]:
    """Predicate ``parsed <= AFTER_POSTS(v)`` for an upper bound.

    Mirror of :func:`_make_above_after_posts`: produced only by
    :meth:`VersionRange.complement` of a range whose lower bound is
    ``AFTER_POSTS(v)``.  ``parsed`` is at or below the boundary when
    it is at or below v cmpkey-wise *or* when it is in v's post family
    (V itself with any local segment, or any V.postN with or without
    local).
    """
    v_ge = v.__ge__
    v_epoch = v.epoch
    v_pre = v.pre
    v_dev = v.dev
    v_release_trimmed = _trim_release(v.release)
    n_trimmed = len(v_release_trimmed)

    def below(parsed: Version) -> bool:
        if v_ge(parsed):
            return True
        # parsed > v cmpkey-wise: below the boundary iff in v's post
        # family.
        if parsed.epoch != v_epoch:
            return False
        parsed_release = parsed.release
        if len(parsed_release) < n_trimmed:
            return False
        if parsed_release[:n_trimmed] != v_release_trimmed:
            return False
        for i in range(n_trimmed, len(parsed_release)):
            if parsed_release[i] != 0:
                return False
        if parsed.pre != v_pre:
            return False
        # Same dev as v with no post -> parsed sorts <= v already
        # (handled by v_ge above); reach here only with parsed.post set.
        return parsed.dev == v_dev or parsed.post is not None

    return below


# ---------------------------------------------------------------------------
# Range bound types
# ---------------------------------------------------------------------------


@functools.total_ordering
class _LowerBound:
    """Lower bound of a version range.

    A ``version`` of ``None`` means unbounded below (-inf).  At equal
    versions, ``[v`` sorts before ``(v`` because an inclusive bound
    starts earlier on the version line.
    """

    __slots__ = ("_above", "inclusive", "version")

    def __init__(self, version: _VersionOrBoundary, inclusive: bool) -> None:
        self.version = version
        self.inclusive = inclusive
        # Pre-bind a single predicate that answers "is parsed at or
        # above this lower bound?" for the hot filter / contains
        # loops.  Each invocation is one function call — no
        # operator-dispatch chain and no dead equality check.
        if version is None:
            self._above: Callable[[Version], bool] | None = None
        elif isinstance(version, _BoundaryVersion):
            # ``>v`` produces an AFTER_POSTS lower bound; the
            # upper-side range of ``!=v`` produces an AFTER_LOCALS
            # lower bound.
            if version._kind == _BoundaryKind.AFTER_POSTS:
                self._above = _make_above_after_posts(version.version)
            else:
                self._above = _make_above_after_locals(version.version)
        elif inclusive:
            self._above = version.__le__
        else:
            self._above = version.__lt__

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _LowerBound):
            return NotImplemented  # pragma: no cover
        return self.version == other.version and self.inclusive == other.inclusive

    def __lt__(self, other: _LowerBound) -> bool:
        if not isinstance(other, _LowerBound):  # pragma: no cover
            return NotImplemented
        # -inf < anything (except -inf itself).
        if self.version is None:
            return other.version is not None
        if other.version is None:
            return False
        if self.version != other.version:
            return self.version < other.version
        # ``[v < (v``: inclusive starts earlier.
        return self.inclusive and not other.inclusive

    def __hash__(self) -> int:
        return hash((self.version, self.inclusive))

    def __repr__(self) -> str:
        bracket = "[" if self.inclusive else "("
        return f"<{self.__class__.__name__} {bracket}{self.version!r}>"


@functools.total_ordering
class _UpperBound:
    """Upper bound of a version range.

    A ``version`` of ``None`` means unbounded above (+inf).  At equal
    versions, ``v)`` sorts before ``v]`` because an exclusive bound
    ends earlier on the version line.
    """

    __slots__ = ("_below", "inclusive", "version")

    def __init__(self, version: _VersionOrBoundary, inclusive: bool) -> None:
        self.version = version
        self.inclusive = inclusive
        # Pre-bind a single predicate that answers "is parsed at or
        # below this upper bound?".  See :class:`_LowerBound` for the
        # rationale.
        if version is None:
            self._below: Callable[[Version], bool] | None = None
        elif isinstance(version, _BoundaryVersion):
            # Standard specifiers only ever produce AFTER_LOCALS upper
            # bounds (from ``<=v`` / ``==v`` / ``!=v`` with no local).
            # Complement reverses bound roles, so a range whose lower
            # bound is ``AFTER_POSTS(v)`` becomes an upper bound after
            # complementing — both kinds need to be supported.
            if version._kind == _BoundaryKind.AFTER_LOCALS:
                self._below = _make_below_after_locals(version.version)
            else:
                self._below = _make_below_after_posts(version.version)
        elif inclusive:
            self._below = version.__ge__
        else:
            self._below = version.__gt__

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _UpperBound):
            return NotImplemented  # pragma: no cover
        return self.version == other.version and self.inclusive == other.inclusive

    def __lt__(self, other: _UpperBound) -> bool:
        if not isinstance(other, _UpperBound):  # pragma: no cover
            return NotImplemented
        # Nothing < +inf (except +inf itself).
        if self.version is None:
            return False
        if other.version is None:
            return True
        if self.version != other.version:
            return self.version < other.version
        # ``v) < v]``: exclusive ends earlier.
        return not self.inclusive and other.inclusive

    def __hash__(self) -> int:
        return hash((self.version, self.inclusive))

    def __repr__(self) -> str:
        bracket = "]" if self.inclusive else ")"
        return f"<{self.__class__.__name__} {self.version!r}{bracket}>"


if TYPE_CHECKING:
    #: A single contiguous version range, as a (lower, upper) pair.
    _VersionRange = Tuple[_LowerBound, _UpperBound]


# Sentinel bounds and the all-versions range.  Defined here, after the
# bound classes, because they are :class:`_LowerBound` / :class:`_UpperBound`
# instances themselves.
_NEG_INF = _LowerBound(None, False)
_POS_INF = _UpperBound(None, False)
_FULL_RANGE: tuple[_VersionRange] = ((_NEG_INF, _POS_INF),)


# ---------------------------------------------------------------------------
# Range helpers: emptiness, intersection, membership, filtering
# ---------------------------------------------------------------------------


def _range_is_empty(lower: _LowerBound, upper: _UpperBound) -> bool:
    """True when the range defined by *lower* and *upper* contains no versions."""
    if lower.version is None or upper.version is None:
        return False
    if lower.version == upper.version:
        return not (lower.inclusive and upper.inclusive)
    return lower.version > upper.version


def _intersect_ranges(
    left: Sequence[_VersionRange],
    right: Sequence[_VersionRange],
) -> list[_VersionRange]:
    """Intersect two sorted, non-overlapping range lists (two-pointer merge)."""
    result: list[_VersionRange] = []
    left_index = right_index = 0
    while left_index < len(left) and right_index < len(right):
        left_lower, left_upper = left[left_index]
        right_lower, right_upper = right[right_index]

        lower = max(left_lower, right_lower)
        upper = min(left_upper, right_upper)

        if not _range_is_empty(lower, upper):
            result.append((lower, upper))

        # Advance whichever side has the smaller upper bound.
        if left_upper < right_upper:
            left_index += 1
        else:
            right_index += 1

    return result


def _union_ranges(
    left: Sequence[_VersionRange],
    right: Sequence[_VersionRange],
) -> list[_VersionRange]:
    """Union two sorted, non-overlapping range lists.

    Linear merge over the two pre-sorted inputs followed by a single
    coalescing pass: adjacent or overlapping ranges collapse so the
    result is itself sorted and non-overlapping (the invariant the
    rest of the module relies on).
    """
    if not left:
        return list(right)
    if not right:
        return list(left)

    # Merge two sorted lists by lower bound (linear, no resort).
    merged_input: list[_VersionRange] = []
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

    merged: list[_VersionRange] = [merged_input[0]]
    for lower, upper in merged_input[1:]:
        prev_lower, prev_upper = merged[-1]

        # Adjacent ranges merge when the previous upper sits at or
        # past the new lower; ``+inf``/``-inf`` short-circuits collapse
        # the unbounded cases.
        if prev_upper.version is None:
            overlaps = True
        elif lower.version is None:
            overlaps = True  # pragma: no cover -- merged_input is sorted by lower
        elif prev_upper.version > lower.version:
            overlaps = True
        elif prev_upper.version == lower.version:
            overlaps = prev_upper.inclusive or lower.inclusive
        else:
            overlaps = False

        if overlaps:
            new_upper = max(prev_upper, upper)
            merged[-1] = (prev_lower, new_upper)
        else:
            merged.append((lower, upper))

    return merged


def _complement_ranges(
    ranges: Sequence[_VersionRange],
) -> list[_VersionRange]:
    """Complement a sorted, non-overlapping range list.

    Yields the gaps between ranges, plus a leading gap before the first
    range and a trailing gap after the last.  Bound inclusivity flips
    so the complement-of-complement round-trips back to the input.
    """
    if not ranges:
        return list(_FULL_RANGE)

    result: list[_VersionRange] = []
    prev_upper: _UpperBound | None = None

    for lower, upper in ranges:
        if prev_upper is None:
            # Leading gap from -inf up to the first range's lower.
            if lower.version is not None:
                gap_upper = _UpperBound(lower.version, not lower.inclusive)
                result.append((_NEG_INF, gap_upper))
        else:
            gap_lower = _LowerBound(prev_upper.version, not prev_upper.inclusive)
            gap_upper = _UpperBound(lower.version, not lower.inclusive)
            # Adjacent ranges in the input are non-touching by
            # construction, so the gap between them is non-empty; the
            # check is a defensive guard.
            if not _range_is_empty(gap_lower, gap_upper):  # pragma: no branch
                result.append((gap_lower, gap_upper))
        prev_upper = upper

    # Trailing gap from the final range's upper to +inf.
    if prev_upper is not None and prev_upper.version is not None:
        gap_lower = _LowerBound(prev_upper.version, not prev_upper.inclusive)
        result.append((gap_lower, _POS_INF))

    return result


def _filter_by_ranges(
    ranges: Sequence[_VersionRange],
    iterable: Iterable[Any],
    key: Callable[[Any], Version | str] | None,
    prereleases: bool | None,
) -> Iterator[Any]:
    """Filter *iterable* against precomputed version *ranges*.

    Used by :class:`Specifier` and :class:`SpecifierSet`.  When
    *prereleases* is ``None``, the PEP 440 default applies:
    pre-releases are excluded unless no final release matches the
    range, in which case the buffered pre-releases are emitted at the
    end.  This is handled inline (single pass, no generator chain).
    """
    if prereleases is None:
        # PEP 440 default: yield finals immediately; buffer
        # pre-releases until at least one final has been emitted.
        # Items that don't parse as PEP 440 versions cannot reach this
        # path because the range filter rejects them; the ``===``
        # arbitrary path is handled separately in the calling
        # ``filter`` methods.
        nonfinal_buffer: list[Any] = []
        found_final = False

        if len(ranges) == 1:
            lower, upper = ranges[0]
            above = lower._above
            below = upper._below
            for item in iterable:
                parsed = _coerce_version(item if key is None else key(item))
                if parsed is None:
                    continue
                if above is not None and not above(parsed):
                    continue
                if below is not None and not below(parsed):
                    continue
                if parsed.is_prerelease:
                    if not found_final:
                        nonfinal_buffer.append(item)
                else:
                    found_final = True
                    yield item
            if not found_final:
                yield from nonfinal_buffer
            return

        for item in iterable:
            parsed = _coerce_version(item if key is None else key(item))
            if parsed is None:
                continue
            for lower, upper in ranges:
                above = lower._above
                if above is not None and not above(parsed):
                    break
                below = upper._below
                if below is None or below(parsed):
                    if parsed.is_prerelease:
                        if not found_final:
                            nonfinal_buffer.append(item)
                    else:
                        found_final = True
                        yield item
                    break
        if not found_final:
            yield from nonfinal_buffer
        return

    exclude_prereleases = prereleases is False

    if len(ranges) == 1:
        # Hot path: most specifiers and small SpecifierSets reduce to
        # a single contiguous range.
        lower, upper = ranges[0]
        above = lower._above
        below = upper._below
        for item in iterable:
            parsed = _coerce_version(item if key is None else key(item))
            if parsed is None:
                continue
            if exclude_prereleases and parsed.is_prerelease:
                continue
            if above is not None and not above(parsed):
                continue
            if below is None or below(parsed):
                yield item
        return

    for item in iterable:
        parsed = _coerce_version(item if key is None else key(item))
        if parsed is None:
            continue
        if exclude_prereleases and parsed.is_prerelease:
            continue
        for lower, upper in ranges:
            above = lower._above
            if above is not None and not above(parsed):
                break
            below = upper._below
            if below is None or below(parsed):
                yield item
                break


def _nearest_non_prerelease(
    v: Version | _BoundaryVersion | None,
) -> Version | None:
    """Smallest non-pre-release version at or above *v*, or ``None``."""
    if v is None:
        return None
    if isinstance(v, _BoundaryVersion):
        inner = v.version
        if inner.is_prerelease:
            # AFTER_LOCALS(1.0a1) -> nearest non-pre is 1.0
            return inner.__replace__(pre=None, dev=None, local=None)
        # AFTER_LOCALS(1.0) -> nearest non-pre is 1.0.post0
        # AFTER_LOCALS(1.0.post0) -> nearest non-pre is 1.0.post1
        k = (inner.post + 1) if inner.post is not None else 0
        return inner.__replace__(post=k, local=None)
    if not v.is_prerelease:
        return v
    return v.__replace__(pre=None, dev=None, local=None)


def _ranges_are_prerelease_only(ranges: Sequence[_VersionRange]) -> bool:
    """``True`` when every range in *ranges* contains only pre-release versions.

    Used to detect unsatisfiable specifier sets when ``prereleases=False``:
    if every range is pre-release-only, every version it contains is excluded.
    """
    for lower, upper in ranges:
        nearest = _nearest_non_prerelease(lower.version)
        if nearest is None:
            return False
        if upper.version is None or nearest < upper.version:
            return False
        if nearest == upper.version and upper.inclusive:
            return False
    return True


# ---------------------------------------------------------------------------
# Specifier-to-range construction
# ---------------------------------------------------------------------------
#
# These functions encode each PEP 440 operator as a sorted,
# non-overlapping list of ``_VersionRange`` pairs.  They are
# module-level helpers (rather than methods on :class:`Specifier`) so
# the bound construction stays in one place alongside the bound
# classes themselves.


def _wildcard_ranges(op: str, base: Version) -> list[_VersionRange]:
    """Ranges for ``==V.*`` and ``!=V.*``.

    ``==1.2.*`` -> ``[1.2.dev0, 1.3.dev0)``;  ``!=1.2.*`` -> complement.
    """
    lower = _base_dev0(base)
    upper = _next_prefix_dev0(base)
    if op == "==":
        return [(_LowerBound(lower, True), _UpperBound(upper, False))]
    # !=
    return [
        (_NEG_INF, _UpperBound(lower, False)),
        (_LowerBound(upper, True), _POS_INF),
    ]


def _standard_ranges(op: str, v: Version, has_local: bool) -> list[_VersionRange]:
    """Ranges for the standard PEP 440 operators (no wildcard, no ``===``).

    *has_local* indicates whether the spec string included a ``+local``
    segment; relevant only for ``==`` / ``!=`` to decide whether the
    upper bound includes V's local family.
    """
    if op == ">=":
        return [(_LowerBound(v, True), _POS_INF)]

    if op == "<=":
        return [
            (
                _NEG_INF,
                _UpperBound(_BoundaryVersion(v, _BoundaryKind.AFTER_LOCALS), True),
            )
        ]

    if op == ">":
        if v.dev is not None:
            # ``>V.devN``: dev versions have no post-releases, so
            # the next real version is V.dev(N+1).
            lower_ver = v.__replace__(dev=v.dev + 1, local=None)
            return [(_LowerBound(lower_ver, True), _POS_INF)]
        if v.post is not None:
            # ``>V.postN``: next real version is V.post(N+1).dev0.
            lower_ver = v.__replace__(post=v.post + 1, dev=0, local=None)
            return [(_LowerBound(lower_ver, True), _POS_INF)]
        # ``>V`` (final or pre-release V): exclude V itself, V+local,
        # and every V.postN per PEP 440.
        return [
            (
                _LowerBound(_BoundaryVersion(v, _BoundaryKind.AFTER_POSTS), False),
                _POS_INF,
            )
        ]

    if op == "<":
        # ``<V`` excludes pre-releases of V when V is not a
        # pre-release.  V.dev0 is the earliest pre-release of V.
        bound = v if v.is_prerelease else v.__replace__(dev=0, local=None)
        if bound <= _MIN_VERSION:
            return []
        return [(_NEG_INF, _UpperBound(bound, False))]

    # ``==`` / ``!=``: local versions of V match when the spec has
    # no local segment.
    after_locals = _BoundaryVersion(v, _BoundaryKind.AFTER_LOCALS)
    upper = v if has_local else after_locals

    if op == "==":
        return [(_LowerBound(v, True), _UpperBound(upper, True))]

    if op == "!=":
        return [
            (_NEG_INF, _UpperBound(v, False)),
            (_LowerBound(upper, False), _POS_INF),
        ]

    if op == "~=":
        prefix = v.__replace__(release=v.release[:-1])
        return [(_LowerBound(v, True), _UpperBound(_next_prefix_dev0(prefix), False))]

    raise ValueError(f"Unknown operator: {op!r}")  # pragma: no cover


# ---------------------------------------------------------------------------
# Pickle support for VersionRange
# ---------------------------------------------------------------------------


def _format_lower(bound: _LowerBound) -> str:
    if bound.version is None:
        return "(-inf"
    bracket = "[" if bound.inclusive else "("
    inner = (
        bound.version.version
        if isinstance(bound.version, _BoundaryVersion)
        else bound.version
    )
    return f"{bracket}{inner}"


def _format_upper(bound: _UpperBound) -> str:
    if bound.version is None:
        return "+inf)"
    bracket = "]" if bound.inclusive else ")"
    inner = (
        bound.version.version
        if isinstance(bound.version, _BoundaryVersion)
        else bound.version
    )
    return f"{inner}{bracket}"


def _pack_bound(bound: _LowerBound | _UpperBound) -> _PackedBound:
    v = bound.version
    if v is None:
        return (None, bound.inclusive, None)
    if isinstance(v, _BoundaryVersion):
        return (str(v.version), bound.inclusive, v._kind.name)
    return (str(v), bound.inclusive, None)


def _unpack_bound(
    cls: type[_LowerBound | _UpperBound],
    packed: _PackedBound,
) -> _LowerBound | _UpperBound:
    version_str, inclusive, kind_name = packed
    if version_str is None:
        return cls(None, inclusive)
    base = Version(version_str)
    if kind_name is not None:
        return cls(_BoundaryVersion(base, _BoundaryKind[kind_name]), inclusive)
    return cls(base, inclusive)


def _restore_version_range(
    packed_bounds: tuple[tuple[_PackedBound, _PackedBound], ...],
    arbitrary: str | None = None,
) -> VersionRange:
    """Module-level pickle restorer for :class:`VersionRange`.

    Reconstructs the :class:`VersionRange` from the primitive form
    produced by :meth:`VersionRange.__reduce__` and bypasses the
    :meth:`__new__` guard via :meth:`VersionRange._build`.  The
    ``arbitrary`` parameter is keyword-defaulted so pickles created
    before the ``===`` carve-out (single-positional payload) still
    restore correctly.
    """
    bounds = tuple(
        (
            typing.cast("_LowerBound", _unpack_bound(_LowerBound, lo)),
            typing.cast("_UpperBound", _unpack_bound(_UpperBound, hi)),
        )
        for lo, hi in packed_bounds
    )
    return VersionRange._build(bounds, arbitrary=arbitrary)


# ---------------------------------------------------------------------------
# VersionRange -> SpecifierSet conversion helpers
# ---------------------------------------------------------------------------
#
# Going the other way -- from a :class:`VersionRange` back to a
# :class:`packaging.specifiers.SpecifierSet` -- is *partial*.  Not every
# range has a SpecifierSet form.  Concretely:
#
# * PEP 440 ``<V`` excludes pre-releases of V, so the mathematical
#   complement of ``>=V`` (which includes those pre-releases) has no
#   single specifier.
# * PEP 440 ``==V`` matches ``V+local`` too, so the strict singleton
#   ``[V, V]`` produced by :meth:`VersionRange.singleton` has no
#   single specifier when V has no local segment.
# * Disjoint unions whose gap is not a complete ``==V.*`` family or a
#   ``==V`` family cannot be expressed as ``base & !=...`` chains.
#
# The helpers below classify each bound by its specifier shape and
# build the encoding incrementally; an unsupported shape collapses
# the whole conversion to ``None``.


def _is_dev0_version(v: Version) -> bool:
    """``True`` when *v* is exactly ``X[.Y]*.dev0`` -- the form ``<X``
    produces as its upper bound."""
    return v.dev == 0 and v.pre is None and v.post is None and v.local is None


def _encode_lower(lower: _LowerBound) -> list[str] | _NotEncodable:
    """Encode a lower bound as a list of specifier fragments.

    Returns:
      * ``[]`` -- bound is ``-inf``; no fragment needed.
      * ``[fragment, ...]`` -- one or more specifier fragments.  Most
        bounds emit a single fragment; ``AFTER_LOCALS(V)`` lower bounds
        require two (``>=V`` plus ``!=V``) since the boundary excludes
        ``V`` and every ``V+local`` but no single ordered specifier
        does that.
      * :data:`_NOT_ENCODABLE` -- this bound shape has no specifier
        representation in PEP 440.

    The latter case includes ``V (excl)`` lower (a "strictly above V"
    lower that no operator produces) and ``AFTER_POSTS(V) (incl)``
    lower (arises from complementing ``>V``).
    """
    v = lower.version
    if v is None:
        return []
    if isinstance(v, _BoundaryVersion):
        if v._kind == _BoundaryKind.AFTER_POSTS and not lower.inclusive:
            return [f">{v.version}"]
        if v._kind == _BoundaryKind.AFTER_LOCALS:
            # Strictly above V's local family.  Express as ``>=V,!=V``:
            # ``>=V`` produces ``[V, +inf)``; intersecting with ``!=V``
            # subtracts ``[V, AFTER_LOCALS(V)]``, leaving exactly
            # ``(AFTER_LOCALS(V), +inf)``.  The boundary's inclusivity
            # does not matter at the real-version level (no real
            # version sits on the synthetic boundary).
            return [f">={v.version}", f"!={v.version}"]
        # AFTER_POSTS lower with inclusive=True is unreachable from
        # any specifier or set-algebra operation; defensive guard.
        return _NOT_ENCODABLE  # pragma: no cover
    if lower.inclusive:
        return [f">={v}"]
    return _NOT_ENCODABLE


def _encode_upper(upper: _UpperBound) -> list[str] | _NotEncodable:
    """Encode an upper bound as a list of specifier fragments.

    Returns ``[]`` for ``+inf``, a list of fragments otherwise, or
    :data:`_NOT_ENCODABLE` when the bound has no specifier form.
    """
    v = upper.version
    if v is None:
        return []
    if isinstance(v, _BoundaryVersion):
        if v._kind == _BoundaryKind.AFTER_LOCALS and upper.inclusive:
            return [f"<={v.version}"]
        return _NOT_ENCODABLE
    if not upper.inclusive:
        if _is_dev0_version(v):
            # ``<V`` produces upper = V.dev0 (excl); strip the
            # synthetic dev0 to recover the original V.
            return [f"<{v.__replace__(dev=None)}"]
        # ``V (excl)`` upper -- "strictly less than V cmpkey-wise,
        # including V's pre-releases".  Expressible as ``<=V,!=V``:
        # ``<=V`` = ``(-inf, AFTER_LOCALS(V)]``; intersecting with
        # ``!=V`` removes ``[V, AFTER_LOCALS(V)]``, leaving exactly
        # ``(-inf, V (excl))``.
        return [f"<={v}", f"!={v}"]
    return _NOT_ENCODABLE


class _NotEncodable:
    """Sentinel for "this bound has no PEP 440 specifier representation"."""

    __slots__ = ()


_NOT_ENCODABLE: Final = _NotEncodable()


def _encode_interval(
    lower: _LowerBound,
    upper: _UpperBound,
) -> list[str] | None:
    """Encode one interval as a list of specifier fragments, or ``None``.

    Includes the special case ``[V, V]`` (singleton interval, both
    bounds the same plain Version inclusive) when V carries a local
    segment.  PEP 440's ``==V+local`` matches only the literal
    ``V+local``, so ``[V+local, V+local]`` is exactly that specifier.
    Without a local, the singleton interval has no specifier form
    (``==V`` is wider since it also matches ``V+local``).
    """
    if (
        lower.version is not None
        and upper.version is not None
        and not isinstance(lower.version, _BoundaryVersion)
        and not isinstance(upper.version, _BoundaryVersion)
        and lower.inclusive
        and upper.inclusive
        and lower.version == upper.version
        and lower.version.local is not None
    ):
        return [f"=={lower.version}"]
    lo = _encode_lower(lower)
    if isinstance(lo, _NotEncodable):
        return None
    up = _encode_upper(upper)
    if isinstance(up, _NotEncodable):
        return None
    return lo + up


def _detect_ne_v(
    left_upper: _UpperBound,
    right_lower: _LowerBound,
) -> Version | None:
    """If ``[..., V (excl)] [AFTER_LOCALS(V) (excl), ...]`` matches, return V.

    This is the gap shape the ``!=V`` specifier produces when intersected
    with surrounding bounds.  It is the only ``!=V`` pattern that can
    appear inside a multi-interval range.
    """
    if isinstance(left_upper.version, _BoundaryVersion):
        return None
    if left_upper.version is None or left_upper.inclusive:
        return None
    if not isinstance(right_lower.version, _BoundaryVersion):
        return None
    if right_lower.version._kind != _BoundaryKind.AFTER_LOCALS:
        return None
    if right_lower.inclusive:
        # AFTER_LOCALS lower with inclusive=True does not arise from
        # any specifier or set-algebra operation; defensive guard.
        return None  # pragma: no cover
    if right_lower.version.version != left_upper.version:
        # The ``!=V`` pattern is contiguous: when bounds match the
        # shape but the V's differ, multi-interval input came from a
        # union of unrelated ranges.  Defensive.
        return None  # pragma: no cover
    return left_upper.version


def _detect_ne_v_star(
    left_upper: _UpperBound,
    right_lower: _LowerBound,
) -> Version | None:
    """If ``[..., V.dev0 (excl)] [V_next.dev0 (incl), ...]`` matches, return V.

    This is the gap shape ``!=V.*`` produces.  ``V`` and ``V_next``
    must share an epoch and a release prefix that differs only in the
    final component being incremented by one.  Returns the prefix
    version (without the synthetic ``.dev0``) so the caller can write
    ``!=V.*``.
    """
    lu = left_upper.version
    rl = right_lower.version
    if isinstance(lu, _BoundaryVersion) or isinstance(rl, _BoundaryVersion):
        return None
    if lu is None or rl is None:
        # First-interval upper or last-interval lower at infinity --
        # such an interval is the universe and no second interval
        # would exist; defensive.
        return None  # pragma: no cover
    if left_upper.inclusive or not right_lower.inclusive:
        return None
    if not (_is_dev0_version(lu) and _is_dev0_version(rl)):
        return None
    if lu.epoch != rl.epoch:
        return None
    left_release = lu.release
    right_release = rl.release
    if len(left_release) != len(right_release) or not left_release:
        return None
    # All components except the last must match; the last increments by 1.
    if left_release[:-1] != right_release[:-1]:
        return None
    if right_release[-1] != left_release[-1] + 1:
        return None
    return lu.__replace__(dev=None)


# ---------------------------------------------------------------------------
# VersionRange
# ---------------------------------------------------------------------------


class VersionRange:
    """A set of :class:`~packaging.version.Version` values, expressed as a
    union of zero or more disjoint intervals on the PEP 440 version
    ordering.

    Instances are not constructed directly.  Use the
    :meth:`from_specifier` or :meth:`from_specifier_set` classmethods,
    or call :meth:`Specifier.to_range` /
    :meth:`SpecifierSet.to_range` on a parsed specifier.

    >>> r = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
    >>> "1.5" in r
    True
    >>> "2.0" in r
    False
    >>> bool(r)
    True
    >>> bool(VersionRange.from_specifier_set(SpecifierSet(">=2.0,<1.0")))
    False

    ``===`` carve-out
    ~~~~~~~~~~~~~~~~~

    PEP 440's ``===`` (arbitrary-string equality) does not describe a
    set of :class:`~packaging.version.Version` values; it matches the
    literal string of the candidate, case-insensitively.  As a special
    case, :meth:`from_specifier` and :meth:`from_specifier_set` *do*
    return a :class:`VersionRange` for ``===``: the ``_arbitrary``
    slot stores the literal, and :meth:`__contains__` /
    :meth:`filter` apply the case-insensitive match (intersected with
    any rangelike specs in the same set).  Such a range **breaks**
    PubGrub's set-theoretic invariants -- :meth:`intersection`,
    :meth:`union`, :meth:`complement` raise :exc:`TypeError` when
    either operand is an arbitrary-equality range, since there is no
    sound way to combine an arbitrary-string match with the
    Boolean-lattice operations the rest of the API guarantees.
    """

    __slots__ = ("_arbitrary", "_bounds")
    # Slot type annotations for static type checkers; ``__slots__``
    # already declares storage.
    _bounds: tuple[_VersionRange, ...]
    #: When non-``None``, the literal string from a ``===`` specifier
    #: that this range matches case-insensitively.  ``_bounds``
    #: continues to apply: a candidate must match the literal *and*
    #: (when it parses as a :class:`Version`) fall inside ``_bounds``.
    _arbitrary: str | None

    def __new__(cls, *args: object, **kwargs: object) -> VersionRange:  # noqa: PYI034
        raise TypeError(
            "cannot create 'VersionRange' instances directly; use "
            "VersionRange.from_specifier(), "
            "VersionRange.from_specifier_set(), "
            "Specifier.to_range(), or SpecifierSet.to_range() instead"
        )

    @classmethod
    def _build(
        cls,
        bounds: tuple[_VersionRange, ...],
        arbitrary: str | None = None,
    ) -> VersionRange:
        """Internal factory bypassing :meth:`__new__`."""
        instance = object.__new__(cls)
        instance._bounds = bounds
        instance._arbitrary = arbitrary
        return instance

    def _reject_arbitrary(self, other: VersionRange | None, op: str) -> None:
        """Raise if either side carries the ``===`` arbitrary-equality flag.

        ``===`` matches a literal string and is not a member of the
        Boolean lattice :class:`VersionRange` otherwise inhabits, so
        intersecting / uniting / complementing it has no
        well-defined semantics and we refuse rather than return a
        misleading result.
        """
        if self._arbitrary is not None or (
            other is not None and other._arbitrary is not None
        ):
            msg = (
                f"{op} is not defined on a VersionRange that came from a "
                f"``===`` specifier; the arbitrary-string match has no "
                f"set-theoretic semantics.  Inspect ``_arbitrary`` and "
                f"handle these ranges separately."
            )
            raise TypeError(msg)

    @classmethod
    def empty(cls) -> VersionRange:
        """Return the empty range — no version satisfies it.

        Useful as the identity element when folding a sequence of
        ranges with :meth:`union`.

        >>> VersionRange.empty().is_empty
        True
        >>> "1.0" in VersionRange.empty()
        False
        """
        return cls._build(())

    @classmethod
    def full(cls) -> VersionRange:
        """Return the full range — every PEP 440 version satisfies it.

        Equivalent to :meth:`from_specifier_set` on an empty
        :class:`SpecifierSet`, but produced without parsing.  Useful as
        the identity element when folding a sequence of ranges with
        :meth:`intersection`.

        Naming follows ``pubgrub-rs``'s ``Ranges::full()`` and uv's
        ``version-ranges`` crate; ``empty`` and ``full`` are the
        canonical set-theoretic identity pair.

        >>> "1.0" in VersionRange.full()
        True
        >>> VersionRange.full().is_empty
        False
        """
        return cls._build(_FULL_RANGE)

    @classmethod
    def singleton(cls, version: Version | str) -> VersionRange:
        """Return the range that contains only *version*.

        *version* may be a :class:`~packaging.version.Version` or a
        string parseable as one.

        Naming follows ``pubgrub-rs``'s ``Ranges::singleton(v)`` and uv;
        ``singleton`` is unambiguous about the set semantics, while
        ``exact`` could be confused with the ``==V`` specifier (which
        differs by also matching ``V+local``).

        >>> r = VersionRange.singleton("1.2.3")
        >>> "1.2.3" in r
        True
        >>> "1.2.4" in r
        False

        :raises packaging.version.InvalidVersion: if *version* is a
            string that does not parse as a PEP 440 version.
        """
        if not isinstance(version, Version):
            version = Version(version)
        lower = _LowerBound(version, True)
        upper = _UpperBound(version, True)
        return cls._build(((lower, upper),))

    def intersection(self, other: VersionRange) -> VersionRange:
        """Range containing exactly the versions in both *self* and *other*.

        Mirrors :meth:`set.intersection` and ``pubgrub-rs``'s
        ``Ranges::intersection``.

        :raises TypeError: when either operand carries a ``===`` literal
            (the ``_arbitrary`` slot is set).  See the class docstring's
            "``===`` carve-out" -- arbitrary-string equality has no
            set-theoretic intersection semantics.

        >>> a = VersionRange.from_specifier_set(SpecifierSet(">=1.0"))
        >>> b = VersionRange.from_specifier_set(SpecifierSet("<2.0"))
        >>> ab = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
        >>> a.intersection(b) == ab
        True
        """
        self._reject_arbitrary(other, "intersection")
        return self._build(tuple(_intersect_ranges(self._bounds, other._bounds)))

    def union(self, other: VersionRange) -> VersionRange:
        """Range containing every version in *self* or *other*.

        Adjacent or overlapping intervals collapse so the result keeps
        the same sorted, non-overlapping invariant the rest of the
        module relies on.

        :raises TypeError: when either operand carries a ``===`` literal.

        >>> a = VersionRange.singleton("1.0")
        >>> b = VersionRange.singleton("2.0")
        >>> "1.0" in a.union(b)
        True
        >>> "2.0" in a.union(b)
        True
        >>> "1.5" in a.union(b)
        False
        """
        self._reject_arbitrary(other, "union")
        return self._build(tuple(_union_ranges(self._bounds, other._bounds)))

    def complement(self) -> VersionRange:
        """Range containing every version *not* in *self*.

        Inverts a range so that ``r.complement().complement() == r``.
        The complement of the full range is empty, and vice versa.

        :raises TypeError: when this range carries a ``===`` literal.

        >>> r = VersionRange.from_specifier(Specifier(">=1.0"))
        >>> "0.5" in r.complement()
        True
        >>> "1.5" in r.complement()
        False
        >>> r.complement().complement() == r
        True
        """
        self._reject_arbitrary(None, "complement")
        return self._build(tuple(_complement_ranges(self._bounds)))

    def __and__(self, other: object) -> VersionRange:
        """Operator alias for :meth:`intersection`.

        >>> a = VersionRange.from_specifier(Specifier(">=1.0"))
        >>> b = VersionRange.from_specifier(Specifier("<2.0"))
        >>> "1.5" in (a & b)
        True
        """
        if not isinstance(other, VersionRange):
            return NotImplemented
        return self.intersection(other)

    def __or__(self, other: object) -> VersionRange:
        """Operator alias for :meth:`union`.

        >>> a = VersionRange.singleton("1.0")
        >>> b = VersionRange.singleton("2.0")
        >>> "1.0" in (a | b) and "2.0" in (a | b)
        True
        """
        if not isinstance(other, VersionRange):
            return NotImplemented
        return self.union(other)

    def __invert__(self) -> VersionRange:
        """Operator alias for :meth:`complement`.

        >>> r = VersionRange.from_specifier(Specifier(">=1.0"))
        >>> "0.5" in ~r
        True
        """
        return self.complement()

    def filter(
        self,
        iterable: Iterable[Any],
        key: Callable[[Any], Version | str] | None = None,
        prereleases: bool | None = None,
    ) -> Iterator[Any]:
        """Yield items from *iterable* whose version falls inside the range.

        When *prereleases* is ``None`` the PEP 440 default applies:
        pre-releases are buffered and only emitted if no final release
        in *iterable* is in range.

        Two carve-outs admit items that don't parse as PEP 440
        versions, mirroring :class:`SpecifierSet`: the full range
        admits any string (matches ``SpecifierSet("")``), and a ``===``
        range admits items whose string equals the literal
        case-insensitively (and falls in the rangelike bounds when
        present).

        >>> r = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
        >>> list(r.filter(["0.9", "1.5", "2.0"]))
        ['1.5']
        >>> arb = VersionRange.from_specifier(Specifier("===wat"))
        >>> list(arb.filter(["wat", "WAT", "other"]))
        ['wat', 'WAT']
        >>> list(VersionRange.full().filter(["1.0", "not-a-version"]))
        ['1.0', 'not-a-version']
        """
        if self._arbitrary is not None:
            return self._filter_with_admission(iterable, key, prereleases)
        if self._bounds == _FULL_RANGE:
            # Full-range carve-out: admit any item, parseable or not,
            # so behaviour matches ``SpecifierSet("").filter``.
            return self._filter_with_admission(iterable, key, prereleases)
        return _filter_by_ranges(self._bounds, iterable, key, prereleases)

    def _filter_with_admission(
        self,
        iterable: Iterable[Any],
        key: Callable[[Any], Version | str] | None,
        prereleases: bool | None,
    ) -> Iterator[Any]:
        """Filter for ranges that admit unparseable strings.

        Drives the ``===`` carve-out and the full-range carve-out.
        Both share the same PEP 440 buffering shape as
        :func:`packaging.specifiers._pep440_filter_prereleases`: in
        ``prereleases is None`` mode unparseable strings buffer
        alongside pre-releases until a final candidate appears.

        The carve-outs differ only in their admission predicate:
        ``===`` requires a case-insensitive match against the literal
        (plus an optional rangelike-bounds check); the full range
        admits everything.
        """
        if self._arbitrary is None:
            full_bounds = True
            spec_lower: str | None = None
        else:
            if not self._bounds:
                return
            full_bounds = self._bounds == _FULL_RANGE
            spec_lower = self._arbitrary.lower()

        def admit(item: object) -> tuple[bool, Version | None]:
            raw = item if key is None else key(item)
            if spec_lower is not None and str(raw).lower() != spec_lower:
                return False, None
            parsed = _coerce_version(raw)
            if (
                parsed is not None
                and not full_bounds
                and not self._matches_bounds(parsed)
            ):
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

        # PEP 440 default: yield finals immediately; buffer the rest
        # until we know whether any final exists.  Mirrors
        # :func:`packaging.specifiers._pep440_filter_prereleases`.
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

    @property
    def is_prerelease_only(self) -> bool:
        """``True`` if every interval contains only pre-release versions.

        With ``prereleases=False`` this is equivalent to the range
        being unsatisfiable: every contained version is excluded.

        >>> r = VersionRange.from_specifier_set(SpecifierSet(">=1.0a1,<1.0"))
        >>> r.is_prerelease_only
        True
        """
        return _ranges_are_prerelease_only(self._bounds)

    @classmethod
    def from_specifier(cls, specifier: Specifier) -> VersionRange:
        """Return the :class:`VersionRange` accepted by *specifier*.

        For the ``===`` arbitrary-equality operator, returns a
        :class:`VersionRange` carrying the literal in its
        :attr:`_arbitrary` slot.  Such ranges support
        :meth:`__contains__`, :meth:`filter`, and the
        ``to_specifier_set`` round-trip but raise on
        :meth:`intersection` / :meth:`union` / :meth:`complement` --
        see the class docstring's "``===`` carve-out" section.

        Non-``===`` results are cached on the *specifier* instance,
        so repeated calls are O(1).

        >>> isinstance(VersionRange.from_specifier(Specifier(">=1.0")), VersionRange)
        True
        >>> r = VersionRange.from_specifier(Specifier("===wat"))
        >>> r._arbitrary
        'wat'
        >>> "wat" in r
        True
        >>> "WAT" in r
        True
        >>> "other" in r
        False
        """
        cached = specifier._range_cache
        if cached is not None:
            return cached

        op = specifier.operator
        if op == "===":
            # Arbitrary-string equality: the range matches the literal
            # ``specifier.version`` case-insensitively.  Bounds are
            # left at the full range so a parseable candidate that
            # string-matches always passes (the bounds check on
            # ``__contains__`` is vacuously true on the full range).
            result = cls._build(_FULL_RANGE, arbitrary=specifier.version)
            specifier._range_cache = result
            return result

        ver_str = specifier.version
        result: VersionRange
        if ver_str.endswith(".*"):
            base = specifier._require_spec_version(ver_str[:-2])
            result = cls._build(tuple(_wildcard_ranges(op, base)))
        else:
            v = specifier._require_spec_version(ver_str)
            has_local = "+" in ver_str
            result = cls._build(tuple(_standard_ranges(op, v, has_local)))

        specifier._range_cache = result
        return result

    @classmethod
    def from_specifier_set(cls, specifier_set: SpecifierSet) -> VersionRange:
        """Return the :class:`VersionRange` accepted by *specifier_set*.

        The result is the intersection of every specifier in the set.
        An empty :class:`SpecifierSet` yields the unbounded range; an
        unsatisfiable set yields an empty :class:`VersionRange` (where
        ``bool(r) is False``).

        Sets containing ``===`` produce a range with the literal in
        :attr:`_arbitrary`; the bounds reflect the rangelike specs in
        the set.  Multiple ``===`` with different literals (or a
        literal that does not satisfy the rangelike intersection)
        yield the empty range.  Such ranges break PubGrub
        invariants -- see the class docstring's "``===`` carve-out".

        Results are cached on the *specifier_set* instance, so
        repeated calls are O(1).

        >>> isinstance(
        ...     VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0")),
        ...     VersionRange,
        ... )
        True
        >>> VersionRange.from_specifier_set(SpecifierSet(">=2.0,<1.0")).is_empty
        True
        >>> r = VersionRange.from_specifier_set(SpecifierSet("===wat"))
        >>> r._arbitrary
        'wat'
        >>> "wat" in r
        True
        """
        cached = specifier_set._range_cache
        if cached is not None:
            return cached

        # Collect ``===`` literals separately from rangelike specs;
        # the rangelike intersection still drives the bounds, the
        # arbitrary literal layers a string-match check on top.
        arbitrary_specs = [s for s in specifier_set._specs if s.operator == "==="]
        rangelike_specs = [s for s in specifier_set._specs if s.operator != "==="]

        if not rangelike_specs:
            rangelike_result: VersionRange = cls._build(_FULL_RANGE)
        else:
            tmp: VersionRange | None = None
            for s in rangelike_specs:
                sub = cls.from_specifier(s)
                if tmp is None:
                    tmp = sub
                else:
                    tmp = tmp.intersection(sub)
                    if tmp.is_empty:
                        break  # empty intersection — already unsatisfiable.
            assert tmp is not None
            rangelike_result = tmp

        if not arbitrary_specs:
            specifier_set._range_cache = rangelike_result
            return rangelike_result

        # Multiple ``===`` literals must all match (case-insensitively)
        # the same string; otherwise no candidate can satisfy them all.
        first_literal = arbitrary_specs[0].version
        if any(s.version.lower() != first_literal.lower() for s in arbitrary_specs[1:]):
            result = cls._build((), arbitrary=first_literal)
        else:
            # If the literal parses as a Version, it must satisfy the
            # rangelike intersection; otherwise no candidate matches.
            parsed_literal: Version | None
            try:
                parsed_literal = Version(first_literal)
            except InvalidVersion:
                parsed_literal = None

            if parsed_literal is None:
                # Unparseable literal: rangelike must be the full range
                # for the literal to satisfy it (any non-trivial bound
                # rejects unparseable strings).
                if rangelike_result._bounds == _FULL_RANGE:
                    result = cls._build(_FULL_RANGE, arbitrary=first_literal)
                else:
                    result = cls._build((), arbitrary=first_literal)
            elif parsed_literal in rangelike_result:
                result = cls._build(rangelike_result._bounds, arbitrary=first_literal)
            else:
                result = cls._build((), arbitrary=first_literal)

        specifier_set._range_cache = result
        return result

    def to_specifier_set(self) -> SpecifierSet | None:
        """Return a single :class:`~packaging.specifiers.SpecifierSet` ``S``
        such that :meth:`from_specifier_set` on ``S`` yields *self*, or
        ``None`` if no such ``S`` exists.

        :class:`SpecifierSet` is **not** closed under :meth:`union` or
        :meth:`complement`: PEP 440 has no specifier for, e.g., the
        strict singleton ``{V}`` (``==V`` also matches ``V+local``) or
        the inclusive ``AFTER_POSTS(V)`` upper bound that arises from
        complementing ``>V``.  The conversion therefore returns
        ``None`` whenever the range has a bound shape no specifier can
        express or an interval gap that is not a complete ``==V.*`` or
        ``==V`` family.

        The empty range maps to ``SpecifierSet("<0")``: ``<0`` parses
        with upper bound ``0.dev0`` exclusive, and ``0.dev0`` is the
        smallest possible PEP 440 version, so the resulting range
        contains nothing.  The full range maps to ``SpecifierSet("")``.

        Use :meth:`to_specifier_sets` when a union of specifier sets
        is acceptable.

        >>> r = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
        >>> str(r.to_specifier_set())
        '<2.0,>=1.0'
        >>> VersionRange.full().to_specifier_set() == SpecifierSet("")
        True
        >>> VersionRange.empty().to_specifier_set() == SpecifierSet("<0")
        True
        >>> # The strict singleton {V} is not specifier-expressible
        >>> # because ``==V`` matches V+local too.
        >>> VersionRange.singleton("1.5").to_specifier_set() is None
        True
        >>> # Complement of ``>V`` produces an inclusive AFTER_POSTS
        >>> # upper bound that no specifier captures.
        >>> gt1 = VersionRange.from_specifier(Specifier(">1.0"))
        >>> gt1.complement().to_specifier_set() is None
        True
        """
        # Avoid an import cycle at module load: SpecifierSet is the
        # parent of every specifier we need to construct, but it lives
        # in :mod:`packaging.specifiers` which already imports from
        # :mod:`packaging.ranges`.
        from .specifiers import SpecifierSet  # noqa: PLC0415

        if self._arbitrary is not None:
            return self._arbitrary_to_specifier_set()
        if self.is_empty:
            # ``<0`` parses to upper = 0.dev0 (excl); 0.dev0 is the
            # smallest possible PEP 440 version, so the range contains
            # no version.  This is the canonical "empty" SpecifierSet.
            return SpecifierSet("<0")
        # Full range round-trips through the empty SpecifierSet.
        if self._bounds == _FULL_RANGE:
            return SpecifierSet("")

        # Walk left-to-right, merging adjacent intervals whose gap is a
        # ``!=V`` or ``!=V.*`` exclusion.  The merged outer bounds plus
        # the chain of ``!=`` fragments form a single SpecifierSet.
        bounds = list(self._bounds)
        outer_lower = bounds[0][0]
        outer_upper = bounds[0][1]
        exclusions: list[str] = []
        for next_lower, next_upper in bounds[1:]:
            ne_v = _detect_ne_v(outer_upper, next_lower)
            ne_v_star = _detect_ne_v_star(outer_upper, next_lower)
            if ne_v is not None:
                exclusions.append(f"!={ne_v}")
            elif ne_v_star is not None:
                exclusions.append(f"!={ne_v_star}.*")
            else:
                return None
            outer_upper = next_upper

        outer_parts = _encode_interval(outer_lower, outer_upper)
        if outer_parts is None:
            return None
        return SpecifierSet(",".join(outer_parts + exclusions))

    def to_specifier_sets(self) -> tuple[SpecifierSet, ...] | None:
        """Return a tuple ``T`` of :class:`SpecifierSet` such that the
        union of ``from_specifier_set(s) for s in T`` equals *self*, or
        ``None`` if no such tuple exists.

        Strictly more permissive than :meth:`to_specifier_set`: when
        the whole range fits in a single SpecifierSet (including the
        ``!=V`` and ``!=V.*`` multi-interval patterns) the result is a
        one-tuple of that SpecifierSet; otherwise each contiguous
        interval is encoded separately.  ``None`` is returned only
        when neither encoding works -- typically when an interval has
        a bound shape that no PEP 440 specifier can express (e.g., the
        ``[V, V]`` shape :meth:`singleton` produces for a
        local-less version, since ``==V`` also matches ``V+local``).

        Empty range produces ``(SpecifierSet("<0"),)``: ``<0`` parses
        with upper bound ``0.dev0`` exclusive, and ``0.dev0`` is the
        smallest possible PEP 440 version, so the range contains
        nothing.  Full range produces ``(SpecifierSet(""),)``.

        >>> r = (
        ...     VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
        ...     | VersionRange.from_specifier_set(SpecifierSet(">=3.0,<4.0"))
        ... )
        >>> [str(s) for s in r.to_specifier_sets()]
        ['<2.0,>=1.0', '<4.0,>=3.0']
        >>> VersionRange.empty().to_specifier_sets() == (SpecifierSet("<0"),)
        True
        >>> VersionRange.full().to_specifier_sets() == (SpecifierSet(""),)
        True
        >>> # ``!=V`` is a multi-interval range whose single-set form
        >>> # exists; the tuple has one element.
        >>> ne = VersionRange.from_specifier(Specifier("!=1.0"))
        >>> ne.to_specifier_sets() == (SpecifierSet("!=1.0"),)
        True
        >>> # Singleton has no specifier representation in either form.
        >>> VersionRange.singleton("1.5").to_specifier_sets() is None
        True
        """
        from .specifiers import SpecifierSet  # noqa: PLC0415

        if self._arbitrary is not None:
            single = self._arbitrary_to_specifier_set()
            return None if single is None else (single,)
        if self.is_empty:
            return (SpecifierSet("<0"),)
        if self._bounds == _FULL_RANGE:
            return (SpecifierSet(""),)

        # Prefer the single-set form when it exists; that catches
        # multi-interval ``!=V`` / ``!=V.*`` patterns that the
        # per-interval encoder rejects (the "(-inf, V)" half of ``!=V``
        # has no specifier in isolation).
        single = self.to_specifier_set()
        if single is not None:
            return (single,)

        out: list[SpecifierSet] = []
        for lower, upper in self._bounds:
            parts = _encode_interval(lower, upper)
            if parts is None:
                return None
            out.append(SpecifierSet(",".join(parts)))
        return tuple(out)

    def _arbitrary_to_specifier_set(self) -> SpecifierSet | None:
        """Round-trip a ``===`` carve-out range to a single SpecifierSet.

        Returned set always begins with ``===<literal>``.  The empty
        carve-out range emits ``===<literal>,<0`` so the round-trip
        preserves both the literal and the unsatisfiability.
        """
        from .specifiers import SpecifierSet  # noqa: PLC0415

        assert self._arbitrary is not None

        if not self._bounds:
            return SpecifierSet(f"==={self._arbitrary},<0")
        if self._bounds == _FULL_RANGE:
            return SpecifierSet(f"==={self._arbitrary}")

        # Encode the rangelike bounds as a SpecifierSet, then prepend
        # the ``===`` literal.  Non-encodable bounds collapse to ``None``.
        rangelike = self._build(self._bounds).to_specifier_set()
        if rangelike is None:
            return None
        return SpecifierSet(f"==={self._arbitrary},{rangelike!s}")

    def __reduce__(self) -> tuple[object, ...]:
        # Pickle support: serialize to a primitive, version-stable
        # form.  See :data:`_PackedBound` for the layout.  Reconstruction
        # uses :func:`_restore_version_range`, which bypasses the
        # :meth:`__new__` guard.
        return (
            _restore_version_range,
            (
                tuple(
                    (_pack_bound(lower), _pack_bound(upper))
                    for lower, upper in self._bounds
                ),
                self._arbitrary,
            ),
        )

    @property
    def is_empty(self) -> bool:
        """``True`` if no version satisfies this range.

        Corresponds to an unsatisfiable specifier such as ``>=2,<1``.

        >>> VersionRange.from_specifier_set(SpecifierSet(">=2,<1")).is_empty
        True
        >>> VersionRange.from_specifier_set(SpecifierSet(">=1,<2")).is_empty
        False
        """
        return not self._bounds

    def is_unsatisfiable(self, *, prereleases: bool | None = None) -> bool:
        """Whether no version can satisfy this range.

        *prereleases* mirrors the flag used by :class:`SpecifierSet`:
        when ``False``, a range that contains only pre-release versions
        is treated as unsatisfiable because every contained version
        would be excluded.  ``None`` (the default) and ``True`` mean
        pre-releases are allowed, so only literal emptiness counts.

        For ``===`` carve-out ranges, the only candidate is the literal
        string.  ``prereleases=False`` makes the range unsatisfiable
        when the literal parses as a pre-release :class:`Version`.

        >>> r = VersionRange.from_specifier_set(SpecifierSet(">=2,<1"))
        >>> r.is_unsatisfiable()
        True
        >>> r = VersionRange.from_specifier_set(SpecifierSet(">=1.0a1,<1.0b1"))
        >>> r.is_unsatisfiable()
        False
        >>> r.is_unsatisfiable(prereleases=False)
        True
        >>> arb = VersionRange.from_specifier(Specifier("===1.0a1"))
        >>> arb.is_unsatisfiable(prereleases=False)
        True
        >>> arb.is_unsatisfiable()
        False
        """
        if self.is_empty:
            return True
        if self._arbitrary is not None:
            if prereleases is False:
                parsed = _coerce_version(self._arbitrary)
                if parsed is not None and parsed.is_prerelease:
                    return True
            return False
        return prereleases is False and self.is_prerelease_only

    def __bool__(self) -> bool:
        """``False`` when the range is empty, ``True`` otherwise.

        >>> bool(VersionRange.from_specifier_set(SpecifierSet(">=1,<2")))
        True
        >>> bool(VersionRange.from_specifier_set(SpecifierSet(">=2,<1")))
        False
        """
        return bool(self._bounds)

    def __contains__(self, item: Version | str) -> bool:
        """Return whether *item* is contained in this range.

        *item* may be a :class:`~packaging.version.Version` or a string
        parseable as one.  Strings that do not parse as PEP 440
        versions are normally not contained, with two carve-outs that
        admit arbitrary strings to match :class:`SpecifierSet` semantics:
        the full range admits any string (mirrors ``SpecifierSet("")``),
        and a ``===`` range admits items whose string equals the literal
        case-insensitively (the ``===`` carve-out -- see the class
        docstring).

        >>> r = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
        >>> "1.5" in r
        True
        >>> "2.0" in r
        False
        >>> "not-a-version" in r
        False
        >>> # ``===`` carve-out: literal-string match.
        >>> arb = VersionRange.from_specifier(Specifier("===wat"))
        >>> "wat" in arb
        True
        >>> "WAT" in arb
        True
        >>> # Full range admits any string -- matches ``SpecifierSet("")``.
        >>> "not-a-version" in VersionRange.full()
        True
        """
        if self._arbitrary is not None:
            # ``===`` carve-out: literal-string match (case-insensitive).
            # Layered on top of the rangelike bounds: a candidate must
            # also pass the bounds check when it parses as a Version.
            item_str = str(item)
            if item_str.lower() != self._arbitrary.lower():
                return False
            if not self._bounds:
                return False
            if self._bounds == _FULL_RANGE:
                return True
            if isinstance(item, Version):
                parsed: Version | None = item
            else:
                try:
                    parsed = Version(item_str)
                except InvalidVersion:
                    return False
            return self._matches_bounds(parsed)
        if self._bounds == _FULL_RANGE:
            # Full range carve-out: admit arbitrary strings so the
            # parsed-or-not distinction matches ``SpecifierSet("")``.
            return True
        # Inline the membership check (rather than delegating to a
        # helper) so the hot path on ``Specifier.contains`` /
        # ``SpecifierSet.contains`` avoids one Python function call.
        if not isinstance(item, Version):
            try:
                item = Version(item)
            except InvalidVersion:
                return False
        return self._matches_bounds(item)

    def _matches_bounds(self, item: Version) -> bool:
        """Helper: pure-bounds membership check (no ``===`` layering)."""
        bounds = self._bounds
        if not bounds:
            return False
        if len(bounds) == 1:
            lower, upper = bounds[0]
            above = lower._above
            if above is not None and not above(item):
                return False
            below = upper._below
            return below is None or below(item)
        for lower, upper in bounds:
            above = lower._above
            if above is not None and not above(item):
                return False
            below = upper._below
            if below is None or below(item):
                return True
        return False

    def __eq__(self, other: object) -> bool:
        """Two ranges are equal when they cover the same set of versions.

        Equality is structural over the internal bounds representation,
        so ranges produced by different specifier strings that describe
        the same set compare equal.  ``===`` carve-out: the
        ``_arbitrary`` literal is compared case-insensitively (matches
        PEP 440's ``===`` semantics).

        >>> VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0")) == (
        ...     VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
        ... )
        True
        >>> VersionRange.from_specifier(Specifier(">=1.0")) == (
        ...     VersionRange.from_specifier(Specifier(">=2.0"))
        ... )
        False
        """
        if not isinstance(other, VersionRange):
            return NotImplemented
        if self._bounds != other._bounds:
            return False
        if self._arbitrary is None and other._arbitrary is None:
            return True
        if self._arbitrary is None or other._arbitrary is None:
            return False
        return self._arbitrary.lower() == other._arbitrary.lower()

    def __hash__(self) -> int:
        if self._arbitrary is None:
            return hash(self._bounds)
        return hash((self._bounds, self._arbitrary.lower()))

    def __repr__(self) -> str:
        """Human-readable representation.

        The internal bound layout is implementation detail; this output
        is for debugging only and is not part of the stable API.  In
        particular, exclusive upper bounds use the internal ``.dev0``
        cap that PEP 440's ``<V`` semantics implies.

        >>> VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
        <VersionRange '[1.0, 2.0.dev0)'>
        >>> VersionRange.from_specifier_set(SpecifierSet(""))
        <VersionRange '(-inf, +inf)'>
        >>> VersionRange.from_specifier_set(SpecifierSet(">=2.0,<1.0"))
        <VersionRange '(empty)'>
        >>> VersionRange.from_specifier(Specifier("===wat"))
        <VersionRange '===wat & (-inf, +inf)'>
        """
        if not self._bounds:
            body = "(empty)"
        else:
            body = " | ".join(
                f"{_format_lower(lower)}, {_format_upper(upper)}"
                for lower, upper in self._bounds
            )
        if self._arbitrary is not None:
            body = f"==={self._arbitrary} & {body}"
        return f"<{self.__class__.__name__} {body!r}>"
