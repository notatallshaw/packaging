"""Collect the same ASV samples for the median and lower-quartile comparisons."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

RANGES = r"ranges\..*time_(?:constructor_warm|singleton)$"
REQUIREMENT = r"requirement\..*time_constructor$"
UNCHANGED = (
    r"^(ranges\..*time_filter_.*|specifiers\..*time_(?:eq_specifier_warm|"
    r"filter_.*simple.*|filter_compatible_warm)|version\..*time_constructor)$"
)

# The first two pairs reverse optimizations. The third measures a regression
# introduced by changing the prerelease admission rules.
REGRESSIONS = (
    ("requirement", "72f951f", "72f951f^", REQUIREMENT),
    ("range-build", "6a69a83", "6a69a83^", RANGES),
    ("prerelease", "cd91074^", "cd91074", RANGES),
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("order", choices=("forward", "reverse"))
    parser.add_argument("output", type=Path)
    parser.add_argument("--python", action="append", dest="pythons")
    args = parser.parse_args()

    git = shutil.which("git")
    if git is None:
        parser.error("git must be installed")

    def rev_parse(ref: str) -> str:
        return subprocess.check_output([git, "rev-parse", ref], text=True).strip()

    for path in ("src", "benchmarks", "pyproject.toml", "asv.conf.json"):
        if rev_parse(f"HEAD^:{path}") != rev_parse(f"HEAD:{path}"):
            raise ValueError(f"The unchanged-source pair differs in {path}")

    config = json.loads(Path("asv.conf.json").read_text(encoding="utf-8"))
    if args.pythons:
        config["pythons"] = args.pythons
    for key, path in (
        ("repo", "."),
        ("benchmark_dir", "benchmarks"),
        ("env_dir", "env"),
    ):
        config[key] = str(Path(config.get(key, path)).resolve())

    args.output.mkdir(parents=True)
    config_path = args.output / "asv.conf.json"
    cases = (("unchanged", "HEAD^", "HEAD", UNCHANGED), *REGRESSIONS)
    if args.order == "reverse":
        cases = cases[::-1]

    for name, base_ref, head_ref, benchmark in cases:
        base, head = rev_parse(base_ref), rev_parse(head_ref)
        print(f"Running {name}: {base} to {head}", flush=True)
        config["results_dir"] = str((args.output / name).resolve())
        config_path.write_text(json.dumps(config), encoding="utf-8")
        invocation = [
            sys.executable,
            "-m",
            "asv",
            "continuous",
            "--config",
            str(config_path),
            base,
            head,
            "--interleave-rounds",
            "--no-only-changed",
            "--sort",
            "default",
            "--bench",
            benchmark,
        ]
        log_path = args.output / f"{name}.log"
        with log_path.open("w", encoding="utf-8") as log:
            result = subprocess.run(
                invocation, stdout=log, stderr=subprocess.STDOUT, check=False
            )

        output = log_path.read_text(encoding="utf-8")
        if result.returncode not in (0, 1) or "All benchmarks:" not in output:
            print(output)
            raise RuntimeError(f"ASV comparison failed for {name}")


if __name__ == "__main__":
    main()
