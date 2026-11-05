"""Tests for normalized C similarity, optional Qwen review, and safeguards."""
import pytest

from agent.analyzers import similarity_flag as mod
from services.rules_service import (get_originality_preferences,
                                    set_originality_preferences)

SOURCE_A = '''
#include <stdio.h>
int contains(int values[], int count, int needle) {
    for (int i = 0; i < count; i++) {
        if (values[i] == needle) return 1;
    }
    return 0;
}
int main(void) {
    int a[100]; int b[100]; int n, m;
    scanf("%d", &n);
    for (int i = 0; i < n; i++) scanf("%d", &a[i]);
    scanf("%d", &m);
    for (int j = 0; j < m; j++) scanf("%d", &b[j]);
    for (int i = 0; i < n; i++) if (contains(b, m, a[i])) printf("%d ", a[i]);
    return 0;
}
'''
SOURCE_B = '''
/* Rename everything, keep same logic */
#include <stdio.h>
int lookup(int items[], int length, int key) {
    for (int pos = 0; pos < length; pos++) {
        if (items[pos] == key) return 1;
    }
    return 0;
}
int main(void) {
    int first[100]; int second[100]; int first_size, second_size;
    scanf("%d", &first_size);
    for (int pos = 0; pos < first_size; pos++) scanf("%d", &first[pos]);
    scanf("%d", &second_size);
    for (int k = 0; k < second_size; k++) scanf("%d", &second[k]);
    for (int pos = 0; pos < first_size; pos++) if (lookup(second, second_size, first[pos])) printf("%d ", first[pos]);
    return 0;
}
'''
SOURCE_DIFFERENT = '''
#include <stdio.h>
int factorial(int n) {
    if (n <= 1) return 1;
    return n * factorial(n - 1);
}
int main(void) {
    int n;
    scanf("%d", &n);
    if (n > 12) puts("too big");
    else printf("%d", factorial(n));
    return 0;
}
'''


def fake_context(source=SOURCE_A, peers=None, cached_ai=None):
    if peers is None:
        peers = [{'submission_id': 17, 'source': SOURCE_B}]
    return {'source': source, 'peers': peers, 'cached_ai': cached_ai}


def test_renamed_identifiers_and_comments_are_normalized():
    assert mod.tokenize_c(SOURCE_A) == mod.tokenize_c(SOURCE_B)
    result = mod.analyze_originality(fake_context(), {'use_ai': False})
    assert result['evidence']['max_similarity'] == 1.0
    assert result['flagged'] is True
    assert result['score'] == 0.0  # provisional inverse-similarity indicator, not an automatic penalty
    assert result['grade_status'] == 'provisional'
    assert result['evidence']['matches'][0]['submission_id'] == 17
    assert '_source' not in str(result['evidence'])


def test_different_algorithms_are_less_similar():
    result = mod.analyze_originality(fake_context(peers=[{'submission_id': 5, 'source': SOURCE_DIFFERENT}]),
                                     {'use_ai': False})
    assert result['evidence']['max_similarity'] < 0.85
    assert not result['flagged']


def test_short_and_missing_comparisons_dont_flag():
    result = mod.analyze_originality(fake_context('int main(){return 0;}',
        [{'submission_id': 88, 'source': 'int main(){return 0;}'}]), {'use_ai': False})
    assert result['status'] == 'insufficient_evidence'
    assert result['evidence']['matches'] == []
    assert result['score'] is None
    assert mod.analyze_originality(fake_context(peers=[]), {'use_ai': True})['status'] == 'insufficient_evidence'


def test_qwen_20_percent_review_signal_and_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(mod, '_request_ollama', lambda *args: calls.append(args) or {
        'overlap_score': 80, 'observations': ['Both use a loop over each array.']})
    result = mod.analyze_originality(fake_context(), {'use_ai': True})
    assert result['evidence']['ai_weight'] == 0.2
    assert result['evidence']['deterministic_weight'] == 0.8
    assert result['evidence']['review_priority'] == 0.96  # 80% of 1.0 + 20% of 0.8
    assert result['score'] == 0.04  # 0.4 / 10 provisional grade
    assert result['grade_status'] == 'provisional'
    assert result['evidence']['ai']['seed'] == 42
    assert len(calls) == 1

    cached = result['evidence']['ai']
    repeated = mod.analyze_originality(fake_context(cached_ai=cached), {'use_ai': True})
    assert repeated['evidence']['ai']['cached'] is True
    assert len(calls) == 1
    changed = mod.analyze_originality(fake_context(source=SOURCE_A + '\n'), {'use_ai': True, 'flag_threshold': 0.85})
    assert len(calls) == 2
    assert changed['evidence']['ai']['cached'] is False


def test_ai_unavailable_keeps_token_result(monkeypatch):
    monkeypatch.setattr(mod, '_request_ollama', lambda *args: (_ for _ in ()).throw(OSError('offline')))
    result = mod.analyze_originality(fake_context(), {'use_ai': True})
    assert result['status'] == 'completed'
    assert result['flagged'] is True
    assert result['evidence']['ai']['status'] == 'unavailable'
    assert result['evidence']['review_priority'] is None
    assert result['score'] == 0.0
    assert 'AI review unavailable' in result['evidence']['grade_basis']


def test_bad_ai_review_is_not_scored(monkeypatch):
    monkeypatch.setattr(mod, '_request_ollama', lambda *args: {'overlap_score': 'certainly AI', 'observations': []})
    result = mod.analyze_originality(fake_context(), {'use_ai': True})
    assert result['evidence']['ai']['status'] == 'unavailable'
    assert result['evidence']['review_priority'] is None
    assert result['score'] == 0.0  # token-only fallback when the AI response is unusable
    assert 'AI review unavailable' in result['evidence']['grade_basis']


def test_originality_grades_and_file_comparison_metadata():
    context = fake_context(peers=[
        {'submission_id': 99, 'source': SOURCE_B, 'filename': 'renamed_solution.c',
         'student_name': 'Student B'},
        {'submission_id': 80, 'source': SOURCE_DIFFERENT,
         'filename': 'factorial.c', 'student_name': 'Student C'},
    ])
    result = mod.analyze_originality(context, {'use_ai': False})
    assert result['score'] == 0.0
    assert result['evidence']['max_similarity'] == 1.0
    matches = result['evidence']['matches']
    assert matches[0]['filename'] == 'renamed_solution.c'
    assert matches[0]['student_name'] == 'Student B'
    assert matches[1]['filename'] == 'factorial.c'
    assert matches[0]['similarity'] > matches[1]['similarity']
    assert all('_source' not in match for match in matches)


def test_originality_grade_when_programs_differ():
    result = mod.analyze_originality(fake_context(peers=[
        {'submission_id': 55, 'source': SOURCE_DIFFERENT,
         'filename': 'different.c'},
    ]), {'use_ai': False})
    similarity = result['evidence']['max_similarity']
    assert result['score'] == round(1 - similarity, 4)
    assert result['grade_status'] == 'provisional'
    assert result['flagged'] is False


@pytest.mark.parametrize('options', [
    {'flag_threshold': -1}, {'flag_threshold': 1.1}, {'use_ai': 'yes'},
    {'min_tokens': 0}, {'model': 'fake:7b'}, {'flag_threshold': True},
])
def test_bad_params(options):
    with pytest.raises(ValueError):
        mod.analyze_originality(fake_context(), options)


def test_teacher_preferences_preserve_metric_weights_and_other_settings():
    source = b'{"time_limit_s":1,"memory_limit_mb":64,"metrics":[' \
             b'{"name":"syntax","analyzer":"compiler_warnings","weight":0.8},' \
             b'{"name":"originality","analyzer":"similarity_flag","weight":0.2}]}'
    updated = set_originality_preferences(source, True, 0.9, False)
    from json import loads
    rules = loads(updated)
    assert [m['weight'] for m in rules['metrics']] == [0.8, 0.2]
    assert get_originality_preferences(rules) == (True, 0.9, False)
    disabled = set_originality_preferences(updated, False, 0.9, False)
    assert get_originality_preferences(loads(disabled))[0] is False


def test_teacher_preferences_for_legacy_rules():
    original = b'{"time_limit_s": 1, "memory_limit_mb": 64}'
    updated = set_originality_preferences(original, True, 0.8, True)
    from json import loads
    prefs = loads(updated)
    assert 'metrics' not in prefs
    assert get_originality_preferences(prefs) == (True, 0.8, True)
