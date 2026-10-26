from __future__ import annotations

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from . import add_attributes


class TimeResolverSuite:
    rounds = 4

    def setup(self) -> None:
        versions = [
            "1.0.0",
            "1.0.0.post1",
            "1.0.1",
            "1.2.0",
            "1.2.5",
            "1.2.7",
            "1.3.0",
            "1.3.1",
            "2.0.0",
            "2.1.0",
            "2.1.1",
            "3.0.0",
        ]
        specifiers = [
            ">=1.0.0,<2.0.0",
            ">=1.2.0,!=1.2.5,<3.0.0",
            ">=2.0.0,<3.0.0",
            ">=1.0.0,<=1.3.0",
            "==1.2.*",
            ">=1.0.0.post1,<2.0.0",
            ">=1.3.0,<2.0.0",
            ">=1.0.0,!=1.3.1,<3.0.0",
            ">=2.1.0,<3.0.0",
            "==1.0.0",
        ]

        self.versions = [Version(version) for version in versions]
        self.specifiers = [SpecifierSet(specifier) for specifier in specifiers]

    @add_attributes(pretty_name="Resolver-style loop")
    def time_resolver_loop(self) -> None:
        for spec in self.specifiers:
            candidates = [v for v in self.versions if v in spec]
            if candidates:
                max(candidates)
