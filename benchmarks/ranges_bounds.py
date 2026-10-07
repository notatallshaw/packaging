from __future__ import annotations

from functools import cache
from itertools import chain
from pathlib import Path
from typing import TYPE_CHECKING

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from . import add_attributes

if TYPE_CHECKING:
    from packaging.ranges import VersionRange

DIR = Path(__file__).parent.resolve()

# Offsets for pairing the sample against itself. They only have to spread the
# pairing across the whole list, so both sides keep the sample's own mix of
# interval counts.
PAIR_OFFSETS = (1, 7, 53, 211)


# asv runs ``setup`` before every sample, so parsing the sample there would
# churn the heap that the timed call then runs against.
@cache
def load_ranges() -> list[VersionRange]:
    """The sample specifier sets as ranges, every bound version's key built."""
    with (DIR / "specs_sample.txt").open() as f:
        spec_strs = [s.strip() for s in f.readlines()]

    ranges = [SpecifierSet(s).to_range() for s in spec_strs]
    for lower, upper in chain.from_iterable(r._bounds for r in ranges):
        for bound in (lower, upper):
            if isinstance(bound.version, Version):
                bound.version._key  # noqa: B018

    return ranges


@cache
def load_pairs() -> list[tuple[VersionRange, VersionRange]]:
    """Each range paired with the one at each offset along, wrapping at the end."""
    ranges = load_ranges()
    count = len(ranges)
    return [
        (ranges[index], ranges[(index + offset) % count])
        for offset in PAIR_OFFSETS
        for index in range(count)
    ]


class TimeRangeAlgebraSuite:
    rounds = 4

    def setup(self) -> None:
        self.ranges = load_ranges()
        self.pairs = load_pairs()

    @add_attributes(pretty_name="VersionRange union")
    def time_union(self) -> None:
        for left, right in self.pairs:
            left.union(right)

    @add_attributes(pretty_name="VersionRange intersection")
    def time_intersection(self) -> None:
        for left, right in self.pairs:
            left.intersection(right)

    @add_attributes(pretty_name="VersionRange difference")
    def time_difference(self) -> None:
        for left, right in self.pairs:
            left.difference(right)

    # Complementing orders no bounds against each other, so this row is the
    # control for a change to how they compare.
    @add_attributes(pretty_name="VersionRange complement")
    def time_complement(self) -> None:
        for version_range in self.ranges:
            version_range.complement()
