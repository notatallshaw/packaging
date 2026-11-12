# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the BSD License. See the LICENSE file in the root of this repository
# for complete details.

from __future__ import annotations

import itertools
import string

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from packaging.tags import parse_tag
from packaging.utils import (
    canonicalize_name,
    parse_sdist_filename,
    parse_wheel_filename,
)
from packaging.version import Version

from .strategies import SETTINGS

pytestmark = pytest.mark.property

NAME_PARTS = st.lists(
    st.text(alphabet=string.ascii_letters + string.digits, min_size=1, max_size=10),
    min_size=1,
    max_size=5,
)
SEPARATORS = st.text(alphabet="-_.", min_size=1, max_size=5)
RELEASES = st.tuples(*(st.integers(min_value=0, max_value=20) for _ in range(3)))
INTERPRETERS = st.sets(st.sampled_from(["py2", "py3", "cp310"]), min_size=1).map(sorted)
ABIS = st.sets(st.sampled_from(["none", "abi3", "cp310"]), min_size=1).map(sorted)
PLATFORMS = st.sets(
    st.sampled_from(["any", "win_amd64", "manylinux_2_17_x86_64"]), min_size=1
).map(sorted)
BUILD_TAGS = st.none() | st.tuples(
    st.integers(min_value=0, max_value=20),
    st.text(alphabet=string.ascii_letters, max_size=6),
)


class TestNameNormalization:
    """The name should be lowercased with all runs of the characters `.`, `-`, or
    `_` replaced with a single `-` character.

    https://packaging.python.org/en/latest/specifications/name-normalization/#name-normalization
    """

    @given(parts=NAME_PARTS, separator=SEPARATORS)
    @example(parts=["FrIeNdLy", "bArD"], separator="-._.-")
    @SETTINGS
    def test_case_and_separator_runs(self, parts: list[str], separator: str) -> None:
        name = separator.join(parts)
        assert canonicalize_name(name, validate=True) == "-".join(
            part.lower() for part in parts
        )


class TestCompressedTagSets:
    """Each component is "a '.'-separated, sorted, collection of tags."

    The specified expansion iterates over every combination of the components.

    https://packaging.python.org/en/latest/specifications/platform-compatibility-tags/#compressed-tag-sets
    """

    @given(interpreters=INTERPRETERS, abis=ABIS, platforms=PLATFORMS)
    @SETTINGS
    def test_expansion_is_the_cartesian_product(
        self, interpreters: list[str], abis: list[str], platforms: list[str]
    ) -> None:
        source = "-".join(".".join(parts) for parts in (interpreters, abis, platforms))
        actual = {
            (tag.interpreter, tag.abi, tag.platform)
            for tag in parse_tag(source, validate_order=True)
        }
        assert actual == set(itertools.product(interpreters, abis, platforms))


class TestWheelFilenameFields:
    """The wheel filename is
    `{distribution}-{version}(-{build tag})?-{python tag}-{abi tag}-{platform tag}.whl`.

    https://packaging.python.org/en/latest/specifications/binary-distribution-format/#file-name-convention
    """

    @given(
        parts=NAME_PARTS, release=RELEASES, interpreters=INTERPRETERS, build=BUILD_TAGS
    )
    @SETTINGS
    def test_encoded_fields(
        self,
        parts: list[str],
        release: tuple[int, int, int],
        interpreters: list[str],
        build: tuple[int, str] | None,
    ) -> None:
        normalized_name = "-".join(part.lower() for part in parts)
        escaped_name = "_".join(part.lower() for part in parts)
        version = ".".join(map(str, release))
        build_text = "" if build is None else f"-{build[0]}{build[1]}"
        tag_text = ".".join(interpreters) + "-none-any"
        filename = f"{escaped_name}-{version}{build_text}-{tag_text}.whl"
        name, parsed_version, parsed_build, tags = parse_wheel_filename(
            filename, validate_order=True
        )

        assert name == normalized_name
        assert parsed_version == Version.from_parts(release=release)
        assert parsed_build == (() if build is None else build)
        assert {(tag.interpreter, tag.abi, tag.platform) for tag in tags} == {
            (interpreter, "none", "any") for interpreter in interpreters
        }


class TestSdistFilenameFields:
    """The file name must be in the form `{name}-{version}.tar.gz`

    https://packaging.python.org/en/latest/specifications/source-distribution-format/#source-distribution-file-name
    """

    @given(parts=NAME_PARTS, release=RELEASES)
    @SETTINGS
    def test_encoded_name_and_version(
        self, parts: list[str], release: tuple[int, int, int]
    ) -> None:
        normalized_name = "-".join(part.lower() for part in parts)
        escaped_name = "_".join(part.lower() for part in parts)
        version = ".".join(map(str, release))
        name, parsed_version = parse_sdist_filename(f"{escaped_name}-{version}.tar.gz")

        assert name == normalized_name
        assert parsed_version == Version.from_parts(release=release)
