"""Reproducible paper-inspired research; collect is the only network stage."""
from __future__ import annotations
import argparse
import json
from data_inputs import HERE, collect, fetch_upstream


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, choices=["collect", "upstream", "run", "verify"])
    args = parser.parse_args()
    config = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
    if args.stage == "collect":
        collect(config)
    elif args.stage == "upstream":
        fetch_upstream(config)
    elif args.stage == "run":
        from experiments import run
        run(config)
    elif args.stage == "verify":
        from experiments import verify
        verify(config)


if __name__ == "__main__":
    main()
