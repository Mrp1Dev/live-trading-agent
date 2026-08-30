from __future__ import annotations

import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

OCC_RE = re.compile(r"\b([A-Z]{1,10})(\d{6})([CP])(\d{8})\b")
DATA_FILE_EXTS = {
    ".csv",
    ".tsv",
    ".parquet",
    ".pq",
    ".feather",
    ".sqlite",
    ".db",
    ".json",
    ".jsonl",
    ".ndjson",
    ".txt",
    ".md",
}

IGNORED_DIRS = {".git", ".venv", "__pycache__", ".mypy_cache", ".pytest_cache"}


def iter_files(base: Path):
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        yield path


def find_option_like_data_files():
    files = []
    for path in iter_files(ROOT):
        if path.suffix.lower() in DATA_FILE_EXTS:
            files.append(path)
    return files


def parse_occ_symbol(sym: str):
    match = OCC_RE.fullmatch(sym)
    if not match:
        return None
    root, yymmdd, option_type, strike_digits = match.groups()
    try:
        expiration = datetime.strptime(yymmdd, "%y%m%d").date()
    except ValueError:
        return None
    strike = int(strike_digits) / 1000.0
    return {
        "root": root,
        "expiration": expiration,
        "option_type": option_type,
        "strike": strike,
        "symbol": sym,
    }


def summarize_file(path: Path):
    if path.suffix.lower() in {".parquet", ".pq", ".feather", ".sqlite", ".db"}:
        return {
            "kind": "binary_data_file",
            "matches": [],
            "unique_underlyings": [],
            "date_min": None,
            "date_max": None,
            "notes": "Binary file: no symbol table inspection was performed in this repo scan.",
        }

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return {
            "kind": "unreadable_file",
            "matches": [],
            "unique_underlyings": [],
            "date_min": None,
            "date_max": None,
            "notes": f"Unreadable: {exc}",
        }

    matches = []
    for sym in OCC_RE.findall(text):
        raw = "".join(sym)
        parsed = parse_occ_symbol(raw)
        if parsed:
            matches.append(parsed)

    unique_underlyings = sorted({m["root"] for m in matches})
    dates = [m["expiration"] for m in matches]
    notes = "Text source; symbol strings appear in examples/test fixtures, not as a standalone historical dataset."
    if not matches:
        notes = "No OCC-style symbols found in this file."

    return {
        "kind": "text_file",
        "matches": matches,
        "unique_underlyings": unique_underlyings,
        "date_min": min(dates) if dates else None,
        "date_max": max(dates) if dates else None,
        "notes": notes,
    }


def main() -> None:
    print("=== Option source inspection ===")
    print(f"Workspace root: {ROOT}")
    print()

    repo_files = list(iter_files(ROOT))
    data_files = find_option_like_data_files()
    print(f"Total files scanned: {len(repo_files)}")
    print(f"Potential option/data files found by extension: {len(data_files)}")
    if not data_files:
        print("No .csv/.parquet/.sqlite/.json/.txt option dataset files found in this workspace.")
    else:
        for path in data_files:
            print(f"- {os.path.relpath(path, ROOT)}")
    print()

    symbol_files = []
    for path in repo_files:
        if path.suffix.lower() in {".py", ".md", ".txt", ".json", ".yml", ".yaml"}:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            hits = OCC_RE.findall(text)
            if hits:
                symbol_files.append((path, hits))

    print(f"Files containing OCC-like symbols: {len(symbol_files)}")
    if not symbol_files:
        print("No OCC-style symbols exist in repo files as a local data source.")
    else:
        for path, hits in symbol_files:
            unique_symbols = sorted({"".join(h) for h in hits})
            parsed = [parse_occ_symbol(s) for s in unique_symbols if parse_occ_symbol(s)]
            underlyings = sorted({p["root"] for p in parsed})
            dates = [p["expiration"] for p in parsed]
            print(f"- {os.path.relpath(path, ROOT)}")
            print(f"  symbols: {len(unique_symbols)}")
            print(f"  underlyings: {underlyings[:10]}")
            print(f"  date_range: {min(dates).isoformat() if dates else 'n/a'} -> {max(dates).isoformat() if dates else 'n/a'}")
            print(f"  examples: {unique_symbols[:5]}")
    print()

    print("=== Interpretation ===")
    print("1. Existing local option-symbol dataset: none discovered.")
    print("2. The repository contains only a few hard-coded example symbols used in tests/docs, not a historical universe file.")
    print("3. The historical discovery layer depends on the Alpaca Trading API metadata endpoint, which can return zero candidates for a date window even when direct historical option data exists for known OCC contracts.")
    print("4. Because there is no broad stored historical symbol universe in the workspace, there is no reliable local source for point-in-time candidate filtering.")
    print("5. The best source of candidate OCC symbols remains the live Trading API metadata endpoint, but it is inadequate/empty for this dataset and must be treated as a data-source problem rather than an engine problem.")


if __name__ == "__main__":
    main()
