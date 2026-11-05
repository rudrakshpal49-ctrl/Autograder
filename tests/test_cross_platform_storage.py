"""Regression checks for submissions saved on Windows and read under WSL."""

import pytest

from services import storage_utils


@pytest.fixture
def private_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_utils, 'ROOT', tmp_path)
    monkeypatch.setattr(storage_utils, 'STORAGE_ROOT', tmp_path / 'storage')
    return tmp_path


def test_legacy_windows_submission_path_reads_under_wsl(private_storage):
    payload = b'#include <stdio.h>\nint main(void){return 0;}\n'
    path, digest = storage_utils.write_private('submissions', 'answer.c', payload)
    assert path.startswith('storage/submissions/')
    assert storage_utils.read_private(path.replace('/', '\\'), 'submissions') == payload
    assert storage_utils.read_private(path, 'submissions') == payload


def test_windows_saved_rules_and_documents(private_storage):
    for kind, filename, payload in (
        ('assignments', 'task.pdf', b'%PDF-1.4 dummy'),
        ('rules', 'rules.json', b'{"metrics": []}'),
        ('references', 'oracle.c', b'int main(void){return 0;}'),
    ):
        path, _ = storage_utils.write_private(kind, filename, payload)
        assert storage_utils.read_private(path.replace('/', '\\'), kind) == payload


def test_legacy_windows_path_can_be_deleted(private_storage):
    path, _ = storage_utils.write_private('submissions', 'answer.c', b'data')
    storage_utils.discard_private(path.replace('/', '\\'), 'submissions')
    assert not storage_utils.resolve_private(path, 'submissions').exists()


@pytest.mark.parametrize('unsafe', [
    r'storage\submissions\..\rules\secret.json',
    r'storage\submissions\..\..\private.txt',
    r'storage/assignments/other.pdf',
    r'C:\Users\someone\secret.c',
    r'\\server\share\other.c',
    r'../storage/submissions/../rules/other.json',
    '/tmp/outside.c',
    'storage/submissions/../../outside.c',
])
def test_unsafe_or_wrong_category_path_is_blocked(private_storage, unsafe):
    with pytest.raises(PermissionError, match='outside the permitted'):
        storage_utils.resolve_private(unsafe, 'submissions')


def test_symlink_escape_is_blocked(private_storage):
    allowed = private_storage / 'storage' / 'submissions'
    allowed.mkdir(parents=True)
    outside = private_storage / 'outside.c'
    outside.write_text('secret')
    link = allowed / 'linked.c'
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip('Symlinks are not supported on this host')
    with pytest.raises(PermissionError, match='outside the permitted'):
        storage_utils.read_private(r'storage\submissions\linked.c', 'submissions')
