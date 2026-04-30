# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the BSD License. See the LICENSE file in the root of this repository
# for complete details.
"""
.. testsetup::

    from packaging.specifiers import (
        InvalidSpecifier,
        Specifier,
        SpecifierSet,
        VersionRange,
    )
    from packaging.version import Version
"""

from __future__ import annotations

import abc
import re
import sys
import typing
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Final,
    Iterable,
    Iterator,
    TypeVar,
    Union,
)

from .ranges import VersionRange
from .utils import canonicalize_version
from .version import InvalidVersion, Version

if sys.version_info >= (3, 10):
    from typing import TypeGuard  # pragma: no cover
elif TYPE_CHECKING:
    from typing_extensions import TypeGuard


__all__ = [
    "BaseSpecifier",
    "InvalidSpecifier",
    "Specifier",
    "SpecifierSet",
    "VersionRange",
]


def __dir__() -> list[str]:
    return __all__


# ---------------------------------------------------------------------------
# Module sentinels and primitive type aliases
# ---------------------------------------------------------------------------

#: Sentinel for "lazily-cached prereleases auto-detect not yet computed".
#: Distinguishes "never computed" from any legitimate value
#: (``True`` / ``False`` / ``None``).
_PRE_UNSET: Final[Any] = object()

T = TypeVar("T")
UnparsedVersion = Union[Version, str]
UnparsedVersionVar = TypeVar("UnparsedVersionVar", bound=UnparsedVersion)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_version(version: Version | str) -> Version | None:
    """Parse *version* into a :class:`Version`, or return ``None`` on failure.

    Duplicated from :mod:`packaging.ranges` (where the same six lines
    are needed by :class:`VersionRange`) to avoid a private cross-module
    import for what is otherwise trivial logic.
    """
    if not isinstance(version, Version):
        try:
            version = Version(version)
        except InvalidVersion:
            return None
    return version


def _validate_spec(spec: object, /) -> TypeGuard[tuple[str, str]]:
    return (
        isinstance(spec, tuple)
        and len(spec) == 2
        and isinstance(spec[0], str)
        and isinstance(spec[1], str)
    )


def _validate_pre(pre: object, /) -> TypeGuard[bool | None]:
    return pre is None or isinstance(pre, bool)


def _pep440_filter_prereleases(
    iterable: Iterable[Any], key: Callable[[Any], UnparsedVersion] | None
) -> Iterator[Any]:
    """Filter per PEP 440: exclude pre-releases unless no finals exist.

    Used only on paths where items may include unparsable strings —
    the ``===`` arbitrary path and the empty-:class:`SpecifierSet`
    path.  The range path handles PEP 440 semantics inline in
    :func:`packaging.ranges._filter_by_ranges`.
    """
    all_nonfinal: list[Any] = []
    arbitrary_strings: list[Any] = []

    found_final = False
    for item in iterable:
        parsed = _coerce_version(item if key is None else key(item))

        if parsed is None:
            # Arbitrary strings already passed all specifiers; whether
            # they are pre-releases is unknowable, so treat them
            # alongside pre-releases for buffering.
            if found_final:
                yield item
            else:
                arbitrary_strings.append(item)
                all_nonfinal.append(item)
            continue

        if not parsed.is_prerelease:
            if not found_final:
                yield from arbitrary_strings
                found_final = True
            yield item
            continue

        if not found_final:
            all_nonfinal.append(item)

    if not found_final:
        yield from all_nonfinal


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class InvalidSpecifier(ValueError):
    """
    Raised when attempting to create a :class:`Specifier` with a specifier
    string that is invalid.

    >>> Specifier("lolwat")
    Traceback (most recent call last):
        ...
    packaging.specifiers.InvalidSpecifier: Invalid specifier: 'lolwat'
    """


class BaseSpecifier(metaclass=abc.ABCMeta):
    __slots__ = ()
    __match_args__ = ("_str",)

    @property
    def _str(self) -> str:
        """Internal property for match_args"""
        return str(self)

    @abc.abstractmethod
    def __str__(self) -> str:
        """
        Returns the str representation of this Specifier-like object. This
        should be representative of the Specifier itself.
        """

    @abc.abstractmethod
    def __hash__(self) -> int:
        """
        Returns a hash value for this Specifier-like object.
        """

    @abc.abstractmethod
    def __eq__(self, other: object) -> bool:
        """
        Returns a boolean representing whether or not the two Specifier-like
        objects are equal.

        :param other: The other object to check against.
        """

    @property
    @abc.abstractmethod
    def prereleases(self) -> bool | None:
        """Whether or not pre-releases as a whole are allowed.

        This can be set to either ``True`` or ``False`` to explicitly enable or disable
        prereleases or it can be set to ``None`` (the default) to use default semantics.
        """

    @prereleases.setter  # noqa: B027
    def prereleases(self, value: bool) -> None:
        """Setter for :attr:`prereleases`.

        :param value: The value to set.
        """

    @abc.abstractmethod
    def contains(self, item: str, prereleases: bool | None = None) -> bool:
        """
        Determines if the given item is contained within this specifier.
        """

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

    @abc.abstractmethod
    def filter(
        self,
        iterable: Iterable[Any],
        prereleases: bool | None = None,
        key: Callable[[Any], UnparsedVersion] | None = None,
    ) -> Iterator[Any]:
        """
        Takes an iterable of items and filters them so that only items which
        are contained within this specifier are allowed in it.
        """


class Specifier(BaseSpecifier):
    """This class abstracts handling of version specifiers.

    .. tip::

        It is generally not required to instantiate this manually. You should instead
        prefer to work with :class:`SpecifierSet` instead, which can parse
        comma-separated version specifiers (which is what package metadata contains).

    Instances are safe to serialize with :mod:`pickle`. They use a stable
    format so the same pickle can be loaded in future packaging releases.

    .. versionchanged:: 26.2

        Added a stable pickle format. Pickles created with packaging 26.2+ can
        be unpickled with future releases.  Backward compatibility with pickles
        from packaging < 26.2 is supported but may be removed in a future
        release.
    """

    __slots__ = (
        "_auto_prereleases",
        "_prereleases",
        "_range_cache",
        "_spec",
        "_spec_version",
    )

    _specifier_regex_str = r"""
        (?:
            (?:
                # The identity operators allow for an escape hatch that will
                # do an exact string match of the version you wish to install.
                # This will not be parsed by PEP 440 and we cannot determine
                # any semantic meaning from it. This operator is discouraged
                # but included entirely as an escape hatch.
                ===  # Only match for the identity operator
                \s*
                [^\s;)]*  # The arbitrary version can be just about anything,
                          # we match everything except for whitespace, a
                          # semi-colon for marker support, and a closing paren
                          # since versions can be enclosed in them.
            )
            |
            (?:
                # The (non)equality operators allow for wild card and local
                # versions to be specified so we have to define these two
                # operators separately to enable that.
                (?:==|!=)            # Only match for equals and not equals

                \s*
                v?
                (?:[0-9]+!)?          # epoch
                [0-9]+(?:\.[0-9]+)*   # release

                # You cannot use a wild card and a pre-release, post-release, a dev or
                # local version together so group them with a | and make them optional.
                (?:
                    \.\*  # Wild card syntax of .*
                    |
                    (?a:                                  # pre release
                        [-_\.]?
                        (alpha|beta|preview|pre|a|b|c|rc)
                        [-_\.]?
                        [0-9]*
                    )?
                    (?a:                                  # post release
                        (?:-[0-9]+)|(?:[-_\.]?(post|rev|r)[-_\.]?[0-9]*)
                    )?
                    (?a:[-_\.]?dev[-_\.]?[0-9]*)?         # dev release
                    (?a:\+[a-z0-9]+(?:[-_\.][a-z0-9]+)*)? # local
                )?
            )
            |
            (?:
                # The compatible operator requires at least two digits in the
                # release segment.
                (?:~=)               # Only match for the compatible operator

                \s*
                v?
                (?:[0-9]+!)?          # epoch
                [0-9]+(?:\.[0-9]+)+   # release  (We have a + instead of a *)
                (?:                   # pre release
                    [-_\.]?
                    (alpha|beta|preview|pre|a|b|c|rc)
                    [-_\.]?
                    [0-9]*
                )?
                (?:                                   # post release
                    (?:-[0-9]+)|(?:[-_\.]?(post|rev|r)[-_\.]?[0-9]*)
                )?
                (?:[-_\.]?dev[-_\.]?[0-9]*)?          # dev release
            )
            |
            (?:
                # All other operators only allow a sub set of what the
                # (non)equality operators do. Specifically they do not allow
                # local versions to be specified nor do they allow the prefix
                # matching wild cards.
                (?:<=|>=|<|>)

                \s*
                v?
                (?:[0-9]+!)?          # epoch
                [0-9]+(?:\.[0-9]+)*   # release
                (?a:                   # pre release
                    [-_\.]?
                    (alpha|beta|preview|pre|a|b|c|rc)
                    [-_\.]?
                    [0-9]*
                )?
                (?a:                                   # post release
                    (?:-[0-9]+)|(?:[-_\.]?(post|rev|r)[-_\.]?[0-9]*)
                )?
                (?a:[-_\.]?dev[-_\.]?[0-9]*)?          # dev release
            )
        )
        """

    _regex = re.compile(
        r"\s*" + _specifier_regex_str + r"\s*", re.VERBOSE | re.IGNORECASE
    )

    def __init__(self, spec: str = "", prereleases: bool | None = None) -> None:
        """Initialize a Specifier instance.

        :param spec:
            The string representation of a specifier which will be parsed and
            normalized before use.
        :param prereleases:
            This tells the specifier if it should accept prerelease versions if
            applicable or not. The default of ``None`` will autodetect it from the
            given specifiers.
        :raises InvalidSpecifier:
            If the given specifier is invalid (i.e. bad syntax).

        Construction performs only the regex validation and the operator/
        version split.  Range building, version parsing, and the
        ``prereleases`` auto-detect are all deferred to first use.
        """
        if not self._regex.fullmatch(spec):
            raise InvalidSpecifier(f"Invalid specifier: {spec!r}")

        spec = spec.strip()
        if spec.startswith("==="):
            operator, version = spec[:3], spec[3:].strip()
        elif spec.startswith(("~=", "==", "!=", "<=", ">=")):
            operator, version = spec[:2], spec[2:].strip()
        else:
            operator, version = spec[:1], spec[1:].strip()

        self._spec: tuple[str, str] = (operator, version)
        self._prereleases = prereleases
        # Lazy caches; ``_PRE_UNSET`` distinguishes "never computed"
        # from any legitimate cached value.  ``_range_cache`` uses
        # plain ``None`` for "uncached" -- ``from_specifier`` always
        # produces a real range, even for ``===``.
        self._spec_version: tuple[str, Version] | None = None
        self._range_cache: VersionRange | None = None
        self._auto_prereleases: object = _PRE_UNSET

    def _get_spec_version(self, version: str) -> Version | None:
        """One-element cache for the spec's parsed :class:`Version`."""
        if self._spec_version is not None and self._spec_version[0] == version:
            return self._spec_version[1]
        version_specifier = _coerce_version(version)
        if version_specifier is None:
            return None
        self._spec_version = (version, version_specifier)
        return version_specifier

    def _require_spec_version(self, version: str) -> Version:
        """Get the spec version, asserting it parses (never for ``===``)."""
        spec_version = self._get_spec_version(version)
        assert spec_version is not None
        return spec_version

    @property
    def _range(self) -> VersionRange:
        """The :class:`VersionRange` accepted by this specifier.

        Each standard operator maps to a range with one or two
        intervals.  ``===`` returns a carve-out range whose
        ``_arbitrary`` slot carries the literal -- see the
        :class:`~packaging.ranges.VersionRange` "``===`` carve-out".
        Computed lazily on first access; the result is cached on this
        :class:`Specifier` instance.
        """
        return VersionRange.from_specifier(self)

    @property
    def prereleases(self) -> bool | None:
        # An explicit value short-circuits the auto-detect.
        if self._prereleases is not None:
            return self._prereleases
        cached = self._auto_prereleases
        if cached is not _PRE_UNSET:
            return cached  # type: ignore[return-value]

        # Auto-detect from operator and version string (both immutable
        # for the lifetime of the specifier, so the result is stable
        # and safe to cache).
        operator, version_str = self._spec
        result: bool | None
        if operator == "!=" or (operator == "==" and version_str.endswith(".*")):
            # ``!=`` does not imply pre-releases even when V is a
            # pre-release; ``==X.*`` excludes pre-releases since the
            # wildcard cannot include them.
            result = False
        else:
            # ``===`` versions may be unparsable; treat their
            # pre-release status as unknown.
            version = self._get_spec_version(version_str)
            result = None if version is None else version.is_prerelease

        self._auto_prereleases = result
        return result

    @prereleases.setter
    def prereleases(self, value: bool | None) -> None:
        self._prereleases = value

    def __getstate__(self) -> tuple[tuple[str, str], bool | None]:
        # Compact 2-tuple state: ``((operator, version), prereleases)``.
        # Cache members are excluded and recomputed on demand.
        return (self._spec, self._prereleases)

    def __setstate__(self, state: object) -> None:
        # Always discard cached values; they will be recomputed on demand.
        self._spec_version = None
        self._range_cache = None
        self._auto_prereleases = _PRE_UNSET

        if isinstance(state, tuple):
            if len(state) == 2:
                # 26.2+ format: ``((operator, version), prereleases)``.
                spec, prereleases = state
                if _validate_spec(spec) and _validate_pre(prereleases):
                    self._spec = spec
                    self._prereleases = prereleases
                    return
            if len(state) == 2 and isinstance(state[1], dict):
                # 26.0-26.1 format: ``(None, {slot: value})``.
                _, slot_dict = state
                spec = slot_dict.get("_spec")
                prereleases = slot_dict.get("_prereleases", "invalid")
                if _validate_spec(spec) and _validate_pre(prereleases):
                    self._spec = spec
                    self._prereleases = prereleases
                    return
        if isinstance(state, dict):
            # <= 25.x format: plain ``__dict__``.
            spec = state.get("_spec")
            prereleases = state.get("_prereleases", "invalid")
            if _validate_spec(spec) and _validate_pre(prereleases):
                self._spec = spec
                self._prereleases = prereleases
                return

        raise TypeError(f"Cannot restore Specifier from {state!r}")

    @property
    def operator(self) -> str:
        """The operator of this specifier.

        >>> Specifier("==1.2.3").operator
        '=='
        """
        return self._spec[0]

    @property
    def version(self) -> str:
        """The version of this specifier.

        >>> Specifier("==1.2.3").version
        '1.2.3'
        """
        return self._spec[1]

    def __repr__(self) -> str:
        """A representation of the Specifier that shows all internal state.

        >>> Specifier('>=1.0.0')
        <Specifier('>=1.0.0')>
        >>> Specifier('>=1.0.0', prereleases=False)
        <Specifier('>=1.0.0', prereleases=False)>
        >>> Specifier('>=1.0.0', prereleases=True)
        <Specifier('>=1.0.0', prereleases=True)>
        """
        pre = (
            f", prereleases={self.prereleases!r}"
            if self._prereleases is not None
            else ""
        )

        return f"<{self.__class__.__name__}({str(self)!r}{pre})>"

    def __str__(self) -> str:
        """A string representation of the Specifier that can be round-tripped.

        >>> str(Specifier('>=1.0.0'))
        '>=1.0.0'
        >>> str(Specifier('>=1.0.0', prereleases=False))
        '>=1.0.0'
        """
        return "{}{}".format(*self._spec)

    @property
    def _canonical_spec(self) -> tuple[str, str]:
        operator, version = self._spec
        if operator == "===" or version.endswith(".*"):
            return operator, version

        spec_version = self._require_spec_version(version)

        canonical_version = canonicalize_version(
            spec_version, strip_trailing_zero=(operator != "~=")
        )

        return operator, canonical_version

    def __hash__(self) -> int:
        return hash(self._canonical_spec)

    def __eq__(self, other: object) -> bool:
        """Whether or not the two Specifier-like objects are equal.

        :param other: The other object to check against.

        The value of :attr:`prereleases` is ignored.

        >>> Specifier("==1.2.3") == Specifier("== 1.2.3.0")
        True
        >>> (Specifier("==1.2.3", prereleases=False) ==
        ...  Specifier("==1.2.3", prereleases=True))
        True
        >>> Specifier("==1.2.3") == "==1.2.3"
        True
        >>> Specifier("==1.2.3") == Specifier("==1.2.4")
        False
        >>> Specifier("==1.2.3") == Specifier("~=1.2.3")
        False
        """
        if isinstance(other, str):
            try:
                other = self.__class__(str(other))
            except InvalidSpecifier:
                return NotImplemented
        elif not isinstance(other, self.__class__):
            return NotImplemented

        return self._canonical_spec == other._canonical_spec

    def __contains__(self, item: str | Version) -> bool:
        """Return whether or not the item is contained in this specifier.

        :param item: The item to check for.

        This is used for the ``in`` operator and behaves the same as
        :meth:`contains` with no ``prereleases`` argument passed.

        >>> "1.2.3" in Specifier(">=1.2.3")
        True
        >>> Version("1.2.3") in Specifier(">=1.2.3")
        True
        >>> "1.0.0" in Specifier(">=1.2.3")
        False
        >>> "1.3.0a1" in Specifier(">=1.2.3")
        True
        >>> "1.3.0a1" in Specifier(">=1.2.3", prereleases=True)
        True
        """
        return self.contains(item)

    def contains(self, item: UnparsedVersion, prereleases: bool | None = None) -> bool:
        """Return whether or not the item is contained in this specifier.

        :param item:
            The item to check for, which can be a version string or a
            :class:`Version` instance.
        :param prereleases:
            Whether or not to match prereleases with this Specifier. If set to
            ``None`` (the default), it will follow the recommendation from
            :pep:`440` and match prereleases, as there are no other versions.

        >>> Specifier(">=1.2.3").contains("1.2.3")
        True
        >>> Specifier(">=1.2.3").contains(Version("1.2.3"))
        True
        >>> Specifier(">=1.2.3").contains("1.0.0")
        False
        >>> Specifier(">=1.2.3").contains("1.3.0a1")
        True
        >>> Specifier(">=1.2.3", prereleases=False).contains("1.3.0a1")
        False
        >>> Specifier(">=1.2.3").contains("1.3.0a1")
        True
        """
        if self.operator == "===":
            # ``===`` is arbitrary-string equality, not range membership.
            return bool(list(self.filter([item], prereleases=prereleases)))

        parsed = _coerce_version(item)
        if parsed is None:
            return False

        if prereleases is None:
            prereleases = self._prereleases
        if prereleases is False and parsed.is_prerelease:
            return False

        # Read the cache slot directly here; routing through the
        # :attr:`_range` property would add descriptor-protocol overhead
        # to every call.
        version_range = self._range_cache
        if version_range is None:
            version_range = VersionRange.from_specifier(self)
        return parsed in version_range

    def to_range(self) -> VersionRange:
        """Return the :class:`VersionRange` accepted by this specifier.

        Equivalent to :meth:`VersionRange.from_specifier`; provided
        here for symmetry with :meth:`SpecifierSet.to_range`.  For
        ``===`` the returned range carries the literal in its
        ``_arbitrary`` slot -- see the
        :class:`~packaging.ranges.VersionRange` "``===`` carve-out".

        >>> isinstance(Specifier(">=1.0").to_range(), VersionRange)
        True
        >>> Specifier("===wat").to_range()._arbitrary
        'wat'
        """
        return VersionRange.from_specifier(self)

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
        key: Callable[[Any], UnparsedVersion] | None = None,
    ) -> Iterator[Any]:
        """Filter items in the given iterable, that match the specifier.

        :param iterable:
            An iterable that can contain version strings and :class:`Version` instances.
            The items in the iterable will be filtered according to the specifier.
        :param prereleases:
            Whether or not to allow prereleases in the returned iterator. If set to
            ``None`` (the default), it will follow the recommendation from :pep:`440`
            and match prereleases if there are no other versions.
        :param key:
            A callable that takes a single argument (an item from the iterable) and
            returns a version string or :class:`Version` instance to be used for
            filtering.

        >>> list(Specifier(">=1.2.3").filter(["1.2", "1.3", "1.5a1"]))
        ['1.3']
        >>> list(Specifier(">=1.2.3").filter(["1.2", "1.2.3", "1.3", Version("1.4")]))
        ['1.2.3', '1.3', <Version('1.4')>]
        >>> list(Specifier(">=1.2.3").filter(["1.2", "1.5a1"]))
        ['1.5a1']
        >>> list(Specifier(">=1.2.3").filter(["1.3", "1.5a1"], prereleases=True))
        ['1.3', '1.5a1']
        >>> list(Specifier(">=1.2.3", prereleases=True).filter(["1.3", "1.5a1"]))
        ['1.3', '1.5a1']
        >>> list(Specifier(">=1.2.3").filter(
        ... [{"ver": "1.2"}, {"ver": "1.3"}],
        ... key=lambda x: x["ver"]))
        [{'ver': '1.3'}]
        """
        if self.operator == "===":
            # ``===`` is arbitrary-string equality.  Pre-releases are
            # still respected when the string parses as a version.
            if prereleases is None and self._prereleases is not None:
                prereleases = self._prereleases
            exclude_pre = prereleases is False
            spec_str = self.version
            for item in iterable:
                raw = item if key is None else key(item)
                if str(raw).lower() != spec_str.lower():
                    continue
                if exclude_pre:
                    parsed = _coerce_version(raw)
                    if parsed is not None and parsed.is_prerelease:
                        continue
                yield item
            return

        prereleases = self._resolve_prereleases(prereleases)

        version_range = self._range_cache
        if version_range is None:
            version_range = VersionRange.from_specifier(self)
        yield from version_range.filter(iterable, key, prereleases)

    def _resolve_prereleases(self, prereleases: bool | None) -> bool | None:
        """Compute the effective ``prereleases`` value the range filter
        should see.

        Mirrors the chain :meth:`filter` and :meth:`contains` use
        internally: explicit caller argument wins; otherwise, fall back
        to the explicit constructor value (``self._prereleases``); else
        fall back to the auto-detected ``self.prereleases`` *only when
        it is True* (an auto-detected ``False`` does not propagate, so
        ``filter`` keeps PEP 440 default behaviour for non-pre
        specifiers).  Exposed so callers driving :meth:`VersionRange.filter`
        directly can replicate the behaviour without duplicating the
        logic.
        """
        if prereleases is not None:
            return prereleases
        if self._prereleases is not None:
            return self._prereleases
        if self.prereleases:
            return True
        return None


class SpecifierSet(BaseSpecifier):
    """This class abstracts handling of a set of version specifiers.

    It can be passed a single specifier (``>=3.0``), a comma-separated list of
    specifiers (``>=3.0,!=3.1``), or no specifier at all.

    Instances are safe to serialize with :mod:`pickle`. They use a stable
    format so the same pickle can be loaded in future packaging
    releases.

    .. versionchanged:: 26.2

        Added a stable pickle format. Pickles created with
        packaging 26.2+ can be unpickled with future releases.
        Backward compatibility with pickles from
        packaging < 26.2 is supported but may be removed in a future
        release.
    """

    __slots__ = (
        "_auto_prereleases",
        "_canonicalized",
        "_has_arbitrary",
        "_is_unsatisfiable",
        "_prereleases",
        "_range_cache",
        "_specs",
    )

    def __init__(
        self,
        specifiers: str | Iterable[Specifier] = "",
        prereleases: bool | None = None,
    ) -> None:
        """Initialize a SpecifierSet instance.

        :param specifiers:
            The string representation of a specifier or a comma-separated list of
            specifiers which will be parsed and normalized before use.
            May also be an iterable of ``Specifier`` instances, which will be used
            as is.
        :param prereleases:
            This tells the SpecifierSet if it should accept prerelease versions if
            applicable or not. The default of ``None`` will autodetect it from the
            given specifiers.

        :raises InvalidSpecifier:
            If the given ``specifiers`` are not parseable than this exception will be
            raised.

        Construction performs only the per-specifier validation.  Range
        intersection, the :class:`VersionRange` cache, and the
        ``prereleases`` auto-detect are all deferred to first use.
        """

        if isinstance(specifiers, str):
            split_specifiers = [s.strip() for s in specifiers.split(",") if s.strip()]
            self._specs: tuple[Specifier, ...] = tuple(map(Specifier, split_specifiers))
            # Fast substring check; avoids iterating parsed specs.
            self._has_arbitrary = "===" in specifiers
        else:
            self._specs = tuple(specifiers)
            # Substring check works for both Specifier objects and
            # plain strings (setuptools passes lists of strings).
            self._has_arbitrary = any("===" in str(s) for s in self._specs)

        self._canonicalized = len(self._specs) <= 1
        self._is_unsatisfiable: bool | None = None
        self._range_cache: VersionRange | None = None
        # The auto-detect cache assumes the underlying specifiers'
        # ``prereleases`` setter is not invoked between SpecifierSet
        # construction and the first ``prereleases`` read; that is the
        # universal case, and the cache is invalidated on every
        # SpecifierSet operation that legitimately changes the result.
        self._auto_prereleases: object = _PRE_UNSET
        self._prereleases = prereleases

    def _canonical_specs(self) -> tuple[Specifier, ...]:
        """Deduplicate, sort, and cache specs for order-sensitive operations."""
        if not self._canonicalized:
            self._specs = tuple(dict.fromkeys(sorted(self._specs, key=str)))
            self._canonicalized = True
            self._is_unsatisfiable = None
            self._range_cache = None
            self._auto_prereleases = _PRE_UNSET
        return self._specs

    @property
    def prereleases(self) -> bool | None:
        # An explicit value short-circuits the auto-detect.
        if self._prereleases is not None:
            return self._prereleases

        cached = self._auto_prereleases
        if cached is not _PRE_UNSET:
            return cached  # type: ignore[return-value]

        # Auto-detect: ``True`` if any specifier accepts pre-releases,
        # ``None`` otherwise.  An empty SpecifierSet leaves the value
        # at ``None``.
        result: bool | None = True if any(s.prereleases for s in self._specs) else None
        self._auto_prereleases = result
        return result

    @prereleases.setter
    def prereleases(self, value: bool | None) -> None:
        self._prereleases = value
        self._is_unsatisfiable = None
        self._range_cache = None

    def __getstate__(self) -> tuple[tuple[Specifier, ...], bool | None]:
        # Compact 2-tuple state: ``(specs, prereleases)``.
        # Cache members are excluded and recomputed on demand.
        return (self._specs, self._prereleases)

    def __setstate__(self, state: object) -> None:
        # Always discard cached values; they will be recomputed on demand.
        self._is_unsatisfiable = None
        self._range_cache = None
        self._auto_prereleases = _PRE_UNSET

        if isinstance(state, tuple):
            if len(state) == 2:
                # 26.2+ format: ``(specs, prereleases)``.
                specs, prereleases = state
                if (
                    isinstance(specs, tuple)
                    and all(isinstance(s, Specifier) for s in specs)
                    and _validate_pre(prereleases)
                ):
                    self._specs = specs
                    self._prereleases = prereleases
                    self._canonicalized = len(specs) <= 1
                    self._has_arbitrary = any("===" in str(s) for s in specs)
                    return
            if len(state) == 2 and isinstance(state[1], dict):
                # 26.0-26.1 format: ``(None, {slot: value})``.
                _, slot_dict = state
                specs = slot_dict.get("_specs", ())
                prereleases = slot_dict.get("_prereleases")
                # 26.0 stored ``_specs`` as a frozenset.
                if isinstance(specs, frozenset):
                    specs = tuple(sorted(specs, key=str))
                if (
                    isinstance(specs, tuple)
                    and all(isinstance(s, Specifier) for s in specs)
                    and _validate_pre(prereleases)
                ):
                    self._specs = specs
                    self._prereleases = prereleases
                    self._canonicalized = len(self._specs) <= 1
                    self._has_arbitrary = any("===" in str(s) for s in self._specs)
                    return
        if isinstance(state, dict):
            # <= 25.x format: plain ``__dict__``.
            specs = state.get("_specs", ())
            prereleases = state.get("_prereleases")
            if isinstance(specs, frozenset):
                specs = tuple(sorted(specs, key=str))
            if (
                isinstance(specs, tuple)
                and all(isinstance(s, Specifier) for s in specs)
                and _validate_pre(prereleases)
            ):
                self._specs = specs
                self._prereleases = prereleases
                self._canonicalized = len(self._specs) <= 1
                self._has_arbitrary = any("===" in str(s) for s in self._specs)
                return

        raise TypeError(f"Cannot restore SpecifierSet from {state!r}")

    def __repr__(self) -> str:
        """A representation of the specifier set that shows all internal state.

        Note that the ordering of the individual specifiers within the set may not
        match the input string.

        >>> SpecifierSet('>=1.0.0,!=2.0.0')
        <SpecifierSet('!=2.0.0,>=1.0.0')>
        >>> SpecifierSet('>=1.0.0,!=2.0.0', prereleases=False)
        <SpecifierSet('!=2.0.0,>=1.0.0', prereleases=False)>
        >>> SpecifierSet('>=1.0.0,!=2.0.0', prereleases=True)
        <SpecifierSet('!=2.0.0,>=1.0.0', prereleases=True)>
        """
        pre = (
            f", prereleases={self.prereleases!r}"
            if self._prereleases is not None
            else ""
        )

        return f"<{self.__class__.__name__}({str(self)!r}{pre})>"

    def __str__(self) -> str:
        """A string representation of the specifier set that can be round-tripped.

        Note that the ordering of the individual specifiers within the set may not
        match the input string.

        >>> str(SpecifierSet(">=1.0.0,!=1.0.1"))
        '!=1.0.1,>=1.0.0'
        >>> str(SpecifierSet(">=1.0.0,!=1.0.1", prereleases=False))
        '!=1.0.1,>=1.0.0'
        """
        return ",".join(str(s) for s in self._canonical_specs())

    def __hash__(self) -> int:
        return hash(self._canonical_specs())

    def __and__(self, other: SpecifierSet | str) -> SpecifierSet:
        """Return a SpecifierSet which is a combination of the two sets.

        :param other: The other object to combine with.

        >>> SpecifierSet(">=1.0.0,!=1.0.1") & '<=2.0.0,!=2.0.1'
        <SpecifierSet('!=1.0.1,!=2.0.1,<=2.0.0,>=1.0.0')>
        >>> SpecifierSet(">=1.0.0,!=1.0.1") & SpecifierSet('<=2.0.0,!=2.0.1')
        <SpecifierSet('!=1.0.1,!=2.0.1,<=2.0.0,>=1.0.0')>
        """
        if isinstance(other, str):
            other = SpecifierSet(other)
        elif not isinstance(other, SpecifierSet):
            return NotImplemented

        specifier = SpecifierSet()
        specifier._specs = self._specs + other._specs
        specifier._canonicalized = len(specifier._specs) <= 1
        specifier._has_arbitrary = self._has_arbitrary or other._has_arbitrary

        if self._prereleases is None or self._prereleases == other._prereleases:
            specifier._prereleases = other._prereleases
        elif other._prereleases is None:
            specifier._prereleases = self._prereleases
        else:
            raise ValueError(
                "Cannot combine SpecifierSets with True and False prerelease overrides."
            )

        return specifier

    def __eq__(self, other: object) -> bool:
        """Whether or not the two SpecifierSet-like objects are equal.

        :param other: The other object to check against.

        The value of :attr:`prereleases` is ignored.

        >>> SpecifierSet(">=1.0.0,!=1.0.1") == SpecifierSet(">=1.0.0,!=1.0.1")
        True
        >>> (SpecifierSet(">=1.0.0,!=1.0.1", prereleases=False) ==
        ...  SpecifierSet(">=1.0.0,!=1.0.1", prereleases=True))
        True
        >>> SpecifierSet(">=1.0.0,!=1.0.1") == ">=1.0.0,!=1.0.1"
        True
        >>> SpecifierSet(">=1.0.0,!=1.0.1") == SpecifierSet(">=1.0.0")
        False
        >>> SpecifierSet(">=1.0.0,!=1.0.1") == SpecifierSet(">=1.0.0,!=1.0.2")
        False
        """
        if isinstance(other, (str, Specifier)):
            other = SpecifierSet(str(other))
        elif not isinstance(other, SpecifierSet):
            return NotImplemented

        return self._canonical_specs() == other._canonical_specs()

    def __len__(self) -> int:
        """Returns the number of specifiers in this specifier set."""
        return len(self._specs)

    def __iter__(self) -> Iterator[Specifier]:
        """
        Returns an iterator over all the underlying :class:`Specifier` instances
        in this specifier set.

        >>> sorted(SpecifierSet(">=1.0.0,!=1.0.1"), key=str)
        [<Specifier('!=1.0.1')>, <Specifier('>=1.0.0')>]
        """
        return iter(self._specs)

    @property
    def _range(self) -> VersionRange:
        """The intersection of every specifier's :class:`VersionRange`.

        Sets containing ``===`` produce a carve-out range with the
        literal in ``_arbitrary`` -- see the
        :class:`~packaging.ranges.VersionRange` "``===`` carve-out".
        An empty :class:`VersionRange` (``is_empty=True``) when the
        intersection is unsatisfiable.  Computed lazily on first
        access; the result is cached on this :class:`SpecifierSet`
        instance.
        """
        return VersionRange.from_specifier_set(self)

    def is_unsatisfiable(self) -> bool:
        """Check whether this specifier set can never be satisfied.

        Returns True if no version can satisfy all specifiers simultaneously.

        >>> SpecifierSet(">=2.0,<1.0").is_unsatisfiable()
        True
        >>> SpecifierSet(">=1.0,<2.0").is_unsatisfiable()
        False
        >>> SpecifierSet("").is_unsatisfiable()
        False
        >>> SpecifierSet("==1.0,!=1.0").is_unsatisfiable()
        True
        """
        cached = self._is_unsatisfiable
        if cached is not None:
            return cached

        if not self._specs:
            self._is_unsatisfiable = False
            return False

        # The combined range encodes every cause of unsatisfiability:
        # an empty rangelike intersection collapses bounds to ``()``,
        # disagreeing ``===`` literals collapse the arbitrary set to
        # ``()``, and a pre-release ``===`` literal under
        # ``prereleases=False`` is rejected by ``is_unsatisfiable``.
        result = self._range.is_unsatisfiable(prereleases=self.prereleases)
        self._is_unsatisfiable = result
        return result

    def to_range(self) -> VersionRange:
        """Return the :class:`VersionRange` accepted by this specifier set.

        The result is the intersection of every specifier in the set.
        An empty :class:`SpecifierSet` yields the unbounded range; an
        unsatisfiable set yields an empty :class:`VersionRange` (where
        ``bool(r) is False``).  Sets containing ``===`` produce a
        carve-out range with the literal in ``_arbitrary`` -- see the
        :class:`~packaging.ranges.VersionRange` "``===`` carve-out".

        Equivalent to :meth:`VersionRange.from_specifier_set`; the
        result is cached on this :class:`SpecifierSet` instance, so
        repeated calls are O(1).

        >>> isinstance(SpecifierSet(">=1.0,<2.0").to_range(), VersionRange)
        True
        >>> SpecifierSet(">=1.0,<2.0").to_range().is_empty
        False
        >>> SpecifierSet(">=2.0,<1.0").to_range().is_empty
        True
        >>> SpecifierSet("===wat").to_range()._arbitrary
        'wat'
        """
        return VersionRange.from_specifier_set(self)

    def __contains__(self, item: UnparsedVersion) -> bool:
        """Return whether or not the item is contained in this specifier.

        :param item: The item to check for.

        This is used for the ``in`` operator and behaves the same as
        :meth:`contains` with no ``prereleases`` argument passed.

        >>> "1.2.3" in SpecifierSet(">=1.0.0,!=1.0.1")
        True
        >>> Version("1.2.3") in SpecifierSet(">=1.0.0,!=1.0.1")
        True
        >>> "1.0.1" in SpecifierSet(">=1.0.0,!=1.0.1")
        False
        >>> "1.3.0a1" in SpecifierSet(">=1.0.0,!=1.0.1")
        True
        >>> "1.3.0a1" in SpecifierSet(">=1.0.0,!=1.0.1", prereleases=True)
        True
        """
        return self.contains(item)

    def contains(
        self,
        item: UnparsedVersion,
        prereleases: bool | None = None,
        installed: bool | None = None,
    ) -> bool:
        """Return whether or not the item is contained in this SpecifierSet.

        :param item:
            The item to check for, which can be a version string or a
            :class:`Version` instance.
        :param prereleases:
            Whether or not to match prereleases with this SpecifierSet. If set to
            ``None`` (the default), it will follow the recommendation from :pep:`440`
            and match prereleases, as there are no other versions.
        :param installed:
            Whether or not the item is installed. If set to ``True``, it will
            accept prerelease versions even if the specifier does not allow them.

        >>> SpecifierSet(">=1.0.0,!=1.0.1").contains("1.2.3")
        True
        >>> SpecifierSet(">=1.0.0,!=1.0.1").contains(Version("1.2.3"))
        True
        >>> SpecifierSet(">=1.0.0,!=1.0.1").contains("1.0.1")
        False
        >>> SpecifierSet(">=1.0.0,!=1.0.1").contains("1.3.0a1")
        True
        >>> SpecifierSet(">=1.0.0,!=1.0.1", prereleases=False).contains("1.3.0a1")
        False
        >>> SpecifierSet(">=1.0.0,!=1.0.1").contains("1.3.0a1", prereleases=True)
        True
        """
        version = _coerce_version(item)

        if version is not None and installed and version.is_prerelease:
            prereleases = True

        # When ``===`` is involved, fall back to the filter path so the
        # raw string form drives the comparison (not the normalized
        # :class:`Version`).
        if self._has_arbitrary:
            if version is None or not isinstance(item, Version):
                check_item: UnparsedVersion = item
            else:
                check_item = version
            return bool(list(self.filter([check_item], prereleases=prereleases)))

        # No ``===`` in the set — the cached ranges are authoritative.
        if not self._specs:
            # Empty set: matches anything that isn't excluded by
            # the explicit ``prereleases`` flag.
            if prereleases is None:
                prereleases = self._prereleases
            if prereleases is False and version is not None and version.is_prerelease:
                return False
            return True

        if version is None:
            # Non-arbitrary specs cannot accept an unparsable string.
            return False

        if prereleases is None:
            auto_pre = self.prereleases
            if auto_pre is not None:
                prereleases = auto_pre
        if prereleases is False and version.is_prerelease:
            return False

        # Read the cache slot directly here; routing through the
        # :attr:`_range` property would add descriptor-protocol overhead
        # to every call.  ``_has_arbitrary`` was False above so the
        # cached range, once built, is never ``None``.
        version_range = self._range_cache
        if version_range is None:
            version_range = VersionRange.from_specifier_set(self)
        return version in version_range  # type: ignore[operator]

    @typing.overload
    def filter(
        self,
        iterable: Iterable[UnparsedVersionVar],
        prereleases: bool | None = None,
        key: None = ...,
    ) -> Iterator[UnparsedVersionVar]: ...

    def _resolve_prereleases(
        self,
        prereleases: bool | None,
        *,
        item: UnparsedVersion | None = None,
        installed: bool = False,
    ) -> bool | None:
        """Compute the effective ``prereleases`` value the range filter
        should see for this :class:`SpecifierSet`.

        Mirrors the chain :meth:`filter` and :meth:`contains` use
        internally:

        * If *installed* is set and *item* is a parseable pre-release
          :class:`~packaging.version.Version`, the result is forced
          to ``True`` (matches the ``installed=True`` upgrade in
          :meth:`contains`).
        * Else, the explicit *prereleases* argument wins.
        * Else, ``self.prereleases`` (which already collapses the
          explicit constructor value with the auto-detected one).

        Exposed so callers driving :meth:`VersionRange.filter` directly
        can match the spec form without duplicating the resolution.
        """
        if installed and item is not None:
            parsed = _coerce_version(item)
            if parsed is not None and parsed.is_prerelease:
                return True
        if prereleases is not None:
            return prereleases
        return self.prereleases

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
        key: Callable[[Any], UnparsedVersion] | None = None,
    ) -> Iterator[Any]:
        """Filter items in the given iterable, that match the specifiers in this set.

        :param iterable:
            An iterable that can contain version strings and :class:`Version` instances.
            The items in the iterable will be filtered according to the specifier.
        :param prereleases:
            Whether or not to allow prereleases in the returned iterator. If set to
            ``None`` (the default), it will follow the recommendation from :pep:`440`
            and match prereleases if there are no other versions.
        :param key:
            A callable that takes a single argument (an item from the iterable) and
            returns a version string or :class:`Version` instance to be used for
            filtering.

        >>> list(SpecifierSet(">=1.2.3").filter(["1.2", "1.3", "1.5a1"]))
        ['1.3']
        >>> list(SpecifierSet(">=1.2.3").filter(["1.2", "1.3", Version("1.4")]))
        ['1.3', <Version('1.4')>]
        >>> list(SpecifierSet(">=1.2.3").filter(["1.2", "1.5a1"]))
        ['1.5a1']
        >>> list(SpecifierSet(">=1.2.3").filter(["1.3", "1.5a1"], prereleases=True))
        ['1.3', '1.5a1']
        >>> list(SpecifierSet(">=1.2.3", prereleases=True).filter(["1.3", "1.5a1"]))
        ['1.3', '1.5a1']
        >>> list(SpecifierSet(">=1.2.3").filter(
        ... [{"ver": "1.2"}, {"ver": "1.3"}],
        ... key=lambda x: x["ver"]))
        [{'ver': '1.3'}]

        An "empty" SpecifierSet will filter items based on the presence of prerelease
        versions in the set.

        >>> list(SpecifierSet("").filter(["1.3", "1.5a1"]))
        ['1.3']
        >>> list(SpecifierSet("").filter(["1.5a1"]))
        ['1.5a1']
        >>> list(SpecifierSet("", prereleases=True).filter(["1.3", "1.5a1"]))
        ['1.3', '1.5a1']
        >>> list(SpecifierSet("").filter(["1.3", "1.5a1"], prereleases=True))
        ['1.3', '1.5a1']
        """
        prereleases = self._resolve_prereleases(prereleases)

        if self._specs:
            if not self._has_arbitrary:
                # No ``===``: use the cached range.  Read the slot
                # directly to skip the ``_range`` property descriptor.
                version_range = self._range_cache
                if version_range is None:
                    version_range = VersionRange.from_specifier_set(self)
                # ``VersionRange.filter`` handles the PEP 440 ``None``
                # mode inline; pass *prereleases* through directly.
                return version_range.filter(  # type: ignore[union-attr]
                    iterable, key, prereleases
                )

            # Set contains ``===``.
            # Items here may not be parseable as PEP 440
            # versions, so the :func:`_pep440_filter_prereleases`
            # wrapper still applies for the ``None`` case.
            resolve_pre = True if prereleases is None else prereleases
            specs = self._specs
            filtered: Iterator[Any] = (
                item
                for item in iterable
                if all(
                    s.contains(
                        item if key is None else key(item),
                        prereleases=resolve_pre,
                    )
                    for s in specs
                )
            )

            if prereleases is not None:
                return filtered

            return _pep440_filter_prereleases(filtered, key)

        # Empty SpecifierSet.
        if prereleases is True:
            return iter(iterable)

        if prereleases is False:
            return (
                item
                for item in iterable
                if (
                    (version := _coerce_version(item if key is None else key(item)))
                    is None
                    or not version.is_prerelease
                )
            )

        # PEP 440 default: exclude pre-releases unless no final matches.
        return _pep440_filter_prereleases(iterable, key)
