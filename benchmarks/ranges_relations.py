# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the BSD License. See the LICENSE file in the root of this repository
# for complete details.

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

from packaging.specifiers import SpecifierSet

if TYPE_CHECKING:
    from packaging.ranges import VersionRange


@cache
def load_pairs() -> list[tuple[VersionRange, VersionRange]]:
    """Pair the existing Requires-Python corpus at four offsets."""
    texts = (Path(__file__).parent / "specs_sample.txt").read_text().splitlines()
    ranges = [SpecifierSet(text).to_range() for text in texts]
    return [
        (a, ranges[(i + offset) % len(ranges)])
        for offset in (1, 7, 53, 211)
        for i, a in enumerate(ranges)
    ]


class TimeRangeRelationsSuite:
    rounds = 4

    def setup(self) -> None:
        self.pairs = load_pairs()

    def time_subset(self) -> None:
        for a, b in self.pairs:
            a.is_subset(b)

    def time_disjoint(self) -> None:
        for a, b in self.pairs:
            a.is_disjoint(b)
