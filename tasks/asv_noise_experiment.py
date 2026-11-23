"""Measure ASV repeatability across two commits with identical package source."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

PROFILES = ("wall", "affinity", "cpu+affinity", "cpu")


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("python_version")
    parser.add_argument("order", choices=("forward", "reverse"))
    parser.add_argument("output", type=Path)
    parser.add_argument("--bench")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--against", default="HEAD")
    args = parser.parse_args()

    base = git("rev-parse", "HEAD^")
    head = git("rev-parse", args.against)
    assert base != head
    assert git("rev-parse", f"{base}:benchmarks") == git(
        "rev-parse", f"HEAD:benchmarks"
    )
    if args.against == "HEAD":
        assert git("rev-parse", f"{base}:src") == git("rev-parse", f"{head}:src")

    args.output.mkdir(parents=True, exist_ok=True)
    cpu = max(os.sched_getaffinity(0))
    print(f"{base=} {head=} {cpu=} {args.python_version=}", flush=True)
    config = json.loads(Path("asv.conf.json").read_text())
    # Leave the normal ASV configuration untouched.
    run_config = args.output / "asv.conf.json"
    for key, default in [
        ("repo", "."),
        ("benchmark_dir", "benchmarks"),
        ("env_dir", "env"),
    ]:
        config[key] = str(Path(config.get(key, default)).resolve())

    modules = {
        path: path.read_text()
        for path in Path("benchmarks").glob("*.py")
        if "    rounds = 4\n" in path.read_text()
    }
    assert len(modules) == 8
    profiles = PROFILES if args.order == "forward" else PROFILES[::-1]
    for profile in profiles:
        print(profile, flush=True)
        config["results_dir"] = str((args.output / profile).resolve())
        run_config.write_text(json.dumps(config))
        command = [
            sys.executable,
            "-m",
            "asv",
            "continuous",
            "--config",
            str(run_config),
            base,
            head,
            "--python",
            args.python_version,
            "--interleave-rounds",
            "--sort",
            "default",
            "--no-only-changed",
        ]
        if "affinity" in profile:
            command.extend(["--cpu-affinity", str(cpu)])
        if args.bench:
            command.extend(["--bench", args.bench])
        if args.quick:
            command.append("--quick")

        started = time.monotonic()
        with (args.output / f"{profile}.log").open("w") as log:
            try:
                if profile.startswith("cpu"):
                    for path, source in modules.items():
                        path.write_text(
                            source.replace(
                                "    rounds = 4\n",
                                "    rounds = 4\n    timer = process_time\n",
                            ).replace(
                                "from __future__ import annotations\n",
                                "from __future__ import annotations\n\nfrom time import process_time\n",
                            )
                        )
                result = subprocess.run(
                    command, stdout=log, stderr=subprocess.STDOUT, check=False
                )
            finally:
                if profile.startswith("cpu"):
                    for path, source in modules.items():
                        path.write_text(source)

        elapsed = time.monotonic() - started
        log_text = (args.output / f"{profile}.log").read_text()
        if result.returncode not in {0, 1} or "All benchmarks:" not in log_text:
            print(log_text)
            raise RuntimeError(f"ASV failed with status {result.returncode}")
        (args.output / f"{profile}.seconds").write_text(f"{elapsed}\n")
        print(f"{profile} finished in {elapsed:.1f}s", flush=True)


if __name__ == "__main__":
    main()
