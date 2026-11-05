"""Safety tests for local source-cache cleanup."""
from pathlib import Path

from tools.clean_generated_files import find_caches


def test_only_generated_caches_are_selected(tmp_path: Path):
    (tmp_path / 'agent' / '__pycache__').mkdir(parents=True)
    (tmp_path / 'agent' / '__pycache__' / 'a.pyc').write_bytes(b'cache')
    (tmp_path / 'agent' / 'a.py').write_text('print(1)')
    (tmp_path / 'loose.pyo').write_bytes(b'cache')
    (tmp_path / '.pytest_cache').mkdir()
    candidates = {p.relative_to(tmp_path).as_posix() for p in find_caches(tmp_path)}
    assert candidates == {'agent/__pycache__', 'loose.pyo', '.pytest_cache'}


def test_private_directories_and_virtualenv_are_never_scanned(tmp_path: Path):
    for name in ('data', 'storage', '.git', '.venv', '.venv-wsl'):
        folder = tmp_path / name / '__pycache__'
        folder.mkdir(parents=True)
        (folder / 'a.pyc').write_bytes(b'secret')
    assert find_caches(tmp_path) == []


def test_symlink_to_external_cache_is_ignored(tmp_path: Path):
    outside = tmp_path.parent / (tmp_path.name + '_outside')
    outside.mkdir()
    (outside / 'a.pyc').write_bytes(b'cache')
    (tmp_path / '__pycache__').symlink_to(outside, target_is_directory=True)
    assert find_caches(tmp_path) == []


def test_bad_root_rejected(tmp_path: Path):
    try:
        find_caches(tmp_path / 'nonexistent')
    except ValueError:
        pass
    else:
        raise AssertionError('Missing root should be rejected')
