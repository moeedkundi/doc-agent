"""Generate the synthetic dataset.

uv run python -m evals.generator [--seed N] [--out evals/dataset]
"""

import argparse
from collections import Counter
from pathlib import Path

from evals.generator.dataset import DEFAULT_SEED, generate


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic POs + invoices.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=Path("evals/dataset"))
    args = parser.parse_args()
    truths = generate(args.out, args.seed)
    by = Counter((t.split, t.doc_type) for t in truths)
    print(f"wrote {len(truths)} documents to {args.out} (seed {args.seed})")
    for (split, doc_type), n in sorted(by.items()):
        print(f"  {split:<7} {doc_type:<15} {n}")


if __name__ == "__main__":
    main()
