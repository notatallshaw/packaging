# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the BSD License. See the LICENSE file in the root of this repository
# for complete details.

"""Check lock-version admission and wheel selection with independent models."""

from __future__ import annotations

import dataclasses
import itertools
import pickle

import pytest
from hypothesis import given
from hypothesis import strategies as st

from packaging.markers import default_environment
from packaging.pylock import (
    PackageWheel,
    Pylock,
    PylockSelectError,
    PylockUnsupportedVersionError,
)
from packaging.tags import Tag

from .strategies import SETTINGS

pytestmark = pytest.mark.property

TAG_TEXTS = [
    "py3-none-any",
    "cp312-cp312-win_amd64",
    "cp312-cp312-manylinux_2_17_x86_64",
]
ENVIRONMENT = default_environment()
ENVIRONMENT["python_full_version"] = "3.12.0"
ENVIRONMENT["python_version"] = "3.12"


def lock_with_wheels(wheels: list[dict[str, object]]) -> Pylock:
    """Build one valid package for wheel-selection properties."""
    return Pylock.from_dict(
        {
            "lock-version": "1.0",
            "created-by": "test",
            "packages": [{"name": "demo", "version": "1.0", "wheels": wheels}],
        }
    )


@given(
    major=st.integers(min_value=0, max_value=4),
    minor=st.integers(min_value=0, max_value=20),
)
@SETTINGS
def test_lock_version_major_controls_admission(major: int, minor: int) -> None:
    data = {"lock-version": f"{major}.{minor}", "created-by": "test", "packages": []}
    if major == 1:
        lock = Pylock.from_dict(data)
        assert lock.lock_version.release == (major, minor)
    else:
        with pytest.raises(PylockUnsupportedVersionError):
            Pylock.from_dict(data)


@given(
    wheel_tags=st.lists(st.sampled_from(TAG_TEXTS), min_size=1, max_size=5),
    preferences=st.lists(st.sampled_from(TAG_TEXTS), unique=True, max_size=3),
)
@SETTINGS
def test_selected_wheel_obeys_tag_priority_and_retains_schema(
    wheel_tags: list[str], preferences: list[str]
) -> None:
    wheels: list[dict[str, object]] = [
        {
            "name": f"demo-1.0-{tag}.whl",
            "url": f"https://example.test/{index}.whl",
            "hashes": {"sha256": "0" * 64},
        }
        for index, tag in enumerate(wheel_tags)
    ]
    lock = lock_with_wheels(wheels)
    tags = [Tag(*text.split("-")) for text in preferences]
    candidates = [
        (preferences.index(tag), index)
        for index, tag in enumerate(wheel_tags)
        if tag in preferences
    ]
    expected_index = min(candidates)[1] if candidates else None
    if expected_index is not None:
        selected = list(lock.select(environment=ENVIRONMENT, tags=tags))
        assert len(selected) == 1
        selected_wheel = selected[0][1]
        assert isinstance(selected_wheel, PackageWheel)
        assert selected_wheel.url == wheels[expected_index]["url"]
    else:
        with pytest.raises(PylockSelectError):
            list(lock.select(environment=ENVIRONMENT, tags=tags))

    serialized = lock.to_dict()
    assert serialized["packages"][0]["wheels"] == wheels

    # Only bytes produced by this test are restored.
    serialized_pickle = pickle.dumps(lock)
    restored = pickle.loads(serialized_pickle)  # noqa: S301
    assert restored.to_dict() == serialized
    if expected_index is not None:
        restored_wheel = next(restored.select(environment=ENVIRONMENT, tags=tags))[1]
        assert isinstance(restored_wheel, PackageWheel)
        assert restored_wheel.url == wheels[expected_index]["url"]


@given(pair=st.sampled_from(list(itertools.permutations(TAG_TEXTS, 2))))
@SETTINGS
def test_replacing_a_wheel_filename_rebuilds_derived_tags(
    pair: tuple[str, str],
) -> None:
    before, after = pair
    lock = lock_with_wheels(
        [
            {
                "name": f"demo-1.0-{before}.whl",
                "url": "https://example.test/wheel.whl",
                "hashes": {"sha256": "0" * 64},
            }
        ]
    )
    selected = list(
        lock.select(environment=ENVIRONMENT, tags=[Tag(*before.split("-"))])
    )
    wheel = selected[0][1]
    assert isinstance(wheel, PackageWheel)

    replacement = dataclasses.replace(wheel, name=f"demo-1.0-{after}.whl")
    package = dataclasses.replace(lock.packages[0], wheels=[replacement])
    changed_lock = dataclasses.replace(lock, packages=[package])
    new_tags = [Tag(*after.split("-"))]
    assert (
        next(changed_lock.select(environment=ENVIRONMENT, tags=new_tags))[1]
        is replacement
    )
    with pytest.raises(PylockSelectError):
        list(lock.select(environment=ENVIRONMENT, tags=new_tags))
