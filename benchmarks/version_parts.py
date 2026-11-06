from __future__ import annotations

import json
from pathlib import Path

from packaging.version import Version

from . import add_attributes


def decode_parts(parts: dict[str, object]) -> dict[str, object]:
    """Restore tuple components from captured JSON keyword arguments."""
    for name in ("release", "pre"):
        component = parts.get(name)
        if isinstance(component, list):
            parts[name] = tuple(component)
    return parts


class TimeVersionPartsSuite:
    rounds = 4

    def setup(self) -> None:
        captured = json.loads(
            Path(__file__).with_name("version_parts_sample.json").read_text()
        )
        self.parts = [decode_parts(row["parts"]) for row in captured["from_parts"]]
        self.replacements = [
            (Version(row["version"]), decode_parts(row["parts"]))
            for row in captured["replace"]
        ]

    @add_attributes(pretty_name="Version.from_parts (captured workload)")
    def time_from_parts(self) -> None:
        for parts in self.parts:
            Version.from_parts(**parts)

    @add_attributes(pretty_name="Version.__replace__ (captured workload)")
    def time_replace(self) -> None:
        for version, parts in self.replacements:
            version.__replace__(**parts)
