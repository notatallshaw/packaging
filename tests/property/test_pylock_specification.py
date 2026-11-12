# This file is dual licensed under the terms of the Apache License, Version
# 2.0, and the BSD License. See the LICENSE file in the root of this repository
# for complete details.

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from packaging.markers import default_environment
from packaging.pylock import PackageWheel, Pylock, PylockSelectError
from packaging.tags import Tag

from .strategies import SETTINGS

pytestmark = pytest.mark.property

PLATFORMS = ["win_amd64", "manylinux_2_17_x86_64"]
WHEEL_TAGS = ["py3-none-any", *(f"cp312-cp312-{platform}" for platform in PLATFORMS)]


class TestWheelSelection:
    """The index of the tag in the supported tags list breaks the tie

    https://packaging.python.org/en/latest/specifications/platform-compatibility-tags/#use

    Without a compatible wheel or sdist:
    "an error MUST be raised about a lack of source for the project."

    https://packaging.python.org/en/latest/specifications/pylock-toml/#installation
    """

    @given(
        platform=st.sampled_from(PLATFORMS),
        wheel_tags=st.lists(st.sampled_from(WHEEL_TAGS), min_size=1, unique=True),
        data=st.data(),
    )
    @SETTINGS
    def test_compatible_wheel_selection(
        self, platform: str, wheel_tags: list[str], data: st.DataObject
    ) -> None:
        compatible = [f"cp312-cp312-{platform}", "py3-none-any"]
        preferences = data.draw(
            st.lists(st.sampled_from(compatible), unique=True),
            label="supported tags in preference order",
        )
        environment = default_environment()
        environment["python_full_version"] = "3.12.0"
        environment["python_version"] = "3.12"
        environment["sys_platform"] = "win32" if platform == "win_amd64" else "linux"

        wheels = [
            {
                "name": f"demo-1.0-{tag}.whl",
                "url": f"https://example.test/{index}.whl",
                "hashes": {"sha256": "0" * 64},
            }
            for index, tag in enumerate(wheel_tags)
        ]
        lock = Pylock.from_dict(
            {
                "lock-version": "1.0",
                "created-by": "test",
                "packages": [{"name": "demo", "version": "1.0", "wheels": wheels}],
            }
        )
        tags = [Tag(*text.split("-")) for text in preferences]
        candidates = [
            (preferences.index(tag), index)
            for index, tag in enumerate(wheel_tags)
            if tag in preferences
        ]

        if not candidates:
            with pytest.raises(PylockSelectError):
                list(lock.select(environment=environment, tags=tags))
            return

        expected_index = min(candidates)[1]
        selected = list(lock.select(environment=environment, tags=tags))
        assert len(selected) == 1
        wheel = selected[0][1]
        assert isinstance(wheel, PackageWheel)
        assert wheel.url == wheels[expected_index]["url"]
