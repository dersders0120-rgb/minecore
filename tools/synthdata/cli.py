from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from synthdata.config import Config
from synthdata.holes import generate
from synthdata.writer import write


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="synth_drillholes")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--holes", type=int, default=200)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dialect", choices=("std", "tr", "tr1254"), default="std")
    parser.add_argument("--headers", choices=("canonical", "short", "turkish"), default="canonical")
    parser.add_argument("--errors", choices=("none", "standard", "all"), default="none")
    args = parser.parse_args(argv)

    if args.holes <= 0:
        parser.error("--holes must be positive")

    config = Config(seed=args.seed, holes=args.holes, errors=args.errors)
    dataset = generate(config)
    write(dataset, args.out, dialect=args.dialect, header_style=args.headers)
    return 0
