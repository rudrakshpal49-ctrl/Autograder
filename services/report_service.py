"""Small, access-controlled student evaluation reports (TXT / PDF).

Reports are rendered from the already-persisted evaluation JSON. This module
never runs, edits, or re-scores an analyzer and never returns peer identities,
source hashes, private paths, raw LLM replies, or the complete hidden test bank.
"""
from __future__ import annotations

from html import escape
import io
import json
import math
from textwrap import wrap

from sqlalchemy import select

from database.db import SessionLocal
from database.orm_models import Assignment, FirstPassResult, Submission, User
from services.authentication import require_role
from services.dates import as_local
from services.final_grade_service import calculate_final_grade


# Report-only score aggregation; time complexity is scored only when Callgrind is decisive.
METRICS = (
    ('io_tests', 'correctness_grading', 'Correctness'),
    ('edge_case_runner', 'logical_grading', 'Logical / edge cases'),
    ('compiler_warnings', 'syntax_grading', 'Syntax'),
    ('static_analysis', 'structure_grading', 'Structure'),
    ('function_split', 'modularity_grading', 'Modularity'),
    ('sanitizer_run', 'memory_safety_grading', 'Memory safety'),
    ('complexity_estimator', 'complexity_grading', 'Time complexity'),
    ('llm_review', 'readability_grading', 'Readability'),
    ('similarity_flag', 'originality_grading', 'Originality'),
)
LABELS = {analyzer: (field, label) for analyzer, field, label in METRICS}


USEFULNESS = (
    'Bulk marking: the same approved C tests can assess 200 submissions consistently.',
    'Error feedback: a failed edge-case input and expected output help locate a bug.',
    'Review triage: high code-similarity signals help instructors prioritize manual checks.',
)
LIMITATIONS = (
    'Missed case: a program may pass all 10 tested inputs but fail on an unseen negative input.',
    'Tool availability: an unsupported sanitizer backend can leave memory defects undetected.',
    'Similarity false positives: independent solutions using a standard algorithm can look alike.',
)

def _short(value: object, limit: int = 360) -> str:
    """Bound user-controlled strings before rendering and normalize line endings."""
    text = str(value if value is not None else '').replace('\r\n', '\n').replace('\r', '\n')
    return text[:limit] + ('...' if len(text) > limit else '')


def _valid_score(value: object) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and 0 <= value <= 1)


def _build_evaluation(meta: dict, report: dict, rules: dict) -> dict:
    configured = {}
    configured_all = []
    for metric in rules.get('metrics', []):
        if not isinstance(metric, dict):
            continue
        analyzer = metric.get('analyzer')
        configured_all.append(metric)
        if analyzer in LABELS and analyzer not in configured:
            configured[analyzer] = metric

    output = {'submission_id': meta['submission_id'],
              'student': _short(meta['student'], 100),
              'assignment': _short(meta['assignment'], 160),
              'filename': _short(meta['filename'], 130),
              'evaluated_at': as_local(meta['evaluated_at']).strftime('%d %b %Y, %I:%M %p IST'),
              'first_pass': report.get('status', 'unknown'),
              'metrics': [], 'failures': [], 'suggestions': [],
              'overall_score': None, 'provisional': False}

    for analyzer, field, label in METRICS:
        # Old reports predate time-complexity integration. Do not fabricate
        # an extra metric until the submission has been evaluated again.
        if analyzer == 'complexity_estimator' and field not in report:
            continue
        raw = report.get(field) or {}
        status = _short(raw.get('status') or 'pending', 42)
        score = raw.get('score') if status in ('completed', 'skipped') else None
        if not _valid_score(score):
            score = None
        rubric = configured.get(analyzer)
        weight = rubric.get('weight') if rubric else None
        if not _valid_score(weight):
            weight = None
        # Use only safe summary fields. In particular, originality feedback
        # can contain another student's filename/submission ID, so omit it.
        if analyzer == 'similarity_flag':
            if raw.get('flagged'):
                feedback = 'Similarity signal flagged for instructor review; not proof of copying.'
            elif status == 'completed':
                feedback = 'Similarity screening completed; not proof of independent authorship.'
            else:
                feedback = 'Similarity screening has no conclusive result.'
        else:
            feedback = _short(raw.get('feedback', ''), 440)
        row = {'label': label, 'analyzer': analyzer, 'status': status,
               'score': score, 'weight': weight,
               'contribution': round(100 * score * weight, 2) if
               score is not None and weight is not None else None,
               'feedback': feedback}
        output['metrics'].append(row)

        ev = raw.get('evidence') or {}
        if analyzer in ('io_tests', 'edge_case_runner'):
            for case in ev.get('results', []):
                if case.get('passed') is False and len(output['failures']) < 3:
                    status_note = _short(case.get('execution_status', ''), 65)
                    output['failures'].append({
                        'metric': label, 'input': _short(case.get('stdin'), 320),
                        'expected': _short(case.get('expected_output'), 320),
                        'actual': _short(case.get('actual_output'), 320),
                        'reason': ('Execution status: ' + status_note if status_note != 'ok'
                                   else 'Program output differs from expected output.')
                    })
        if analyzer == 'llm_review':
            for issue in (ev.get('ai') or {}).get('issues', [])[:2]:
                if isinstance(issue, dict) and issue.get('suggestion'):
                    output['suggestions'].append(_short(issue['suggestion'], 250))
        if analyzer == 'sanitizer_run':
            for finding in ev.get('findings', [])[:2]:
                if isinstance(finding, dict):
                    output['suggestions'].append('Memory safety: ' +
                        _short(finding.get('type', 'diagnostic'), 90) + '. ' +
                        _short(finding.get('detail', ''), 200))
        if (analyzer in ('static_analysis', 'function_split', 'compiler_warnings')
                and score is not None and score < 1 and feedback):
            output['suggestions'].append(feedback)

    # Use the same canonical grading rule as the evaluation pipeline. Old saved
    # reports remain readable without pretending they contain new analyzer data.
    final = calculate_final_grade(report, rules)
    output['overall_score'] = final['score']
    output['provisional'] = final['provisional']
    output['grade_status'] = final['status']
    output['ungraded_metrics'] = final['missing']

    # Keep report brief and avoid duplicating essentially identical advice.
    output['suggestions'] = list(dict.fromkeys(output['suggestions']))[:5]
    return output


def get_student_evaluation(student_id: int, submission_id: int) -> dict | None:
    """Return an OWNED submission's sanitized saved evaluation, or None.

    Do not reuse get_first_pass here: its detailed teacher-only evidence includes
    peer identities, private diagnostics, and the approved test bank.
    """
    require_role(student_id, {'student'})
    with SessionLocal() as session:
        submission = session.get(Submission, submission_id)
        if submission is None:
            raise ValueError('Submission not found.')
        if submission.student_id != student_id:
            raise PermissionError('You cannot access another student\'s report.')
        assignment = session.get(Assignment, submission.assignment_id)
        result = session.scalar(select(FirstPassResult).where(
            FirstPassResult.submission_id == submission_id))
        if result is None:
            return None
        user = session.get(User, student_id)
        # Configured metric weights are available from a private rules file.
        # A missing rules file simply prevents a combined weighted score.
        rules = {}
        if assignment.rules_path:
            from services.storage_utils import read_private
            try:
                rules = json.loads(read_private(assignment.rules_path, 'rules').decode('utf-8'))
            except (OSError, UnicodeError, ValueError):
                rules = {}
        meta = {'submission_id': submission.id, 'student': user.full_name or user.username,
                'assignment': assignment.title,
                'filename': submission.original_filename or 'source.c',
                'evaluated_at': result.evaluated_at}
        return _build_evaluation(meta, json.loads(result.report_json), rules)


def _score_text(value) -> str:
    return f'{value * 10:.1f}/10' if value is not None else 'N/A'


def _lines(text: str, *, width=95) -> list[str]:
    results = []
    for line in text.splitlines() or ['']:
        results.extend(wrap(line, width, replace_whitespace=False,
                            drop_whitespace=False, break_long_words=True) or [''])
    return results


def render_txt_report(data: dict) -> bytes:
    """Build a readable, self-contained text report (no file-system writes)."""
    lines = [
        'AUTOGRADER | STUDENT EVALUATION', '=' * 78,
        f'Student       : {data["student"]}',
        f'Assignment    : {data["assignment"]}',
        f'Submission    : #{data["submission_id"]} - {data["filename"]}',
        f'Evaluated     : {data["evaluated_at"]}',
        f'First pass    : {str(data["first_pass"]).capitalize()}',
        '',
    ]
    if data['overall_score'] is None:
        lines.append('Overall grade : N/A (no valid rubric or graded analyzers)')
    else:
        prefix = 'Provisional weighted score' if data['provisional'] else 'Weighted score'
        lines.append(f'{prefix}: {data["overall_score"]:.1f} / 100')
    if data['provisional']:
        lines.append('Note: N/A analyzers excluded; available weights normalized to 100%.')
    lines += ['', 'ANALYZER SCORES', '-' * 78,
              f'{"Analyzer":<23} {"Score":<10} {"Weight":<9} Result']
    for m in data['metrics']:
        wt = f'{m["weight"] * 100:g}%' if m['weight'] is not None else '--'
        lines.append(f'{m["label"]:<23} {_score_text(m["score"]):<10} '
                     f'{wt:<9} {m["status"]}')
    lines += ['', 'FEEDBACK', '-' * 78]
    for m in data['metrics']:
        if m['feedback'] and m['status'] not in ('not_configured', 'pending'):
            lines.extend(_lines(f'{m["label"]}: {m["feedback"]}'))
    if data['failures']:
        lines += ['', 'EXAMPLES OF FAILED TESTS', '-' * 78]
        for number, failure in enumerate(data['failures'], 1):
            lines.append(f'{number}. {failure["metric"]}')
            for key, name in [('input', 'Input'), ('expected', 'Expected'),
                              ('actual', 'Actual'), ('reason', 'Why')]:
                for line in _lines(failure[key] or '(empty)', width=68):
                    lines.append(f'   {name:<9}: {line}')
                    name = ''
    if data['suggestions']:
        lines += ['', 'SUGGESTED IMPROVEMENTS', '-' * 78]
        for suggestion in data['suggestions']:
            lines.extend(_lines('- ' + suggestion))
    lines += ['', 'USEFULNESS', '-' * 78]
    for item in USEFULNESS:
        lines.extend(_lines('- ' + item))
    lines += ['', 'LIMITATIONS', '-' * 78]
    for item in LIMITATIONS:
        lines.extend(_lines('- ' + item))
    lines += ['', 'Report based on the saved evaluation for this submission.']
    return ('\n'.join(lines) + '\n').encode('utf-8')


def render_pdf_report(data: dict) -> bytes:
    """Generate a lightweight A4 PDF from the same sanitized report data."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                   Table, TableStyle, KeepTogether)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=19 * mm,
                            rightMargin=19 * mm, topMargin=20 * mm,
                            bottomMargin=19 * mm,
                            title='AutoGrader - Student Evaluation')
    ink = colors.HexColor('#171c36')
    muted = colors.HexColor('#64748b')
    accent = colors.HexColor('#4f46e5')
    lines = colors.HexColor('#e8ebf3')
    sheet = getSampleStyleSheet()
    sheet.add(ParagraphStyle(name='AGTitle', fontName='Helvetica-Bold', fontSize=19,
                             textColor=ink, leading=24, spaceAfter=7))
    sheet.add(ParagraphStyle(name='AGMeta', fontName='Helvetica', fontSize=8.7,
                             textColor=muted, leading=14, spaceAfter=3))
    sheet.add(ParagraphStyle(name='AGSection', fontName='Helvetica-Bold', fontSize=10,
                             textColor=ink, leading=15, spaceBefore=13, spaceAfter=7))
    sheet.add(ParagraphStyle(name='AGBody', fontName='Helvetica', fontSize=8.7,
                             textColor=ink, leading=13, spaceAfter=5, alignment=TA_LEFT,
                             wordWrap='CJK'))
    sheet.add(ParagraphStyle(name='AGCell', fontName='Helvetica', fontSize=8,
                             textColor=ink, leading=11, wordWrap='CJK'))
    sheet.add(ParagraphStyle(name='AGCellHead', fontName='Helvetica-Bold', fontSize=8,
                             textColor=colors.white, leading=12))
    sheet.add(ParagraphStyle(name='AGCode', fontName='Courier', fontSize=8,
                             textColor=ink, leading=11, wordWrap='CJK'))

    def P(value, style='AGBody'):
        return Paragraph(escape(_short(value, 680)).replace('\n', '<br/>'), sheet[style])

    story = [Paragraph('AutoGrader / Evaluation', sheet['AGTitle']),
             P(data['student'] + '  |  ' + data['assignment'], 'AGMeta'),
             P(f'Submission #{data["submission_id"]}  |  {data["filename"]}', 'AGMeta'),
             P('Evaluated: ' + data['evaluated_at'] +
               '  |  First pass: ' + str(data['first_pass']).capitalize(), 'AGMeta'),
             Spacer(1, 9)]

    if data['overall_score'] is None:
        total = 'Overall grade: Not available (incomplete rubric)'
    else:
        total = ('Provisional weighted score' if data['provisional'] else 'Weighted score')
        total += f': {data["overall_score"]:.1f} / 100'
    score_box = Table([[P(total)]], colWidths=[172 * mm])
    score_box.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f3f5ff')),
        ('BOX', (0, 0), (-1, -1), 0.5, lines),
        ('LEFTPADDING', (0, 0), (-1, -1), 11),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
    ]))
    story += [score_box, Paragraph('Analyzer scores', sheet['AGSection'])]
    rows = [[P(s, 'AGCellHead') for s in ('Analyzer', 'Score', 'Weight', 'Status')]]
    for metric in data['metrics']:
        rows.append([P(metric['label'], 'AGCell'),
                     P(_score_text(metric['score']), 'AGCell'),
                     P(f'{metric["weight"] * 100:g}%' if metric['weight'] is not None else '--', 'AGCell'),
                     P(metric['status'].replace('_', ' ').capitalize(), 'AGCell')])
    table = Table(rows, colWidths=[66 * mm, 29 * mm, 27 * mm, 50 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), accent),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f8f9fc')]),
        ('LINEBELOW', (0, -1), (-1, -1), 0.4, lines),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 9),
        ('TOPPADDING', (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
    ]))
    story.append(table)
    story.append(Paragraph('Feedback', sheet['AGSection']))
    feedback = [m for m in data['metrics'] if m['feedback']
                and m['status'] not in ('not_configured', 'pending')]
    if not feedback:
        story.append(P('No detailed analyzer feedback is available.'))
    for metric in feedback:
        story.append(P(metric['label'] + ': ' + metric['feedback']))

    if data['failures']:
        story.append(Paragraph('Failed test examples', sheet['AGSection']))
        for item in data['failures']:
            # Plain paragraph layout avoids rendering hidden or raw binary logs.
            detail = [P(label + ': ' + (item[field] or '(empty)')) for field, label in
                      [('input', 'Input'), ('expected', 'Expected'),
                       ('actual', 'Actual'), ('reason', 'Reason')]]
            story.append(KeepTogether([P(item['metric']), *detail, Spacer(1, 5)]))
    if data['suggestions']:
        story.append(Paragraph('Suggested improvements', sheet['AGSection']))
        for item in data['suggestions']:
            story.append(P('- ' + item))
    story.append(KeepTogether([Paragraph('Usefulness', sheet['AGSection']),
                              *(P('- ' + value) for value in USEFULNESS)]))
    story.append(KeepTogether([Paragraph('Limitations', sheet['AGSection']),
                              *(P('- ' + value) for value in LIMITATIONS)]))

    def footer(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(lines)
        canvas.line(19 * mm, 16 * mm, A4[0] - 19 * mm, 16 * mm)
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(muted)
        canvas.drawString(19 * mm, 12 * mm, 'AutoGrader  |  Submission report')
        canvas.drawRightString(A4[0] - 19 * mm, 12 * mm, str(document.page))
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
