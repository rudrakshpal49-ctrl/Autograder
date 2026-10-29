"""AutoGrader teacher/student dashboard and secure first-pass evaluation."""
from datetime import datetime, timedelta
import json

import streamlit as st

from database.init_db import initialize_database
from services.authentication import authenticate_user, get_user_by_id
from services.assignment_service import (
    create_assignment, download_assignment_document, download_assignment_rules,
    get_all_assignments, update_assignment,
)
from services.dates import LOCAL_ZONE, as_local, is_overdue, pretty_deadline
from services.rules_service import example_rules_bytes
from services.first_pass_service import get_first_pass, run_first_pass
from services.testcase_service import (save_reference, get_testset, suggest_cases,
                                       prepare_testset, approve_testset,
                                       parse_optional_cases, merge_cases)
from services.correctness_service import get_correctness, run_correctness
from services.easy_analyzer_service import get_easy_analyzers, run_easy_analyzers
from services.automatic_rubric_service import (get_automatic_configuration,
    save_configuration, recommend_and_save)
from services.student_service import create_student, delete_student, list_students, update_student
from services.submission_service import (
    MAX_SOURCE_SIZE, delete_submission, get_student_submissions,
    get_submission_source, get_teacher_submissions, save_submission,
)

st.set_page_config(
    page_title='AutoGrader | C Programming', page_icon='◈',
    layout='wide', initial_sidebar_state='expanded',
)
initialize_database()  # Idempotent: also migrates older SQLite databases.

if 'user_id' not in st.session_state:
    st.session_state.user_id = None

# All HTML below is static and contains NO database / user-supplied text.
# UI stays usable if any future Streamlit version changes its CSS selectors.
st.markdown('''
<style>
  :root { --ag-blue: #007aff; --ag-ink: #1d1d1f; --ag-muted: #6e6e73; }
  html, body, [class*="stApp"] {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", sans-serif;
  }
  .stApp { background: #f5f5f7; color: var(--ag-ink); }
  .block-container { max-width: 1220px; padding-top: 2.15rem; padding-bottom: 4rem; }
  section[data-testid="stSidebar"] { background: #fff; border-right: 1px solid #e7e7ec; }
  h1,h2,h3 { color: #1d1d1f; letter-spacing: -.035em; }
  h1 { font-weight: 750 !important; }
  .ag-eyebrow { font-size: .78rem; color: #007aff; letter-spacing: .12em; font-weight: 750; text-transform: uppercase; margin: 4px 0 7px; }
  .ag-hero { font-size: clamp(2rem,4.7vw,3.3rem); font-weight: 770; letter-spacing: -.055em; line-height: 1.13; margin: 0 0 .55rem; }
  .ag-subtitle { font-size: clamp(.94rem,1.5vw,1.09rem); color: #6e6e73; line-height:1.55; margin-bottom: 1.25rem; }
  .ag-brand { font-size: 1.5rem; font-weight: 770; letter-spacing:-.06em; padding: .65rem 0 .85rem; }
  .ag-brand-mark { color:#007aff; padding-right:.35rem; }
  .ag-pill { display:inline-block; background:#eaf3ff; color:#0061ca; border:1px solid #d5e7ff; border-radius:100px; padding:5px 12px; font-size:.79rem; font-weight:640; }
  [data-testid="stVerticalBlockBorderWrapper"] { border-radius: 21px !important; }
  [data-testid="stMetric"] { background: #fff; border: 1px solid #ebebef; border-radius: 20px; padding: 18px 20px; }
  [data-testid="stMetricValue"] { font-weight: 740; letter-spacing: -.045em; }
  .stButton > button, .stDownloadButton > button, div[data-testid="stFormSubmitButton"] button {
    border-radius: 12px; font-weight: 640; min-height: 42px;
  }
  .stButton > button[kind="primary"], div[data-testid="stFormSubmitButton"] button[kind="primary"] { background:#007aff; border-color:#007aff; color:white; }
  .stButton > button[kind="primary"]:hover, div[data-testid="stFormSubmitButton"] button[kind="primary"]:hover { background:#0064d5; border-color:#0064d5; }
  [data-testid="stFileUploader"] { border-radius: 16px; }
  [data-testid="stTabs"] [data-baseweb="tab-list"] { overflow-x: auto; }
  div[data-baseweb="input"] > div, div[data-baseweb="select"] > div { border-radius: 11px; }
  @media (max-width: 760px) {
    .block-container { padding:1.1rem .85rem 3rem; }
    [data-testid="stHorizontalBlock"] { flex-wrap: wrap; gap: .55rem; }
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { min-width: min(100%, 280px) !important; flex: 1 1 100% !important; width: 100% !important; }
    [data-testid="stMetric"] { padding: 12px 14px; }
    .ag-hero { line-height:1.2; }
  }
</style>
''', unsafe_allow_html=True)


def heading(section, title, subtitle):
    # Input strings to heading() are developer-controlled, not user-generated.
    st.markdown(f'<div class="ag-eyebrow">{section}</div><div class="ag-hero">{title}</div>'
                f'<div class="ag-subtitle">{subtitle}</div>', unsafe_allow_html=True)


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
            st.error(f'Closed · Deadline: {pretty_deadline(assignment["deadline_at"])}', icon='🔒')
        else:
            st.success(f'Open · Due {pretty_deadline(assignment["deadline_at"])}', icon='🗓️')
    else:
        st.info('Legacy assignment · No deadline configured')


def show_login():
    st.markdown('<div class="ag-brand"><span class="ag-brand-mark">◈</span> AutoGrader</div>',
                unsafe_allow_html=True)
    st.write('')
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
    heading('Dashboard', 'Teaching, simplified.', 'Manage assignments, student profiles and submission activity.')
    assignments = get_all_assignments(teacher_id=user['id'])
    students = list_students(user['id'])
    submissions = get_teacher_submissions(user['id'])
    a, b, c, d = st.columns(4)
    a.metric('Assignments', len(assignments))
    b.metric('Students', len(students))
    c.metric('Submissions', len(submissions))
    d.metric('Open deadlines', sum(not is_overdue(x['deadline_at']) for x in assignments if x['deadline_at']))
    st.write('')
    with st.container(border=True):
        st.subheader('Recent student activity')
        if not submissions:
            st.info('Submissions will appear here once students upload C source files.')
        else:
            for sub in submissions[:6]:
                st.write(f'**{sub["student_name"]}** · {sub["assignment_title"]} · Submission #{sub["id"]}')
                st.caption(f'{sub["filename"]} · {pretty_deadline(sub["created_at"])}')
                st.divider()
    st.caption('First-pass checks and PDF-generated correctness grading are available.')


def teacher_test_manager(user, assignment):
    """Teacher-only PDF-based generation and review of fixed correctness tests."""
    aid = assignment['id']
    with st.container(border=True):
        st.markdown('#### Correctness tests · Generate from assignment')
        st.caption('The uploaded assignment PDF is read automatically. Test inputs, expected outputs, and marks are teacher-only.')
        current = get_testset(user['id'], aid)

        st.markdown('**Step 1 · Generate tests directly from the assignment**')
        if assignment.get('sample_input'):
            st.caption('Ollama will also use the saved Sample stdin as an exact input-format example'
                       + (' and Sample stdout as an output example.' if assignment.get('sample_output') else '.'))
        else:
            st.caption('Tip: Add a Sample stdin in Edit Assignment to help Ollama follow complex input formats.')
        st.caption('Ollama reads the uploaded PDF and proposes complete input/output pairs. '
                   'There is no input-contract form. AI-generated tests are NOT verified '
                   'until you inspect and approve them.')
        extra_context = st.text_area('Optional clarification of problem constraints',
                                     key=f'problem_context_{aid}', height=75,
                                     placeholder='Only if the assignment PDF leaves something unclear.')
        st.caption('For multiline stdin, use JSON instead of a TXT file. '
                   'Each line of a TXT file is treated as one independent test input.')
        st.markdown('**Optional · Add your own test inputs**')
        st.caption('You can leave both fields blank. JSON supports multi-line inputs and optional expected outputs. '
                   'A TXT file treats each nonempty line as one complete input case.')
        uploaded_cases = st.file_uploader('Upload additional tests (.json or .txt, optional)',
                                          type=['json', 'txt'], key=f'extra_test_upload_{aid}')
        manual_cases = st.text_area('Or paste additional cases as JSON (optional)',
                                    key=f'manual_test_cases_{aid}', height=95,
                                    placeholder='[{"input": "2 3\\n", "category": "teacher"}]')
        num_cases = st.selectbox('Number of AI-generated test cases', [3, 6, 9],
                                 index=0, key=f'llm_test_count_{aid}',
                                 help='Start with 3 (fastest). More tests take longer on CPU.')
        if st.button('Generate test cases from assignment PDF', type='primary',
                     key=f'generate_cases_{aid}'):
            with st.status('Generating test cases with Ollama…', expanded=True) as progress:
                try:
                    extras = parse_optional_cases(manual_cases,
                                                  uploaded_cases.name if uploaded_cases else None,
                                                  uploaded_cases.getvalue() if uploaded_cases else None)
                    progress.write('Reading assignment document…')
                    generated = suggest_cases(user['id'], aid, extra_context,
                                              num_cases=num_cases,
                                              progress_callback=progress.write)
                    cases = merge_cases(generated, extras)
                    st.session_state[f'case_editor_{aid}'] = json.dumps(cases, ensure_ascii=False, indent=2)
                    st.session_state.pop(f'prepared_tests_{aid}', None)
                    progress.update(label=f'Generated {len(generated)} candidate tests',
                                    state='complete', expanded=False)
                    st.rerun()
                except (ValueError, PermissionError, OSError) as exc:
                    progress.update(label='Test generation could not finish',
                                    state='error', expanded=True)
                    st.error(str(exc))

        if st.button('Use only teacher-provided inputs',
                     key=f'manual_only_cases_{aid}'):
            try:
                extras = parse_optional_cases(manual_cases,
                                              uploaded_cases.name if uploaded_cases else None,
                                              uploaded_cases.getvalue() if uploaded_cases else None)
                cases = merge_cases([], extras)
                st.session_state[f'case_editor_{aid}'] = json.dumps(cases, ensure_ascii=False, indent=2)
                st.session_state.pop(f'prepared_tests_{aid}', None)
                st.rerun()
            except (ValueError, PermissionError, OSError) as exc:
                st.error(str(exc))

        st.divider()
        st.markdown('**Step 2 · Review inputs and proposed expected outputs**')
        if f'case_editor_{aid}' not in st.session_state:
            st.session_state[f'case_editor_{aid}'] = '[]'
        editor = st.text_area('Generated / combined test cases (editable JSON)',
                              key=f'case_editor_{aid}', height=220)

        with st.expander('Optional: upload trusted reference C solution for output verification'):
            st.caption('Not required. When uploaded, the reference program is executed in Docker '
                       'and its outputs replace the model predictions.')
            if current['reference_name']:
                st.success(f'Reference uploaded: {current["reference_name"]}')
            reference_file = st.file_uploader('Reference solution (.c, optional)', type=['c'],
                                              key=f'reference_upload_{aid}')
            if st.button('Save reference solution', key=f'reference_save_{aid}',
                         disabled=reference_file is None):
                try:
                    save_reference(user['id'], aid, reference_file.name, reference_file.getvalue())
                    st.session_state.pop(f'prepared_tests_{aid}', None)
                    st.success('Reference saved; previous test approval cleared.')
                    st.rerun()
                except (ValueError, PermissionError, OSError) as exc:
                    st.error(str(exc))

        if st.button('Prepare and preview expected outputs', key=f'prepare_tests_{aid}'):
            with st.spinner('Validating tests and verifying or completing expected outputs…'):
                try:
                    st.session_state[f'prepared_tests_{aid}'] = prepare_testset(user['id'], aid, editor)
                    st.session_state[f'prepared_editor_{aid}'] = editor
                    st.success('Candidate test set prepared. Verify the outputs below before approval.')
                except (ValueError, PermissionError, OSError) as exc:
                    st.session_state.pop(f'prepared_tests_{aid}', None)
                    st.error(str(exc))
        prepared = st.session_state.get(f'prepared_tests_{aid}')
        if prepared:
            st.markdown('**Step 3 · Approve tests for correctness grading**')
            for note in prepared.get('warnings', []):
                st.warning(note)
            for n, case in enumerate(prepared['cases'], 1):
                with st.container(border=True):
                    st.write(f'**Test {n} · {case["category"]} · Expected by: {case.get("expected_source", "teacher")}**')
                    if case.get('input_validation') == 'requires_teacher_review':
                        st.warning('Input format could not be checked automatically. Verify this stdin against the PDF.')
                    if case.get('expected_source') == 'checker':
                        st.caption('Expected output computed by a limited, deterministic Python checker.')
                    left, right = st.columns(2)
                    with left:
                        st.caption('Input')
                        st.code(case['input'] or '(empty input)', language='text')
                    with right:
                        st.caption('Expected output')
                        st.code(case['expected'] or '(empty output)', language='text')
            st.caption('To correct any expected output, edit the JSON above and click Prepare again.')
            if editor != st.session_state.get(f'prepared_editor_{aid}'):
                st.warning('Test-case JSON changed since the preview. Click Prepare again before approving.')
            approved_review = st.checkbox('I verified every input and expected output against '
                                          'the assignment PDF (especially AI-generated outputs).',
                                          key=f'confirm_review_{aid}')
            if st.button('Approve and freeze these tests', type='primary', key=f'approve_tests_{aid}',
                         disabled=(not approved_review or
                                   editor != st.session_state.get(f'prepared_editor_{aid}'))):
                try:
                    approve_testset(user['id'], aid, prepared)
                    st.session_state.pop(f'prepared_tests_{aid}', None)
                    st.success('Tests approved. Students will be evaluated against this fixed set.')
                    st.rerun()
                except (ValueError, PermissionError, OSError) as exc:
                    st.error(str(exc))

        if current['tests_approved']:
            st.success(f'Approved fixed set: {len(current["cases"])} tests')
            with st.expander('View approved inputs and expected outputs (teacher only)'):
                for n, case in enumerate(current['cases'], 1):
                    st.write(f'**#{n} · {case["category"]} ({case.get("expected_source", "teacher")})**')
                    st.code(f'INPUT:\n{case["input"]}\nEXPECTED:\n{case["expected"]}', language='text')
            if st.button('Run these tests on existing student submissions', key=f'bulk_grade_{aid}'):
                subs = get_teacher_submissions(user['id'], assignment_id=aid)
                completed, errors = 0, []
                with st.spinner(f'Running {len(subs)} submission(s) securely in Docker…'):
                    for sub in subs:
                        try:
                            run_first_pass(user['id'], sub['id'])
                            report = run_correctness(user['id'], sub['id'])
                            if report.get('status') == 'complete':
                                completed += 1
                            else:
                                errors.append(f'#{sub["id"]}: {report.get("message", report.get("status", "Unavailable"))}')
                        except (ValueError, PermissionError, OSError) as exc:
                            errors.append(f'#{sub["id"]}: {exc}')
                st.success(f'Finished: {completed} of {len(subs)} submissions evaluated.')
                for error in errors[:10]:
                    st.warning(error)
        else:
            st.caption('No tests approved yet. Students can upload, but correctness scoring is disabled.')



def teacher_adaptive_rules(user, assignment):
    """Teacher-only, automatic and directly persisted adaptive rubric controls."""
    if not assignment.get('rules_path'):
        return
    aid = assignment['id']
    suffix = f'{aid}_{str(assignment.get("rules_hash", ""))[:10]}'
    with st.expander('Automatic grading configuration · Teacher only'):
        st.caption('Safe Structure and Modularity defaults are applied to new assignments. '
                   'You can optionally improve the limits with Ollama or override them here. '
                   'Changes save directly—no JSON download or re-upload.')
        try:
            state = get_automatic_configuration(user['id'], aid)
        except (OSError, ValueError, PermissionError) as exc:
            st.error(f'Cannot load grading configuration: {exc}')
            return
        current, version = state['rubric'], state['rules_hash']
        structure = next((m for m in current['metrics'] if m['analyzer'] == 'static_analysis'), None)
        modularity = next((m for m in current['metrics'] if m['analyzer'] == 'function_split'), None)
        if structure is None or modularity is None:
            st.info('This custom grading rubric does not contain both Structure and Modularity metrics.')
            return
        sp, mp = structure.get('params') or {}, modularity.get('params') or {}
        mode = mp.get('mode', 'required' if mp.get('expect_helpers', False) else 'auto')
        config = state['configuration']
        sources = {'automatic_defaults': 'Automatic defaults',
                   'teacher_override': 'Teacher-adjusted',
                   'ollama_suggestion': 'AI-assisted, saved',
                   'teacher_uploaded': 'Custom instructor rubric'}
        st.info(f'Current source: {sources.get(config.get("source"), "Custom rubric")} · '
                f'CCN limit: {sp.get("max_cyclomatic", 8)} · '
                f'NLOC limit: {sp.get("max_function_nloc", sp.get("max_function_length", 40))} · '
                f'Modularity: {mode}')
        if config.get('reason'):
            st.caption(config['reason'])
        left, right = st.columns(2)
        with left:
            if st.button('Restore automatic defaults', key=f'auto_reset_{suffix}',
                         use_container_width=True):
                try:
                    save_configuration(user['id'], aid, expected_rules_hash=version,
                                       reset_defaults=True)
                    st.success('Automatic defaults saved to this assignment.')
                    st.rerun()
                except (ValueError, OSError, PermissionError) as exc:
                    st.error(str(exc))
        with right:
            if st.button('Improve with Ollama & save', key=f'auto_ai_{suffix}',
                         use_container_width=True):
                with st.spinner('Reading this assignment and suggesting soft thresholds…'):
                    try:
                        result = recommend_and_save(user['id'], aid, expected_rules_hash=version)
                        st.session_state[f'rubric_ai_message_{aid}'] = result['recommendation'].get('reason', '')
                        st.rerun()
                    except (ValueError, OSError, PermissionError, TimeoutError) as exc:
                        st.warning(f'Ollama recommendation unavailable. Current saved rules are unchanged: {exc}')
        if st.session_state.get(f'rubric_ai_message_{aid}'):
            st.success('AI-assisted limits saved. ' + st.session_state.pop(f'rubric_ai_message_{aid}'))
        st.caption('AI only suggests non-mandatory structure limits. Helper functions are '
                   'required only if the teacher explicitly chooses Required below. '
                   'All students use the same saved rules version.')
        show_advanced = st.checkbox('Show advanced teacher overrides',
                                    key=f'show_advanced_{aid}')
        if show_advanced:
            with st.form(f'auto_advanced_{suffix}'):
                c1, c2 = st.columns(2)
                with c1:
                    ccn = st.number_input('Maximum cyclomatic complexity', min_value=1,
                                max_value=10000, value=int(sp.get('max_cyclomatic', 8)))
                    nloc = st.number_input('Maximum function NLOC', min_value=1,
                                max_value=10000,
                                value=int(sp.get('max_function_nloc', sp.get('max_function_length', 40))))
                    st.caption('Structure uses 70% cyclomatic complexity + 30% NLOC; soft penalties.')
                with c2:
                    choices = ['auto', 'required', 'disabled']
                    selected_mode = st.selectbox('Modularity mode', choices,
                                  index=choices.index(mode),
                                  help='Auto: helper suggestions only. Required: teacher-specified count. '
                                       'Disabled: remove and redistribute metric weight.')
                    min_funcs = st.number_input('Minimum functions (Required mode only)',
                                  min_value=1, max_value=100, value=int(mp.get('min_functions', 2)))
                    suggest_ccn = st.number_input('Suggest splitting above CCN',
                                  min_value=1, max_value=10000,
                                  value=int(mp.get('suggest_if_ccn_over', 10)))
                    suggest_nloc = st.number_input('Suggest splitting above NLOC',
                                  min_value=1, max_value=10000,
                                  value=int(mp.get('suggest_if_nloc_over', 60)))
                destinations = [m['name'] for m in current['metrics'] if m is not modularity]
                recipient = st.selectbox('Transfer modularity weight to (when Disabled)', destinations)
                apply_settings = st.form_submit_button('Save grading settings directly',
                                                       type='primary')
            if apply_settings:
                try:
                    save_configuration(user['id'], aid, expected_rules_hash=version, settings={
                        'max_cyclomatic': int(ccn),
                        'max_function_nloc': int(nloc),
                        'modularity_mode': selected_mode,
                        'min_functions': int(min_funcs),
                        'suggest_if_ccn_over': int(suggest_ccn),
                        'suggest_if_nloc_over': int(suggest_nloc),
                        'transfer_weight_to': recipient,
                    })
                    st.success('Teacher override saved immediately.')
                    st.rerun()
                except (ValueError, OSError, PermissionError) as exc:
                    st.error(str(exc))


def teacher_assignments(user):
    heading('Assignments', 'Create and publish.', 'Upload a problem document, set a deadline, and let AutoGrader prepare default rules automatically.')
    with st.container(border=True):
        col_title, col_template = st.columns([3, 1])
        with col_title:
            st.subheader('New assignment')
            st.caption('Accepted assignment formats: PDF, DOCX, TXT, MD (up to 12 MB).')
        with col_template:
            st.download_button('↓ Example rules.json', data=example_rules_bytes(),
                               file_name='example_rules.json', mime='application/json', use_container_width=True)
        with st.form('assignment_create', clear_on_submit=True):
            title = st.text_input('Assignment title *', placeholder='e.g. Sorting algorithms')
            notes = st.text_area('Instructions (optional)', placeholder='Short introduction or submission guidelines', height=94)
            deadline = date_time_fields('create_deadline')
            doc = st.file_uploader('Assignment document *', type=['pdf', 'docx', 'txt', 'md'])
            rules = st.file_uploader('Custom grading rules (optional JSON override)', type=['json'])
            sample_input = st.text_area('Sample stdin (also guides Ollama test generation)', height=100,
                                        help='Optional example of valid stdin. Used for Smoke Run and shown to Ollama as an input-format example.')
            sample_output = st.text_area('Sample stdout (optional, helps Ollama understand expected output)', height=70,
                                         help='Expected output corresponding to Sample stdin. Teachers must verify it.')
            st.caption('Leave JSON empty for automatic, reproducible grading defaults. Rules and hidden tests remain teacher-only.')
            sent = st.form_submit_button('Publish assignment', type='primary', use_container_width=True)
        if sent:
            if not doc:
                st.error('Upload an assignment document.')
            else:
                try:
                    created_id = create_assignment(user['id'], title, notes, deadline, doc.name, doc.getvalue(),
                                                   rules.name if rules else None, rules.getvalue() if rules else None,
                                                   sample_input, sample_output)
                    st.session_state.flash = f'Assignment #{created_id} published.'
                    st.rerun()
                except (ValueError, PermissionError, OSError) as exc:
                    st.error(str(exc))
    st.write('')
    st.subheader('Manage assignments')
    assignments = get_all_assignments(teacher_id=user['id'])
    if not assignments:
        st.info('No assignments published yet.')
        return
    for a in assignments:
        with st.expander(f'#{a["id"]} · {a["title"]}', expanded=False):
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
            with st.form(f'edit_assignment_{a["id"]}'):
                st.markdown('**Edit assignment**')
                title = st.text_input('Title', value=a['title'])
                notes = st.text_area('Instructions', value=a['description'], height=95)
                deadline = date_time_fields(f'edit_{a["id"]}', a['deadline_at'])
                new_doc = st.file_uploader('Replace assignment document (optional)',
                                           type=['pdf', 'docx', 'txt', 'md'], key=f'edit_doc_{a["id"]}')
                new_rules = st.file_uploader('Replace grading rules (optional)', type=['json'],
                                             key=f'edit_rules_{a["id"]}')
                sample_input = st.text_area('Sample stdin (for Smoke Run and Ollama)', value=a['sample_input'] or '',
                                             key=f'edit_sample_{a["id"]}', height=90)
                sample_output = st.text_area('Sample stdout (optional)', value=a['sample_output'] or '',
                                              key=f'edit_sample_output_{a["id"]}', height=70)
                saved = st.form_submit_button('Save changes', type='primary')
            if saved:
                try:
                    update_assignment(user['id'], a['id'], title, notes, deadline,
                                      new_doc.name if new_doc else None, new_doc.getvalue() if new_doc else None,
                                      new_rules.name if new_rules else None, new_rules.getvalue() if new_rules else None,
                                      sample_input, sample_output)
                    st.session_state.flash = 'Assignment updated.'
                    st.rerun()
                except (ValueError, PermissionError, OSError) as exc:
                    st.error(str(exc))

            teacher_adaptive_rules(user, a)
            teacher_test_manager(user, a)


def teacher_students(user):
    heading('Student management', 'Your class, organized.', 'Create profiles, update records, reset credentials, and control student access.')
    with st.container(border=True):
        st.subheader('Add a student')
        with st.form('create_student', clear_on_submit=True):
            c1, c2 = st.columns(2)
            with c1:
                full_name = st.text_input('Full name *', placeholder='Student name')
                username = st.text_input('Username *', placeholder='student_001')
                student_num = st.text_input('Student ID / roll number', placeholder='CS-001')
            with c2:
                email = st.text_input('Email (optional)', placeholder='student@college.edu')
                password = st.text_input('Initial password * (12+ characters)', type='password')
                confirm = st.text_input('Confirm password *', type='password')
            create = st.form_submit_button('Create student account', type='primary')
        if create:
            if password != confirm:
                st.error('Passwords do not match.')
            else:
                try:
                    student_id = create_student(user['id'], username, password, full_name, student_num, email)
                    st.session_state.flash = f'Student #{student_id} created.'
                    st.rerun()
                except (ValueError, PermissionError) as exc:
                    st.error(str(exc))
    st.write('')
    students = list_students(user['id'])
    st.subheader(f'Student directory · {len(students)}')
    if not students:
        st.info('Add a student to manage their profile.')
        return
    st.dataframe([
        {'ID': s['id'], 'Name': s['full_name'], 'Username': s['username'],
         'Roll number': s['student_number'], 'Email': s['email'],
         'Status': 'Active' if s['is_active'] else 'Inactive', 'Submissions': s['submission_count']}
        for s in students], use_container_width=True, hide_index=True)
    lookup = {s['id']: s for s in students}
    selected = st.selectbox('Select a student to manage', list(lookup),
                            format_func=lambda uid: f'{lookup[uid]["full_name"] or lookup[uid]["username"]} · #{uid}')
    entry = lookup[selected]
    with st.container(border=True):
        st.subheader('Edit student profile')
        with st.form(f'edit_student_{selected}'):
            c1, c2 = st.columns(2)
            with c1:
                full_name = st.text_input('Full name', value=entry['full_name'])
                username = st.text_input('Username', value=entry['username'])
                student_num = st.text_input('Roll number', value=entry['student_number'])
            with c2:
                email = st.text_input('Email', value=entry['email'])
                is_active = st.toggle('Allow student to log in', value=entry['is_active'])
                password = st.text_input('New password (optional, 12+ characters)', type='password')
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
    with st.expander('Delete student permanently'):
        st.warning('Deleting a student also deletes their submission records and uploaded C files. This cannot be undone.')
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


def show_first_pass(report):
    """Teacher-only presentation of stored first-pass evidence."""
    if not report:
        st.info('No first-pass report yet. Ask your teacher to run preliminary checks.')
        return
    overall = report.get('status', 'unknown')
    st.markdown('**Preliminary evaluation**')
    if overall == 'ready':
        st.success('Ready for analyzer dispatch — this is not a correctness grade.')
    elif overall == 'unavailable':
        st.warning('Docker environment unavailable; source upload was saved.')
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
    st.caption('First pass checks execution readiness only, not expected output or grading scores.')


def teacher_submissions(user):
    heading('Submissions', 'Review student work.', 'Filter assignments, inspect uploaded source files and manage submission records.')
    assignments = get_all_assignments(teacher_id=user['id'])
    students = list_students(user['id'])
    a_options = {0: 'All assignments', **{a['id']: a['title'] for a in assignments}}
    s_options = {0: 'All students', **{s['id']: s['full_name'] or s['username'] for s in students}}
    left, right = st.columns(2)
    with left:
        aid = st.selectbox('Filter by assignment', list(a_options), format_func=lambda k: a_options[k])
    with right:
        sid = st.selectbox('Filter by student', list(s_options), format_func=lambda k: s_options[k])
    subs = get_teacher_submissions(user['id'], aid or None, sid or None)
    st.metric('Matching submissions', len(subs))
    if not subs:
        st.info('There are no submissions matching the selected filters.')
        return
    for sub in subs:
        with st.expander(f'#{sub["id"]} · {sub["student_name"]} · {sub["assignment_title"]}'):
            st.write(f'**Submitted:** {pretty_deadline(sub["created_at"])}')
            st.write(f'**Original filename:** {sub["filename"]}')
            st.caption(f'SHA-256: {sub["source_hash"]}')
            try:
                source = get_submission_source(user['id'], sub['id'])
                st.code(source.decode('utf-8'), language='c')
                st.download_button('↓ Download C source', data=source,
                                   file_name=sub['filename'], mime='text/x-csrc',
                                   key=f'source_{sub["id"]}')
            except (OSError, UnicodeError, PermissionError, ValueError) as exc:
                st.error(f'Cannot load source file: {exc}')
            if st.button('↻ Run first-pass checks', key=f'run_first_pass_{sub["id"]}'):
                with st.spinner('Compiling and checking inside Docker…'):
                    try:
                        run_first_pass(user['id'], sub['id'])
                        st.rerun()
                    except (OSError, ValueError, PermissionError) as exc:
                        st.error(str(exc))
            try:
                show_first_pass(get_first_pass(user['id'], sub['id']))
            except (ValueError, PermissionError) as exc:
                st.error(str(exc))
            st.divider()
            st.markdown('**Correctness analyzer · Teacher only**')
            if st.button('Run correctness tests', key=f'grade_correctness_{sub["id"]}'):
                with st.spinner('Running student solution in Docker against approved test cases…'):
                    try:
                        run_first_pass(user['id'], sub['id'])
                        result = run_correctness(user['id'], sub['id'])
                        if result.get('status') == 'not_configured':
                            st.warning('Approve the PDF-generated test set for this assignment first.')
                        else:
                            st.rerun()
                    except (ValueError, PermissionError, OSError) as exc:
                        st.error(str(exc))
            try:
                correctness = get_correctness(user['id'], sub['id'])
                if correctness:
                    if correctness.get('stale'):
                        st.warning('This result uses an older reference, test set, or rubric. Rerun correctness.')
                    elif correctness['score'] is None:
                        st.warning(correctness.get('message', 'Correctness checks not available.'))
                    else:
                        st.metric('Correctness result (raw score, not final grade)',
                                  f'{correctness["passed"]} / {correctness["total"]}',
                                  help=f'Raw correctness score: {correctness["score"] * 100:.1f}%')
                    st.caption(correctness.get('message', ''))
                    with st.container(border=True):
                        st.caption('Correctness tests and counterexamples')
                        for case in correctness.get('results', []):
                            st.write(f'**Test #{case["number"]} · {case["status"]} · {case["category"]}**')
                            if case['status'] != 'passed':
                                left, right, third = st.columns(3)
                                left.code(case['input'] or '(empty input)', language='text')
                                right.code(case['expected'] or '(empty output)', language='text')
                                third.code(case['actual'] or '(empty output)', language='text')
                                if case.get('stderr'):
                                    st.code(case['stderr'], language='text')
                            st.divider()
            except (ValueError, PermissionError) as exc:
                st.error(str(exc))
            st.divider()
            st.markdown('**Syntax · Structure · Modularity (Teacher only)**')
            if st.button('Run 3 easy analyzers', key=f'easy_run_{sub["id"]}'):
                with st.spinner('Checking GCC warnings and inspecting C functions…'):
                    try:
                        run_easy_analyzers(user['id'], sub['id'])
                        st.rerun()
                    except (ValueError, PermissionError, OSError) as exc:
                        st.error(str(exc))
            try:
                easy = get_easy_analyzers(user['id'], sub['id'])
                if easy:
                    if easy.get('stale'):
                        st.warning('Analyzer results are stale. Run the three checks again.')
                    else:
                        st.caption(easy['message'])
                        for item in easy['metrics']:
                            with st.container(border=True):
                                left, right = st.columns([3, 1])
                                with left:
                                    st.markdown(f'**{item["name"].replace("_", " ").title()}** · {item["analyzer"]}')
                                    st.write(item['feedback'])
                                with right:
                                    if item['score'] is None:
                                        st.metric('Score', 'Unavailable')
                                    else:
                                        st.metric('Raw score', f'{item["score"] * 10:.1f} / 10')
                                with st.expander('Evidence and rubric thresholds'):
                                    st.json(item.get('evidence', {}))
                        st.caption(f'Partial rubric contribution: {easy["earned_points"]:.2f} / '
                                   f'{easy["available_points"]:.2f} available points. Not a final grade.')
            except (ValueError, PermissionError) as exc:
                st.error(str(exc))
            confirm = st.checkbox('Confirm deletion of this submission', key=f'confirm_submission_{sub["id"]}')
            if st.button('Delete submission', key=f'delete_submission_{sub["id"]}', disabled=not confirm):
                try:
                    delete_submission(user['id'], sub['id'])
                    st.session_state.flash = 'Submission deleted.'
                    st.rerun()
                except (OSError, PermissionError, ValueError) as exc:
                    st.error(str(exc))


def student_dashboard(user):
    heading('Student portal', 'Ready to submit.', 'Find your assignments and upload C source code before the deadline.')
    assignments = get_all_assignments()
    submissions = get_student_submissions(user['id'])
    a, b, c = st.columns(3)
    a.metric('Assignments', len(assignments))
    b.metric('Your submissions', len(submissions))
    c.metric('Open deadlines', sum(not is_overdue(x['deadline_at']) for x in assignments if x['deadline_at']))
    st.write('')
    if assignments:
        st.subheader('Your assignments')
        lookup = {a['id']: a for a in assignments}
        aid = st.selectbox('Select an assignment', list(lookup),
                           format_func=lambda key: f'#{key} · {lookup[key]["title"]}')
        a = lookup[aid]
        with st.container(border=True):
            st.subheader(a['title'])
            status_caption(a)
            st.write(a['description'])
            if a['document_path']:
                try:
                    st.download_button('↓ Download assignment',
                                       data=download_assignment_document(aid, user['id']),
                                       file_name=a['document_name'] or 'assignment.pdf',
                                       use_container_width=True)
                except (OSError, PermissionError) as exc:
                    st.error(f'Assignment file unavailable: {exc}')
            else:
                st.caption('Older assignment: the instructions are shown above.')
            if not is_overdue(a['deadline_at']):
                st.divider()
                st.subheader('Upload your solution')
                with st.form(f'upload_submission_{aid}'):
                    file = st.file_uploader('C source code (.c; maximum 100 KiB)', type=['c'],
                                            key=f'upload_source_{aid}')
                    submit = st.form_submit_button('Submit C program', type='primary', use_container_width=True)
                if submit:
                    if file is None:
                        st.error('Choose a .c file first.')
                    else:
                        try:
                            result = save_submission(user['id'], aid, file.name, file.getvalue())
                            # Run checks server-side, but keep evaluation private to teachers.
                            # A first-pass failure does not undo the student's uploaded submission.
                            try:
                                run_first_pass(user['id'], result['submission_id'])
                            except (ValueError, OSError, PermissionError):
                                pass  # Teacher can rerun checks from the Submissions page.
                            try:
                                run_correctness(user['id'], result['submission_id'])
                            except (ValueError, OSError, PermissionError):
                                pass  # No detailed grading status is shown to students.
                            st.session_state.flash = f'Submission #{result["submission_id"]} uploaded successfully.'
                            st.rerun()
                        except (ValueError, PermissionError, OSError) as exc:
                            st.error(str(exc))
            else:
                st.warning('This assignment is closed. Contact your teacher about an extension.')
    else:
        st.info('No assignments available yet. Your teacher can publish one from the dashboard.')
    st.write('')
    st.subheader('Submission history')
    if not submissions:
        st.info('Your submitted C files will show up here.')
    else:
        for sub in submissions:
            with st.expander(f'#{sub["id"]} · {sub["title"]}'):
                st.write(f'**File:** {sub["filename"]}')
                st.write(f'**Uploaded:** {pretty_deadline(sub["created_at"])}')
                st.caption('Your teacher can review this submission.')


def main():
    if st.session_state.user_id is None:
        show_login()
        return
    user = get_user_by_id(st.session_state.user_id)
    if not user:
        st.session_state.user_id = None
        st.rerun()
    with st.sidebar:
        st.markdown('<div class="ag-brand"><span class="ag-brand-mark">◈</span> AutoGrader</div>',
                    unsafe_allow_html=True)
        st.markdown('<span class="ag-pill">C Programming Workspace</span>', unsafe_allow_html=True)
        st.write('')
        st.write(user['full_name'])
        st.caption(user['role'].capitalize() + ' account')
        st.divider()
        if user['role'] == 'teacher':
            page = st.radio('Workspace', ['Overview', 'Assignments', 'Students', 'Submissions'],
                            label_visibility='collapsed')
        else:
            page = 'Student portal'
            st.caption('My assignments and submissions')
        st.divider()
        if st.button('Sign out', use_container_width=True):
            st.session_state.user_id = None
            st.rerun()
        st.caption('AutoGrader · Correctness milestone')
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
    elif user['role'] == 'student':
        student_dashboard(user)
    else:
        st.error('This account has an unsupported role.')


if __name__ == '__main__':
    main()
