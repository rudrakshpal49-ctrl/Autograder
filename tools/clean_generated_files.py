"""Remove generated Python/test caches, never database or uploaded submissions.

Run from the project root:
    python tools/clean_generated_files.py             # dry-run
    python tools/clean_generated_files.py --apply     # actual removal

Designed to operate only inside this source checkout. It skips all private
storage, the SQLite database, version-control metadata and virtual environments.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIRS = frozenset({'__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache'})
SKIP_DIRS = frozenset({'data', 'storage', '.git', '.venv', '.venv-wsl', 'venv', 'env', 'node_modules'})
BYTECODE_EXTENSIONS = frozenset({'.pyc', '.pyo'})


def find_caches(root: Path) -> list[Path]:
    """Return cache paths inside *root* without crossing symbolic links."""
    if not root.is_dir() or root.is_symlink():
        raise ValueError('Expected a real project directory.')
    candidates: list[Path] = []
    for folder, dirs, files in os.walk(root, followlinks=False):
        parent = Path(folder)
        kept = []
        for name in dirs:
            path = parent / name
            if path.is_symlink() or name in SKIP_DIRS:
                continue
            if name in CACHE_DIRS:
                candidates.append(path)
            else:
                kept.append(name)
        dirs[:] = kept
        for name in files:
            path = parent / name
            if not path.is_symlink() and path.suffix in BYTECODE_EXTENSIONS:
                candidates.append(path)
    return sorted(candidates)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Remove detected caches (dry-run by default).')
    args = parser.parse_args()
    candidates = find_caches(ROOT)
    if not candidates:
        print('No generated caches found. Private data was not examined.')
        return 0
    print(f'{"Removing" if args.apply else "Would remove"} {len(candidates)} cache item(s):')
    for path in candidates:
        print(' -', path.relative_to(ROOT))
        if args.apply:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    if not args.apply:
        print('Dry run only. Add --apply to delete these caches.')
    print('data/, storage/, virtual environments, and .git/ were left untouched.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
