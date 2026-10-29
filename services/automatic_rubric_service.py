"""Teacher-only, direct-to-database adaptive grading configuration.

Rules are immutable files: publishing an update writes a new version and hash.
Already approved correctness test cases remain approved because changing NLOC /
modularity thresholds does not change expected program outputs.
"""
import json

from database.db import SessionLocal
from database.orm_models import Assignment
from services.authentication import require_role
from services.rules_service import (adaptive_rules_from_settings,
                                    automatic_rules_bytes, validate_rules)
from services.storage_utils import read_private, write_private, discard_private


def _owned(session, teacher_id: int, assignment_id: int) -> Assignment:
    require_role(teacher_id, {'teacher'})
    assignment = session.get(Assignment, assignment_id)
    if assignment is None or assignment.created_by != teacher_id:
        raise PermissionError('Only the assignment teacher may modify these grading rules.')
    if not assignment.rules_path:
        raise ValueError('This assignment has no grading rubric.')
    return assignment


def _serialize(rules: dict) -> bytes:
    return (json.dumps(rules, indent=2, ensure_ascii=False) + '\n').encode('utf-8')


def get_automatic_configuration(teacher_id: int, assignment_id: int) -> dict:
    """Return the teacher's current persisted rubric and configuration source."""
    with SessionLocal() as session:
        row = _owned(session, teacher_id, assignment_id)
        rubric = validate_rules(read_private(row.rules_path, 'rules'))
        return {'rules_hash': row.rules_hash, 'rubric': rubric,
                'configuration': rubric.get('adaptive_configuration', {'source': 'teacher_uploaded'})}


def save_configuration(teacher_id: int, assignment_id: int, *, expected_rules_hash: str,
                       settings: dict | None = None, reset_defaults: bool = False,
                       recommendation: dict | None = None) -> dict:
    """Apply a teacher override, safe defaults, or validated AI thresholds.

    Only one of settings, reset_defaults, recommendation can be used. The
    existing rule hash prevents overwriting more recent edits from another tab.
    AI cannot require helper functions: only an explicit teacher override can.
    """
    if sum(value is not None and value is not False for value in
           (settings, recommendation, reset_defaults)) != 1:
        raise ValueError('Select exactly one rubric update operation.')
    if not isinstance(expected_rules_hash, str) or not expected_rules_hash:
        raise ValueError('Missing rules version. Reload the assignment first.')
    require_role(teacher_id, {'teacher'})
    new_path = None
    with SessionLocal() as session:
        row = _owned(session, teacher_id, assignment_id)
        if row.rules_hash != expected_rules_hash:
            raise ValueError('Grading rules changed in another tab. Refresh the assignment and retry.')
        old_rules = validate_rules(read_private(row.rules_path, 'rules'))
        if reset_defaults:
            default_rules = validate_rules(automatic_rules_bytes())
            # Retain the instructor's weights, limits on sandbox execution and
            # all unrelated metrics; only restore the two adaptive rubrics.
            structure = next((m for m in default_rules['metrics'] if m['analyzer'] == 'static_analysis'), None)
            if structure is None:
                raise ValueError('Automatic defaults are unavailable.')
            prepared = adaptive_rules_from_settings(
                old_rules, max_cyclomatic=8, max_function_nloc=40,
                modularity_mode='auto', suggest_if_ccn_over=10, suggest_if_nloc_over=60,
                source='automatic_defaults', reason='Restored fixed, fair starter limits.',
            )
        elif recommendation is not None:
            if not isinstance(recommendation, dict):
                raise ValueError('Invalid recommendation object.')
            # LLM recommendations may tune soft limits, but never make
            # mandatory helpers or change metric weights without an explicit
            # instructor decision.
            prepared = adaptive_rules_from_settings(
                old_rules,
                max_cyclomatic=recommendation.get('max_cyclomatic'),
                max_function_nloc=recommendation.get('max_function_nloc'),
                modularity_mode='auto',
                suggest_if_ccn_over=10, suggest_if_nloc_over=60,
                source='ollama_suggestion',
                reason=str(recommendation.get('reason', 'AI-suggested thresholds')),
            )
        else:
            if not isinstance(settings, dict):
                raise ValueError('Invalid advanced grading settings.')
            allowed = {'max_cyclomatic', 'max_function_nloc', 'modularity_mode',
                       'min_functions', 'suggest_if_ccn_over', 'suggest_if_nloc_over',
                       'transfer_weight_to'}
            if set(settings) - allowed:
                raise ValueError('Unsupported grading setting.')
            prepared = adaptive_rules_from_settings(
                old_rules, **settings, source='teacher_override',
                reason='Teacher-reviewed advanced configuration.',
            )
        content = _serialize(prepared)
        if content == read_private(row.rules_path, 'rules'):
            return {'rules_hash': row.rules_hash, 'changed': False,
                    'configuration': prepared['adaptive_configuration']}
        try:
            new_path, new_hash = write_private('rules', 'grading_rules.json', content)
            row.rules_path = new_path
            row.rules_name = 'grading_rules.json'
            row.rules_hash = new_hash
            # Reports become stale automatically via their stored rules_hash.
            # Do not remove approved correctness tests for structure-only edits.
            session.commit()
        except Exception:
            session.rollback()
            discard_private(new_path, 'rules')
            raise
        return {'rules_hash': row.rules_hash, 'changed': True,
                'configuration': prepared['adaptive_configuration']}


def recommend_and_save(teacher_id: int, assignment_id: int, *, expected_rules_hash: str,
                       model: str | None = None) -> dict:
    """Optional slow path: one Ollama recommendation, then direct persistence.

    Get the document through teacher-owned records; never accept arbitrary
    student-provided PDF paths. Failed Ollama calls leave the stored rubric as is.
    """
    from agent.rubric_recommender import suggest_adaptive_rubric

    with SessionLocal() as session:
        a = _owned(session, teacher_id, assignment_id)
        if a.rules_hash != expected_rules_hash:
            raise ValueError('Rules have changed. Refresh before requesting recommendations.')
        if not a.document_path:
            raise ValueError('An assignment document is required for AI recommendations.')
        doc_name = a.document_name
        doc_bytes = read_private(a.document_path, 'assignments')
    proposal = suggest_adaptive_rubric(doc_name or 'assignment.pdf', doc_bytes, model=model)
    saved = save_configuration(teacher_id, assignment_id,
                               expected_rules_hash=expected_rules_hash,
                               recommendation=proposal)
    saved['recommendation'] = proposal
    return saved
