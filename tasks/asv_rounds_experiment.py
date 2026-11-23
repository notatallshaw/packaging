"""Compare four and eight ASV rounds on identical package source."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

BENCHMARKS = (
    r"^(specifiers\..*(?:warm|filter_.*_cold)$|ranges\..*time_(?:contains|filter_.*)$|"
    r"markers\..*time_evaluate$|version\..*time_constructor$|pubgrub\.)"
)


def run_asv(
    config: Path, log: Path, command: str, *args: str, ok: tuple[int, ...] = (0,)
) -> None:
    """Keep the full output of one ASV invocation for verification."""
    invocation = [sys.executable, "-m", "asv", command, "--config", str(config), *args]
    with log.open("w") as stream:
        status = subprocess.run(
            invocation, stdout=stream, stderr=subprocess.STDOUT, check=False
        ).returncode
    if status not in ok or "All benchmarks:" not in log.read_text():
        print(log.read_text())
        raise subprocess.CalledProcessError(status, invocation)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("order", choices=("forward", "reverse"))
    parser.add_argument("output", type=Path)
    parser.add_argument("--python", action="append", dest="pythons")
    parser.add_argument("--bench", default=BENCHMARKS)
    args = parser.parse_args()

    git = shutil.which("git")
    if git is None:
        raise RuntimeError("Git is required")

    def git_hash(revision: str) -> str:
        return subprocess.check_output([git, "rev-parse", revision], text=True).strip()

    base = git_hash("HEAD^")
    head = git_hash("HEAD")
    for path in ("src", "benchmarks", "pyproject.toml"):
        if git_hash(f"{base}:{path}") != git_hash(f"{head}:{path}"):
            raise ValueError(f"Different benchmark inputs at {path}")

    config = json.loads(Path("asv.conf.json").read_text())
    if args.pythons:
        config["pythons"] = args.pythons
    for key, default in [
        ("repo", "."),
        ("benchmark_dir", "benchmarks"),
        ("env_dir", "env"),
    ]:
        config[key] = str(Path(config.get(key, default)).resolve())
    args.output.mkdir(parents=True)
    run_config = args.output / "asv.conf.json"

    profiles = ("four", "eight") if args.order == "forward" else ("eight", "four")
    for profile in profiles:
        print(f"Running {profile} rounds on {', '.join(config['pythons'])}", flush=True)
        config["results_dir"] = str((args.output / profile).resolve())
        run_config.write_text(json.dumps(config))
        options = [
            "--interleave-rounds",
            "--bench",
            args.bench,
            "--sort",
            "default",
            "--no-only-changed",
        ]
        if profile == "eight":
            options.extend(("-a", "rounds=8", "-a", "repeat=3"))
        started = time.monotonic()
        run_asv(
            run_config,
            args.output / f"{profile}.log",
            "continuous",
            base,
            head,
            *options,
            ok=(0, 1),
        )
        elapsed = time.monotonic() - started
        (args.output / f"{profile}.seconds").write_text(f"{elapsed}\n")
        print(f"{profile} finished in {elapsed:.1f}s", flush=True)


if __name__ == "__main__":
    main()
