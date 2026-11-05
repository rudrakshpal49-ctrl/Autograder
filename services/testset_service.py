"""Teacher-owned assignment test sets, Qwen proposals and reference-output verification."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from sqlalchemy import select, delete
from database.db import SessionLocal
from database.orm_models import Assignment, GradingCase, FirstPassResult
from services.authentication import require_role
from services.storage_utils import read_private, write_private, discard_private
from services.problem_text import assignment_text
from agent.test_generation import request_candidates, MODEL, _normalize_stdin
from agent.test_fallback import generate_fallback_cases
from agent.test_runner import run_cases
from services.edge_scenarios import edge_type_from_rationale, tag_rationale
from services.rules_service import get_edge_types, rules_warnings


def _assignment(session, teacher_id: int, assignment_id: int) -> Assignment:
    require_role(teacher_id, {'teacher'})
    a = session.get(Assignment, assignment_id)
    if not a or a.created_by != teacher_id:
        raise PermissionError('Assignment is not available to this teacher.')
    return a


def _case_dict(case: GradingCase) -> dict:
    return {'id': case.id, 'category': case.category, 'source': case.source,
            'title': case.title, 'rationale': case.rationale, 'stdin': case.stdin,
            'expected_output': case.expected_output,
            'approved': case.approved, 'oracle_verified': case.oracle_verified,
            'edge_type': edge_type_from_rationale(case.rationale)}


def list_cases(teacher_id: int, assignment_id: int) -> list[dict]:
    with SessionLocal() as session:
        _assignment(session, teacher_id, assignment_id)
        return [_case_dict(case) for case in session.scalars(select(GradingCase).where(
            GradingCase.assignment_id == assignment_id).order_by(GradingCase.id)).all()]


def approved_cases(session, assignment_id: int) -> list[dict]:
    return [_case_dict(case) for case in session.scalars(select(GradingCase).where(
        GradingCase.assignment_id == assignment_id, GradingCase.approved.is_(True)
    ).order_by(GradingCase.id)).all()]


def suite_hash(cases: list[dict]) -> str:
    data = [(c['id'], c['category'], c['stdin'], c['expected_output']) for c in cases]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode('utf-8')).hexdigest()


def _invalidate(session, assignment_id: int):
    # Discard stale evaluation reports when approved test suite changes.
    # New grading on each submission produces fresh evidence.
    from database.orm_models import Submission
    ids = select(Submission.id).where(Submission.assignment_id == assignment_id)
    session.execute(delete(FirstPassResult).where(FirstPassResult.submission_id.in_(ids)))


def _add_case(session, assignment_id, category, source, title, stdin, expected, approved, oracle_verified=False, rationale=''):
    if category not in {'normal', 'edge'} or source not in {'teacher', 'qwen'}:
        raise ValueError('Invalid test category or source.')
    if (not isinstance(stdin, str) or not stdin.strip() or len(stdin.encode('utf-8')) > 8192
            or not isinstance(expected, str) or len(expected.encode('utf-8')) > 65536):
        raise ValueError('Test stdin must be nonempty (max 8KiB); expected stdout max 64KiB.')
    normal = stdin.replace('\r\n', '\n').replace('\r', '\n')
    # A given stdin may belong to only one category to prevent double counting.
    existing = session.scalars(select(GradingCase).where(GradingCase.assignment_id == assignment_id)).all()
    if any(c.stdin.replace('\r\n', '\n').replace('\r', '\n') == normal for c in existing):
        return False
    session.add(GradingCase(assignment_id=assignment_id, category=category,
                            source=source, title=str(title)[:100], rationale=str(rationale)[:500], stdin=stdin,
                            expected_output=expected, approved=approved,
                            oracle_verified=oracle_verified))
    return True


def add_teacher_case(teacher_id: int, assignment_id: int, category: str,
                     stdin: str, expected_output: str, title: str = '') -> bool:
    with SessionLocal() as session:
        _assignment(session, teacher_id, assignment_id)
        added = _add_case(session, assignment_id, category, 'teacher', title, stdin,
                          expected_output, approved=True)
        if added:
            _invalidate(session, assignment_id)
            session.commit()
        return added


def set_approval(teacher_id: int, assignment_id: int, case_id: int, approved: bool):
    with SessionLocal() as session:
        _assignment(session, teacher_id, assignment_id)
        case = session.get(GradingCase, case_id)
        if not case or case.assignment_id != assignment_id:
            raise ValueError('Test case not found.')
        if case.approved != approved:
            case.approved = approved
            _invalidate(session, assignment_id)
        session.commit()



def approve_all_drafts(teacher_id: int, assignment_id: int) -> int:
    """Teacher confirms they reviewed *all* unapproved expected outputs.

    Never used automatically by generation without oracle verification.
    """
    with SessionLocal() as session:
        _assignment(session, teacher_id, assignment_id)
        pending = session.scalars(select(GradingCase).where(
            GradingCase.assignment_id == assignment_id,
            GradingCase.approved.is_(False))).all()
        for case in pending:
            case.approved = True
        if pending:
            _invalidate(session, assignment_id)
        session.commit()
        return len(pending)


def update_case(teacher_id: int, assignment_id: int, case_id: int, *,
                stdin: str, expected_output: str, title: str, category: str):
    if category not in {'normal', 'edge'} or not stdin.strip() or len(stdin.encode('utf-8')) > 8192:
        raise ValueError('Invalid test input or category.')
    if len(expected_output.encode('utf-8')) > 65536:
        raise ValueError('Expected output is too large.')
    with SessionLocal() as session:
        _assignment(session, teacher_id, assignment_id)
        case = session.get(GradingCase, case_id)
        if not case or case.assignment_id != assignment_id:
            raise ValueError('Test case not found.')
        others = session.scalars(select(GradingCase).where(
            GradingCase.assignment_id == assignment_id, GradingCase.id != case_id)).all()
        if any(c.stdin.replace('\r\n', '\n') == stdin.replace('\r\n', '\n') for c in others):
            raise ValueError('Another test already has that stdin.')
        case.stdin = stdin
        case.expected_output = expected_output
        case.title = title[:100]
        case.category = category
        case.oracle_verified = False  # Manually edited results need teacher approval.
        case.approved = False
        _invalidate(session, assignment_id)
        session.commit()


def delete_case(teacher_id: int, assignment_id: int, case_id: int):
    with SessionLocal() as session:
        _assignment(session, teacher_id, assignment_id)
        case = session.get(GradingCase, case_id)
        if not case or case.assignment_id != assignment_id:
            raise ValueError('Test case not found.')
        session.delete(case)
        _invalidate(session, assignment_id)
        session.commit()


def set_reference(teacher_id: int, assignment_id: int, name: str, code: bytes):
    if not name.lower().endswith('.c') or not 0 < len(code) <= 100 * 1024:
        raise ValueError('The reference must be a C source file (maximum 100 KiB).')
    try:
        code.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ValueError('Reference C must use UTF-8.') from exc
    newpath, _ = write_private('references', name, code)
    try:
        with SessionLocal() as session:
            a = _assignment(session, teacher_id, assignment_id)
            a.oracle_path = newpath
            # Previously approved generated tests should be reviewed if reference changes.
            for case in session.scalars(select(GradingCase).where(
                GradingCase.assignment_id == assignment_id, GradingCase.source == 'qwen')):
                case.approved = False
                case.oracle_verified = False
            _invalidate(session, assignment_id)
            session.commit()
    except Exception:
        discard_private(newpath, 'references')
        raise


def save_case_bank(teacher_id: int, assignment_id: int, edited_rows: list[dict]) -> dict:
    """Save all case edits, approval changes and deletions in one transaction.

    A modified oracle output or stdin is no longer marked oracle-verified.
    Explicitly keeping Approved checked counts as a teacher approval.
    """
    with SessionLocal() as session:
        _assignment(session, teacher_id, assignment_id)
        original = session.scalars(select(GradingCase).where(
            GradingCase.assignment_id == assignment_id).order_by(GradingCase.id)).all()
        by_id = {case.id: case for case in original}
        incoming = {}
        for row in edited_rows:
            case_id = int(row['ID'])
            if case_id in incoming or case_id not in by_id:
                raise ValueError('Invalid or repeated test ID in the test bank.')
            incoming[case_id] = row
        if set(incoming) != set(by_id):
            raise ValueError('The test bank changed. Refresh the page and retry.')
        seen_inputs = set()
        prepared = []
        for case_id, row in incoming.items():
            if bool(row.get('Delete', False)):
                continue
            category = row['Category']
            stdin = row['stdin']
            expected = row['Expected stdout']
            title = row['Title']
            edge_type = row.get('Edge type', edge_type_from_rationale(by_id[case_id].rationale))
            if edge_type in ('', None):
                edge_type = None
            if edge_type is not None and (not isinstance(edge_type, str)
                    or not __import__('re').fullmatch(r'[a-z][a-z0-9_]*', edge_type)):
                raise ValueError(f'Test #{case_id}: invalid edge type identifier.')
            if category == 'normal':
                edge_type = None
            if category not in {'normal', 'edge'}:
                raise ValueError(f'Test #{case_id}: category must be normal or edge.')
            if not isinstance(stdin, str) or not stdin.strip() or len(stdin.encode('utf-8')) > 8192:
                raise ValueError(f'Test #{case_id}: invalid stdin.')
            if not isinstance(expected, str) or len(expected.encode('utf-8')) > 65536:
                raise ValueError(f'Test #{case_id}: invalid expected output.')
            if not isinstance(title, str):
                raise ValueError(f'Test #{case_id}: invalid title.')
            normalized = _normalize_stdin(stdin)
            if normalized in seen_inputs:
                raise ValueError(f'Test #{case_id}: duplicate stdin in test bank.')
            seen_inputs.add(normalized)
            prepared.append((case_id, category, stdin, expected, title[:100],
                             bool(row['Approved']), edge_type))
        edited = deleted = 0
        for case_id, row in incoming.items():
            if bool(row.get('Delete', False)):
                session.delete(by_id[case_id]); deleted += 1
        for case_id, category, stdin, expected, title, approved, edge_type in prepared:
            case = by_id[case_id]
            new_rationale = tag_rationale(case.rationale, edge_type)
            if edge_type is None:
                from services.edge_scenarios import TAG
                new_rationale = TAG.sub('', case.rationale).strip()
            changed = (case.rationale != new_rationale or case.category != category or case.stdin != stdin or
                       case.expected_output != expected or case.title != title or
                       case.approved != approved)
            if changed:
                if case.stdin != stdin or case.expected_output != expected:
                    case.oracle_verified = False
                case.category = category
                case.stdin = stdin
                case.expected_output = expected
                case.title = title
                case.approved = approved
                case.rationale = new_rationale
                edited += 1
        if edited or deleted:
            _invalidate(session, assignment_id)
        session.commit()
    return {'updated': edited, 'deleted': deleted}


def generate_tests(teacher_id: int, assignment_id: int, *, normal_count: int = 5,
                   edge_count: int = 5, constraints: str = '',
                   sample_output: str = '', model: str = MODEL) -> dict:
    """Fill an assignment-wide bank using Qwen, then safe problem-specific fallback.

    Teacher-created cases and existing cases count toward each requested target.
    An instructor-trusted oracle supplies expected output and allows automatic
    approval; without it Qwen's proposed outputs remain *unapproved* drafts.
    A recognized format (e.g. Balanced Parentheses) can fill normal shortfalls
    deterministically. Unknown formats are never guessed just to hit a quota.
    """
    if not 1 <= normal_count <= 15 or not 0 <= edge_count <= 5:
        raise ValueError('Request 1–15 normal and up to 5 edge test cases.')
    with SessionLocal() as session:
        a = _assignment(session, teacher_id, assignment_id)
        statement = a.description or ''
        if a.document_path:
            try:
                doc = assignment_text(a.document_name, read_private(a.document_path, 'assignments'))
                if doc.strip():
                    statement += '\n\nFULL ASSIGNMENT DOCUMENT:\n' + doc
            except (OSError, ValueError, ImportError, KeyError):
                pass
        oracle_bytes = read_private(a.oracle_path, 'references') if a.oracle_path else None
        oracle_text = oracle_bytes.decode('utf-8', 'replace') if oracle_bytes else ''
        sample = a.sample_input or ''
        requested_edge_types = []
        rule_warnings = []
        limits = {'time_limit_s': 1.0, 'memory_limit_mb': 64}
        if a.rules_path:
            from services.rules_service import validate_rules
            rules = validate_rules(read_private(a.rules_path, 'rules'))
            limits = {'time_limit_s': rules['time_limit_s'],
                      'memory_limit_mb': rules['memory_limit_mb']}
            requested_edge_types = get_edge_types(rules)
            rule_warnings = rules_warnings(rules)
        saved = session.scalars(select(GradingCase).where(
            GradingCase.assignment_id == assignment_id).order_by(GradingCase.id)).all()
        existing_cases = [_case_dict(c) for c in saved]

    seen = {_normalize_stdin(c['stdin']) for c in existing_cases}
    rejected = set()
    counts = {kind: sum(c['category'] == kind for c in existing_cases)
              for kind in ('normal', 'edge')}
    targets = {'normal': normal_count, 'edge': edge_count}
    accepted: list[dict] = []
    warnings: list[str] = list(rule_warnings)
    oracle_status = None
    refreshed: dict[int, str] = {}

    # Teacher can add oracle.c after earlier unapproved Qwen drafts. Verify
    # and approve those existing drafts automatically rather than clicking each.
    drafts = [c for c in existing_cases if c['source'] == 'qwen' and not c['approved']]
    if oracle_bytes and drafts:
        result = run_cases(oracle_bytes, [c['stdin'] for c in drafts], **limits)
        oracle_status = result['status']
        if oracle_status == 'completed':
            for c, run in zip(drafts, result['runs']):
                if run['status'] == 'ok':
                    refreshed[c['id']] = run['stdout']
            if len(refreshed) < len(drafts):
                warnings.append(f'{len(drafts)-len(refreshed)} older drafts could not be reference-verified.')
        else:
            warnings.append(f'Existing drafts: reference execution failed ({oracle_status}).')

    def accept_candidates(candidates: list[dict], category: str):
        """Only count candidates accepted by reference, or unapproved drafts."""
        nonlocal oracle_status
        slots = targets[category] - counts[category]
        if slots <= 0:
            return
        candidates_clean = []
        local_seen = set()
        for case in candidates:
            stdin = case.get('stdin', '')
            if not isinstance(stdin, str) or not stdin.strip() or len(stdin.encode('utf-8')) > 8192:
                continue
            stdin = _normalize_stdin(stdin)
            if stdin in seen or stdin in rejected or stdin in local_seen:
                continue
            local_seen.add(stdin)
            candidate = dict(case)
            candidate['stdin'] = stdin
            candidate['category'] = category
            if category == 'edge' and requested_edge_types:
                label = candidate.get('edge_type')
                if label not in requested_edge_types:
                    rejected.add(stdin)
                    continue
            candidates_clean.append(candidate)
            if len(candidates_clean) >= slots:
                break
        if not candidates_clean:
            return
        if oracle_bytes:
            result = run_cases(oracle_bytes, [c['stdin'] for c in candidates_clean], **limits)
            oracle_status = result['status']
            if oracle_status != 'completed':
                warnings.append(f'{category}: reference execution failed ({oracle_status}).')
                return
            verified = []
            for case, execution in zip(candidates_clean, result['runs']):
                if execution['status'] == 'ok':
                    case['expected_output'] = execution['stdout']
                    case['oracle_verified'] = True
                    case['approved'] = True
                    verified.append(case)
                else:
                    rejected.add(case['stdin'])
            if len(verified) < len(candidates_clean):
                warnings.append(f'{category}: reference rejected '
                                f'{len(candidates_clean)-len(verified)} candidate input(s).')
            candidates_clean = verified
        else:
            for case in candidates_clean:
                case['oracle_verified'] = False
                case['approved'] = False
        for case in candidates_clean:
            seen.add(case['stdin'])
            accepted.append(case)
            counts[category] += 1

    for category in ('normal', 'edge'):
        # Qwen gets multiple chances at varied inputs. Missing cases are
        # completed by a deterministic generator only for recognized formats.
        for _ in range(3):
            if counts[category] >= targets[category]:
                break
            need = targets[category] - counts[category]
            try:
                proposals = request_candidates(
                    statement, sample, sample_output, constraints, oracle_text,
                    category, need, model, existing_inputs=sorted(seen | rejected),
                    **({'requested_edge_types': requested_edge_types}
                       if category == 'edge' and requested_edge_types else {}))
            except ValueError as exc:
                warnings.append(f'{category}: Qwen: {exc}')
                break
            accept_candidates(proposals, category)
        if counts[category] < targets[category] and oracle_bytes:
            missing = targets[category] - counts[category]
            fallback = generate_fallback_cases(
                statement + '\n' + constraints, sample, oracle_text,
                category, missing, sorted(seen | rejected),
                **({'requested_edge_types': requested_edge_types}
                   if category == 'edge' and requested_edge_types else {}))
            accept_candidates(fallback, category)
        if counts[category] < targets[category]:
            warnings.append(
                f'{category}: {counts[category]} of {targets[category]} cases available. '
                'The problem format was not safely fillable from verified candidates; '
                'provide a trusted reference and a clear sample input/constraints.'
            )

    added = 0
    auto_approved = 0
    with SessionLocal() as session:
        _assignment(session, teacher_id, assignment_id)
        for cid, stdout in refreshed.items():
            case = session.get(GradingCase, cid)
            if case and case.assignment_id == assignment_id and not case.approved:
                case.expected_output = stdout
                case.approved = True
                case.oracle_verified = True
        for case in accepted:
            ok = _add_case(session, assignment_id, case['category'], 'qwen',
                           case.get('title', ''), case['stdin'], case.get('expected_output', ''),
                           approved=case['approved'], oracle_verified=case['oracle_verified'],
                           rationale=tag_rationale(case.get('rationale', ''),
                                                   case.get('edge_type') if case['category'] == 'edge' else None))
            added += int(ok)
            auto_approved += int(ok and case['approved'])
        if auto_approved or refreshed:
            _invalidate(session, assignment_id)
        session.commit()
        actual = session.scalars(select(GradingCase).where(
            GradingCase.assignment_id == assignment_id)).all()
        counts = {cat: sum(c.category == cat for c in actual)
                  for cat in ('normal', 'edge')}
        approved_counts = {cat: sum(c.category == cat and c.approved for c in actual)
                           for cat in ('normal', 'edge')}
    edge_covered = sorted({edge_type_from_rationale(c.rationale)
                           for c in actual if c.category == 'edge'
                           and edge_type_from_rationale(c.rationale)})
    missing_edge_types = [k for k in requested_edge_types if k not in edge_covered]
    if missing_edge_types:
        warnings.append('Uncovered configured edge scenarios: ' + ', '.join(missing_edge_types)
                        + '. These must be valid for the problem; no invalid cases were forced.')
    return {'requested_edge_types': requested_edge_types,
            'covered_edge_types': edge_covered, 'missing_edge_types': missing_edge_types,
            'drafts_added': added - auto_approved, 'cases_added': added,
            'auto_approved': auto_approved + len(refreshed),
            'previous_drafts_verified': len(refreshed),
            'oracle_status': oracle_status, 'warnings': warnings,
            'category_counts': counts, 'approved_counts': approved_counts,
            'message': (f'{added} new tests saved, '
                        f'{auto_approved + len(refreshed)} reference-verified and automatically approved. '
                        'Unverified AI outputs remain drafts.')}


MAX_CASE_IMPORT_BYTES = 256 * 1024
MAX_CASE_IMPORT_COUNT = 100


def parse_teacher_cases_json(content: bytes) -> list[dict]:
    """Validate a teacher-authored JSON test bank, without executing any input.

    Accepted shape: {"cases": [{"category": "normal"|"edge", "stdin": "...",
    "expected_output": "...", "title": "...", "edge_type": "..."}]}.
    A top-level array is also supported. Uploaded expected outputs are treated
    as explicitly teacher-provided, not LLM-verified.
    """
    import re
    if not isinstance(content, bytes) or len(content) > MAX_CASE_IMPORT_BYTES:
        raise ValueError('JSON test bank must be at most 256 KiB.')
    try:
        decoded = json.loads(content.decode('utf-8-sig'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('Upload a valid UTF-8 JSON test bank.') from exc
    rows = decoded.get('cases') if isinstance(decoded, dict) else decoded
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_CASE_IMPORT_COUNT:
        raise ValueError('JSON must contain a cases array with 1–100 entries.')
    cleaned, seen = [], set()
    for idx, item in enumerate(rows, 1):
        if not isinstance(item, dict):
            raise ValueError(f'Case {idx} must be a JSON object.')
        kind = item.get('category', 'normal')
        stdin = item.get('stdin')
        expected = item.get('expected_output')
        title = item.get('title', f'Imported case {idx}')
        edge_type = item.get('edge_type')
        if kind not in {'normal', 'edge'}:
            raise ValueError(f'Case {idx}: category must be normal or edge.')
        if not isinstance(stdin, str) or not stdin.strip() or len(stdin.encode('utf-8')) > 8192:
            raise ValueError(f'Case {idx}: stdin must be nonempty UTF-8 text up to 8 KiB.')
        if not isinstance(expected, str) or len(expected.encode('utf-8')) > 65536:
            raise ValueError(f'Case {idx}: expected_output must be a string up to 64 KiB.')
        if not isinstance(title, str) or len(title) > 100:
            raise ValueError(f'Case {idx}: title must be a string up to 100 characters.')
        if edge_type is not None and (kind != 'edge' or not isinstance(edge_type, str)
                                      or not re.fullmatch(r'[a-z][a-z0-9_]*', edge_type)):
            raise ValueError(f'Case {idx}: edge_type must be a valid edge-case identifier.')
        normalized = _normalize_stdin(stdin)
        if normalized in seen:
            raise ValueError(f'Case {idx}: duplicate stdin within uploaded JSON.')
        seen.add(normalized)
        cleaned.append({'category': kind, 'stdin': stdin, 'expected_output': expected,
                        'title': title, 'edge_type': edge_type})
    return cleaned


def import_teacher_cases_json(teacher_id: int, assignment_id: int, content: bytes) -> dict:
    """Insert teacher-verified cases atomically; skip identical existing stdin.

    Invalidation occurs once, only when a new approved case was added.
    Failed validation never leaves a partial upload in the database.
    """
    rows = parse_teacher_cases_json(content)
    with SessionLocal() as session:
        _assignment(session, teacher_id, assignment_id)
        existing = session.scalars(select(GradingCase).where(
            GradingCase.assignment_id == assignment_id)).all()
        seen = {_normalize_stdin(case.stdin) for case in existing}
        added = skipped = 0
        for row in rows:
            key = _normalize_stdin(row['stdin'])
            if key in seen:
                skipped += 1
                continue
            seen.add(key)
            rationale = tag_rationale('', row['edge_type']) if row['edge_type'] else ''
            session.add(GradingCase(assignment_id=assignment_id, category=row['category'],
                                    source='teacher', title=row['title'], rationale=rationale,
                                    stdin=row['stdin'], expected_output=row['expected_output'],
                                    approved=True, oracle_verified=False))
            added += 1
        if added:
            _invalidate(session, assignment_id)
        session.commit()
        return {'added': added, 'skipped_duplicates': skipped, 'total_in_file': len(rows)}
