"""AutoGrader teacher/student dashboard and secure first-pass evaluation."""
from datetime import datetime, timedelta
from html import escape
import json

import streamlit as st

from database.init_db import initialize_database
from services.authentication import authenticate_user, get_user_by_id
from services.assignment_service import (
    create_assignment, download_assignment_document, download_assignment_rules,
    get_all_assignments, update_assignment, delete_assignment,
)
from services.dates import LOCAL_ZONE, as_local, is_overdue, pretty_deadline
from services.rules_service import (example_rules_bytes, get_modularity_preferences, get_originality_preferences,
                                    validate_rules, rules_warnings, get_edge_types)
from services.first_pass_service import get_first_pass, run_first_pass
from services.report_service import get_student_evaluation, render_txt_report, render_pdf_report
from services.teacher_report_service import get_teacher_report_json
from services.testset_service import (list_cases, add_teacher_case, delete_case,
    set_reference, generate_tests, save_case_bank, approve_all_drafts,
    import_teacher_cases_json, parse_teacher_cases_json)

from services.student_service import create_student, delete_student, list_students, update_student
from services.submission_service import (
    MAX_SOURCE_SIZE, delete_submission, get_student_submissions,
    get_submission_source, get_teacher_submissions, save_submission,
)

st.set_page_config(
    page_title='AutoGrader | C Programming', page_icon='◈',
    layout='wide', initial_sidebar_state='collapsed',
)
initialize_database()  # Idempotent: also migrates older SQLite databases.

if 'user_id' not in st.session_state:
    st.session_state.user_id = None

# All HTML below is static and contains NO database / user-supplied text.
# UI stays usable if any future Streamlit version changes its CSS selectors.
st.markdown('''
<style>
/* Monochrome workspace. Selectors supplement Streamlit styles; no app internals are modified. */
:root { --ag-black:#101010; --ag-white:#ffffff; --ag-line:#919191; --ag-ink:#121212; }
html, body, [class*="stApp"] {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
.stApp { background:#f5f5f5 !important; color:var(--ag-ink) !important; }
/* Clear the fixed Streamlit toolbar so the brand is never clipped. */
[data-testid="stMainBlockContainer"], .main .block-container, .block-container {
  width:100% !important; max-width:none !important; box-sizing:border-box;
  padding:5.35rem clamp(1rem, 2.5vw, 3rem) 3rem !important;
}
[data-testid="stSidebar"], [data-testid="collapsedControl"] {display:none !important;}
header[data-testid="stHeader"] { background:#f5f5f5 !important; }
header[data-testid="stHeader"] * { color:#111 !important; }
h1,h2,h3 { letter-spacing:-.025em; color:#111; }
h1 { font-weight:760 !important; }
/* Top banner: all brand and navigation elements belong to a single dark bar. */
.st-key-ag_topbar {
  background:var(--ag-black) !important; color:#fff !important;
  border:1.5px solid #111 !important; border-radius:23px !important;
  padding:1.25rem clamp(1.15rem, 2vw, 2rem) .9rem !important;
  box-shadow:0 9px 25px rgba(0,0,0,.11) !important;
  margin-bottom:1.4rem;
}
.st-key-ag_topbar [data-testid="stMarkdownContainer"] p,
.st-key-ag_topbar [data-testid="stCaptionContainer"],
.st-key-ag_topbar [data-testid="stCaptionContainer"] *,
.st-key-ag_topbar label { color:#f3f3f3 !important; }
.ag-brand {
  font-size:clamp(1.9rem, 2.8vw, 2.75rem); line-height:1.25;
  font-weight:830; letter-spacing:-.055em; padding:.08rem 0 .35rem;
  color:#fff !important; display:block; overflow:visible;
}
.ag-brand-mark { color:#fff; margin-right:.55rem; }
.ag-account {font-size:.92rem; font-weight:800; color:#fff !important; text-align:center;
  padding-top:.65rem; letter-spacing:.01em;}
.st-key-ag_topbar .ag-account, .st-key-ag_topbar .ag-account * {color:#fff !important;}
.st-key-ag_topbar [data-testid="stButton"] button {
  background:#161616 !important; color:#fff !important;
  border:1.4px solid #c5c5c5 !important;
}
.st-key-ag_topbar [data-testid="stButton"] button:hover {background:#303030 !important; border-color:#fff !important;}
.st-key-ag_topbar [data-testid="stButtonGroup"] button {
  background:transparent !important; color:#fff !important;
  border:1.2px solid #777 !important;
}
.st-key-ag_topbar [data-testid="stButtonGroup"] button[aria-pressed="true"],
.st-key-ag_topbar [data-testid="stButtonGroup"] button[aria-selected="true"],
.st-key-ag_topbar [data-testid="stButtonGroup"] button[data-active="true"] {
  background:#fff !important; border-color:#fff !important; color:#111 !important;
}
.st-key-ag_topbar [data-testid="stButtonGroup"] button:hover { border-color:#fff !important; }
/* Section title: black header with white text, anchored well below the toolbar. */
.ag-section-header {
  background:#111; border:1.4px solid #111; border-radius:21px;
  padding:clamp(1.15rem, 2.1vw, 1.85rem) clamp(1.2rem, 2.5vw, 2.4rem);
  margin:.15rem 0 1.25rem; box-shadow:0 7px 20px rgba(0,0,0,.07);
}
.ag-eyebrow {font-size:.74rem; color:#d0d0d0 !important; letter-spacing:.14em;
  font-weight:750; text-transform:uppercase; margin:0 0 .46rem;}
.ag-hero {font-size:clamp(1.9rem, 3vw, 2.85rem); font-weight:780;
  letter-spacing:-.05em; line-height:1.18; margin:0 0 .35rem; color:#fff !important;}
.ag-subtitle {font-size:clamp(.88rem,1.1vw,1.05rem); color:#dfdfdf !important;
  line-height:1.6; margin:0;}
/* Visible borders and soft corners for inputs, forms, cards and results. */
[data-testid="stVerticalBlockBorderWrapper"] {
  border:1.35px solid var(--ag-line) !important; border-radius:19px !important;
  background:#fff !important; box-shadow:0 2px 8px rgba(0,0,0,.035) !important;
}
[data-testid="stMetric"] {background:#fff; border:1.3px solid #888 !important;
  border-radius:17px; padding:15px 19px;}
[data-testid="stMetricValue"] {font-weight:750; letter-spacing:-.04em;}
[data-testid="stDataFrame"] {border:1px solid #999; border-radius:13px; overflow:hidden;}
[data-testid="stButton"] button, [data-testid="stDownloadButton"] button,
div[data-testid="stFormSubmitButton"] button {
  border-radius:13px !important; font-weight:650; min-height:42px;
  color:#111; border:1.25px solid #8a8a8a; background:#fff;
  transition:background-color .22s ease, color .22s ease,
    border-color .22s ease, box-shadow .22s ease, transform .22s ease !important;
}
[data-testid="stButton"] button:hover, [data-testid="stDownloadButton"] button:hover,
div[data-testid="stFormSubmitButton"] button:hover {
  border-color:#111 !important; box-shadow:0 4px 12px rgba(0,0,0,.09) !important;
  transform:translateY(-1px);
}
.stButton > button[kind="primary"], div[data-testid="stFormSubmitButton"] button[kind="primary"] {
  background:#111 !important; border-color:#111 !important; color:#fff !important;
}
.stButton > button[kind="primary"]:hover, div[data-testid="stFormSubmitButton"] button[kind="primary"]:hover {
  background:#333 !important; color:#fff !important;
}
[data-testid="stButtonGroup"] button {
  background:#fff !important; border:1.25px solid #888 !important;
  color:#111 !important; border-radius:999px !important;
  padding:.38rem 1rem !important; min-height:41px;
  box-shadow:none !important; font-weight:650;
  transition:background-color .22s ease, border-color .22s ease, box-shadow .22s ease !important;
}
[data-testid="stButtonGroup"] button[aria-pressed="true"],
[data-testid="stButtonGroup"] button[aria-selected="true"],
[data-testid="stButtonGroup"] button[data-active="true"] {
  color:#fff !important; background:#111 !important; border-color:#111 !important;
}
[data-testid="stTabs"] [role="tab"] {
  border:1.25px solid #888 !important; border-radius:999px !important;
  padding:.4rem 1rem !important; margin-right:.4rem !important;
  color:#111 !important; background:#fff !important;
  transition:background-color .22s ease, color .22s ease !important;
}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] {
  color:#fff !important; background:#111 !important; border-color:#111 !important;
}
[data-testid="stTabs"] [data-baseweb="tab-highlight"],
[data-testid="stTabs"] [data-baseweb="tab-border"],
[data-testid="stTabs"] [role="tablist"]::after,
[data-testid="stTabs"] [role="tab"][aria-selected="true"]::after,
[data-testid="stButtonGroup"] button::after {
  display:none !important; background:transparent !important; border:0 !important;
}
[data-testid="stFileUploader"], div[data-baseweb="input"] > div,
div[data-baseweb="select"] > div, textarea {border-radius:13px !important;}
div[data-baseweb="input"] > div:focus-within,
div[data-baseweb="select"] > div:focus-within {
  border-color:#333 !important; box-shadow:0 0 0 1px #333 !important;
}
[data-testid="stAlert"] {background:#f5f5f5 !important;
  color:#161616 !important; border:1.25px solid #999 !important; border-radius:14px !important;}
[data-testid="stAlert"] * {color:#222 !important;}
[data-testid="stProgressBar"] div[role="progressbar"] {background:#111 !important;}
a {color:#111;}
@media (max-width:760px) {
  [data-testid="stMainBlockContainer"], .main .block-container, .block-container {
    padding:5.1rem .85rem 2.5rem !important;
  }
  .st-key-ag_topbar {padding:1rem !important; border-radius:18px !important;}
  [data-testid="stHorizontalBlock"] {flex-wrap:wrap; gap:.5rem;}
  [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
    min-width:min(100%,250px) !important; flex:1 1 100% !important;
  }
  .ag-account {text-align:left; padding:0;}
  .ag-brand {font-size:2rem;}
  .ag-section-header {border-radius:16px; padding:1.25rem;}
}
@media (prefers-reduced-motion:reduce) {
  [data-testid="stButton"] button, [data-testid="stDownloadButton"] button,
  [data-testid="stButtonGroup"] button, [data-testid="stTabs"] [role="tab"] {
    transition:none !important; transform:none !important;
  }
}
</style>
''', unsafe_allow_html=True)



def heading(section, title, subtitle):
    # Input strings to heading() are developer-controlled, not user-generated.
    st.markdown(f'<div class="ag-section-header"><div class="ag-eyebrow">{section}</div>'
                f'<div class="ag-hero">{title}</div>'
                f'<div class="ag-subtitle">{subtitle}</div></div>', unsafe_allow_html=True)

def floating_tabs(label, items, key):
    """One selected pill renders only its panel; no hidden forms/analyzers run."""
    choices = list(items)
    if not choices:
        raise ValueError('Navigation requires at least one option.')
    choice = st.pills(label, choices, default=choices[0], key=key,
                      selection_mode='single', label_visibility='collapsed')
    return choice if choice in choices else choices[0]



def date_time_fields(key, default=None):
    local = as_local(default) if default else datetime.now(LOCAL_ZONE) + timedelta(days=7)
    left, right = st.columns(2)
    with left:
        due_date = st.date_input('Deadline date', value=local.date(), key=f'{key}_date')
    with right:
        due_time = st.time_input('Deadline time (IST)', value=local.time().replace(second=0, microsecond=0),
                                 key=f'{key}_time', step=900)
    return datetime.combine(due_date, due_time, tzinfo=LOCAL_ZONE)


def status_caption(assignment):
    if assignment['deadline_at']:
        if is_overdue(assignment['deadline_at']):
            st.caption(f'Closed · Deadline: {pretty_deadline(assignment["deadline_at"])}')
        else:
            st.caption(f'Open · Due {pretty_deadline(assignment["deadline_at"])}')
    else:
        st.caption('No deadline configured')


def show_login():
    with st.container(key='ag_topbar'):
        st.markdown('<div class="ag-brand"><span class="ag-brand-mark">◈</span> AutoGrader</div>',
                    unsafe_allow_html=True)
    spacer, content, spacer2 = st.columns([1, 1.12, 1])
    with content:
        with st.container(border=True):
            heading('Welcome back', 'Sign in.', 'One workspace for assignments, students, and C submissions.')
            with st.form('login_form'):
                username = st.text_input('Username', placeholder='Your username')
                password = st.text_input('Password', type='password', placeholder='Your password')
                login = st.form_submit_button('Continue', type='primary', use_container_width=True)
            if login:
                user = authenticate_user(username, password)
                if user is None:
                    st.error('Invalid credentials or inactive account.')
                else:
                    st.session_state.user_id = user['id']
                    st.rerun()
            st.caption('Accounts are created by an instructor or by the setup command.')


def teacher_overview(user):
    heading('Overview', 'Your workspace.', 'Assignments, students, and submission activity in one place.')
    section = floating_tabs('Overview sections', ['Summary', 'Recent activity'], 'teacher_overview_section')
    if section == 'Summary':
        assignments = get_all_assignments(teacher_id=user['id'])
        students = list_students(user['id'])
        submissions = get_teacher_submissions(user['id'])
        a, b, c, d = st.columns(4)
        a.metric('Assignments', len(assignments))
        b.metric('Students', len(students))
        c.metric('Submissions', len(submissions))
        d.metric('Open deadlines', sum(not is_overdue(x['deadline_at'])
                                       for x in assignments if x['deadline_at']))
        st.caption('First pass and nine analyzers (complexity needs Linux/WSL2 Callgrind); completed rubric metrics contribute to the final weighted grade.')
    else:
        submissions = get_teacher_submissions(user['id'])
        if not submissions:
            st.info('New submission activity will appear here.')
        else:
            for sub in submissions[:8]:
                with st.container(border=True):
                    st.write(f'**{sub["student_name"]}** · {sub["assignment_title"]}')
                    st.caption(f'{sub["filename"]} · {pretty_deadline(sub["created_at"])}')


def teacher_assignments(user):
    heading('Assignments', 'Assignment workspace.', 'Create, edit and manage one assignment at a time.')
    section = floating_tabs('Assignment sections', ['Create assignment', 'Manage assignments'],
                            'teacher_assignment_section')
    if section == 'Create assignment':
        teacher_create_assignment(user)
    else:
        teacher_manage_assignments(user)


def teacher_create_assignment(user):
    with st.container(border=True):
        col_title, col_template = st.columns([3, 1])
        with col_title:
            st.caption('Accepted assignment formats: PDF, DOCX, TXT, MD (up to 12 MB).')
        with col_template:
            st.download_button('↓ Example rules.json', data=example_rules_bytes(),
                               file_name='execution_limits.json', mime='application/json', use_container_width=True)
        with st.form('assignment_create', clear_on_submit=True):
            basics, quality, tests = st.tabs(['Assignment details', 'Grading preferences', 'Test generation'])
            with basics:
                title = st.text_input('Assignment title *', placeholder='e.g. Sorting algorithms')
                notes = st.text_area('Instructions (optional)', placeholder='Short introduction or submission guidelines', height=94)
                deadline = date_time_fields('create_deadline')
                doc = st.file_uploader('Assignment document *', type=['pdf', 'docx', 'txt', 'md'])
                rules = st.file_uploader('Grading rules (optional JSON override)', type=['json'])
                if rules is not None:
                    try:
                        for rule_warning in rules_warnings(validate_rules(rules.getvalue())):
                            st.warning('Uploaded rules: ' + rule_warning)
                    except ValueError as exc:
                        st.error(f'Invalid uploaded rules: {exc}')
                sample_input = st.text_area('Sample stdin (used for the smoke run)', height=100,
                                            help='The program receives this exact stdin during its preliminary smoke run.')
            with quality:
                require_helpers = st.checkbox('Require helper functions', value=False,
                    help='Uncheck for simple problems where main() is sufficient. This choice overrides uploaded rules.')
                min_functions = st.number_input('Minimum total functions (only if helpers required)',
                    min_value=2, max_value=50, value=2, step=1)
                st.caption('Leave JSON empty for default limits: 1 second and 64 MB. Custom analyzer params in grading rules are checked on upload.')
                st.caption('Originality review')
                originality_enabled = st.checkbox('Compare code with other students on this assignment', value=True)
                originality_threshold = st.slider('Similarity flag threshold (%)', 50, 100, 85, 5)
                originality_use_ai = st.checkbox('Use Qwen for originality review (weight from rules.json)', value=True,
                    help='Advisory only; AI authorship cannot be reliably determined from C source code.')
            with tests:
                oracle = st.file_uploader('Trusted reference solution oracle.c (optional)', type=['c'],
                                          help='Only provide trusted source. Native GCC executes the reference without isolation.')
                constraints = st.text_area('Input constraints for Qwen', height=65)
                gen_on_publish = st.checkbox('Generate Qwen tests on publish (normal + 5 edge)', value=False)
                normal_count = st.number_input('Normal Qwen cases', min_value=1, max_value=15, value=5)
                sample_output = st.text_area('Sample stdout for Qwen', height=65)
            sent = st.form_submit_button('Publish assignment', type='primary', use_container_width=True)
        if sent:
            if not doc:
                st.error('Upload an assignment document.')
            else:
                try:
                    created_id = create_assignment(user['id'], title, notes, deadline, doc.name, doc.getvalue(),
                                                   rules.name if rules else None, rules.getvalue() if rules else None,
                                                   sample_input, require_helpers=require_helpers,
                                                   min_functions=int(min_functions),
                                                   originality_enabled=originality_enabled,
                                                   originality_threshold=originality_threshold / 100,
                                                   originality_use_ai=originality_use_ai)
                    feedback = 'Assignment published.'
                    if oracle:
                        set_reference(user['id'], created_id, oracle.name, oracle.getvalue())
                    if gen_on_publish:
                        try:
                            outcome = generate_tests(user['id'], created_id,
                                normal_count=int(normal_count), edge_count=5,
                                constraints=constraints, sample_output=sample_output)
                            feedback += f' Qwen cases saved: {outcome["cases_added"]} ({outcome["auto_approved"]} reference-verified and auto-approved).'
                            if outcome['warnings']:
                                feedback += ' Generation warnings: ' + '; '.join(outcome['warnings'])
                        except (ValueError, OSError) as exc:
                            feedback += f' Generation incomplete: {exc}. Retry from Manage Assignments.'
                    st.session_state.flash = feedback
                    st.rerun()
                except (ValueError, PermissionError, OSError) as exc:
                    st.error(str(exc))


def teacher_manage_assignments(user):
    assignments = get_all_assignments(teacher_id=user['id'])
    if not assignments:
        st.info('No assignments published yet.')
        return
    lookup = {assignment['id']: assignment for assignment in assignments}
    selected_id = st.selectbox('Select assignment', list(lookup),
                               format_func=lambda key: lookup[key]['title'],
                               key='teacher_assignment_picker')
    a = lookup[selected_id]
    section = floating_tabs('Selected assignment',
                            ['Overview', 'Edit assignment', 'Test cases', 'Delete assignment'],
                            f'teacher_assignment_subsection_{selected_id}')
    with st.container(border=True):
        if section == 'Overview':
            teacher_assignment_overview(user, a)
        elif section == 'Edit assignment':
            teacher_assignment_edit(user, a)
        elif section == 'Test cases':
            teacher_assignment_tests(user, a)
        else:
            teacher_assignment_delete(user, a)


def teacher_assignment_delete(user, a):
    st.warning('Permanent deletion removes this assignment and every student submission, grading result and test case attached to it.')
    st.caption('This cannot be undone. Only the teacher who created this assignment can delete it.')
    with st.form(f'delete_assignment_{a["id"]}'):
        confirm_text = st.text_input('Type the assignment title to confirm')
        confirmed = st.checkbox('I understand every submitted C file for this assignment will be deleted')
        clicked = st.form_submit_button('Delete assignment permanently')
    if clicked:
        if not confirmed or confirm_text != a['title']:
            st.error('Check the confirmation box and type the exact assignment title.')
        else:
            try:
                summary = delete_assignment(user['id'], a['id'])
                st.session_state.flash = (
                    f'Assignment deleted: {summary["submissions_deleted"]} submissions and '
                    f'{summary["test_cases_deleted"]} test cases removed.')
                if summary['file_cleanup_warnings']:
                    st.warning('Database deletion succeeded, but some stored files need manual cleanup.')
                st.rerun()
            except (PermissionError, ValueError, OSError) as exc:
                st.error(str(exc))


def teacher_assignment_overview(user, a):
    status_caption(a)
    st.write(a['description'])
    downloads = st.columns(2)
    with downloads[0]:
        if a['document_path']:
            try:
                st.download_button('↓ Download assignment',
                                   data=download_assignment_document(a['id'], user['id']),
                                   file_name=a['document_name'] or 'assignment.pdf',
                                   key=f'down_doc_{a["id"]}', use_container_width=True)
            except (OSError, PermissionError) as exc:
                st.error(f'Document unavailable: {exc}')
        else:
            st.caption('Legacy assignment: no document uploaded')
    with downloads[1]:
        if a['rules_path']:
            try:
                st.download_button('↓ Download grading rules',
                                   data=download_assignment_rules(a['id'], user['id']),
                                   file_name=a['rules_name'] or 'rules.json', mime='application/json',
                                   key=f'down_rules_{a["id"]}', use_container_width=True)
            except (OSError, PermissionError) as exc:
                st.error(f'Rules unavailable: {exc}')
        else:
            st.caption('Legacy assignment: no grading rules uploaded')
    if a['rules_path']:
        saved_rules = validate_rules(download_assignment_rules(a['id'], user['id']))
        for rule_warning in rules_warnings(saved_rules):
            st.warning('Grading rules: ' + rule_warning)
        edge_scenarios = get_edge_types(saved_rules)
        if edge_scenarios:
            st.caption('Configured Logical edge scenarios: ' + ', '.join(edge_scenarios))


def teacher_assignment_edit(user, a):
    if a['rules_path']:
        try:
            saved_rules = validate_rules(download_assignment_rules(a['id'], user['id']))
            saved_require_helpers, saved_min_functions = get_modularity_preferences(saved_rules)
            saved_orig_enabled, saved_orig_threshold, saved_orig_ai = get_originality_preferences(saved_rules)
        except (ValueError, OSError, PermissionError) as exc:
            st.warning(f'Cannot load saved grading preferences: {exc}')
            saved_require_helpers, saved_min_functions = False, 2
            saved_orig_enabled, saved_orig_threshold, saved_orig_ai = False, 0.85, True
    else:
        saved_require_helpers, saved_min_functions = False, 2
        saved_orig_enabled, saved_orig_threshold, saved_orig_ai = False, 0.85, True
    with st.form(f'edit_assignment_{a["id"]}'):
        details, modularity_tab, originality_tab = st.tabs(
            ['Assignment details', 'Modularity', 'Originality'])
        with details:
            title = st.text_input('Title', value=a['title'])
            notes = st.text_area('Instructions', value=a['description'], height=95)
            deadline = date_time_fields(f'edit_{a["id"]}', a['deadline_at'])
            new_doc = st.file_uploader('Replace assignment document (optional)',
                                       type=['pdf', 'docx', 'txt', 'md'], key=f'edit_doc_{a["id"]}')
            new_rules = st.file_uploader('Replace grading rules (optional)', type=['json'],
                                         key=f'edit_rules_{a["id"]}')
            if new_rules is not None:
                try:
                    for rule_warning in rules_warnings(validate_rules(new_rules.getvalue())):
                        st.warning('Replacement rules: ' + rule_warning)
                except ValueError as exc:
                    st.error(f'Invalid replacement rules: {exc}')
            sample_input = st.text_area('Sample stdin (for Smoke Run)', value=a['sample_input'] or '',
                                         key=f'edit_sample_{a["id"]}', height=90)
        with modularity_tab:
            require_helpers = st.checkbox('Require helper functions', value=saved_require_helpers,
                key=f'edit_helpers_{a["id"]}',
                help='Uncheck for problems that do not require a helper function. This setting overrides uploaded rules.')
            min_functions = st.number_input('Minimum total functions (only if helpers required)',
                min_value=2, max_value=50, value=max(2, saved_min_functions), step=1,
                key=f'edit_min_functions_{a["id"]}')
        with originality_tab:
            originality_enabled = st.checkbox('Compare with other students', value=saved_orig_enabled,
                key=f'edit_orig_enabled_{a["id"]}')
            originality_threshold = st.slider('Similarity flag threshold (%)', 50, 100,
                int(round(saved_orig_threshold * 100 / 5) * 5), 5,
                key=f'edit_orig_threshold_{a["id"]}')
            originality_use_ai = st.checkbox('Qwen originality review (configured advisory weight)',
                value=saved_orig_ai, key=f'edit_orig_ai_{a["id"]}')
        saved = st.form_submit_button('Save changes', type='primary')
    if saved:
        try:
            update_assignment(user['id'], a['id'], title, notes, deadline,
                              new_doc.name if new_doc else None, new_doc.getvalue() if new_doc else None,
                              new_rules.name if new_rules else None, new_rules.getvalue() if new_rules else None,
                              sample_input, require_helpers=require_helpers,
                              min_functions=int(min_functions),
                              originality_enabled=originality_enabled,
                              originality_threshold=originality_threshold / 100,
                              originality_use_ai=originality_use_ai)
            st.session_state.flash = 'Assignment updated. Re-run first-pass checks to refresh grading.'
            st.rerun()
        except (ValueError, PermissionError, OSError) as exc:
            st.error(str(exc))


def teacher_assignment_tests(user, a):
    st.caption('Normal cases grade Correctness; edge cases grade Logical. '
               'Only approved test cases contribute to grades. Run trusted C programs only.')
    section = floating_tabs('Test sections',
                            ['Add test case', 'Generate tests', 'Test bank'],
                            f'teacher_assignment_test_section_{a["id"]}')
    if section == 'Add test case':
        teacher_assignment_add_case(user, a)
    elif section == 'Generate tests':
        teacher_assignment_generate_cases(user, a)
    else:
        teacher_assignment_test_bank(user, a)


def teacher_assignment_add_case(user, a):
    with st.form(f'add_test_{a["id"]}', clear_on_submit=True):
        st.markdown('**Add teacher-provided case**')
        kind = st.selectbox('Test category', ['normal', 'edge'], key=f'case_kind_{a["id"]}')
        case_title = st.text_input('Case title', key=f'case_title_{a["id"]}')
        case_input = st.text_area('Raw stdin', key=f'case_input_{a["id"]}', height=85)
        case_output = st.text_area('Expected stdout', key=f'case_output_{a["id"]}', height=85)
        add = st.form_submit_button('Add approved teacher case')
    if add:
        try:
            if not add_teacher_case(user['id'], a['id'], kind, case_input, case_output, case_title):
                st.warning('Duplicate stdin: existing case kept.')
            else:
                st.rerun()
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))

    st.divider()
    st.markdown('**Import test cases from JSON**')
    st.caption('Uploaded expected outputs are teacher-approved only after your confirmation. Existing duplicate stdin is skipped.')
    template = {'cases': [
        {'title': 'Simple input', 'category': 'normal', 'stdin': '2 3\n', 'expected_output': '5\n'},
        {'title': 'Boundary input', 'category': 'edge', 'edge_type': 'zero_values',
         'stdin': '0 0\n', 'expected_output': '0\n'},
    ]}
    st.download_button('Download JSON template',
                       data=json.dumps(template, indent=2).encode('utf-8'),
                       file_name='test_cases_template.json', mime='application/json',
                       key=f'case_json_template_{a["id"]}')
    uploaded = st.file_uploader('Upload test-case JSON (maximum 100 cases)', type=['json'],
                                key=f'case_json_upload_{a["id"]}')
    if uploaded is not None:
        raw = uploaded.getvalue()
        try:
            preview = parse_teacher_cases_json(raw)
            st.caption(f'{len(preview)} valid case(s): '
                       f'{sum(item["category"] == "normal" for item in preview)} normal, '
                       f'{sum(item["category"] == "edge" for item in preview)} edge.')
            confirmed = st.checkbox('I reviewed the expected outputs and approve these test cases',
                                    key=f'case_json_confirm_{a["id"]}')
            if st.button('Import approved JSON cases', key=f'case_json_import_{a["id"]}',
                         disabled=not confirmed):
                outcome = import_teacher_cases_json(user['id'], a['id'], raw)
                st.session_state.flash = (
                    f'Imported {outcome["added"]} approved test case(s); '
                    f'{outcome["skipped_duplicates"]} duplicate(s) skipped.')
                st.rerun()
        except (ValueError, PermissionError, OSError) as exc:
            st.error(str(exc))


def teacher_assignment_generate_cases(user, a):
    with st.form(f'generate_test_{a["id"]}'):
        st.markdown('**Generate Qwen tests**')
        ref = st.file_uploader('Trusted oracle.c (optional; replaces existing reference)',
                               type=['c'], key=f'oracle_{a["id"]}')
        constraints = st.text_area('Input format and constraints', key=f'constraints_{a["id"]}', height=85)
        stdout_sample = st.text_area('Sample stdout', key=f'sample_output_{a["id"]}', height=70)
        normal_requested = st.number_input('Normal correctness cases', min_value=1, max_value=15,
                                           value=5, key=f'normal_count_{a["id"]}')
        st.caption('Logical edge cases: 5. Qwen reads the assignment document and saved sample stdin. '
                   'For recognized formats, a deterministic generator can fill missing test inputs.')
        if a.get('has_reference'):
            st.caption('Trusted oracle.c already saved: generated outputs will be verified and approved automatically.')
        else:
            st.warning('No reference solution saved. Upload trusted oracle.c above to automate expected outputs and approval; '
                       'otherwise Qwen tests remain unapproved drafts.')
        do_generate = st.form_submit_button('Generate cases using Qwen')
    if do_generate:
        try:
            if ref:
                set_reference(user['id'], a['id'], ref.name, ref.getvalue())
            with st.spinner('Generating and validating test candidates…'):
                outcome = generate_tests(user['id'], a['id'],
                    normal_count=int(normal_requested), edge_count=5,
                    constraints=constraints, sample_output=stdout_sample)
            st.success(f'{outcome["cases_added"]} tests added; '
                       f'{outcome["auto_approved"]} reference-verified and automatically approved.')
            totals = outcome['category_counts']
            reviewed = outcome['approved_counts']
            st.info(f'Assignment test bank: Normal {totals["normal"]}/{int(normal_requested)} '
                    f'(approved {reviewed["normal"]}); Logical {totals["edge"]}/5 '
                    f'(approved {reviewed["edge"]}).')
            if totals['normal'] < int(normal_requested):
                st.error('Normal test target not reached. This problem format needs more valid distinct inputs '
                         'or a matching trusted oracle; the generator did not invent invalid cases.')
            if outcome.get('requested_edge_types'):
                st.caption('Edge coverage: ' + ', '.join(outcome.get('covered_edge_types', []))
                           + ' | Requested: ' + ', '.join(outcome['requested_edge_types']))
            for warning in outcome['warnings']:
                st.warning(warning)
        except (ValueError, PermissionError, OSError) as exc:
            st.error(str(exc))


def teacher_assignment_test_bank(user, a):
    # One shared, assignment-scoped test bank instead of one form per test.
    cases = list_cases(user['id'], a['id'])
    st.markdown('**Assignment test bank — preview and bulk edit**')
    if not cases:
        st.info('No tests yet. Add teacher cases or generate Qwen proposals.')
    else:
        approved_count = sum(bool(c['approved']) for c in cases)
        normal_total = sum(c['category'] == 'normal' for c in cases)
        edge_total = len(cases) - normal_total
        st.caption(f'{len(cases)} saved tests · {normal_total} normal · {edge_total} edge · '
                   f'{approved_count} approved. All belong to this assignment.')
        st.caption('Edit stdin, expected stdout, category or title in the table. '
                   'Toggle Approved for any unverified AI drafts. Check Delete to remove rows. '
                   'Save all changes together. Edits to verified input/output are marked teacher-reviewed.')
        rows = [{
            'ID': case['id'],
            'Category': case['category'],
            'Source': case['source'],
            'Title': case['title'],
            'Edge type': case.get('edge_type') or '',
            'stdin': case['stdin'],
            'Expected stdout': case['expected_output'],
            'Approved': bool(case['approved']),
            'Reference verified': bool(case['oracle_verified']),
            'Reason': case.get('rationale', ''),
            'Delete': False,
        } for case in cases]
        with st.form(f'test_bank_{a["id"]}'):
            edited_bank = st.data_editor(
                rows, hide_index=True, num_rows='fixed',
                use_container_width=True, height=min(580, 90 + 37 * len(rows)),
                disabled=['ID', 'Source', 'Reference verified', 'Reason'],
                column_order=['Category', 'Source', 'Title', 'Edge type', 'stdin',
                              'Expected stdout', 'Approved', 'Reference verified',
                              'Reason', 'Delete'],
                column_config={
                    'Category': st.column_config.SelectboxColumn(
                        'Category', options=['normal', 'edge'], required=True),
                    'Edge type': st.column_config.TextColumn('Edge type', help='Scenario label from logical.params.cases; editable.'),
                    'stdin': st.column_config.TextColumn('stdin', width='large', required=True),
                    'Expected stdout': st.column_config.TextColumn('Expected stdout', width='large'),
                    'Approved': st.column_config.CheckboxColumn('Approved'),
                    'Delete': st.column_config.CheckboxColumn('Delete'),
                }, key=f'test_bank_editor_{a["id"]}',
            )
            save_bank = st.form_submit_button('Save test bank changes', type='primary')
        if save_bank:
            try:
                updated = save_case_bank(user['id'], a['id'], edited_bank)
                st.session_state.flash = (f'Test bank updated: {updated["updated"]} edited, '
                                          f'{updated["deleted"]} deleted.')
                st.rerun()
            except (ValueError, PermissionError, TypeError, KeyError) as exc:
                st.error(str(exc))

        if approved_count < len(cases):
            with st.form(f'approve_all_bank_{a["id"]}'):
                confirmed = st.checkbox(
                    'I have reviewed the proposed inputs and expected outputs '
                    'for the remaining unapproved cases.',
                    key=f'approve_confirmation_{a["id"]}')
                approve_bulk = st.form_submit_button(
                    f'Approve all {len(cases) - approved_count} remaining drafts')
            if approve_bulk:
                if not confirmed:
                    st.warning('Please confirm you reviewed the draft cases first.')
                else:
                    count = approve_all_drafts(user['id'], a['id'])
                    st.session_state.flash = f'Approved {count} reviewed test cases.'
                    st.rerun()

        st.download_button(
            'Download test bank (JSON)',
            data=json.dumps(cases, ensure_ascii=False, indent=2).encode('utf-8'),
            file_name='test_bank.json',
            mime='application/json', key=f'bank_export_{a["id"]}',
        )
        selected = st.selectbox('Full test preview',
                                options=[c['id'] for c in cases],
                                format_func=lambda cid: next(
                                    f'{c["category"]} · {c["title"]}'
                                    for c in cases if c['id'] == cid),
                                key=f'bank_preview_{a["id"]}')
        case = next(c for c in cases if c['id'] == selected)
        left, right = st.columns(2)
        with left:
            st.text_area('Full stdin preview', value=case['stdin'],
                         disabled=True, height=130, key=f'bank_input_{a["id"]}_{selected}')
        with right:
            st.text_area('Full expected stdout preview', value=case['expected_output'],
                         disabled=True, height=130, key=f'bank_output_{a["id"]}_{selected}')
        if case.get('rationale'):
            st.caption(f'Case rationale: {case["rationale"]}')


def teacher_students(user):
    heading('Students', 'Student management.', 'Manage accounts without clutter or internal database IDs.')
    section = floating_tabs('Student sections',
                            ['Add a student', 'Student directory', 'Edit student profile', 'Delete student'],
                            'teacher_students_section')
    if section == 'Add a student':
        teacher_add_student(user)
        return
    students = list_students(user['id'])
    if not students:
        st.info('No students yet. Use Add a student to create an account.')
        return
    if section == 'Student directory':
        st.caption(f'{len(students)} student account(s)')
        st.dataframe([
            {'Name': s['full_name'], 'Username': s['username'],
             'Roll number': s['student_number'], 'Email': s['email'],
             'Status': 'Active' if s['is_active'] else 'Inactive',
             'Submissions': s['submission_count']}
            for s in students], use_container_width=True, hide_index=True)
        return
    lookup = {s['id']: s for s in students}
    selected = st.selectbox('Select student', list(lookup),
                            format_func=lambda uid: (
                                f'{lookup[uid]["full_name"] or lookup[uid]["username"]} '
                                f'· {lookup[uid]["username"]}'), key='teacher_student_picker')
    entry = lookup[selected]
    with st.container(border=True):
        if section == 'Edit student profile':
            teacher_edit_student(user, selected, entry)
        else:
            teacher_delete_student(user, selected, entry)


def teacher_add_student(user):
    with st.container(border=True):
        with st.form('create_student', clear_on_submit=True):
            c1, c2 = st.columns(2)
            with c1:
                full_name = st.text_input('Full name *', placeholder='Student name')
                username = st.text_input('Username *', placeholder='student_001')
                student_num = st.text_input('Student ID / roll number', placeholder='CS-001')
            with c2:
                email = st.text_input('Email (optional)', placeholder='student@college.edu')
                password = st.text_input('Initial password * (6+ characters)', type='password')
                confirm = st.text_input('Confirm password *', type='password')
            create = st.form_submit_button('Create student account', type='primary')
        if create:
            if password != confirm:
                st.error('Passwords do not match.')
            else:
                try:
                    student_id = create_student(user['id'], username, password, full_name, student_num, email)
                    st.session_state.flash = 'Student account created.'
                    st.rerun()
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))


def teacher_edit_student(user, selected, entry):
    with st.container():
        with st.form(f'edit_student_{selected}'):
            c1, c2 = st.columns(2)
            with c1:
                full_name = st.text_input('Full name', value=entry['full_name'])
                username = st.text_input('Username', value=entry['username'])
                student_num = st.text_input('Roll number', value=entry['student_number'])
            with c2:
                email = st.text_input('Email', value=entry['email'])
                is_active = st.toggle('Allow student to log in', value=entry['is_active'])
                password = st.text_input('New password (optional, 6+ characters)', type='password')
                password_again = st.text_input('Confirm new password (if changing)', type='password')
            save = st.form_submit_button('Save student profile', type='primary')
        if save:
            if password != password_again:
                st.error('The new password and confirmation must match.')
            else:
                try:
                    update_student(user['id'], selected, username, full_name, student_num, email, is_active, password)
                    st.session_state.flash = 'Student profile saved.'
                    st.rerun()
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))


def teacher_delete_student(user, selected, entry):
    st.warning('This permanently deletes the student account, submissions and uploaded C files.')
    with st.form(f'delete_student_{selected}'):
        confirmation = st.text_input(f'Type DELETE {entry["username"]} to confirm')
        do_delete = st.form_submit_button('Permanently delete student')
    if do_delete:
        if confirmation != f'DELETE {entry["username"]}':
            st.error('Confirmation did not match. Nothing was deleted.')
        else:
            try:
                delete_student(user['id'], selected)
                st.session_state.flash = 'Student removed.'
                st.rerun()
            except (ValueError, PermissionError, OSError) as exc:
                st.error(str(exc))


ANALYZER_ITEMS = (
    ('correctness_grading', 'Correctness', 'correctness'),
    ('logical_grading', 'Logical', 'logical'),
    ('syntax_grading', 'Syntax', 'syntax'),
    ('structure_grading', 'Structure', 'structure'),
    ('modularity_grading', 'Modularity', 'modularity'),
    ('memory_safety_grading', 'Memory safety', 'memory_safety'),
    ('complexity_grading', 'Time complexity', 'complexity'),
    ('readability_grading', 'Readability', 'readability'),
    ('originality_grading', 'Originality', 'originality'),
)


def analyzer_score(result):
    """Display persisted analyzer scores; never invent a combined grade."""
    if not isinstance(result, dict):
        return 'Pending'
    score = result.get('score')
    if result.get('status') in ('completed', 'skipped') and isinstance(score, (int, float)):
        return f'{score * 10:.1f}/10'
    status = result.get('status', 'pending')
    return {'not_configured': 'Off', 'insufficient_evidence': 'N/A',
            'skipped': 'Skipped', 'unavailable': 'Unavailable',
            'inconclusive': 'N/A'}.get(status, 'Pending')


def show_first_pass(report, submission_id):
    """Keep all existing evidence but display only the selected analyzer."""
    if not report:
        st.caption('Evaluation not available yet. Run checks to generate a report.')
        return
    selected_key = f'analyzer_detail_{submission_id}'
    if selected_key not in st.session_state:
        st.session_state[selected_key] = None
    first_status = report.get('status', 'unknown')
    st.caption(f'First pass: {first_status.capitalize()} · Select a metric for details.')
    items = [('first_pass', 'First pass')]
    items.extend((detail_key, f'{title} · {analyzer_score(report.get(field))}')
                 for field, title, detail_key in ANALYZER_ITEMS)
    for start in range(0, len(items), 4):
        columns = st.columns(4)
        for column, (detail_key, label) in zip(columns, items[start:start + 4]):
            with column:
                if st.button(label, key=f'analyzer_{submission_id}_{detail_key}',
                             use_container_width=True,
                             type='primary' if st.session_state[selected_key] == detail_key
                             else 'secondary'):
                    st.session_state[selected_key] = (
                        None if st.session_state[selected_key] == detail_key else detail_key)
    # Final grade sits BELOW the complete analyzer score grid, not inside
    # an individual analyzer detail pane.
    final = report.get('final_grade') or {}
    if final.get('score') is not None:
        st.metric('FINAL WEIGHTED GRADE', f'{final["score"]:.2f} / 100')
        if final.get('provisional'):
            st.caption('Provisional: N/A analyzers are excluded and available weights are rescaled to 100%.')
            if final.get('missing'):
                st.caption('Not graded: ' + '; '.join(str(x) for x in final['missing'][:6]))
    else:
        st.metric('FINAL WEIGHTED GRADE', 'N/A')
        missing = final.get('missing') or ['Re-run checks to compute a final grade under the current rubric.']
        st.caption('No grade can be computed without a valid rubric and at least one scored analyzer. '
                   + 'Pending: ' + '; '.join(str(item) for item in missing[:6]))
    selection = st.session_state[selected_key]
    if selection is None:
        return
    st.divider()
    show_analyzer_details(report, selection, submission_id)


def show_analyzer_details(report, selected, submission_id):
    """Original diagnostic views: render on demand, without recomputing grades."""
    if selected == 'first_pass':
        overall = report.get('status', 'unknown')
        st.caption('Local GCC mode: run only trusted C programs. Native execution is not isolated.')
        st.markdown('**Preliminary evaluation**')
        if overall == 'ready':
            st.success('Compile, smoke run and static checks passed. This does not verify correctness.')
        elif overall == 'unavailable':
            st.warning('Native GCC unavailable; source upload was saved. Check gcc --version in PowerShell.')
        elif overall == 'incomplete':
            st.warning('Partial evaluation: a required step was skipped.')
        else:
            st.error('Preliminary evaluation has a blocking issue.')
        compiler = report.get('compiler', {})
        smoke = report.get('smoke', {})
        sanity = report.get('static_sanity', {})
        for title, part in [('Compile check', compiler), ('Smoke run', smoke), ('Static sanity', sanity)]:
            st.write(f'**{title}** · {part.get("status", "unknown").capitalize()}')
            st.caption(part.get('message', ''))
        if compiler.get('warning_count') is not None:
            st.caption(f'GCC warning count: {compiler["warning_count"]}')
        if smoke.get('time_limit_s') is not None:
            st.caption(f'Program limit: {smoke["time_limit_s"]} seconds · {smoke["memory_limit_mb"]} MB')
        with st.container(border=True):
            st.caption('Compiler diagnostics and smoke output')
            st.code(compiler.get('diagnostics') or '(no compiler diagnostics)', language='text')
            st.write('**Program stdout**')
            st.code(smoke.get('stdout') or '(no output)', language='text')
            if smoke.get('stderr'):
                st.write('**Program stderr**')
                st.code(smoke['stderr'], language='text')
            st.write('**Static checks**')
            st.json(sanity.get('checks', {}))
        for reason in report.get('execution_gate', {}).get('reasons', []):
            st.warning('Execution analyzers blocked: ' + reason)
        for rule_warning in report.get('rules_warnings', []):
            st.warning('Grading rule: ' + rule_warning)
    if selected in ('correctness', 'logical'):
        for field, label in [('correctness_grading', 'Correctness grading'),
                             ('logical_grading', 'Logical / edge-case grading')]:
            if selected != ('correctness' if field == 'correctness_grading' else 'logical'):
                continue
            st.subheader(label)
            result = report.get(field)
            if not result:
                st.info('No evaluation stored. Re-run grading after approving test cases.')
                continue
            if result['status'] == 'skipped':
                st.warning(result.get('feedback') or 'Skipped after first-pass failure/warning (0/10).')
            elif result['status'] == 'not_configured':
                st.info('No approved tests in this category.')
            elif result['status'] != 'completed':
                st.warning(result.get('feedback', 'Evaluation unavailable.'))
            else:
                st.caption(f'Score: {result["score"] * 10:.1f}/10')
                st.write(result['feedback'])
                ev = result.get('evidence', {})
                st.caption(f'Output comparison: {ev.get("output_comparison", "exact")}')
                if field == 'logical_grading' and ev.get('requested_edge_types'):
                    st.caption('Requested edge types: ' + ', '.join(ev['requested_edge_types'])
                               + ' | Covered: ' + ', '.join(ev.get('covered_edge_types', [])))
                details = ev.get('results', [])
                if details:
                    st.caption(f'Test cases: {sum(bool(c.get("passed")) for c in details)}/{len(details)} passed')
                    case_index = st.selectbox('Inspect a test case', range(len(details)),
                        format_func=lambda i: (f'Case {i + 1} · '
                                               f'{"Pass" if details[i].get("passed") else "Fail"} · '
                                               f'{details[i].get("title", "")}'.rstrip(' · ')),
                        key=f'case_{submission_id}_{selected}')
                    case = details[case_index]
                    st.caption(f'Origin: {case.get("source", "unknown")} · '
                               f'Execution: {case.get("execution_status", "unknown")}')
                    st.write('**stdin**')
                    st.code(case.get('stdin', ''), language='text')
                    st.write('**Expected stdout**')
                    st.code(case.get('expected_output', ''), language='text')
                    st.write('**Actual stdout**')
                    st.code(case.get('actual_output', ''), language='text')
            st.caption('Only teacher-approved cases contribute. Scores are independent and not an overall grade.')
            st.divider()
    if selected == 'memory_safety':
        st.subheader('Memory Safety grading')
        memory = report.get('memory_safety_grading')
        if memory is None:
            st.info('Memory Safety not evaluated yet. Re-run first-pass checks.')
        elif memory['status'] == 'not_configured':
            st.info('Memory Safety is not enabled in rules.json.')
        elif memory['status'] == 'skipped':
            st.warning(memory.get('feedback') or 'Skipped after first-pass failure/warning (0/10).')
        else:
            if memory.get('score') is not None:
                st.caption(f'Score: {memory["score"] * 10:.1f}/10')
            else:
                st.warning(f'Memory Safety: {memory["status"]} (not graded)')
            st.write(memory.get('feedback',''))
            ev=memory.get('evidence',{})
            backend = ev.get('backend', 'linux_gcc')
            name = 'GCC ASan / UBSan (WSL2)' if backend == 'wsl2_gcc' else 'GCC ASan / UBSan (Linux)'
            completed = ev.get('completed_checks')
            if completed is None:
                st.caption(f'Memory checker: {name} · '
                           f'Attempted: {ev.get("executed",0)}/{ev.get("total",0)}')
            else:
                st.caption(f'Memory checker: {name} · '
                           f'Completed: {completed}/{ev.get("total",0)} · '
                           f'Attempted: {ev.get("attempted", ev.get("executed", 0))}')
            if ev.get('inconclusive_cases'):
                st.error(f'{len(ev["inconclusive_cases"])} memory checks were inconclusive. '
                         'No score has been assigned.')
                st.dataframe([{'Test': t.get('title') or t.get('case_id'),
                               'Status': t.get('status'),
                               'Reason': t.get('reason','')}
                              for t in ev['inconclusive_cases']],
                             hide_index=True,use_container_width=True)
            if ev.get('flags'):
                st.caption(f'Sanitizer flags: {ev["flags"]}')
            if ev.get('memory_tool'):
                st.caption(f'Execution backend: {ev["memory_tool"]}')
            if ev.get('compiler'):
                st.caption(f'Compiler: {ev["compiler"]}')
            if ev.get('coverage_note'):
                st.warning(ev['coverage_note'])
            if ev.get('sanitizer_memory_limit_mb'):
                st.caption(f'Memory-check RSS allowance: {ev["sanitizer_memory_limit_mb"]} MB')
            findings = ev.get('findings', [])
            if findings:
                finding_index = st.selectbox('Inspect memory finding', range(len(findings)),
                    format_func=lambda i: (
                        f'{i + 1}. {findings[i].get("type", "Finding").replace("_", " ").title()} '
                        f'· Test {i + 1}'),
                    key=f'memory_finding_{submission_id}')
                finding = findings[finding_index]
                st.write(finding.get('detail', ''))
                st.write('**Failing input**')
                st.code(finding.get('stdin', ''), language='text')
                st.write('**Memory-check diagnostic**')
                st.code(finding.get('diagnostic', ''), language='text')
            tests = ev.get('tests', [])
            if tests:
                st.write('**Memory-check tests**')
                st.dataframe([{'Case': t.get('case_id'), 'Title': t.get('title'),
                               'Category': t.get('category'), 'Status': t.get('status'),
                               'Reason': t.get('reason', ''),
                               'Exit code': t.get('exit_code'),
                               'Findings': ', '.join(t.get('findings', [])) or 'none'}
                              for t in tests], hide_index=True, use_container_width=True)
                failed = [t for t in tests if t.get('status') not in ('ok', 'nonzero_exit')]
                if failed:
                    test_index = st.selectbox('Inspect memory diagnostics', range(len(failed)),
                        format_func=lambda i: (
                            f'{failed[i].get("title") or failed[i].get("case_id")} '
                            f'({failed[i].get("status", "unknown")})'),
                        key=f'memory_diagnostics_{submission_id}')
                    test = failed[test_index]
                    st.write(test.get('reason') or 'No error explanation available.')
                    if test.get('tool_stderr'):
                        st.code(test['tool_stderr'], language='text')
                    elif test.get('stderr'):
                        st.code(test['stderr'], language='text')
            st.caption(ev.get('leak_detection_note','No issue detected does not prove memory safety.'))
        st.divider()
    if selected == 'complexity':
        st.subheader('Time complexity')
        complexity = report.get('complexity_grading') or {}
        status = complexity.get('status', 'pending')
        evidence = complexity.get('evidence') or {}
        if 'complexity_grading' not in report:
            st.info('This saved evaluation was created before complexity grading was installed. '
                    'Select Run first-pass checks again for this submission.')
        elif status == 'not_configured':
            st.info('Time complexity is disabled for this assignment. '
                    'Add complexity_estimator to the rules and re-run evaluation.')
        elif status in ('unavailable', 'insufficient_evidence', 'skipped'):
            st.warning(complexity.get('feedback') or 'Time complexity was skipped or is unavailable.')
            if status == 'unavailable':
                st.caption('Profiling requires Linux/WSL2 with Valgrind/Callgrind and GCC. '
                           'Running on native Windows cannot produce this score.')
        elif status == 'completed' and isinstance(complexity.get('score'), (int, float)):
            st.caption(f'Score: {complexity["score"] * 10:.1f}/10')
            st.write(complexity.get('feedback', ''))
        else:
            st.caption('Complexity evaluation is pending or inconclusive. '
                       'Check the diagnostics below or re-run the evaluation.')
        if evidence:
            st.caption('Required: ' + str(evidence.get('expected_complexity') or 'Not set') +
                       ' · Empirical: ' + str(evidence.get('empirical_detected_complexity') or 'N/A'))
            fit = evidence.get('fit') or {}
            st.caption('Method: ' + str(evidence.get('profiling_method') or 'Unavailable') +
                       ' · Confidence: ' + str(fit.get('confidence') or 'N/A'))
            trials = evidence.get('trials') or []
            if trials:
                st.dataframe([{'Input size': x.get('n'), 'Instructions': x.get('cost')}
                              for x in trials], hide_index=True, use_container_width=True)
            with st.expander('Profiling diagnostics'):
                st.json(evidence)
        st.caption('Measured instruction growth is an estimate, not a formal worst-case proof.')
        st.divider()
    if selected == 'syntax':
        st.subheader('Syntax grading')
        syntax = report.get('syntax_grading')
        if syntax is None:
            st.info('No syntax score stored yet. Click "Run first-pass checks" above to calculate it.')
        elif syntax['status'] == 'not_configured':
            st.info('Syntax is not enabled in the assignment rules.')
        elif syntax['status'] == 'unavailable':
            st.warning('Syntax grading requires a completed compiler check.')
        else:
            score = syntax['score']
            st.caption(f'Score: {score * 10:.1f}/10')
            st.write(syntax['feedback'])
            if syntax['rule_source'] == 'default_preview':
                st.caption('Preview score using defaults: 0 allowed warnings, '
                           '0.1 deduction per warning. Not an overall grade.')
            elif syntax.get('weight') is not None:
                st.caption(f'Instructor weight: {syntax["weight"] * 100:g}%. '
                           'Overall grading will be added after the other analyzers.')
            evidence = syntax.get('evidence', {})
            st.caption(f'GCC warnings: {evidence.get("warning_count", 0)}')
            if evidence.get('diagnostics'):
                st.caption('Syntax issues identified by GCC')
                st.code('\n'.join(evidence['diagnostics']), language='text')
        st.caption('Syntax grading uses compiler diagnostics only; it does not test program correctness.')

        st.divider()
    if selected == 'structure':
        st.subheader('Structure grading')
        structure = report.get('structure_grading')
        if structure is None:
            st.info('No structure score stored yet. Click "Run first-pass checks" above to calculate it.')
        elif structure['status'] == 'not_configured':
            st.info('Structure grading is not enabled in the assignment rules. '
                    'Add the static_analysis metric to enable it.')
        elif structure['status'] == 'unavailable':
            st.warning(structure.get('feedback', 'Structure analyzer is unavailable.'))
        else:
            score = structure['score']
            st.caption(f'Score: {score * 10:.1f}/10')
            st.write(structure['feedback'])
            if structure['rule_source'] == 'default_preview':
                st.caption('Preview score using defaults: maximum complexity 8, '
                           'maximum function length 40 lines, 0.1 deduction per violation.')
            elif structure.get('weight') is not None:
                st.caption(f'Instructor weight: {structure["weight"] * 100:g}%. '
                           'Overall grading is not implemented yet.')
            evidence = structure.get('evidence', {})
            st.caption(f'Functions: {evidence.get("function_count", 0)} · '
                       f'Max cyclomatic complexity: {evidence.get("max_cyclomatic_found", 0)} · '
                       f'Longest function: {evidence.get("max_function_length_found", 0)} lines')
            if evidence.get('functions'):
                st.caption('Per-function measurements')
                st.dataframe(evidence['functions'], hide_index=True, use_container_width=True)
            cppcheck = evidence.get('cppcheck', {})
            st.caption(f'Cppcheck: {cppcheck.get("message", "not checked")} '
                       'Cppcheck findings are informational and do not change this score.')
            if cppcheck.get('issues'):
                st.caption('Cppcheck findings')
                st.dataframe(cppcheck['issues'], hide_index=True, use_container_width=True)
        st.caption('Structure is source-only analysis; it also runs after a smoke timeout.')


        st.divider()
    if selected == 'modularity':
        st.subheader('Modularity grading')
        modularity = report.get('modularity_grading')
        if modularity is None:
            st.info('No modularity score stored yet. Click "Run first-pass checks" above to calculate it.')
        elif modularity['status'] == 'not_configured':
            st.info('Modularity is not enabled in the assignment rules. '
                    'Add the function_split metric to enable it.')
        elif modularity['status'] == 'unavailable':
            st.warning(modularity.get('feedback', 'Modularity analyzer is unavailable.'))
        else:
            score = modularity['score']
            st.caption(f'Score: {score * 10:.1f}/10')
            st.write(modularity['feedback'])
            if modularity['rule_source'] == 'default_preview':
                st.caption('Preview: helpers not required, minimum 1 function. '
                           'Instructor rules can require helper functions.')
            elif modularity.get('weight') is not None:
                st.caption(f'Instructor weight: {modularity["weight"] * 100:g}%. '
                           'Overall grading is not implemented yet.')
            evidence = modularity.get('evidence', {})
            st.caption(f'Functions: {evidence.get("function_count", 0)} · '
                       f'Helpers: {evidence.get("helper_count", 0)} · '
                       f'Minimum required: {evidence.get("min_functions", 1)}')
            if evidence.get('function_names'):
                st.caption('Defined functions')
                st.write(', '.join(evidence['function_names']))
        st.caption('Modularity examines function definitions only, even if the smoke run times out.')

        st.divider()
    if selected == 'readability':
        st.subheader('Readability grading')
        readability = report.get('readability_grading')
        if readability is None:
            st.info('No readability score stored yet. Click "Run first-pass checks" to calculate it.')
        elif readability['status'] == 'not_configured':
            st.info('Readability is not enabled in the assignment rules. '
                    'Add the llm_review metric to enable it.')
        else:
            evidence = readability.get('evidence', {})
            ai = evidence.get('ai', {})
            score = readability.get('score')
            method = evidence.get('scoring_method', readability.get('scoring_method'))
            if score is None:
                st.warning('This older report has no final readability grade. Re-run first-pass checks.')
            else:
                st.caption(f'Score: {score * 10:.1f}/10')
                if method == 'llm':
                    st.success(f'Graded entirely by {ai.get("model", "Ollama")}; seed {ai.get("seed", 42)}.')
                elif method == 'python_configured':
                    st.info('Python grading was explicitly selected in the assignment rules.')
                elif method == 'python_fallback':
                    st.warning('Python fallback grading used because no usable LLM grade was available.')
                else:
                    st.caption('Grading method not recorded; re-run checks to use the updated analyzer.')
            st.write(readability.get('feedback', ''))
            if readability['rule_source'] == 'default_preview':
                st.caption('Preview using default readability rules. Not part of an overall grade.')
            elif readability.get('weight') is not None:
                st.caption(f'Instructor metric weight: {readability["weight"] * 100:g}%. '
                           'Overall grading is not implemented yet.')
            if evidence:
                st.caption(f'Lines: {evidence.get("line_count", 0)} · '
                           f'Formatting observations: {evidence.get("issue_count", 0)}')
                if evidence.get('issues'):
                    st.caption('Python formatting observations (only scored on fallback)')
                    st.dataframe(evidence['issues'], hide_index=True, use_container_width=True)
                st.markdown('**Qwen / Ollama readability review**')
                if ai.get('status') == 'completed':
                    if ai.get('strengths'):
                        st.write('Strengths:')
                        for strength in ai['strengths']:
                            st.write(f'• {strength}')
                    if ai.get('issues'):
                        st.write('Evidence-backed improvements:')
                        for issue in ai['issues']:
                            st.write(f'• `{issue["evidence"]}` — {issue["suggestion"]}')
                    else:
                        st.caption('No verified AI readability issues were found.')
                    if ai.get('rejected_issues'):
                        st.caption(f'{ai["rejected_issues"]} unsupported or duplicate suggestion(s) ignored.')
                    if ai.get('cached'):
                        st.caption('Reused saved AI review for this source, model and prompt.')
                elif ai.get('status') in ('unavailable', 'invalid_response'):
                    st.info(ai.get('message', 'LLM review unavailable; Python fallback used.'))
                else:
                    st.caption(ai.get('message', 'LLM review is disabled in the grading rules.'))
        st.caption('A valid Qwen review provides 100% of readability marks; Python provides the '
                   'full fallback grade if Qwen cannot. Fixed seed 42 and saved reviews help consistency. '
                   'Readability still runs when the smoke test times out.')



        st.divider()
    if selected == 'originality':
        st.subheader('Originality grading and file comparison')
        originality = report.get('originality_grading')
        if originality is None:
            st.info('No originality results stored yet. Run first-pass checks to calculate them.')
        elif originality['status'] == 'not_configured':
            st.caption('Originality review is disabled for this assignment.')
        else:
            evidence = originality.get('evidence', {})
            source_filename = originality.get('source_filename') or 'source.c'
            st.caption(f'Submitted file: {source_filename}')
            if originality['status'] == 'insufficient_evidence':
                st.info('Originality grade: N/A. Upload a C file from another student to the same '
                        'assignment, then run first-pass checks again.')
                st.caption(f'Eligible comparisons: {evidence.get("compared_count", 0)}. '
                           'Very short programs may be excluded from similarity scoring.')
            else:
                score = originality.get('score')
                similarity = evidence.get('max_similarity')
                grade_col, similarity_col = st.columns(2)
                with grade_col:
                    if score is not None:
                        st.caption(f'Provisional score: {score * 10:.1f}/10')
                    else:
                        st.caption('Provisional score: N/A')
                with similarity_col:
                    st.caption('Highest similarity: ' +
                               (f'{similarity * 100:.1f}%' if similarity is not None else 'N/A'))
                st.caption(evidence.get('grade_basis', 'Similarity-based review') +
                           '. Not an automatic academic-integrity deduction.')
                if originality.get('flagged'):
                    st.warning('Similarity threshold exceeded — teacher review recommended.')
                else:
                    st.caption('Below the configured similarity review threshold.')
                matches = evidence.get('matches', [])
                if matches:
                    st.markdown('**Compared submitted files (same assignment, different students)**')
                    st.dataframe([
                        {
                            'Current file': source_filename,
                            'Matched file': match.get('filename') or 'source.c',
                            'Student': match.get('student_name', 'Unknown'),
                            'Submission ID': match['submission_id'],
                            'Similarity': f'{match["similarity"] * 100:.1f}%',
                        } for match in matches
                    ], hide_index=True, use_container_width=True)
                    st.caption(f'Showing {len(matches)} of {evidence.get("compared_count", 0)} '
                               'eligible peer submission(s), ranked by similarity.')
                ai = evidence.get('ai', {})
                st.markdown('**Qwen structural similarity review (20% of provisional grade when available)**')
                if ai.get('status') == 'completed':
                    if evidence.get('review_priority') is not None:
                        st.caption(f'Weighted overlap signal: {evidence["review_priority"] * 100:.1f}%.')
                    for note in ai.get('observations', []):
                        st.write('• ' + note)
                    if ai.get('cached'):
                        st.caption('Reused saved AI review from the prior evaluation.')
                elif ai.get('status') == 'unavailable':
                    st.info('Qwen review unavailable; the grade shown above uses token similarity '
                            'only and is provisional.')
                    st.caption(ai.get('message', ''))
                else:
                    st.caption('AI review disabled; the grade uses token similarity only.')
            st.write(originality.get('feedback', ''))
            st.caption('The originality grade is a similarity-based screening indicator, not proof '
                       'of copying or AI authorship. Teachers should inspect flagged matches before '
                       'assigning any academic penalty. New peer submissions can change these results.')

def teacher_submissions(user):
    heading('Submissions', 'Review submissions.', 'Select a student submission, then open the section you need.')
    assignments = get_all_assignments(teacher_id=user['id'])
    students = list_students(user['id'])
    assignment_options = {0: 'All assignments', **{a['id']: a['title'] for a in assignments}}
    student_options = {0: 'All students', **{s['id']: s['full_name'] or s['username'] for s in students}}
    left, right = st.columns(2)
    with left:
        aid = st.selectbox('Assignment', list(assignment_options),
                           format_func=lambda k: assignment_options[k], key='review_assignment_filter')
    with right:
        sid = st.selectbox('Student', list(student_options),
                           format_func=lambda k: student_options[k], key='review_student_filter')
    subs = get_teacher_submissions(user['id'], aid or None, sid or None)
    if not subs:
        st.info('No matching submissions.')
        return
    st.caption(f'{len(subs)} matching submission(s)')
    lookup = {sub['id']: sub for sub in subs}
    submission_id = st.selectbox('Select submission', list(lookup),
        format_func=lambda key: (f'{lookup[key]["student_name"]} · '
                                 f'{lookup[key]["assignment_title"]} · '
                                 f'{lookup[key]["filename"]} · '
                                 f'{pretty_deadline(lookup[key]["created_at"])}'),
        key='teacher_submission_picker')
    sub = lookup[submission_id]
    section = floating_tabs('Submission sections',
                            ['Evaluation', 'Source code', 'Delete submission'],
                            f'teacher_submission_section_{submission_id}')
    with st.container(border=True):
        st.caption(f'{sub["student_name"]} · {sub["assignment_title"]} · '
                   f'Submitted {pretty_deadline(sub["created_at"])}')
        if section == 'Evaluation':
            if st.button('Run first-pass checks', key=f'run_first_pass_{submission_id}',
                         type='primary'):
                with st.spinner('Running local GCC and configured grading analyzers…'):
                    try:
                        run_first_pass(user['id'], submission_id)
                        st.rerun()
                    except (OSError, ValueError, PermissionError) as exc:
                        st.error(str(exc))
            try:
                saved_evaluation = get_first_pass(user['id'], submission_id)
                if saved_evaluation is not None:
                    # Export is independently authorized in the service layer and
                    # built from saved results (no grading or LLM calls here).
                    report_json = get_teacher_report_json(user['id'], submission_id)
                    if report_json is not None:
                        st.download_button(
                            'Download report.json', data=report_json,
                            file_name='report.json', mime='application/json',
                            key=f'teacher_json_{submission_id}', use_container_width=False)
                show_first_pass(saved_evaluation, submission_id)
            except (ValueError, PermissionError, OSError) as exc:
                st.error(str(exc))
        elif section == 'Source code':
            st.caption(f'Original filename: {sub["filename"]}')
            st.caption(f'SHA-256: {sub["source_hash"]}')
            try:
                source = get_submission_source(user['id'], submission_id)
                st.code(source.decode('utf-8'), language='c')
                st.download_button('Download C source', data=source,
                                   file_name=sub['filename'], mime='text/x-csrc',
                                   key=f'source_{submission_id}')
            except (OSError, UnicodeError, PermissionError, ValueError) as exc:
                st.error(f'Cannot load source file: {exc}')
        else:
            st.warning('Deleting the submission also deletes its saved evaluation. This cannot be undone.')
            confirm = st.checkbox('I understand this will permanently delete the submission',
                                  key=f'confirm_submission_{submission_id}')
            if st.button('Delete submission', key=f'delete_submission_{submission_id}',
                         disabled=not confirm):
                try:
                    delete_submission(user['id'], submission_id)
                    st.session_state.flash = 'Submission deleted.'
                    st.rerun()
                except (OSError, PermissionError, ValueError) as exc:
                    st.error(str(exc))


def student_dashboard(user, page):
    assignments = get_all_assignments()
    submissions = get_student_submissions(user['id'])
    if page == 'Overview':
        heading('Overview', 'My workspace.', 'Your assignments and submission activity.')
        a, b, c = st.columns(3)
        a.metric('Assignments', len(assignments))
        b.metric('Your submissions', len(submissions))
        c.metric('Open deadlines', sum(not is_overdue(x['deadline_at'])
                                       for x in assignments if x['deadline_at']))
        section = floating_tabs('Overview sections', ['Upcoming assignments', 'Recent submissions'],
                                'student_overview_section')
        if section == 'Upcoming assignments':
            if not assignments:
                st.info('No assignments have been published yet.')
            for assignment in assignments:
                with st.container(border=True):
                    st.write(f'**{assignment["title"]}**')
                    status_caption(assignment)
        else:
            if not submissions:
                st.info('Your submissions will appear here.')
            for sub in submissions[:6]:
                with st.container(border=True):
                    st.write(f'**{sub["title"]}** · {sub["filename"]}')
                    st.caption(f'Submitted {pretty_deadline(sub["created_at"])}')
    elif page == 'Assignments':
        heading('Assignments', 'My assignments.', 'Read instructions and submit your C program.')
        student_assignment_page(user, assignments)
    else:
        heading('Submissions', 'Submission history.', 'Download the evaluation for your submitted C program.')
        student_submission_page(user, submissions)


def student_assignment_page(user, assignments):
    if not assignments:
        st.info('No assignments available yet. Your teacher can publish one from the dashboard.')
        return
    lookup = {assignment['id']: assignment for assignment in assignments}
    assignment_id = st.selectbox('Select assignment', list(lookup),
                                 format_func=lambda key: lookup[key]['title'],
                                 key='student_assignment_picker')
    a = lookup[assignment_id]
    section = floating_tabs('Assignment sections', ['Instructions', 'Submit code'],
                            f'student_assignment_section_{assignment_id}')
    with st.container(border=True):
        st.write(f'**{a["title"]}**')
        status_caption(a)
        if section == 'Instructions':
            st.write(a['description'])
            if a['document_path']:
                try:
                    st.download_button('Download assignment',
                                       data=download_assignment_document(assignment_id, user['id']),
                                       file_name=a['document_name'] or 'assignment.pdf',
                                       key=f'student_doc_{assignment_id}', use_container_width=True)
                except (OSError, PermissionError) as exc:
                    st.error(f'Assignment file unavailable: {exc}')
            else:
                st.caption('No document attached. Follow the instructions above.')
        elif is_overdue(a['deadline_at']):
            st.warning('This assignment is closed. Contact your teacher about an extension.')
        else:
            with st.form(f'upload_submission_{assignment_id}'):
                file = st.file_uploader('C source code (.c; maximum 100 KiB)', type=['c'],
                                        key=f'upload_source_{assignment_id}')
                submit = st.form_submit_button('Submit C program', type='primary',
                                               use_container_width=True)
            if submit:
                if file is None:
                    st.error('Choose a .c file first.')
                else:
                    try:
                        result = save_submission(user['id'], assignment_id, file.name, file.getvalue())
                        # Preserve automatic grading. A failed analyzer never cancels the upload.
                        try:
                            run_first_pass(user['id'], result['submission_id'])
                        except (ValueError, OSError, PermissionError):
                            pass  # Teacher can rerun checks later.
                        st.session_state.flash = 'C program submitted successfully.'
                        st.rerun()
                    except (ValueError, PermissionError, OSError) as exc:
                        st.error(str(exc))


def student_submission_page(user, submissions):
    if not submissions:
        st.info('Your submitted C programs will appear here.')
        return
    lookup = {sub['id']: sub for sub in submissions}
    submission_id = st.selectbox('Select submitted assignment', list(lookup),
        format_func=lambda key: (f'{lookup[key]["title"]} · {lookup[key]["filename"]} '
                                 f'· {pretty_deadline(lookup[key]["created_at"])}'),
        key='student_submission_picker')
    sub = lookup[submission_id]
    section = floating_tabs('Submission sections', ['Submission details', 'Evaluation report'],
                            f'student_submission_section_{submission_id}')
    with st.container(border=True):
        if section == 'Submission details':
            st.write(f'**{sub["title"]}**')
            st.caption(f'File: {sub["filename"]} · Submitted {pretty_deadline(sub["created_at"])}')
            st.caption('Use the Evaluation report tab for feedback and downloads.')
            return
        try:
            evaluation = get_student_evaluation(user['id'], submission_id)
            if evaluation is None:
                st.info('Evaluation not available yet. Your teacher can rerun checks if needed.')
                return
            score = evaluation.get('overall_score')
            if score is None:
                st.caption('Evaluation ready · An overall weighted grade is not yet available.')
            else:
                st.metric('Weighted grade', f'{score:.1f}/100')
            left, right = st.columns(2)
            with left:
                st.download_button('Download TXT report', data=render_txt_report(evaluation),
                                   file_name='evaluation_report.txt', mime='text/plain',
                                   key=f'student_txt_{submission_id}', use_container_width=True)
            with right:
                st.download_button('Download PDF report', data=render_pdf_report(evaluation),
                                   file_name='evaluation_report.pdf', mime='application/pdf',
                                   key=f'student_pdf_{submission_id}', use_container_width=True)
        except (ValueError, PermissionError, OSError) as exc:
            st.warning(f'Report is unavailable: {exc}')


def main():
    if st.session_state.user_id is None:
        show_login()
        return
    user = get_user_by_id(st.session_state.user_id)
    if not user:
        st.session_state.user_id = None
        st.rerun()

    with st.container(key='ag_topbar'):
        brand_col, signout_col = st.columns([6.5, 1.8], vertical_alignment='center')
        with brand_col:
            st.markdown('<div class="ag-brand"><span class="ag-brand-mark">◈</span>AutoGrader</div>',
                        unsafe_allow_html=True)
        with signout_col:
            if st.button('Sign out', key='top_sign_out', use_container_width=True):
                st.session_state.user_id = None
                st.rerun()
            # Escape database-backed names before injecting styled markup.
            account = escape(str(user['full_name'] or user['username']))
            role = escape(str(user['role']).capitalize())
            st.markdown(f'<div class="ag-account">{account} · {role}</div>',
                        unsafe_allow_html=True)
        if user['role'] == 'teacher':
            page = floating_tabs('Workspace navigation',
                                 ['Overview', 'Assignments', 'Students', 'Submissions'],
                                 'teacher_top_navigation')
        elif user['role'] == 'student':
            page = floating_tabs('Workspace navigation',
                                 ['Overview', 'Assignments', 'Submissions'],
                                 'student_top_navigation')
        else:
            st.error('This account has an unsupported role.')
            return
    flash = st.session_state.pop('flash', None)
    if flash:
        st.success(flash)
    if user['role'] == 'teacher':
        if page == 'Overview':
            teacher_overview(user)
        elif page == 'Assignments':
            teacher_assignments(user)
        elif page == 'Students':
            teacher_students(user)
        else:
            teacher_submissions(user)
    else:
        student_dashboard(user, page)


if __name__ == '__main__':
    main()
