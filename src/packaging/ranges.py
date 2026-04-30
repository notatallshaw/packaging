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
            # ``<=v`` / ``==v`` / ``!=v`` (upper side, no local) all
            # produce an AFTER_LOCALS upper bound; this is the only
            # boundary kind that ever appears as an upper bound.
            assert version._kind == _BoundaryKind.AFTER_LOCALS
            self._below = _make_below_after_locals(version.version)
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
) -> VersionRange:
    """Module-level pickle restorer for :class:`VersionRange`.

    Reconstructs the :class:`VersionRange` from the primitive form
    produced by :meth:`VersionRange.__reduce__` and bypasses the
    :meth:`__new__` guard via :meth:`VersionRange._build`.
    """
    bounds = tuple(
        (
            typing.cast("_LowerBound", _unpack_bound(_LowerBound, lo)),
            typing.cast("_UpperBound", _unpack_bound(_UpperBound, hi)),
        )
        for lo, hi in packed_bounds
    )
    return VersionRange._build(bounds)


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
    """

    __slots__ = ("_bounds",)
    # Slot type annotation for static type checkers; ``__slots__``
    # already declares storage.
    _bounds: tuple[_VersionRange, ...]

    def __new__(cls, *args: object, **kwargs: object) -> VersionRange:  # noqa: PYI034
        raise TypeError(
            "cannot create 'VersionRange' instances directly; use "
            "VersionRange.from_specifier(), "
            "VersionRange.from_specifier_set(), "
            "Specifier.to_range(), or SpecifierSet.to_range() instead"
        )

    @classmethod
    def _build(cls, bounds: tuple[_VersionRange, ...]) -> VersionRange:
        """Internal factory bypassing :meth:`__new__`."""
        instance = object.__new__(cls)
        instance._bounds = bounds
        return instance

    def intersect(self, other: VersionRange) -> VersionRange:
        """Range containing exactly the versions in both *self* and *other*.

        >>> a = VersionRange.from_specifier_set(SpecifierSet(">=1.0"))
        >>> b = VersionRange.from_specifier_set(SpecifierSet("<2.0"))
        >>> ab = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
        >>> a.intersect(b) == ab
        True
        """
        return self._build(tuple(_intersect_ranges(self._bounds, other._bounds)))

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

        >>> r = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
        >>> list(r.filter(["0.9", "1.5", "2.0"]))
        ['1.5']
        """
        return _filter_by_ranges(self._bounds, iterable, key, prereleases)

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
    def from_specifier(cls, specifier: Specifier) -> VersionRange | None:
        """Return the :class:`VersionRange` accepted by *specifier*.

        Returns ``None`` for the ``===`` operator, which performs
        arbitrary-string equality and is not expressible as a version
        range.

        Non-``===`` results are cached on the *specifier* instance,
        so repeated calls are O(1).  ``===`` returns ``None`` after a
        single operator check, which is also effectively O(1).

        >>> isinstance(VersionRange.from_specifier(Specifier(">=1.0")), VersionRange)
        True
        >>> VersionRange.from_specifier(Specifier("===wat")) is None
        True
        """
        cached = specifier._range_cache
        if cached is not None:
            return cached

        op = specifier.operator
        if op == "===":
            return None  # ``===`` has no range; nothing to cache.

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
    def from_specifier_set(cls, specifier_set: SpecifierSet) -> VersionRange | None:
        """Return the :class:`VersionRange` accepted by *specifier_set*.

        The result is the intersection of every specifier in the set.
        An empty :class:`SpecifierSet` yields the unbounded range; an
        unsatisfiable set yields an empty :class:`VersionRange` (where
        ``bool(r) is False``).

        Returns ``None`` when any specifier uses ``===``.

        Non-``===`` results are cached on the *specifier_set* instance,
        so repeated calls are O(1).  Sets containing ``===`` return
        ``None`` after a single flag check, which is also effectively
        O(1).

        >>> isinstance(
        ...     VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0")),
        ...     VersionRange,
        ... )
        True
        >>> VersionRange.from_specifier_set(SpecifierSet(">=2.0,<1.0")).is_empty
        True
        >>> VersionRange.from_specifier_set(SpecifierSet("===wat")) is None
        True
        """
        cached = specifier_set._range_cache
        if cached is not None:
            return cached
        if specifier_set._has_arbitrary:
            return None  # ``===`` has no range; nothing to cache.
        if not specifier_set._specs:
            result = cls._build(_FULL_RANGE)
        else:
            result = None
            for s in specifier_set._specs:
                sub = cls.from_specifier(s)
                # Not ``has_arbitrary``, so ``from_specifier`` is never
                # ``None`` here.
                assert sub is not None
                if result is None:
                    result = sub
                else:
                    result = result.intersect(sub)
                    if result.is_empty:
                        break  # empty intersection — already unsatisfiable.
            assert result is not None  # ``_specs`` is non-empty above.
        specifier_set._range_cache = result
        return result

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

        >>> r = VersionRange.from_specifier_set(SpecifierSet(">=2,<1"))
        >>> r.is_unsatisfiable()
        True
        >>> r = VersionRange.from_specifier_set(SpecifierSet(">=1.0a1,<1.0"))
        >>> r.is_unsatisfiable()
        False
        >>> r.is_unsatisfiable(prereleases=False)
        True
        """
        return self.is_empty or (prereleases is False and self.is_prerelease_only)

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
        versions are not contained.

        >>> r = VersionRange.from_specifier_set(SpecifierSet(">=1.0,<2.0"))
        >>> "1.5" in r
        True
        >>> "2.0" in r
        False
        >>> "not-a-version" in r
        False
        """
        # Inline the membership check (rather than delegating to a
        # helper) so the hot path on ``Specifier.contains`` /
        # ``SpecifierSet.contains`` avoids one Python function call.
        if not isinstance(item, Version):
            try:
                item = Version(item)
            except InvalidVersion:
                return False
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
        the same set compare equal.

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
        return self._bounds == other._bounds

    def __hash__(self) -> int:
        return hash(self._bounds)

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
        """
        if not self._bounds:
            body = "(empty)"
        else:
            body = " | ".join(
                f"{_format_lower(lower)}, {_format_upper(upper)}"
                for lower, upper in self._bounds
            )
        return f"<{self.__class__.__name__} {body!r}>"
