"""End-to-end oracle generation, teacher approval, native GCC and separate scores."""
from datetime import datetime, timedelta
import shutil
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from services import (authentication, assignment_service, student_service,
                      submission_service, first_pass_service, testset_service, storage_utils)
from services.dates import LOCAL_ZONE
from agent.test_runner import run_cases
from agent.analyzers.io_tests import grade_cases

ORACLE = b'''#include <stdio.h>
int main(void){ int a,b; if(scanf("%d %d", &a,&b)!=2)return 1;
printf("%d\\n", a+b); return 0; }
'''
FAULT = b'''#include <stdio.h>
int main(void){ int a,b; if(scanf("%d %d", &a,&b)!=2)return 1;
printf("%d\\n", a-b); return 0; }
'''


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "tests.db"}')
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine)
    for module in (authentication, assignment_service, student_service,
                   submission_service, first_pass_service, testset_service):
        monkeypatch.setattr(module, 'SessionLocal', sf)
    monkeypatch.setattr(storage_utils, 'ROOT', tmp_path)
    monkeypatch.setattr(storage_utils, 'STORAGE_ROOT', tmp_path / 'storage')
    teacher = authentication.create_user('teacher_oracle', 'strong-pass-123', 'teacher')
    student = student_service.create_student(teacher, 'student_oracle', 'strong-pass-123', 'Student')
    assignment = assignment_service.create_assignment(
        teacher, 'Add integers', 'Read two integers and print sum.',
        datetime.now(LOCAL_ZONE) + timedelta(days=3),
        'problem.txt', b'Read two integers from stdin, output their sum.',
        sample_input='1 2\n')
    return teacher, student, assignment, sf


def test_cases_deduplicated_and_isolated(workspace):
    teacher, student, aid, _ = workspace
    assert testset_service.add_teacher_case(teacher, aid, 'normal', '1 2\n', '3\n')
    assert not testset_service.add_teacher_case(teacher, aid, 'edge', '1 2\n', '3\n')
    cases = testset_service.list_cases(teacher, aid)
    assert len(cases) == 1 and cases[0]['approved']
    with pytest.raises(PermissionError):
        testset_service.list_cases(student, aid)


def test_qwen_drafts_reference_outputs_and_approval(workspace, monkeypatch):
    if shutil.which('gcc') is None:
        pytest.skip('gcc not installed')
    teacher, student, aid, _ = workspace
    testset_service.set_reference(teacher, aid, 'oracle.c', ORACLE)
    monkeypatch.setattr(testset_service, 'request_candidates', lambda p, si, so, cons, oracle, category, count, model, **kw: [
        {'stdin': '4 7\n' if category == 'normal' else '0 0\n',
         'expected_output': 'INCORRECT\n', 'title': category,
         'rationale': 'zero boundary' if category == 'edge' else 'regular values',
         'category': category}])
    outcome = testset_service.generate_tests(teacher, aid, normal_count=1, edge_count=1)
    assert outcome['cases_added'] == 2
    assert outcome['auto_approved'] == 2
    assert outcome['drafts_added'] == 0
    cases = testset_service.list_cases(teacher, aid)
    assert cases[0]['approved'] and cases[0]['oracle_verified']
    assert [(c['category'], c['expected_output']) for c in cases] == [
        ('normal', '11\n'), ('edge', '0\n')]
    assert all(case['approved'] for case in cases)
    submission = submission_service.save_submission(student, aid, 'fault.c', FAULT)['submission_id']
    first_pass_service.run_first_pass(teacher, submission)
    report = first_pass_service.get_first_pass(teacher, submission)
    assert report['smoke']['status'] == 'passed'
    assert report['correctness_grading']['score'] == 0
    assert report['logical_grading']['score'] == 1
    assert report['correctness_grading']['evidence']['results'][0]['stdin'] == '4 7\n'
    assert report['correctness_grading']['evidence']['results'][0]['actual_output'].strip() == '-3'
    assert report['logical_grading']['evidence']['results'][0]['actual_output'].strip() == '0'
    testset_service.set_approval(teacher, aid, cases[0]['id'], False)
    assert first_pass_service.get_first_pass(teacher, submission) is None


def test_timeout_skips_both_execution_analyzers(workspace, monkeypatch):
    teacher, student, aid, _ = workspace
    testset_service.add_teacher_case(teacher, aid, 'normal', '1 2\n', '3\n')
    submission = submission_service.save_submission(student, aid, 'fault.c', FAULT)['submission_id']
    monkeypatch.setattr(first_pass_service, 'evaluate_source', lambda *a, **k: {
        'status': 'blocked', 'compiler': {'status': 'passed', 'warning_count': 0, 'diagnostics': ''},
        'smoke': {'status': 'timeout'}, 'static_sanity': {'status': 'passed'}})
    first_pass_service.run_first_pass(teacher, submission)
    report = first_pass_service.get_first_pass(teacher, submission)
    assert report['correctness_grading']['status'] == 'skipped'
    assert report['logical_grading']['status'] == 'skipped'
    assert report['flags'] == ['suspected_infinite_loop']
    assert report['syntax_grading']['status'] == 'completed'


def test_output_normalization_no_whitespace_overnormalizing():
    cases = [{'id': 2, 'category': 'normal', 'source': 'teacher',
              'stdin': '5 7\n', 'expected_output': '12\n'}]
    mock = lambda *args, **kwargs: {'status': 'completed', 'runs': [
        {'status': 'ok', 'stdout': '12\r\n'}]}
    assert grade_cases(ORACLE, cases, time_limit_s=1, memory_limit_mb=64, runner=mock)['score'] == 1.0
    cases[0]['expected_output'] = '12 \n'
    assert grade_cases(ORACLE, cases, time_limit_s=1, memory_limit_mb=64, runner=mock)['score'] == 0.0


def test_real_native_runner_multiple_inputs():
    if shutil.which('gcc') is None:
        pytest.skip('gcc not installed')
    result = run_cases(ORACLE, ['1 2\n', '10 -3\n'])
    assert result['status'] == 'completed'
    assert [r['stdout'].strip() for r in result['runs']] == ['3', '7']


def test_generation_parser_small_batches_and_grounding(monkeypatch):
    from agent import test_generation
    import json
    calls = []

    class FakeReply:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self):
            return json.dumps({'response': json.dumps({'cases': [{
                'stdin': '1 2\n', 'expected_output': '3\n',
                'title': 'Regular addition', 'rationale': 'typical inputs'}
            ]})}).encode('utf-8')

    def fake_open(req, timeout):
        calls.append(json.loads(req.data))
        return FakeReply()

    monkeypatch.setattr(test_generation.urllib.request, 'urlopen', fake_open)
    result = test_generation.request_candidates(
        'add two integers', '2 3\n', '5\n', 'two integers',
        ORACLE.decode(), 'normal', 1)
    assert result[0]['stdin'] == '1 2\n'
    assert calls[0]['options']['seed'] == 42
    assert calls[0]['options']['temperature'] == 0
    assert 'add two integers' in calls[0]['prompt']
    assert 'REFERENCE CODE' in calls[0]['prompt']


def test_extracts_text_from_problem_document():
    from services.problem_text import assignment_text
    assert 'Read two numbers' in assignment_text('problem.md', b'Read two numbers')


def test_generator_refills_partially_filled_batches(monkeypatch):
    """Older generator stopped after one returned case even when two requested."""
    from agent import test_generation
    import json
    calls = []

    class FakeReply:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self):
            index = len(calls)
            item = {'stdin': f'{index} {index+1}\n', 'expected_output': '0\n',
                    'title': f'Ordinary #{index}', 'rationale': 'different values'}
            return json.dumps({'response': json.dumps({'cases': [item]})}).encode('utf-8')

    def fake_open(req, timeout):
        calls.append(json.loads(req.data))
        return FakeReply()

    monkeypatch.setattr(test_generation.urllib.request, 'urlopen', fake_open)
    items = test_generation.request_candidates('Add two integers', '1 2\n', '3\n',
                                                'two ints', ORACLE.decode(), 'normal', 5)
    assert len(items) == 5
    assert len({x['stdin'] for x in items}) == 5
    assert len(calls) == 5
    assert all(call['options']['seed'] == 42 for call in calls)


def test_existing_teacher_tests_count_towards_generation_target(workspace, monkeypatch):
    if shutil.which('gcc') is None:
        pytest.skip('gcc not installed')
    teacher, student, aid, _ = workspace
    testset_service.set_reference(teacher, aid, 'oracle.c', ORACLE)
    testset_service.add_teacher_case(teacher, aid, 'normal', '1 2\n', '3\n', 'Teacher basic')
    calls = []

    def fake_qwen(problem, sample, output, constraints, code, category, count, model, **kwargs):
        calls.append((category, count, kwargs['existing_inputs']))
        offset = 30 if category == 'normal' else 60
        return [dict(stdin=f'{offset+i} {i}\n', expected_output='WRONG\n',
                     title=f'{category} case {i}', rationale='distinct scenario', category=category)
                for i in range(count)]

    monkeypatch.setattr(testset_service, 'request_candidates', fake_qwen)
    result = testset_service.generate_tests(teacher, aid, normal_count=4, edge_count=2)
    assert result['cases_added'] == 5 and result['auto_approved'] == 5
    assert result['category_counts'] == {'normal': 4, 'edge': 2}
    assert calls[0][0:2] == ('normal', 3)
    assert calls[1][0:2] == ('edge', 2)
    assert '1 2\n' in calls[0][2]
    bank = testset_service.list_cases(teacher, aid)
    assert len(bank) == 6 and all(c['approved'] for c in bank)
    assert next(c for c in bank if c['stdin'] == '30 0\n')['expected_output'] == '30\n'
    second = testset_service.generate_tests(teacher, aid, normal_count=4, edge_count=2)
    assert second['cases_added'] == 0 and len(calls) == 2


def test_bulk_bank_edit_approve_delete_and_invalidate(workspace):
    teacher, student, aid, _ = workspace
    testset_service.add_teacher_case(teacher, aid, 'normal', '1 2\n', '3\n', 'First')
    testset_service.add_teacher_case(teacher, aid, 'edge', '0 0\n', '0\n', 'Second')
    old = testset_service.list_cases(teacher, aid)
    rows = [dict(ID=c['id'], Category=c['category'], Source=c['source'], Title=c['title'],
                 stdin=c['stdin'], **{'Expected stdout': c['expected_output']},
                 Approved=c['approved'], Delete=False) for c in old]
    rows[0]['Title'] = 'Improved title'
    rows[0]['stdin'] = '4 7\n'
    rows[0]['Expected stdout'] = '11\n'
    rows[1]['Delete'] = True
    result = testset_service.save_case_bank(teacher, aid, rows)
    assert result == {'updated': 1, 'deleted': 1}
    bank = testset_service.list_cases(teacher, aid)
    assert len(bank) == 1
    assert bank[0]['stdin'] == '4 7\n' and bank[0]['approved']
    assert not bank[0]['oracle_verified']


def test_bulk_bank_rejects_duplicate_inputs_atomically(workspace):
    teacher, student, aid, _ = workspace
    testset_service.add_teacher_case(teacher, aid, 'normal', '1 2\n', '3\n')
    testset_service.add_teacher_case(teacher, aid, 'edge', '0 0\n', '0\n')
    bank = testset_service.list_cases(teacher, aid)
    rows = [dict(ID=c['id'], Category=c['category'], Title=c['title'], stdin=c['stdin'],
                 **{'Expected stdout': c['expected_output']}, Approved=c['approved']) for c in bank]
    rows[1]['stdin'] = '1 2\r\n'
    with pytest.raises(ValueError, match='duplicate stdin'):
        testset_service.save_case_bank(teacher, aid, rows)
    assert testset_service.list_cases(teacher, aid)[1]['stdin'] == '0 0\n'


def test_no_oracle_keeps_drafts_until_bulk_teacher_approval(workspace, monkeypatch):
    teacher, student, aid, _ = workspace
    monkeypatch.setattr(testset_service, 'request_candidates',
                        lambda p, si, so, con, oracle, category, count, model, **kwargs:
                        [dict(stdin='9 4\n', expected_output='13\n', title='AI sample',
                              rationale='representative valid case')] if category == 'normal' else [])
    result = testset_service.generate_tests(teacher, aid, normal_count=1, edge_count=0)
    assert result['cases_added'] == 1 and result['auto_approved'] == 0
    c = testset_service.list_cases(teacher, aid)[0]
    assert not c['approved']
    rows = [dict(ID=c['id'], Category='normal', Title=c['title'], stdin=c['stdin'],
                 **{'Expected stdout': c['expected_output']}, Approved=True)]
    testset_service.save_case_bank(teacher, aid, rows)
    assert testset_service.list_cases(teacher, aid)[0]['approved']


def test_teacher_can_approve_all_drafts_after_review(workspace, monkeypatch):
    teacher, student, aid, _ = workspace
    monkeypatch.setattr(testset_service, 'request_candidates',
                        lambda p, si, so, con, oracle, category, count, model, **kwargs:
                        [dict(stdin=f'{i+2} {i+3}\n', expected_output=f'{2*i+5}\n',
                              title='Candidate', rationale='Normal valid values') for i in range(count)])
    result = testset_service.generate_tests(teacher, aid, normal_count=3, edge_count=0)
    assert result['drafts_added'] == 3
    assert testset_service.approve_all_drafts(teacher, aid) == 3
    assert all(c['approved'] for c in testset_service.list_cases(teacher, aid))
    assert testset_service.approve_all_drafts(teacher, aid) == 0
    with pytest.raises(PermissionError):
        testset_service.approve_all_drafts(student, aid)

BALANCED_REFERENCE = b'''#include <stdio.h>
int main(void) {
 int count; char sequence[1001];
 if (scanf("%d", &count) != 1 || count < 1 || count > 50) return 1;
 for (int k=0;k<count;k++) {
  if (scanf("%1000s", sequence) != 1) return 1;
  int depth=0, valid=1;
  for (int i=0;sequence[i];i++) {
   if (sequence[i]=='(') depth++;
   else if (sequence[i]==')') { depth--; if(depth<0)valid=0; }
   else valid=0;
  }
  puts(valid && depth==0 ? "YES" : "NO");
 }
 return 0;
}
'''

BALANCED_SAMPLE = '7\n()\n(())\n()()\n(()\n)(\n())(\n(()))(\n'
BALANCED_DESCRIPTION = '''Balanced Parentheses: Given T strings containing only ( and ),
print YES when balanced and NO otherwise. 1 <= T <= 50. Sequence length 1..1000.
Each input starts with T followed by T lines of parentheses.'''


def test_deterministic_parenthesis_fallback_normal_variations():
    from agent.test_fallback import generate_fallback_cases
    generated = generate_fallback_cases(BALANCED_DESCRIPTION, BALANCED_SAMPLE,
                                        BALANCED_REFERENCE.decode(), 'normal', 12, [])
    assert len(generated) == 12
    assert len({case['stdin'] for case in generated}) == 12
    assert len({case['stdin'].splitlines()[0] for case in generated}) >= 5
    for c in generated:
        lines = c['stdin'].splitlines()
        assert 1 <= int(lines[0]) <= 50 and len(lines) - 1 == int(lines[0])
        assert all(1 <= len(line) <= 1000 and set(line) <= set('()') for line in lines[1:])


def test_unknown_format_does_not_invent_fallback_cases():
    from agent.test_fallback import generate_fallback_cases
    assert generate_fallback_cases('Graph path with custom edge constraints', '1 2 3\n',
                                   'int main(){return 0;}', 'normal', 5, []) == []


def test_balanced_parentheses_fills_five_normal_and_edge_and_auto_approves(workspace, monkeypatch):
    if shutil.which('gcc') is None:
        pytest.skip('gcc not installed')
    from services import assignment_service
    teacher, student, original_id, _ = workspace
    aid = assignment_service.create_assignment(
        teacher, 'Balanced Parentheses', BALANCED_DESCRIPTION,
        datetime.now(LOCAL_ZONE) + timedelta(days=2),
        'balanced.md', BALANCED_DESCRIPTION.encode('utf8'), sample_input=BALANCED_SAMPLE)
    testset_service.set_reference(teacher, aid, 'oracle.c', BALANCED_REFERENCE)
    calls = []

    def few_qwen(problem, sample, output, constraints, oracle, category, count, model, **kwargs):
        calls.append(category)
        if calls.count(category) > 1:
            return []
        if category == 'normal':
            options = ['2\n()\n()()\n', '3\n(())\n(())\n(()\n',
                       '2\n((()))\n(()())\n']
        else:
            options = ['1\n(\n', '1\n)(\n', '1\n'+'('*500+')'*500+'\n']
        return [{'stdin': x, 'title': 'Qwen proposal', 'rationale': 'Distinct scenario',
                 'expected_output': 'UNTRUSTWORTHY\n'} for x in options]

    monkeypatch.setattr(testset_service, 'request_candidates', few_qwen)
    result = testset_service.generate_tests(teacher, aid, normal_count=5, edge_count=5)
    assert result['category_counts'] == {'normal': 5, 'edge': 5}
    assert result['approved_counts'] == {'normal': 5, 'edge': 5}
    assert result['auto_approved'] == 10
    bank = testset_service.list_cases(teacher, aid)
    assert len(bank) == 10
    assert len({c['stdin'] for c in bank}) == 10
    assert all(c['oracle_verified'] and c['approved'] and 'UNTRUSTWORTHY' not in c['expected_output']
               for c in bank)
    assert len({c['stdin'].splitlines()[0] for c in bank if c['category'] == 'normal'}) >= 2
    assert not any('of 5 cases available' in x for x in result['warnings'])


def test_new_oracle_automatically_verifies_existing_unapproved_drafts(workspace, monkeypatch):
    if shutil.which('gcc') is None:
        pytest.skip('gcc not installed')
    teacher, student, aid, _ = workspace
    monkeypatch.setattr(testset_service, 'request_candidates', lambda *a, **kw: [
        dict(stdin='8 9\n', title='candidate', rationale='normal',
             expected_output='WRONG\n')])
    first = testset_service.generate_tests(teacher, aid, normal_count=1, edge_count=0)
    assert first['drafts_added'] == 1 and first['auto_approved'] == 0
    testset_service.set_reference(teacher, aid, 'oracle.c', ORACLE)
    second = testset_service.generate_tests(teacher, aid, normal_count=1, edge_count=0)
    assert second['cases_added'] == 0
    assert second['previous_drafts_verified'] == 1
    bank = testset_service.list_cases(teacher, aid)
    assert len(bank) == 1 and bank[0]['approved'] and bank[0]['oracle_verified']
    assert bank[0]['expected_output'] == '17\n'


def test_rules_edge_params_flow_from_assignment_to_qwen_and_logical_report(workspace, monkeypatch):
    """Teacher-uploaded logical params must control generation and report evidence."""
    if shutil.which('gcc') is None:
        pytest.skip('gcc not installed')
    teacher, student, _, _ = workspace
    from services import assignment_service
    rules_json = b'''{
       "time_limit_s": 2, "memory_limit_mb": 64,
       "metrics": [
         {"name":"correctness", "analyzer":"io_tests", "weight":0.5,
          "params":{"output_comparison":"trim_trailing"}},
         {"name":"logical", "analyzer":"edge_case_runner", "weight":0.5,
          "params":{"cases":["zero","negatives"], "output_comparison":"trim_trailing"}}
       ]
    }'''
    aid = assignment_service.create_assignment(
        teacher, 'Sum with edge categories', 'Two integers, output sum.',
        datetime.now(LOCAL_ZONE) + timedelta(days=2),
        'sum.txt', b'Read two integers and output sum.',
        rules_name='rules.json', rules_bytes=rules_json, sample_input='1 2\n')
    testset_service.set_reference(teacher, aid, 'oracle.c', ORACLE)
    calls = []

    def propose(problem, sample, out, constraints, oracle, category, count, model, **kwargs):
        calls.append((category, kwargs.get('requested_edge_types')))
        if category == 'normal':
            return [dict(stdin='7 11\n', expected_output='0', title='Regular', rationale='Typical numbers')]
        return [
            dict(stdin='0 12\n', expected_output='WRONG', title='Zero',
                 rationale='zero operand', edge_type='zero'),
            dict(stdin='-2 6\n', expected_output='WRONG', title='Negative',
                 rationale='negative operand', edge_type='negatives'),
        ]

    monkeypatch.setattr(testset_service, 'request_candidates', propose)
    generation = testset_service.generate_tests(teacher, aid, normal_count=1, edge_count=2)
    assert ('edge', ['zero', 'negatives']) in calls
    assert generation['auto_approved'] == 3
    assert generation['covered_edge_types'] == ['negatives', 'zero']
    assert generation['missing_edge_types'] == []
    cases = testset_service.list_cases(teacher, aid)
    assert {c['edge_type'] for c in cases if c['category'] == 'edge'} == {'zero', 'negatives'}
    assert all(c['approved'] for c in cases)
    sub_id = submission_service.save_submission(student, aid, 'sum.c', ORACLE)['submission_id']
    first_pass_service.run_first_pass(teacher, sub_id)
    report = first_pass_service.get_first_pass(teacher, sub_id)
    assert report['logical_grading']['score'] == 1
    assert report['correctness_grading']['score'] == 1
    assert report['logical_grading']['evidence']['output_comparison'] == 'trim_trailing'
    assert report['logical_grading']['evidence']['requested_edge_types'] == ['zero', 'negatives']
    assert report['logical_grading']['evidence']['missing_edge_types'] == []

    rows = [dict(ID=c['id'], Category=c['category'], Source=c['source'], Title=c['title'],
                 **{'Edge type': 'zero' if c['edge_type'] == 'negatives' else (c['edge_type'] or ''),
                    'Expected stdout': c['expected_output']}, stdin=c['stdin'],
                 Approved=c['approved']) for c in cases]
    assert testset_service.save_case_bank(teacher, aid, rows)['updated'] == 1
    assert first_pass_service.get_first_pass(teacher, sub_id) is None
    changed = testset_service.list_cases(teacher, aid)
    assert {c['edge_type'] for c in changed if c['category'] == 'edge'} == {'zero'}
