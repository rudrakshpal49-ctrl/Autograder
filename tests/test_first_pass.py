"""First-pass tests for the native GCC backend (trusted samples only)."""
from datetime import datetime, timedelta
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from agent import first_pass, sandbox
from agent.static_sanity import analyze_static_sanity
from database.db import Base
from database.orm_models import FirstPassResult
from services import (
    authentication, assignment_service, student_service,
    submission_service, storage_utils, first_pass_service,
)
from services.dates import LOCAL_ZONE
from services.rules_service import example_rules_bytes


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "grade.db"}')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    for module in (authentication, assignment_service, student_service,
                   submission_service, first_pass_service):
        monkeypatch.setattr(module, 'SessionLocal', factory)
    monkeypatch.setattr(storage_utils, 'ROOT', tmp_path)
    monkeypatch.setattr(storage_utils, 'STORAGE_ROOT', tmp_path / 'storage')
    teacher = authentication.create_user('teacher1', 'teacher-pass-123', 'teacher')
    student = student_service.create_student(teacher, 'stu1', 'student-pass-123', 'Student One')
    assignment = assignment_service.create_assignment(
        teacher, 'Addition', 'Add two integers', datetime.now(LOCAL_ZONE) + timedelta(days=1),
        'assignment.txt', b'Add numbers', 'rules.json', example_rules_bytes(), '5 7\n')
    submission = submission_service.save_submission(
        student, assignment, 'add.c',
        b'#include <stdio.h>\nint main(void) { int a,b; scanf("%d%d",&a,&b); printf("%d\\n",a+b); return 0; }\n'
    )['submission_id']
    return teacher, student, submission, factory


def test_static_sanity_strips_comments_and_strings():
    code = '/* int main(){scanf("x");} */\nchar *s="printf(";\n'
    result = analyze_static_sanity(code)
    assert result['status'] == 'failed'
    assert result['checks']['input_reader'] is False
    good = 'int main(void){ int x=getchar(); putchar(x); return 0; }'
    assert analyze_static_sanity(good)['status'] == 'passed'
    no_explicit_return = 'int main(void) { puts("ok"); }'
    assert analyze_static_sanity(no_explicit_return)['status'] == 'warning'


def test_missing_gcc_reports_unavailable(monkeypatch):
    monkeypatch.setattr(sandbox, 'check_environment', lambda: (False, 'GCC missing', None))
    result = first_pass.evaluate_source(b'int main(void){return 0;}', sample_input='')
    assert result['status'] == 'unavailable'
    assert result['compiler']['status'] == 'unavailable'
    assert result['static_sanity']['checks']['main'] is True
    assert result['execution_backend'] == 'native_gcc'


def test_compile_smoke_commands_and_timeout(monkeypatch):
    monkeypatch.setattr(sandbox, 'check_environment', lambda: (True, 'ok', 'gcc fake'))
    monkeypatch.setattr(sandbox.shutil, 'which', lambda command: 'C:/gcc/bin/gcc.exe')
    calls = []

    def fake_invoke(command, *, workdir, input_file, wall_timeout, memory_limit_mb):
        calls.append((command, workdir, input_file, wall_timeout, memory_limit_mb))
        if '-std=c11' in command:
            from pathlib import Path
            Path(command[command.index('-o') + 1]).write_bytes(b'fake executable')
            return {'returncode': 0, 'stdout': '', 'stderr': 'student.c: warning: unused\n',
                    'wall_time_ms': 20, 'timed_out': False, 'output_limit': False,
                    'memory_limit': False, 'peak_rss_mb': 8}
        return {'returncode': -9, 'stdout': '', 'stderr': '',
                'wall_time_ms': 1000, 'timed_out': True, 'output_limit': False,
                'memory_limit': False, 'peak_rss_mb': 3}

    monkeypatch.setattr(sandbox, '_invoke', fake_invoke)
    report = sandbox.compile_and_smoke(b'int main(void){return 0;}', '5 7\n')
    assert report['compiler']['status'] == 'passed'
    assert report['compiler']['warning_count'] == 1
    assert report['smoke']['status'] == 'timeout'
    assert report['compiler_id'] == 'gcc fake'
    assert len(calls) == 2
    assert calls[0][4] == sandbox.COMPILE_MEMORY_MB
    assert calls[1][4] == 64
    assert calls[1][2] is not None
    assert 'docker' not in str(calls)


def test_persist_permissions_and_remove_report(workspace, monkeypatch):
    teacher, student, submission, factory = workspace
    report = {'status': 'ready', 'compiler': {'status': 'passed'},
              'smoke': {'status': 'passed'}, 'static_sanity': {'status': 'passed'},
              'image_id': 'sha256:abc', 'source_hash': 'xyz', 'rules_hash': 'abc'}
    monkeypatch.setattr(first_pass_service, 'evaluate_source', lambda *a, **kw: report)
    assert first_pass_service.run_first_pass(student, submission) == {'submission_id': submission}
    assert first_pass_service.get_first_pass(teacher, submission) == report
    assert first_pass_service.run_first_pass(teacher, submission) == report
    with pytest.raises(PermissionError):
        first_pass_service.get_first_pass(student, submission)
    student_submissions = submission_service.get_student_submissions(student)
    assert student_submissions[0]['id'] == submission
    assert 'source_hash' not in student_submissions[0]
    with factory() as session:
        assert session.scalar(select(FirstPassResult).where(
            FirstPassResult.submission_id == submission)) is not None
    another = authentication.create_user('teacher2', 'teacher2-pass-123', 'teacher')
    with pytest.raises(PermissionError):
        first_pass_service.get_first_pass(another, submission)
    submission_service.delete_submission(teacher, submission)
    with factory() as session:
        assert session.scalar(select(FirstPassResult)) is None


def test_student_upload_result_does_not_expose_hash_or_storage_path(workspace):
    teacher, student, _submission, _factory = workspace
    assignment_id = assignment_service.get_all_assignments(teacher)[0]['id']
    result = submission_service.save_submission(
        student, assignment_id, 'another.c', b'int main(void) { return 0; }')
    assert set(result) == {'submission_id', 'assignment_id'}
    teacher_record = submission_service.get_teacher_submissions(teacher)[0]
    assert 'source_hash' in teacher_record
