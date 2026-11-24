"""Serialised runner: runs the pipeline stages in order and logs to run.log.

Stages run one at a time on purpose: the three upstream services (INPE, GBIF, EPA)
are slow and rate-limited, and parallel jobs simply halve each other's throughput.
Every stage is idempotent and caches its own raw payload, so re-running after a
failure resumes rather than restarts.

Usage
    python src/run_all.py                 # everything
    python src/run_all.py connectivity    # from that stage onwards
    python src/run_all.py --offline       # verify the bundled data, block all network
    python src/run_all.py --list          # show stage names

The repository ships every upstream payload under data/raw and the derived
tables under data/interim, so a normal run already prefers local files and only
downloads when one is missing.  --offline additionally sets
CARBON_LEDGER_OFFLINE=1, which turns any stray request into an immediate error.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(os.path.dirname(HERE), "run.log")

STAGES = [
    # data acquisition
    ("carbon", "s01_fetch_carbon.py"),
    ("units", "s01b_fetch_units.py"),
    ("national", "s10_fetch_national.py"),
    ("official-areas", "s02b_official_area.py"),
    ("layer-check", "s02c_annual_sweep.py"),
    ("landcover", "s02_fetch_landcover.py"),
    ("deter", "s03_fetch_deter.py"),
    ("species-fetch", "s04_fetch_gbif.py"),
    ("fire", "s11_fetch_fire.py"),
    # analysis
    ("connectivity", "s05_connectivity.py"),
    ("species-analysis", "s06_species.py"),
    ("stats", "s09_stats.py"),
    # publication
    ("database", "s07_build_db.py"),
    ("figures", "s08_figures.py"),
    ("validate", "s12_validate.py"),
]


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if "--list" in sys.argv:
        for name, script in STAGES:
            print(f"  {name:18s} {script}")
        return 0

    offline = "--offline" in sys.argv
    if offline:
        root = os.path.dirname(HERE)
        for sub in ("data/raw", "data/interim"):
            d = os.path.join(root, *sub.split("/"))
            n, size = 0, 0
            for base, _dirs, files in os.walk(d):
                for f in files:
                    n += 1
                    size += os.path.getsize(os.path.join(base, f))
            print(f"[offline] {sub}: {n} files, {size/1e6:.0f} MB present locally")
            if n == 0:
                print(f"[offline] {sub} is empty - refusing to start",
                      file=sys.stderr)
                return 2
        print("[offline] network calls are blocked for every stage")

    stages = STAGES
    if args:
        names = [s[0] for s in STAGES]
        if args[0] not in names:
            print(f"unknown stage {args[0]!r}; known: {', '.join(names)}")
            return 2
        stages = STAGES[names.index(args[0]):]

    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    if offline:
        env["CARBON_LEDGER_OFFLINE"] = "1"
    with open(LOG, "a", encoding="utf-8") as log:
        log.write(f"\n===== run started {time.strftime('%Y-%m-%d %H:%M:%S')} "
                  f"({len(stages)} stages) =====\n")
        log.flush()
        for name, script in stages:
            log.write(f"\n--- {name}: {script} ---\n")
            log.flush()
            t0 = time.time()
            p = subprocess.Popen([sys.executable, script], cwd=HERE, env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding="utf-8", errors="replace",
                                 bufsize=1)
            for line in p.stdout:
                log.write(line)
                log.flush()
                print(line, end="")
            rc = p.wait()
            log.write(f"--- {name} finished rc={rc} in {time.time()-t0:.0f}s ---\n")
            log.flush()
            if rc != 0:
                log.write("stopping: stage failed\n")
                return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
