"""Access-controlled first-pass grading orchestration and DB persistence."""
import json
from sqlalchemy import select
from database.db import SessionLocal
from database.orm_models import Assignment, FirstPassResult, Submission, User
from services.authentication import require_role
from services.rules_service import validate_rules, rules_warnings
from services.storage_utils import read_private
from agent.first_pass import evaluate_source, can_run_execution, execution_blockers
from agent.coordinator import dispatch_analyzers
from agent.analyzers import HANDLERS
from services.testset_service import approved_cases, suite_hash
from services.final_grade_service import calculate_final_grade


def attach_syntax_grading(report: dict, rules: dict) -> None:
    """Add the syntax grade to the stored first-pass report.

    Older assignments with only execution limits get an explicitly labeled
    preview score using the analyzer's documented defaults. An instructor's
    configured syntax metric takes precedence. No final grade is calculated.
    """
    configured = [metric for metric in rules.get('metrics', [])
                  if isinstance(metric, dict) and metric.get('analyzer') == 'compiler_warnings']
    if configured:
        metric = configured[0]
        rule_source = 'instructor'
    elif not rules.get('metrics'):
        metric = {'name': 'syntax', 'analyzer': 'compiler_warnings', 'params': {}}
        rule_source = 'default_preview'
    else:
        # Instructor provided other metric(s), but did not enable syntax.
        report['syntax_grading'] = {'status': 'not_configured'}
        return

    if report['compiler']['status'] not in {'passed', 'failed'}:
        report['syntax_grading'] = {'status': 'unavailable'}
        return

    dispatched = dispatch_analyzers(
        report, [metric], HANDLERS, {'first_pass': report}
    )
    syntax = dispatched['analyzers'][0]
    report['syntax_grading'] = {
        'status': syntax['status'],
        'score': syntax['result']['score'],
        'evidence': syntax['result']['evidence'],
        'feedback': syntax['result']['feedback'],
        'rule_source': rule_source,
        'weight': metric.get('weight') if rule_source == 'instructor' else None,
    }


def attach_structure_grading(report: dict, rules: dict, source: bytes) -> None:
    """Add a source-only structure score to the existing report.

    If the instructor enabled metrics but not structure, leave it disabled.
    With legacy execution-only rules, show a clearly labeled preview.
    """
    configured = [metric for metric in rules.get('metrics', [])
                  if isinstance(metric, dict) and metric.get('analyzer') == 'static_analysis']
    if configured:
        metric = configured[0]
        rule_source = 'instructor'
    elif not rules.get('metrics'):
        metric = {'name': 'structure', 'analyzer': 'static_analysis', 'params': {}}
        rule_source = 'default_preview'
    else:
        report['structure_grading'] = {'status': 'not_configured'}
        return

    dispatched = dispatch_analyzers(
        report, [metric], HANDLERS, {'source': source}
    )
    result = dispatched['analyzers'][0]['result']
    report['structure_grading'] = {
        'status': result.get('status', 'completed'),
        'score': result['score'],
        'evidence': result['evidence'],
        'feedback': result['feedback'],
        'rule_source': rule_source,
        'weight': metric.get('weight') if rule_source == 'instructor' else None,
    }



def attach_modularity_grading(report: dict, rules: dict, source: bytes) -> None:
    """Add an independent source-only function-split score to the report."""
    configured = [metric for metric in rules.get('metrics', [])
                  if isinstance(metric, dict) and metric.get('analyzer') == 'function_split']
    if configured:
        metric = configured[0]
        rule_source = 'instructor'
    elif not rules.get('metrics') or rules.get('modularity_options', {}).get('expect_helpers'):
        # With execution-only rules, show the preview. A teacher can also
        # explicitly request helpers without rewriting weighted rubric metrics.
        params = rules.get('modularity_options', {})
        metric = {'name': 'modularity', 'analyzer': 'function_split', 'params': params}
        rule_source = 'teacher_option' if 'modularity_options' in rules else 'default_preview'
    else:
        report['modularity_grading'] = {'status': 'not_configured'}
        return

    dispatched = dispatch_analyzers(report, [metric], HANDLERS, {'source': source})
    result = dispatched['analyzers'][0]['result']
    report['modularity_grading'] = {
        'status': result.get('status', 'completed'),
        'score': result['score'],
        'evidence': result['evidence'],
        'feedback': result['feedback'],
        'rule_source': rule_source,
        'weight': metric.get('weight') if rule_source == 'instructor' else None,
    }


def attach_readability_grading(report: dict, rules: dict, source: bytes,
                               previous: dict | None = None) -> None:
    """Add an Ollama readability score, with full Python fallback and caching."""
    configured = [metric for metric in rules.get('metrics', [])
                  if isinstance(metric, dict) and metric.get('analyzer') == 'llm_review']
    if configured:
        metric = configured[0]
        rule_source = 'instructor'
    elif not rules.get('metrics'):
        metric = {'name': 'readability', 'analyzer': 'llm_review', 'params': {}}
        rule_source = 'default_preview'
    else:
        report['readability_grading'] = {'status': 'not_configured'}
        return

    existing_ai = (previous or {}).get('readability_grading', {}).get('evidence', {}).get('ai')
    dispatched = dispatch_analyzers(
        report, [metric], HANDLERS, {'source': source, 'cached_ai': existing_ai}
    )
    result = dispatched['analyzers'][0]['result']
    report['readability_grading'] = {
        'status': result['status'], 'score': result['score'],
        'scoring_method': result.get('scoring_method'),
        'evidence': result['evidence'], 'feedback': result['feedback'],
        'rule_source': rule_source,
        'weight': metric.get('weight') if rule_source == 'instructor' else None,
    }




def attach_originality_grading(report: dict, rules: dict, source: bytes,
                               peers: list[dict], previous: dict | None = None,
                               source_filename: str = 'source.c') -> None:
    """Source-only similarity grade and filenames, with an advisory Qwen review."""
    configured = [metric for metric in rules.get('metrics', [])
                  if isinstance(metric, dict) and metric.get('analyzer') == 'similarity_flag']
    if configured:
        metric = configured[0]
        params = metric.get('params', {})
        rule_source = 'instructor'
        if params.get('enabled', True) is False:
            report['originality_grading'] = {'status': 'not_configured'}
            return
    elif 'originality_options' in rules:
        options = rules['originality_options']
        if not options.get('enabled', False):
            report['originality_grading'] = {'status': 'not_configured'}
            return
        metric = {'name': 'originality', 'analyzer': 'similarity_flag', 'params': options}
        rule_source = 'teacher_option'
    elif not rules.get('metrics'):
        metric = {'name': 'originality', 'analyzer': 'similarity_flag', 'params': {}}
        rule_source = 'default_preview'
    else:
        report['originality_grading'] = {'status': 'not_configured'}
        return

    existing_ai = (previous or {}).get('originality_grading', {}).get('evidence', {}).get('ai')
    dispatched = dispatch_analyzers(report, [metric], HANDLERS,
                                    {'source': source, 'peers': peers, 'cached_ai': existing_ai})
    result = dispatched['analyzers'][0]['result']
    report['originality_grading'] = {
        'status': result['status'], 'score': result['score'],
        'grade_status': result.get('grade_status'),
        'source_filename': source_filename,
        'flagged': result['flagged'], 'evidence': result['evidence'],
        'feedback': result['feedback'], 'rule_source': rule_source,
        'weight': metric.get('weight') if rule_source == 'instructor' else None,
    }



def _first_pass_zero(report: dict, analyzer: str, weight=None, **evidence) -> dict:
    """A true 0/10 for an execution metric forbidden by first-pass checks.

    Distinguish this from an unavailable analyzer (N/A): only first-pass
    blocked/skipped execution is assigned zero; no analyzer is executed.
    """
    reasons = execution_blockers(report)
    return {
        'status': 'skipped',
        'score': 0.0,
        'analyzer': analyzer,
        'weight': weight,
        'rule_source': 'instructor' if weight is not None else None,
        'feedback': '0/10: execution analyzer not run because first pass failed or warned. '
                    + ('; '.join(reasons) or 'First pass was not ready.'),
        'evidence': {'first_pass_blocked': True, 'blockers': reasons, **evidence},
    }


def attach_memory_safety_grading(report: dict, rules: dict, source: bytes, cases: list[dict]) -> None:
    """Grade configured memory safety only after a warning-free first pass."""
    metric = next((m for m in rules.get('metrics', []) if isinstance(m, dict)
                   and m.get('analyzer') == 'sanitizer_run'), None)
    if metric is None:
        report['memory_safety_grading'] = {'status': 'not_configured', 'score': None}
        return
    if not can_run_execution(report):
        report['memory_safety_grading'] = _first_pass_zero(
            report, 'sanitizer_run', metric.get('weight'), total=0,
            executed=0, findings=[], tests=[])
        return
    dispatched = dispatch_analyzers(report, [metric], HANDLERS, {
        'source': source, 'cases': cases, 'time_limit_s': rules['time_limit_s'],
        'memory_limit_mb': rules['memory_limit_mb']})
    item = dispatched['analyzers'][0]
    result = (item['result'] if item['status'] == 'completed' else
              {'status': 'unavailable', 'score': None,
               'feedback': 'Memory safety analyzer could not run.',
               'evidence': {'total': 0, 'executed': 0, 'findings': [], 'tests': []}})
    report['memory_safety_grading'] = {**result, 'analyzer': 'sanitizer_run',
                                       'weight': metric.get('weight'),
                                       'rule_source': 'instructor'}


def attach_complexity_grading(report: dict, rules: dict, source: bytes,
                              sample_input: str | None) -> None:
    """Record optional Callgrind result, or a first-pass-blocked zero."""
    metric = next((m for m in rules.get('metrics', []) if isinstance(m, dict)
                   and m.get('analyzer') == 'complexity_estimator'), None)
    if metric is None:
        report['complexity_grading'] = {'status': 'not_configured', 'score': None}
        return
    if not can_run_execution(report):
        report['complexity_grading'] = _first_pass_zero(
            report, 'complexity_estimator', metric.get('weight'))
        return
    dispatched = dispatch_analyzers(report, [metric], HANDLERS, {
        'source': source, 'sample_input': sample_input,
        'time_limit_s': rules['time_limit_s'],
        'memory_limit_mb': rules['memory_limit_mb'],
    })
    item = dispatched['analyzers'][0]
    result = item['result'] if item['status'] == 'completed' else {
        'status': 'unavailable', 'score': None,
        'feedback': 'Complexity grading unavailable.', 'evidence': {},
    }
    report['complexity_grading'] = {
        **result, 'analyzer': 'complexity_estimator',
        'weight': metric.get('weight'), 'rule_source': 'instructor',
    }


def _allowed_to_run(user_id: int, sub: Submission, assignment: Assignment) -> str:
    """Students may trigger checks on their own uploads, but cannot read reports."""
    user = require_role(user_id, {'student', 'teacher'})
    if user['role'] == 'student' and sub.student_id != user_id:
        raise PermissionError('You cannot evaluate another student\'s submission.')
    if user['role'] == 'teacher' and assignment.created_by != user_id:
        raise PermissionError('You cannot evaluate another teacher\'s submission.')
    return user['role']


def get_first_pass(user_id: int, submission_id: int):
    """Only the assignment's teacher can retrieve evaluation details."""
    require_role(user_id, {'teacher'})
    with SessionLocal() as session:
        sub = session.get(Submission, submission_id)
        if sub is None:
            raise ValueError('Submission not found.')
        assignment = session.get(Assignment, sub.assignment_id)
        if assignment is None:
            raise ValueError('Assignment not found.')
        if assignment.created_by != user_id:
            raise PermissionError('You cannot inspect another teacher\'s evaluation.')
        report = session.scalar(select(FirstPassResult).where(FirstPassResult.submission_id == submission_id))
        if report is None:
            return None
        data = json.loads(report.report_json)
        # Historical evaluations predate the canonical final-grade field.
        # Render a read-only aggregate of their *saved* scores if rules still match;
        # do not rerun analyzers or edit historical evidence.
        if 'final_grade' not in data and assignment.rules_path:
            try:
                current_rules = validate_rules(read_private(assignment.rules_path, 'rules'))
                if data.get('rules_hash') == assignment.rules_hash:
                    data['final_grade'] = calculate_final_grade(data, current_rules)
            except (OSError, UnicodeError, ValueError):
                pass
        return data


def run_first_pass(user_id: int, submission_id: int) -> dict:
    with SessionLocal() as session:
        sub = session.get(Submission, submission_id)
        if sub is None:
            raise ValueError('Submission not found.')
        assignment = session.get(Assignment, sub.assignment_id)
        if assignment is None:
            raise ValueError('Assignment not found.')
        role = _allowed_to_run(user_id, sub, assignment)
        source = read_private(sub.source_path, 'submissions')
        if assignment.rules_path:
            rules = validate_rules(read_private(assignment.rules_path, 'rules'))
        else:
            # Legacy assignments cannot grade until an instructor uploads rules.
            raise ValueError('The instructor has not provided grading rules for this assignment.')
        cases = approved_cases(session, assignment.id)
        sample_input = (assignment.sample_input if assignment.sample_input not in (None, '')
                        else cases[0]['stdin'] if cases else None)
        row = session.scalar(select(FirstPassResult).where(FirstPassResult.submission_id == submission_id))
        previous = json.loads(row.report_json) if row and row.report_json else None
        report = evaluate_source(source, sample_input=sample_input,
                                 time_limit_s=rules['time_limit_s'],
                                 memory_limit_mb=rules['memory_limit_mb'],
                                 rules_hash=assignment.rules_hash)
        # A warning in any first-pass check is blocking, even when smoke passes.
        # Keep mock/legacy first-pass data safe through the same shared gate.
        if report.get('status') == 'ready' and execution_blockers(report):
            report['status'] = 'blocked'
        report['execution_gate'] = {
            'allowed': can_run_execution(report),
            'reasons': execution_blockers(report),
        }
        # Only run execution-based grading after a warning-free first pass.
        report['flags'] = ['suspected_infinite_loop'] if report['smoke']['status'] == 'timeout' else []
        report['rules_warnings'] = rules_warnings(rules)
        report['test_suite_hash'] = suite_hash(cases)
        for category, name, analyzer in (
            ('normal', 'correctness_grading', 'io_tests'),
            ('edge', 'logical_grading', 'edge_case_runner'),
        ):
            selected = [c for c in cases if c['category'] == category]
            if category == 'edge':
                # Keep the tag in graded evidence without changing legacy case selection.
                for c in selected:
                    c.setdefault('edge_type', None)
            rubric = next((metric for metric in rules.get('metrics', [])
                           if isinstance(metric, dict) and metric.get('analyzer') == analyzer), None)
            if not can_run_execution(report):
                result = _first_pass_zero(report, analyzer, rubric.get('weight') if rubric else None,
                                          total=len(selected), passed=0, results=[])
            elif not selected:
                result = {'status': 'not_configured', 'score': None,
                          'feedback': 'No approved cases in this category.',
                          'evidence': {'total': 0, 'passed': 0, 'results': []}}
            else:
                dispatched = dispatch_analyzers(report, [rubric or {
                    'name': category, 'analyzer': analyzer, 'params': {}}], HANDLERS,
                    {'source': source, 'cases': selected,
                     'time_limit_s': rules['time_limit_s'],
                     'memory_limit_mb': rules['memory_limit_mb']})
                item = dispatched['analyzers'][0]
                result = item.get('result') if item['status'] == 'completed' else {
                    'status': 'unavailable', 'score': None,
                    'feedback': 'Execution analyzer could not be completed.',
                    'evidence': {'total': len(selected), 'passed': 0, 'results': []}}
            report[name] = dict(result, weight=rubric.get('weight') if rubric else None,
                                analyzer=analyzer)
        attach_memory_safety_grading(report, rules, source, cases)
        attach_complexity_grading(report, rules, source, sample_input)
        attach_syntax_grading(report, rules)
        attach_structure_grading(report, rules, source)
        attach_modularity_grading(report, rules, source)
        attach_readability_grading(report, rules, source, previous=previous)
        # Compare only with other students on THIS assignment. Never leak peer
        # sources or identifiers to the student's private response.
        other_rows = session.execute(
            select(Submission, User).join(User, User.id == Submission.student_id).where(
                Submission.assignment_id == assignment.id,
                Submission.student_id != sub.student_id,
                Submission.id != sub.id,
            ).order_by(Submission.id)
        ).all()
        peers = [{'submission_id': other.id,
                  'filename': other.original_filename or 'source.c',
                  'student_name': user.full_name or user.username,
                  'source': read_private(other.source_path, 'submissions')}
                 for other, user in other_rows]
        attach_originality_grading(report, rules, source, peers, previous=previous,
                                   source_filename=sub.original_filename or 'source.c')
        # One persisted, canonical grade for teacher and student reports.
        report['final_grade'] = calculate_final_grade(report, rules)
        if not row:
            row = FirstPassResult(submission_id=submission_id)
            session.add(row)
        row.status = report['status']
        row.report_json = json.dumps(report, ensure_ascii=False)
        session.commit()
        # Detailed status, hashes, compiler logs and evidence are teacher-only.
        return report if role == 'teacher' else {'submission_id': submission_id}
