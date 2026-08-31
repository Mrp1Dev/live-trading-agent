#!/usr/bin/env python3
"""
Utility script to combine all relevant Python source files in the project
into annotated text/markdown files (split into parts, default 2).

Usage:
    python combine_project.py
    python combine_project.py -e
    python combine_project.py --parts 2
    python combine_project.py --parts 1
    python combine_project.py --output combined_codebase.txt
    python combine_project.py --no-empty-lines
"""

import argparse
import os
from pathlib import Path

# Directories to exclude from scanning
IGNORED_DIRS = {
    ".venv",
    "venv",
    "env",
    ".env",
    "__pycache__",
    ".git",
    ".github",
    ".vscode",
    ".idea",
    ".agents",
    ".pytest_cache",
    ".mypy_cache",
    "build",
    "dist",
    "site-packages",
    "node_modules",
    "backtest",
    "tests",
    "test",
}

# Files to exclude from concatenation
IGNORED_FILES = {
    "combine_project.py",
    "setup.py",
    "backtest_main.py",
}

# Filename prefixes to exclude (e.g. test files, diagnostic scripts)
IGNORED_PREFIXES = (
    "test_",
    "diagnose_",
    "inspect_",
    "backtest_",
)


def is_ignored_path(path: Path) -> bool:
    """Check if any part of the path belongs to ignored directories."""
    for part in path.parts:
        if part in IGNORED_DIRS or part.startswith(".venv") or part.startswith("venv"):
            return True
    return False


def collect_python_files(root_dir: Path) -> list[Path]:
    """Find all relevant .py files, keeping only the core pipeline."""
    py_files: list[Path] = []

    for path in sorted(root_dir.rglob("*.py")):
        rel = path.relative_to(root_dir)
        if is_ignored_path(rel):
            continue
        if path.name in IGNORED_FILES:
            continue
        if path.name.startswith(IGNORED_PREFIXES) or path.name.endswith("_test.py"):
            continue
        # Skip completely empty files (0 bytes)
        if path.stat().st_size == 0:
            continue
        # Skip 0-byte or whitespace-only __init__.py files
        if path.name == "__init__.py" and not path.read_text(encoding="utf-8").strip():
            continue
        py_files.append(path)

    # Sort files logically: config -> alpaca_client -> strategy -> risk -> main.py
    def sort_key(p: Path):
        rel = p.relative_to(root_dir).as_posix()
        if rel == "config.py":
            return (0, rel)
        if rel.startswith("alpaca_client/"):
            return (1, rel)
        if rel.startswith("strategy/"):
            return (2, rel)
        if rel.startswith("risk/"):
            return (3, rel)
        if rel == "main.py":
            return (9, rel)
        return (4, rel)

    return sorted(py_files, key=sort_key)


def get_file_content_and_lines(file_path: Path, no_empty_lines: bool) -> tuple[str, int]:
    """Read a file and return formatted content along with its line count."""
    content = file_path.read_text(encoding="utf-8")
    if no_empty_lines:
        lines = [line for line in content.splitlines() if line.strip()]
        return "\n".join(lines), len(lines)
    return content, len(content.splitlines())


def partition_files(
    file_items: list[tuple[Path, str, int]], num_parts: int
) -> list[list[tuple[Path, str, int]]]:
    """Partition ordered file items into contiguous groups balancing line count."""
    n = len(file_items)
    if n == 0:
        return []
    if num_parts <= 1 or n <= 1:
        return [file_items]

    num_parts = min(num_parts, n)
    target = sum(cnt for _, _, cnt in file_items) / num_parts

    best_cost = float("inf")
    best_partition = None

    def search(idx: int, parts_left: int, current_partition: list[list[tuple[Path, str, int]]], cost: float):
        nonlocal best_cost, best_partition
        if parts_left == 1:
            last_part = file_items[idx:]
            part_sum = sum(cnt for _, _, cnt in last_part)
            total_cost = cost + (part_sum - target) ** 2
            if total_cost < best_cost:
                best_cost = total_cost
                best_partition = current_partition + [last_part]
            return

        for next_idx in range(idx + 1, n - parts_left + 2):
            part = file_items[idx:next_idx]
            part_sum = sum(cnt for _, _, cnt in part)
            new_cost = cost + (part_sum - target) ** 2
            if new_cost < best_cost:
                search(next_idx, parts_left - 1, current_partition + [part], new_cost)

    search(0, num_parts, [], 0.0)
    return best_partition if best_partition is not None else [file_items]


def combine_files(
    root_dir: Path,
    output_file: Path,
    num_parts: int = 2,
    no_empty_lines: bool = False,
) -> None:
    """Combine selected python files into one or more annotated output files."""
    files = collect_python_files(root_dir)

    if not files:
        print("No Python files found to combine.")
        return

    # Pre-read files to get contents and accurate line counts
    file_items = [
        (fp, *get_file_content_and_lines(fp, no_empty_lines))
        for fp in files
    ]

    num_parts = max(1, num_parts)
    parts = partition_files(file_items, num_parts)
    actual_num_parts = len(parts)

    separator = "=" * 5
    written_files: list[tuple[Path, int, int]] = []
    total_all_lines = sum(cnt for _, _, cnt in file_items)

    for i, part in enumerate(parts, start=1):
        if actual_num_parts == 1:
            part_out_path = output_file
        else:
            stem = output_file.stem
            suffix = output_file.suffix
            part_out_path = output_file.parent / f"{stem}_part{i}{suffix}"

        part_lines = sum(cnt for _, _, cnt in part)
        print(f"\n--- Part {i}/{actual_num_parts}: {part_out_path.name} ({len(part)} files, {part_lines:,} lines) ---")

        with open(part_out_path, "w", encoding="utf-8") as out:
            for file_path, content, line_count in part:
                rel_path = file_path.relative_to(root_dir).as_posix()
                if no_empty_lines:
                    header = f"{separator}\n# FILE: {rel_path}\n{separator}\n"
                    out.write(header)
                    if content:
                        out.write(content + "\n")
                else:
                    header = f"{separator}\n# FILE: {rel_path}\n{separator}\n\n"
                    out.write(header)
                    out.write(content)
                    out.write("\n\n")

                print(f"  + Added {rel_path} ({line_count} lines)")

        written_files.append((part_out_path, len(part), part_lines))

    if actual_num_parts == 1:
        print(f"\nSuccessfully combined {len(files)} files ({total_all_lines:,} lines) into '{output_file.name}'")
    else:
        print(f"\nSuccessfully combined {len(files)} files ({total_all_lines:,} lines) into {actual_num_parts} parts:")
        for path, file_cnt, line_cnt in written_files:
            print(f"  - {path.name} ({file_cnt} files, {line_cnt:,} lines)")


def main():
    parser = argparse.ArgumentParser(
        description="Combine project Python source files into annotated files (split into parts, default 2)."
    )
    parser.add_argument(
        "-o",
        "--output",
        default="combined_codebase.txt",
        help="Base target output file path (default: combined_codebase.txt)",
    )
    parser.add_argument(
        "-p",
        "--parts",
        type=int,
        default=2,
        help="Number of parts to split the codebase into (default: 2). Set to 1 for a single file.",
    )
    parser.add_argument(
        "-e",
        "--no-empty-lines",
        "--eliminate-empty-lines",
        dest="no_empty_lines",
        action="store_true",
        help="Eliminate empty/blank lines from the output files.",
    )
    args = parser.parse_args()

    root_dir = Path(__file__).resolve().parent
    output_path = root_dir / args.output

    combine_files(
        root_dir=root_dir,
        output_file=output_path,
        num_parts=args.parts,
        no_empty_lines=args.no_empty_lines,
    )


if __name__ == "__main__":
    main()

