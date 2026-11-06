from __future__ import annotations

from pathlib import Path

from packaging.version import InvalidVersion, Version

from . import add_attributes

_COMPACT_NUMBER_MAX = 2**16 - 1


def full_version(value: Version) -> bool:
    """Select corpus values requiring the general component representation."""
    if value.epoch or value.local or len(value.release) > 4:
        return True
    numbers = (*value.release, value.post or 0, value.dev or 0)
    if value.pre is not None:
        numbers += (value.pre[1],)
    return any(number > _COMPACT_NUMBER_MAX for number in numbers)


class TimeFullVersionSuite:
    rounds = 4

    def setup(self) -> None:
        self.texts = []
        self.values = []
        for raw_text in (
            Path(__file__).with_name("version_sample.txt").read_text().splitlines()
        ):
            text = raw_text.strip()
            try:
                value = Version(text)
            except InvalidVersion:
                continue
            if full_version(value):
                self.texts.append(text)
                self.values.append(value)
        for value in self.values:
            hash(value)

    @add_attributes(pretty_name="General versions: construction and hash")
    def time_construct_and_hash(self) -> None:
        for text in self.texts:
            hash(Version(text))

    @add_attributes(pretty_name="General versions: str")
    def time_str(self) -> None:
        for value in self.values:
            str(value)

    @add_attributes(pretty_name="General versions: base_version")
    def time_base_version(self) -> None:
        for value in self.values:
            _ = value.base_version

    @add_attributes(pretty_name="General versions: public")
    def time_public(self) -> None:
        for value in self.values:
            _ = value.public
