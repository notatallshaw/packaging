# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the BSD License. See the LICENSE file in the root of this repository
# for complete details.

"""Model normalized names, filename fields and compressed tags independently."""

from __future__ import annotations

import itertools
import string

import pytest
from hypothesis import given
from hypothesis import strategies as st

from packaging.tags import TooManyTagsError, UnsortedTagsError, parse_tag
from packaging.utils import (
    canonicalize_name,
    parse_sdist_filename,
    parse_wheel_filename,
)

from .strategies import SETTINGS

pytestmark = pytest.mark.property

NAME_PARTS = st.lists(
    st.text(alphabet=string.ascii_letters + string.digits, min_size=1, max_size=10),
    min_size=1,
    max_size=5,
)
SEPARATORS = st.text(alphabet="-_.", min_size=1, max_size=5)
INTERPRETERS = st.lists(
    st.sampled_from(["py2", "py3", "cp310"]), min_size=1, max_size=4
)
ABIS = st.lists(st.sampled_from(["none", "abi3", "cp310"]), min_size=1, max_size=3)
PLATFORMS = st.lists(
    st.sampled_from(["any", "win_amd64", "manylinux_2_17_x86_64"]),
    min_size=1,
    max_size=4,
)


@given(parts=NAME_PARTS, separator=SEPARATORS)
@SETTINGS
def test_name_separator_runs_collapse_and_case_is_normalized(
    parts: list[str], separator: str
) -> None:
    raw = separator.join(parts)
    assert canonicalize_name(raw, validate=True) == "-".join(
        part.lower() for part in parts
    )


@given(interpreters=INTERPRETERS, abis=ABIS, platforms=PLATFORMS)
@SETTINGS
def test_compressed_tags_form_the_cartesian_product(
    interpreters: list[str], abis: list[str], platforms: list[str]
) -> None:
    text = "-".join(".".join(parts) for parts in (interpreters, abis, platforms))
    actual = {(tag.interpreter, tag.abi, tag.platform) for tag in parse_tag(text)}
    assert actual == set(itertools.product(interpreters, abis, platforms))

    combinations = len(interpreters) * len(abis) * len(platforms)
    assert parse_tag(text, limit=combinations) == parse_tag(text)
    # The resource bound prices raw combinations, including duplicate entries.
    with pytest.raises(TooManyTagsError):
        parse_tag(text, limit=combinations - 1)

    if all(parts == sorted(parts) for parts in (interpreters, abis, platforms)):
        assert parse_tag(text, validate_order=True) == parse_tag(text)
    else:
        with pytest.raises(UnsortedTagsError):
            parse_tag(text, validate_order=True)


@given(
    parts=NAME_PARTS,
    release=st.tuples(*(st.integers(min_value=0, max_value=20) for _ in range(3))),
    interpreters=INTERPRETERS,
)
@SETTINGS
def test_distribution_filename_fields_preserve_the_encoded_values(
    parts: list[str], release: tuple[int, int, int], interpreters: list[str]
) -> None:
    normalized_name = "-".join(part.lower() for part in parts)
    escaped_name = "_".join(part.lower() for part in parts)
    version = ".".join(map(str, release))
    tag_text = ".".join(sorted(interpreters)) + "-none-any"
    name, parsed_version, build, tags = parse_wheel_filename(
        f"{escaped_name}-{version}-{tag_text}.whl", validate_order=True
    )

    assert name == normalized_name
    assert parsed_version.release == release
    assert build == ()
    assert {(tag.interpreter, tag.abi, tag.platform) for tag in tags} == {
        (interpreter, "none", "any") for interpreter in interpreters
    }

    sdist_name, sdist_version = parse_sdist_filename(f"{escaped_name}-{version}.tar.gz")
    assert sdist_name == normalized_name
    assert sdist_version.release == release
