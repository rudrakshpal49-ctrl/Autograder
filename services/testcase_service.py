"""Teacher-only PDF-driven test generation, optional inputs, and fixed test-set approval."""
import hashlib
import json
from pathlib import Path
from sqlalchemy import select
from database.db import SessionLocal
from database.orm_models import Assignment
from services.authentication import require_role
from services.rules_service import validate_rules
from services.storage_utils import read_private, write_private, discard_private
from agent.sandbox import run_c_tests
from agent.test_validation import review_case
from agent.test_generator import (extract_problem_text, generate_inputs,
                                  generate_expected_outputs)

MAX_CASES = 20
MAX_EXPECTED_BYTES = 65536


def _owned(session, teacher_id, assignment_id):
    require_role(teacher_id, {'teacher'})
    row = session.get(Assignment, assignment_id)
    if not row or row.created_by != teacher_id:
        raise PermissionError('Assignment not found or not owned by this teacher.')
    return row


def validate_input_cases(payload) -> list[dict]:
    """Accept JSON cases with input and optional expected stdout and category."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError('Enter valid JSON test cases.') from exc
    if isinstance(payload, dict):
        payload = payload.get('cases')
    if not isinstance(payload, list) or not 1 <= len(payload) <= MAX_CASES:
        raise ValueError(f'Provide 1 to {MAX_CASES} test cases in a JSON array.')
    seen = set()
    output = []
    for i, item in enumerate(payload, 1):
        if not isinstance(item, dict) or not isinstance(item.get('input'), str):
            raise ValueError(f'Test #{i} needs a string input field.')
        value = item['input']
        if '\x00' in value or len(value.encode('utf-8')) > 8192:
            raise ValueError(f'Test #{i} input is too long or contains a NUL byte.')
        if value in seen:
            raise ValueError(f'Duplicate input at test #{i}.')
        seen.add(value)
        category = item.get('category', 'normal')
        if not isinstance(category, str) or not 1 <= len(category) <= 40:
            raise ValueError(f'Test #{i} has an invalid category.')
        clean = {'input': value, 'category': category}
        if 'expected' in item:
            expected = item['expected']
            if (not isinstance(expected, str) or '\x00' in expected or
                    len(expected.encode('utf-8')) > MAX_EXPECTED_BYTES):
                raise ValueError(f'Test #{i} has an invalid expected output.')
            clean['expected'] = expected
        alternatives = item.get('accepted_outputs')
        if alternatives is not None:
            if (not isinstance(alternatives, list) or len(alternatives) > 10 or
                    any(not isinstance(v, str) or '\x00' in v or
                        len(v.encode('utf-8')) > MAX_EXPECTED_BYTES for v in alternatives)):
                raise ValueError(f'Test #{i} has invalid accepted_outputs (maximum 10 strings).')
            clean['accepted_outputs'] = alternatives
        source = item.get('expected_source')
        if source in {'llm', 'teacher', 'reference', 'checker'}:
            clean['expected_source'] = source
        if item.get('input_validation') == 'requires_teacher_review':
            clean['input_validation'] = 'requires_teacher_review'
        output.append(clean)
    return output


def parse_optional_cases(text: str = '', filename: str | None = None,
                         file_data: bytes | None = None) -> list[dict]:
    """Teacher may add optional inputs as JSON or one input per line in TXT."""
    if file_data is not None and text.strip():
        raise ValueError('Use either an uploaded test file or the text box, not both.')
    if file_data is not None:
        if len(file_data) > 65536:
            raise ValueError('Test input file must be no larger than 64 KiB.')
        try:
            text = file_data.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise ValueError('Test input file must be UTF-8.') from exc
        suffix = Path(filename or '').suffix.lower()
        if suffix not in {'.json', '.txt'}:
            raise ValueError('Upload optional inputs in .json or .txt format.')
        if suffix == '.txt':
            return validate_input_cases([{'input': line + '\n', 'category': 'teacher'}
                                         for line in text.splitlines() if line.strip()]) if text.strip() else []
    if not text.strip():
        return []
    return validate_input_cases(text)


def merge_cases(generated: list[dict], additional: list[dict]) -> list[dict]:
    """Add teacher inputs, with teacher-supplied expected outputs taking precedence."""
    generated = validate_input_cases(generated) if generated else []
    additional = validate_input_cases(additional) if additional else []
    merged = {case['input']: dict(case) for case in generated}
    for case in additional:
        previous = merged.get(case['input'])
        if previous:
            if 'expected' in case:
                previous.update(case)
                previous['expected_source'] = 'teacher'
        else:
            new_case = dict(case)
            if 'expected' in new_case:
                new_case['expected_source'] = 'teacher'
            merged[case['input']] = new_case
    if not 1 <= len(merged) <= MAX_CASES:
        raise ValueError(f'A combined set must contain 1 to {MAX_CASES} distinct test inputs.')
    return list(merged.values())


def _problem_text(row):
    """The uploaded assignment document is mandatory; notes are only supporting context."""
    if not row.document_path or not row.document_name:
        raise ValueError('Upload an assignment PDF (or DOCX/TXT/MD) first.')
    content = read_private(row.document_path, 'assignments')
    problem = extract_problem_text(row.document_name, content)
    # Put teacher samples FIRST so they are not lost when the prompt shortens a
    # long PDF to fit a small local model's context window.
    context = f'ASSIGNMENT TITLE: {row.title}\n'
    if row.sample_input:
        context += 'TEACHER SAMPLE STDIN (format example; do not copy):\n' + row.sample_input + '\n'
        if row.sample_output:
            context += ('TEACHER SAMPLE STDOUT (for stdin above):\n' +
                        row.sample_output + '\n')
    context += (f'PROBLEM DOCUMENT:\n{problem}\n'
                f'ADDITIONAL TEACHER NOTES:\n{row.description or "None"}')
    return context[:16500]


def save_reference(teacher_id: int, assignment_id: int, filename: str, data: bytes):
    """Optional: use a teacher reference to verify/override model predictions."""
    require_role(teacher_id, {'teacher'})
    if Path(filename).suffix.lower() != '.c' or not data or len(data) > 100 * 1024 or b'\x00' in data:
        raise ValueError('Upload a nonempty .c reference solution up to 100 KiB.')
    try:
        data.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ValueError('Reference source must use UTF-8.') from exc
    new_path = None
    with SessionLocal() as session:
        row = _owned(session, teacher_id, assignment_id)
        try:
            new_path, source_hash = write_private('references', filename, data)
            old_path = row.reference_path
            row.reference_path, row.reference_name, row.reference_hash = new_path, Path(filename).name, source_hash
            row.testset_json = row.testset_hash = None
            row.tests_approved = False
            session.commit()
        except Exception:
            session.rollback()
            discard_private(new_path, 'references')
            raise
    discard_private(old_path, 'references')
    return source_hash


def get_testset(teacher_id: int, assignment_id: int) -> dict:
    with SessionLocal() as session:
        row = _owned(session, teacher_id, assignment_id)
        return {'reference_name': row.reference_name, 'reference_hash': row.reference_hash,
                'tests_approved': row.tests_approved, 'testset_hash': row.testset_hash,
                'cases': json.loads(row.testset_json) if row.testset_json else []}


def suggest_cases(teacher_id: int, assignment_id: int, extra_context: str = '',
                  *, num_cases: int = 3, progress_callback=None) -> list[dict]:
    """Read the uploaded assignment PDF and generate candidate inputs AND expected outputs."""
    if len(extra_context) > 5000:
        raise ValueError('Extra details must contain no more than 5,000 characters.')
    with SessionLocal() as session:
        row = _owned(session, teacher_id, assignment_id)
        problem = _problem_text(row)
        saved_sample_input = row.sample_input
    if extra_context.strip():
        problem += '\nTEACHER CLARIFICATION:\n' + extra_context.strip()
    return generate_inputs(problem, num_cases=num_cases, progress_callback=progress_callback,
                           sample_input=saved_sample_input)


def _spec_hash(row):
    """Bind prepared tests to the exact problem document and instructions."""
    if not row.document_path:
        return None
    hasher = hashlib.sha256()
    hasher.update(read_private(row.document_path, 'assignments'))
    hasher.update(b'\0')
    hasher.update(row.title.encode('utf-8'))
    hasher.update(b'\0')
    hasher.update(row.description.encode('utf-8'))
    # Sample stdin/stdout influence generated tests and expected output examples.
    for sample in (row.sample_input, row.sample_output):
        hasher.update(b'\0')
        hasher.update((sample or '').encode('utf-8'))
    return hasher.hexdigest()


def _get_resources(row):
    if not row.rules_path:
        raise ValueError('Upload grading rules before preparing tests.')
    rules = validate_rules(read_private(row.rules_path, 'rules'))
    if not any(m['analyzer'] == 'io_tests' for m in rules['metrics']):
        raise ValueError('Rules must contain an io_tests analyzer.')
    return rules['time_limit_s'], rules['memory_limit_mb']


def prepare_testset(teacher_id: int, assignment_id: int, inputs) -> dict:
    """Validate PDF input formats and verify outputs before teacher approval.

    Reference C outputs take priority. Known deterministic checkers run locally;
    unsupported formats and outputs remain marked for teacher review.
    """
    cases = validate_input_cases(inputs)
    with SessionLocal() as session:
        row = _owned(session, teacher_id, assignment_id)
        time_limit, memory_limit = _get_resources(row)
        reference = read_private(row.reference_path, 'references') if row.reference_path else None
        reference_hash, rules_hash, spec_hash = row.reference_hash, row.rules_hash, _spec_hash(row)
        problem = _problem_text(row)

    warnings = []
    for i, case in enumerate(cases, 1):
        assessment = review_case(problem, case['input'], case.get('expected'))
        if assessment.status == 'invalid':
            raise ValueError(f'Test #{i} violates the assignment input format: {assessment.reason}')
        if assessment.status == 'review':
            case['input_validation'] = 'requires_teacher_review'
        else:
            case.pop('input_validation', None)
        # Reference execution will independently verify output, but a trusted
        # deterministic checker can provide outputs when no reference exists.
        if reference is None and assessment.verified_output is not None:
            if case.get('expected_source') == 'teacher' and 'expected' in case \
                    and case['expected'].strip() != assessment.verified_output.strip():
                raise ValueError(f'Test #{i} expected output conflicts with an exact '
                                 'Python checker. Please review the teacher-provided output.')
            if case.get('expected_source') != 'teacher' or 'expected' not in case:
                case['expected'] = assessment.verified_output
                case['expected_source'] = 'checker'

    if reference:
        result = run_c_tests(reference, [c['input'] for c in cases],
                             time_limit_s=time_limit, memory_limit_mb=memory_limit)
        if result['status'] != 'ok':
            raise ValueError('Reference solution could not run: ' + result.get('message', 'Unknown error') +
                             ' ' + result.get('compiler', {}).get('diagnostics', '')[:300])
        if len(result.get('results', [])) != len(cases):
            raise ValueError('Reference execution returned an incomplete result set.')
        for i, (case, actual) in enumerate(zip(cases, result['results']), 1):
            if actual['status'] != 'passed':
                raise ValueError(f'Reference failed on test #{i} ({actual["status"]}).')
            if 'expected' in case and case['expected'] != actual['stdout']:
                warnings.append(f'Test #{i}: proposed output differs from reference. '
                                'Reference output was used; review its correctness.')
            case['expected'], case['expected_source'] = actual['stdout'], 'reference'
    else:
        missing = [case for case in cases if 'expected' not in case]
        if missing:
            outputs = generate_expected_outputs(problem, missing)
            for case, output in zip(missing, outputs):
                case['expected'], case['expected_source'] = output, 'llm'
        for case in cases:
            case.setdefault('expected_source', 'teacher')
        warnings.append('No reference solution: Ollama-predicted outputs must be manually verified; '
                        'Python checker outputs are available only for explicitly recognized tasks.')

    if any(c.get('input_validation') == 'requires_teacher_review' for c in cases):
        warnings.append('Some input formats could not be verified automatically. '
                        'Check their exact stdin structure against the PDF.')
    warnings.append('Verify every input and expected output against the assignment before approval.')
    return {'reference_hash': reference_hash, 'rules_hash': rules_hash,
            'spec_hash': spec_hash, 'cases': cases, 'warnings': warnings}


def approve_testset(teacher_id: int, assignment_id: int, prepared: dict) -> str:
    """Freeze inputs and expected outputs *only after teacher preview and approval*."""
    if not isinstance(prepared, dict):
        raise ValueError('Prepare and review the tests first.')
    cases = prepared.get('cases')
    if not isinstance(cases, list):
        raise ValueError('Prepared tests not found.')
    validated = validate_input_cases(cases)
    if any('expected' not in c for c in validated):
        raise ValueError('Every case must have an expected output.')
    # Recheck approved candidates; do not let an invalid manually edited test
    # bypass the preparation screen. Unknown formats still need teacher review.
    with SessionLocal() as session:
        owned_row = _owned(session, teacher_id, assignment_id)
        problem_for_approval = _problem_text(owned_row)
    for i, case in enumerate(validated, 1):
        assessment = review_case(problem_for_approval, case['input'], case.get('expected'))
        if assessment.status == 'invalid':
            raise ValueError(f'Test #{i} violates assignment input rules: {assessment.reason}')
        if (case.get('expected_source') == 'checker' and assessment.verified_output is not None
                and case['expected'].strip() != assessment.verified_output.strip()):
            raise ValueError(f'Test #{i} differs from its deterministic checker.')
    with SessionLocal() as session:
        row = _owned(session, teacher_id, assignment_id)
        if (row.reference_hash != prepared.get('reference_hash') or
                row.rules_hash != prepared.get('rules_hash') or
                _spec_hash(row) != prepared.get('spec_hash')):
            raise ValueError('Assignment document, instructions, reference, or rules changed. Prepare tests again.')
        blob = json.dumps(validated, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        fingerprint = hashlib.sha256(blob.encode('utf-8')).hexdigest()
        row.testset_json, row.testset_hash, row.tests_approved = blob, fingerprint, True
        session.commit()
    return fingerprint
