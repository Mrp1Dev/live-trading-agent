#!/usr/bin/env python3
"""
Utility script to combine all relevant Python source files in the project
into a single annotated text/markdown file.

Usage:
    python combine_project.py
    python combine_project.py --output combined_codebase.txt
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
}

# Files to exclude from concatenation
IGNORED_FILES = {
    "combine_project.py",
    "setup.py",
}


def is_ignored_path(path: Path) -> bool:
    """Check if any part of the path belongs to ignored directories."""
    for part in path.parts:
        if part in IGNORED_DIRS or part.startswith(".venv") or part.startswith("venv"):
            return True
    return False


def collect_python_files(root_dir: Path) -> list[Path]:
    """Find all relevant .py files, ignoring virtualenvs, caches, and tooling."""
    py_files: list[Path] = []

    for path in sorted(root_dir.rglob("*.py")):
        if is_ignored_path(path.relative_to(root_dir)):
            continue
        if path.name in IGNORED_FILES:
            continue
        # Skip 0-byte __init__.py files, but keep meaningful ones
        if path.name == "__init__.py" and path.stat().st_size == 0:
            continue
        py_files.append(path)

    # Sort files logically: modules first, main.py last
    def sort_key(p: Path):
        rel = p.relative_to(root_dir).as_posix()
        if rel == "main.py":
            return (1, rel)
        return (0, rel)

    return sorted(py_files, key=sort_key)


def combine_files(root_dir: Path, output_file: Path) -> None:
    """Combine the selected python files into a single annotated output file."""
    files = collect_python_files(root_dir)

    if not files:
        print("No Python files found to combine.")
        return

    separator = "=" * 80
    total_lines = 0

    with open(output_file, "w", encoding="utf-8") as out:
        for file_path in files:
            rel_path = file_path.relative_to(root_dir).as_posix()
            content = file_path.read_text(encoding="utf-8")
            line_count = len(content.splitlines())
            total_lines += line_count

            header = f"{separator}\n# FILE: {rel_path}\n{separator}\n\n"
            out.write(header)
            out.write(content)
            out.write("\n\n")

            print(f"  + Added {rel_path} ({line_count} lines)")

    print(f"\nSuccessfully combined {len(files)} files ({total_lines:,} lines) into '{output_file.name}'")


def main():
    parser = argparse.ArgumentParser(
        description="Combine project Python source files into a single annotated file."
    )
    parser.add_argument(
        "-o",
        "--output",
        default="combined_codebase.txt",
        help="Target output file path (default: combined_codebase.txt)",
    )
    args = parser.parse_args()

    root_dir = Path(__file__).resolve().parent
    output_path = root_dir / args.output

    combine_files(root_dir, output_path)


if __name__ == "__main__":
    main()
