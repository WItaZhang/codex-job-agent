"""Run with ``python -m applypilot_agent.evaluation --config <yaml>``."""

import argparse
import sys

from .runner import run_evaluation


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate externally labelled application-agent observations.")
    parser.add_argument("--config", required=True, help="YAML config; paths are relative to this file")
    args = parser.parse_args()
    try:
        report = run_evaluation(args.config)
    except (OSError, ValueError) as exc:
        print(f"Evaluation failed: {exc}", file=sys.stderr)
        return 2
    return 0 if report["versions"]["candidate"]["gates"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
