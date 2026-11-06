# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the BSD License. See the LICENSE file in the root of this repository
# for complete details.
"""PEP 440 version values and normalization helpers."""

from __future__ import annotations

from typing import Self, override

from _version import InvalidVersion
from _version import Version as _NativeVersion

InvalidVersion.__module__ = __name__

__all__ = ["VERSION_PATTERN", "InvalidVersion", "Version", "normalize_pre", "parse"]


def __dir__() -> list[str]:
    return __all__


_LETTER_NORMALIZATION = {
    "alpha": "a",
    "beta": "b",
    "c": "rc",
    "pre": "rc",
    "preview": "rc",
    "rev": "post",
    "r": "post",
}


def normalize_pre(letter: str, /) -> str:
    """Normalize known pre-release identifiers and lowercase other strings."""
    letter = letter.lower()
    return _LETTER_NORMALIZATION.get(letter, letter)


def parse(version: str) -> Version:
    """Parse a version string into a Version object."""
    return Version(version)


_VERSION_PATTERN = r"""
    v?+                                                   # optional leading v
    (?a:
        (?:(?P<epoch>[0-9]+)!)?+                          # epoch
        (?P<release>[0-9]+(?:\.[0-9]+)*+)                 # release segment
        (?P<pre>                                          # pre-release
            [._-]?+
            (?P<pre_l>alpha|a|beta|b|preview|pre|c|rc)
            [._-]?+
            (?P<pre_n>[0-9]+)?
        )?+
        (?P<post>                                         # post release
            (?:-(?P<post_n1>[0-9]+))
            |
            (?:
                [._-]?
                (?P<post_l>post|rev|r)
                [._-]?
                (?P<post_n2>[0-9]+)?
            )
        )?+
        (?P<dev>                                          # dev release
            [._-]?+
            (?P<dev_l>dev)
            [._-]?+
            (?P<dev_n>[0-9]+)?
        )?+
    )
    (?a:\+
        (?P<local>                                        # local version
            [a-z0-9]+
            (?:[._-][a-z0-9]+)*+
        )
    )?+
"""

VERSION_PATTERN = _VERSION_PATTERN
"""
A string containing the regular expression used to match a valid version.

The pattern is not anchored at either end, and is intended for embedding in larger
expressions (for example, matching a version number as part of a file name). The
regular expression should be compiled with the ``re.VERBOSE`` and ``re.IGNORECASE``
flags set.

.. versionchanged:: 26.0

   The regex now uses possessive qualifiers on Python 3.11 if they are
   supported (CPython 3.11.5+, PyPy 3.11.13+).

:meta hide-value:
"""


class Version(_NativeVersion):
    """A native PEP 440 version value."""

    __slots__ = ()
    __match_args__ = ("_str",)

    @property
    def _str(self) -> str:
        return str(self)

    @override
    def __reduce__(self) -> tuple[type[Self], tuple[str], object]:
        return type(self), (_NativeVersion.__str__(self),), object.__getstate__(self)


class _TrimmedRelease(Version):
    """Build a version whose release has no unnecessary trailing zeros."""

    __slots__ = ()

    def __new__(cls, version: str | Version) -> Self:
        parsed = version if isinstance(version, Version) else Version(version)
        release = parsed.release
        end = len(release)
        while end > 1 and release[end - 1] == 0:
            end -= 1
        return cls.from_parts(
            epoch=parsed.epoch,
            release=release[:end],
            pre=parsed.pre,
            post=parsed.post,
            dev=parsed.dev,
            local=parsed.local,
        )
