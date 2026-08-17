from __future__ import annotations

# Select the whole version listing of a few real projects
#
# Get data with, for each name in PROJECTS:
# curl -H 'Accept: application/vnd.pypi.simple.v1+json'
# https://pypi.org/simple/<name>/ > <name>.json
#
# The six stand for the size bands of a 1857-project capture of that same API,
# whose distinct-version count runs to 41 at the median, 149 at the 90th
# percentile and 1000 at the 99th, with 27.5% of listings below 20 versions. Each
# band was picked from the listings nearest its target size, taking the one with
# the most final releases. That second rule keeps the sample near the middle of
# the distribution, where half of the captured listings hold no pre-release at
# all, and it leaves the sample pre-release-light: 8 of its 3715 rows.
#
# PyPI keeps publishing, so a re-run will not reproduce the committed file. The
# method is what is committed here, as it is for the other selectors.
import json

from packaging.utils import (
    InvalidSdistFilename,
    InvalidWheelFilename,
    parse_sdist_filename,
    parse_wheel_filename,
)
from packaging.version import Version

PROJECTS = ["appdirs", "requests-toolbelt", "attrs", "numpy", "trimesh", "awscli"]


def version_of(filename: str) -> str | None:
    """The version *filename* carries, or ``None`` where nothing parses it.

    An old listing still holds the ``.exe`` installers PyPI used to accept, and
    neither parser reads those.
    """
    try:
        if filename.endswith(".whl"):
            return str(parse_wheel_filename(filename)[1])
        return str(parse_sdist_filename(filename)[1])
    except (InvalidWheelFilename, InvalidSdistFilename):
        return None


def distinct_versions(filenames: list[str]) -> list[str]:
    """The versions those filenames carry, deduplicated, oldest first.

    The string breaks the sort tie, so two spellings of one version come out in
    a stable order rather than a set-iteration one.
    """
    versions = {v for name in filenames if (v := version_of(name)) is not None}
    return sorted(versions, key=lambda version: (Version(version), version))


with open("listing_sample.txt", "w") as out:
    for project in PROJECTS:
        with open(f"{project}.json") as source:
            listing = json.load(source)

        filenames = [entry["filename"] for entry in listing["files"]]
        out.writelines(f"{project} {v}\n" for v in distinct_versions(filenames))
