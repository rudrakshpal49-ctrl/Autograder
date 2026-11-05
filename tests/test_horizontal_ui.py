"""Regression checks for the UI-only refactor (no Streamlit server required)."""
from __future__ import annotations

import ast
from contextlib import nullcontext
from pathlib import Path

APP_PATH = Path(__file__).resolve().parents[1] / 'app.py'


def make_function(name, **namespace):
    module = ast.parse(APP_PATH.read_text(encoding='utf-8'))
    node = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == name)
    code = compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                   str(APP_PATH), 'exec')
    scope = dict(namespace)
    exec(code, scope)
    return scope[name]


class FakeST:
    def __init__(self):
        self.labels = []
        self.tables = []
        self.messages = []
        self.downloads = []

    def selectbox(self, label, options, *, format_func, **kwargs):
        options = list(options)
        self.labels.extend(format_func(item) for item in options)
        return options[0]

    def container(self, **kwargs):
        return nullcontext()

    def columns(self, number):
        return [nullcontext() for _ in range(number if isinstance(number, int) else len(number))]

    def button(self, label, **kwargs):
        return False

    def download_button(self, label, **kwargs):
        self.downloads.append((label, kwargs.get('file_name')))

    def metric(self, *args, **kwargs):
        return None

    def dataframe(self, data, **kwargs):
        self.tables.append(data)

    def caption(self, text, **kwargs):
        self.messages.append(str(text))

    def write(self, text, **kwargs):
        self.messages.append(str(text))

    def info(self, text, **kwargs):
        self.messages.append(str(text))


def test_teacher_assignment_picker_hides_internal_ids_and_opens_only_selected_section():
    st = FakeST()
    visited = []
    fn = make_function('teacher_manage_assignments', st=st,
        get_all_assignments=lambda **kw: [
            {'id': 2, 'title': 'Problem 2'}, {'id': 41, 'title': 'Sorting'}],
        floating_tabs=lambda *args: 'Overview',
        teacher_assignment_overview=lambda user, assignment: visited.append(assignment['id']),
        teacher_assignment_edit=lambda *args: (_ for _ in ()).throw(AssertionError('edit rendered')),
        teacher_assignment_tests=lambda *args: (_ for _ in ()).throw(AssertionError('tests rendered')))
    fn({'id': 1})
    assert st.labels == ['Problem 2', 'Sorting']
    assert visited == [2]


def test_student_directory_shows_no_database_id_column():
    st = FakeST()
    fn = make_function('teacher_students', st=st,
        heading=lambda *args: None,
        floating_tabs=lambda *args: 'Student directory',
        list_students=lambda teacher_id: [dict(id=2, full_name='Alex', username='alex',
            student_number='CS-01', email='', is_active=True, submission_count=1)])
    fn({'id': 1})
    assert len(st.tables) == 1
    assert 'ID' not in st.tables[0][0]
    assert st.tables[0][0]['Name'] == 'Alex'


def test_student_assignment_picker_uses_title_not_hidden_problem_id():
    st = FakeST()
    fn = make_function('student_assignment_page', st=st,
        floating_tabs=lambda *args: 'Instructions',
        status_caption=lambda a: None,
        download_assignment_document=lambda *args: b'document')
    st.download_button = lambda *args, **kwargs: None
    fn({'id': 42}, [{'id': 2, 'title': 'Problem 2', 'deadline_at': None,
                    'description': 'Instructions', 'document_path': 'doc.pdf',
                    'document_name': 'assignment.pdf'}])
    assert st.labels == ['Problem 2']
    assert not any('#2' in text for text in st.messages)


def test_navigation_is_horizontal_and_role_specific():
    source = APP_PATH.read_text(encoding='utf-8')
    assert 'st.sidebar' not in source
    assert "['Overview', 'Assignments', 'Students', 'Submissions']" in source
    assert "['Overview', 'Assignments', 'Submissions']" in source
    assert 'st.pills(' in source
    assert "['Add a student', 'Student directory', 'Edit student profile', 'Delete student']" in source
    assert "format_func=lambda key: f'#{key}" not in source
    assert "run_first_pass(user['id'], result['submission_id'])" in source
    assert 'render_txt_report(evaluation)' in source
    assert 'render_pdf_report(evaluation)' in source


def test_teacher_submission_picker_hides_ids_and_opens_saved_report_only():
    st = FakeST()
    viewed = []
    fn = make_function('teacher_submissions', st=st,
        heading=lambda *args: None,
        get_all_assignments=lambda **kw: [{'id': 2, 'title': 'Problem 2'}],
        list_students=lambda teacher_id: [{'id': 7, 'full_name': 'Alex', 'username': 'alex'}],
        get_teacher_submissions=lambda teacher_id, aid, sid: [dict(
            id=19, student_name='Alex', assignment_title='Problem 2',
            filename='solution.c', created_at='timestamp')],
        pretty_deadline=lambda ts: '1 Nov 2026',
        floating_tabs=lambda *args: 'Evaluation',
        get_first_pass=lambda user_id, submission_id: {'status': 'ready'},
        get_teacher_report_json=lambda user_id, submission_id: b'{}',
        show_first_pass=lambda report, submission_id: viewed.append(submission_id))
    fn({'id': 1})
    assert 'Problem 2' in st.labels
    assert not any('#19' in label or '#2' in label for label in st.labels)
    assert viewed == [19]
    assert st.downloads == [('Download report.json', 'report.json')]


def test_student_downloads_keep_report_features_without_visible_ids():
    st = FakeST()
    fn = make_function('student_submission_page', st=st,
        floating_tabs=lambda *args: 'Evaluation report',
        pretty_deadline=lambda ts: '1 Nov 2026',
        get_student_evaluation=lambda uid, sid: {'overall_score': 87.0},
        render_txt_report=lambda data: b'text',
        render_pdf_report=lambda data: b'%PDF-mock')
    fn({'id': 4}, [dict(id=55, title='Problem 2', filename='solution.c', created_at='timestamp')])
    assert st.labels == ['Problem 2 · solution.c · 1 Nov 2026']
    assert st.downloads == [('Download TXT report', 'evaluation_report.txt'),
                            ('Download PDF report', 'evaluation_report.pdf')]


def test_teacher_json_download_not_shown_without_saved_evaluation():
    st = FakeST()
    fn = make_function('teacher_submissions', st=st,
        heading=lambda *args: None,
        get_all_assignments=lambda **kw: [{'id': 2, 'title': 'Problem 2'}],
        list_students=lambda teacher_id: [{'id': 7, 'full_name': 'Alex', 'username': 'alex'}],
        get_teacher_submissions=lambda teacher_id, aid, sid: [dict(
            id=19, student_name='Alex', assignment_title='Problem 2',
            filename='solution.c', created_at='timestamp')],
        pretty_deadline=lambda ts: '1 Nov 2026',
        floating_tabs=lambda *args: 'Evaluation',
        get_first_pass=lambda user_id, submission_id: None,
        get_teacher_report_json=lambda *args: (_ for _ in ()).throw(
            AssertionError('JSON must not be generated without saved evaluation')),
        show_first_pass=lambda report, submission_id: None)
    fn({'id': 1})
    assert st.downloads == []
