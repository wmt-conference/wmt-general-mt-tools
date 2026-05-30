#!/usr/bin/env python3

import argparse
import json
from pathlib import Path


def count_posts(path: Path) -> int:
    if path.suffix == ".jsonl":
        with path.open("r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return len(data)

    if isinstance(data, dict):
        for key in ["posts", "statuses", "items", "data"]:
            if isinstance(data.get(key), list):
                return len(data[key])

    raise ValueError(f"Could not infer number of posts in {path}")


def has_individual_png(folder: Path, basename: str, i: int, suffix: str) -> bool:
    candidates = [
        folder / f"{basename}_{i}{suffix}.png",
        folder / f"{basename}_extracted_{i}{suffix}.png",
        folder / f"{basename}_tmp_{i}{suffix}.png",
    ]
    return any(p.exists() for p in candidates)


def has_all_png(folder: Path, basename: str, suffix: str) -> bool:
    candidates = [
        folder / f"{basename}-all{suffix}.png",   # e.g. id-all-anon.png
        folder / f"{basename}{suffix}-all.png",   # e.g. id-anon-all.png
    ]
    return any(p.exists() for p in candidates)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", help="Folder containing .json/.jsonl files")
    parser.add_argument(
        "--anon",
        action="store_true",
        help="Check for files with -anon suffix"
    )
    args = parser.parse_args()

    root = Path(args.folder)
    suffix = "-anon" if args.anon else ""

    bad = []

    for path in sorted(list(root.glob("*.json")) + list(root.glob("*.jsonl"))):
        basename = path.stem
        folder = root / basename

        reasons = []

        if not folder.is_dir():
            reasons.append("missing folder")
            bad.append((basename, reasons))
            continue

        try:
            n_posts = count_posts(path)
        except Exception as e:
            reasons.append(f"could not count posts: {e}")
            bad.append((basename, reasons))
            continue

        missing_posts = [
            i for i in range(n_posts)
            if not has_individual_png(folder, basename, i, suffix)
        ]

        if missing_posts:
            reasons.append(f"missing individual PNGs: {missing_posts}")

        if not has_all_png(folder, basename, suffix):
            reasons.append("missing -all PNG")

        if reasons:
            bad.append((basename, reasons))

    for basename, reasons in bad:
        print(f"{basename}\t" + "; ".join(reasons))

    print(f"\nFound {len(bad)} problematic file(s).")


if __name__ == "__main__":
    main()
