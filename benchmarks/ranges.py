from __future__ import annotations

import inspect
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from packaging.ranges import VersionRange
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from . import add_attributes

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    # ``filter`` with the declared order already bound in.
    SortedFilter = Callable[[VersionRange, Sequence[Any]], Iterator[Any]]

DIR = Path(__file__).parent.resolve()

# One real PyPI listing per size band, each paired with a requirement of the
# shape that selects from it. ``listing_sample.txt`` holds the distinct versions
# parsed out of the filenames in six PyPI Simple-API responses captured in July
# and August 2026, and ``tasks/select_pypi_listings.py`` is the derivation.
#
# The quantiles are from the same 1857-listing capture, whose distinct-version
# count has a median of 41, a 90th percentile of 149 and a 99th of 1000, with
# 27% of listings below 20 versions. So these six run from well under the median
# to the largest listing in the capture.
WINDOWS = {
    "appdirs": ">=1.2,<1.4.4",
    "requests-toolbelt": ">=0.4,<0.10",
    "attrs": ">=20.1,<26.0",
    "numpy": ">=1.20,<2.0",
    "trimesh": ">=3.0,<5.0",
    "awscli": ">=1.20,<1.40",
}

# Exclusions inside one of those windows, so the range carries four intervals
# rather than one.
PUNCTURED = ">=3.0,<5.0,!=3.9.0,!=4.0.0,!=4.4.0"

# A pre-release floor over the same window, which opts the whole range in and
# takes the no-buffer path.
PRERELEASE_FLOOR = ">=3.0.0a1,<5.0"


# The corpus is cached because ``setup`` runs before every sample. Parsing 3715
# versions each time churned the heap that the timed call then ran against,
# which was enough to read a win as a loss. Nothing here mutates what it is
# handed.


@cache
def load_listings() -> dict[str, list[Version]]:
    """The sample listings, parsed and sorted oldest first, with warm keys."""
    listings: dict[str, list[Version]] = {}
    with (DIR / "listing_sample.txt").open() as f:
        for line in f:
            name, _, raw = line.strip().partition(" ")
            listings.setdefault(name, []).append(Version(raw))
    for versions in listings.values():
        versions.sort()
        for version in versions:
            version._key  # noqa: B018
    return listings


@cache
def load_newest_first() -> dict[str, list[Version]]:
    """The same listings the other way round, for the descending order."""
    return {name: v[::-1] for name, v in load_listings().items()}


@cache
def load_strings() -> dict[str, list[str]]:
    """The same listings as ``str`` entries, which the projection must coerce."""
    return {
        name: [str(version) for version in versions]
        for name, versions in load_listings().items()
    }


@cache
def load_ranges() -> dict[str, VersionRange]:
    """The sample requirements as ranges, warmed by one membership test."""
    ranges = {name: SpecifierSet(spec).to_range() for name, spec in WINDOWS.items()}
    ranges["punctured"] = SpecifierSet(PUNCTURED).to_range()
    ranges["prerelease_floor"] = SpecifierSet(PRERELEASE_FLOOR).to_range()
    probe = load_listings()["numpy"][0]
    for version_range in ranges.values():
        version_range.contains(probe)
    return ranges


# asv picks ``number = 1`` for a benchmark this short, which times one
# microsecond-scale call against the timer. Every method below sets it instead,
# to put roughly a millisecond of work inside each sample.


class TimeFilterSuite:
    """The per-entry walk, which ``assume_sorted`` leaves alone."""

    rounds = 4

    def setup(self) -> None:
        self.listings = load_listings()
        self.ranges = load_ranges()
        self.punctured = self.ranges["punctured"]

    @add_attributes(pretty_name="VersionRange filter (median listing, 36)", number=150)
    def time_filter_median(self) -> None:
        list(self.ranges["attrs"].filter(self.listings["attrs"]))

    @add_attributes(pretty_name="VersionRange filter (p90 listing, 135)", number=40)
    def time_filter_p90(self) -> None:
        list(self.ranges["numpy"].filter(self.listings["numpy"]))

    @add_attributes(pretty_name="VersionRange filter (p99 listing, 1024)", number=6)
    def time_filter_p99(self) -> None:
        list(self.ranges["trimesh"].filter(self.listings["trimesh"]))

    @add_attributes(pretty_name="VersionRange filter (largest listing, 2492)", number=2)
    def time_filter_largest(self) -> None:
        list(self.ranges["awscli"].filter(self.listings["awscli"]))

    @add_attributes(pretty_name="VersionRange filter (four intervals, 1024)", number=3)
    def time_filter_punctured(self) -> None:
        list(self.punctured.filter(self.listings["trimesh"]))


class TimeSortedFilterSuite:
    """Filtering a listing the caller already holds in version order.

    On a revision whose ``filter`` has no ``assume_sorted`` every method here
    times the same call without it, so an A/B across that revision compares what
    a caller holding an ordered listing pays either side of it.
    """

    rounds = 4

    def setup(self) -> None:
        self.listings = load_listings()
        self.newest_first = load_newest_first()
        self.as_strings = load_strings()
        self.ranges = load_ranges()
        self.punctured = self.ranges["punctured"]
        self.ascending = self._bind("ascending")
        self.descending = self._bind("descending")

    def _bind(self, order: Literal["ascending", "descending"]) -> SortedFilter:
        """Bind ``filter`` to *order*, or plain where the keyword does not exist.

        The signature is read rather than probed with a trial call, so an
        unrelated ``TypeError`` cannot reroute the benchmark to the other path.
        """
        parameters = inspect.signature(VersionRange.filter).parameters
        if "assume_sorted" not in parameters:
            return lambda version_range, versions: version_range.filter(versions)
        return lambda version_range, versions: version_range.filter(
            versions, assume_sorted=order
        )

    @add_attributes(
        pretty_name="VersionRange sorted filter (tiny listing, 8)", number=300
    )
    def time_sorted_tiny(self) -> None:
        list(self.ascending(self.ranges["appdirs"], self.listings["appdirs"]))

    @add_attributes(
        pretty_name="VersionRange sorted filter (short listing, 20)", number=250
    )
    def time_sorted_short(self) -> None:
        list(
            self.ascending(
                self.ranges["requests-toolbelt"], self.listings["requests-toolbelt"]
            )
        )

    @add_attributes(
        pretty_name="VersionRange sorted filter (median listing, 36)", number=150
    )
    def time_sorted_median(self) -> None:
        list(self.ascending(self.ranges["attrs"], self.listings["attrs"]))

    @add_attributes(
        pretty_name="VersionRange sorted filter (p90 listing, 135)", number=40
    )
    def time_sorted_p90(self) -> None:
        list(self.ascending(self.ranges["numpy"], self.listings["numpy"]))

    @add_attributes(
        pretty_name="VersionRange sorted filter (p99 listing, 1024)", number=6
    )
    def time_sorted_p99(self) -> None:
        list(self.ascending(self.ranges["trimesh"], self.listings["trimesh"]))

    @add_attributes(
        pretty_name="VersionRange sorted filter (largest listing, 2492)", number=2
    )
    def time_sorted_largest(self) -> None:
        list(self.ascending(self.ranges["awscli"], self.listings["awscli"]))

    @add_attributes(
        pretty_name="VersionRange sorted filter (newest first, 2492)", number=2
    )
    def time_sorted_newest_first(self) -> None:
        list(self.descending(self.ranges["awscli"], self.newest_first["awscli"]))

    @add_attributes(
        pretty_name="VersionRange sorted filter (four intervals, 1024)", number=3
    )
    def time_sorted_punctured(self) -> None:
        list(self.ascending(self.punctured, self.listings["trimesh"]))

    @add_attributes(
        pretty_name="VersionRange sorted filter (pre-release floor, 1024)", number=6
    )
    def time_sorted_prerelease_floor(self) -> None:
        list(self.ascending(self.ranges["prerelease_floor"], self.listings["trimesh"]))

    @add_attributes(
        pretty_name="VersionRange sorted filter (median listing, strings)", number=30
    )
    def time_sorted_strings(self) -> None:
        list(self.ascending(self.ranges["attrs"], self.as_strings["attrs"]))
