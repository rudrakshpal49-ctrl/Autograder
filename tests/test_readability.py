"""Readability: model gives 100% of grade; Python is only a fallback."""
import json

import pytest

from agent.analyzers import HANDLERS, llm_review
from agent.coordinator import dispatch_analyzers


def review(score=10, *issues, strengths=None):
    return {'score': score, 'strengths': strengths or [], 'issues': list(issues)}


def naming(name, suggestion=None):
    return {'type': 'naming', 'evidence': name,
            'suggestion': suggestion or f'Rename {name} to describe its purpose.'}


def test_llm_is_entire_grade_even_if_python_has_formatting_issues(monkeypatch):
    source = 'int main(void) {  \n    int a = 4;\n    return a;\n}\n'
    monkeypatch.setattr(llm_review, '_request_ollama',
                        lambda *args: review(8.5, naming('a')))
    result = llm_review.analyze_readability({'source': source}, {})
    assert result['status'] == 'completed'
    assert result['score'] == 0.85  # No blending with Python
    assert result['scoring_method'] == 'llm'
    assert result['evidence']['python_score'] == 0.95
    assert result['evidence']['ai_score'] == 0.85
    assert result['evidence']['ai_weight'] == 1.0
    assert result['evidence']['python_weight'] == 0.0
    assert result['evidence']['ai']['score_out_of_10'] == 8.5
    assert result['evidence']['ai']['seed'] == 42


def test_llm_perfect_score_with_existing_comments(monkeypatch):
    monkeypatch.setattr(llm_review, '_request_ollama', lambda *args: review(
        10, strengths=['Clear function names.']))
    result = llm_review.analyze_readability({
        'source': '/* Calculate value */\nint main(void){return 0;}\n'}, {})
    assert result['score'] == 1.0
    assert result['evidence']['ai']['strengths'] == ['Clear function names.']
    assert result['evidence']['ai']['issues'] == []


def test_legacy_ai_weight_is_ignored(monkeypatch):
    monkeypatch.setattr(llm_review, '_request_ollama', lambda *args: review(7, naming('a')))
    source = 'int main(void){int a=2; return a;}\n'
    old = llm_review.analyze_readability({'source': source}, {'ai_weight': 0.2})
    new = llm_review.analyze_readability({'source': source}, {})
    assert old['score'] == new['score'] == 0.7


def test_python_fallback_if_ollama_offline(monkeypatch):
    def offline(*args):
        raise OSError('not connected')
    monkeypatch.setattr(llm_review, '_request_ollama', offline)
    result = llm_review.analyze_readability({
        'source': 'int main(void) {  \n    return 0;\n}\n'}, {})
    assert result['status'] == 'completed'
    assert result['score'] == 0.95
    assert result['scoring_method'] == 'python_fallback'
    assert result['evidence']['ai']['status'] == 'unavailable'
    assert result['evidence']['python_weight'] == 1.0
    assert result['evidence']['ai_score'] is None


def test_python_fallback_if_ollama_returns_no_score(monkeypatch):
    monkeypatch.setattr(llm_review, '_request_ollama', lambda *args: {
        'strengths': [], 'issues': []})
    result = llm_review.analyze_readability({'source': 'int main(void){return 0;}'}, {})
    assert result['score'] == 1.0
    assert result['scoring_method'] == 'python_fallback'
    assert result['evidence']['ai']['status'] == 'invalid_response'


@pytest.mark.parametrize('bad_score', [None, -1, 11, '9', True, float('nan')])
def test_invalid_llm_score_triggers_python_fallback(monkeypatch, bad_score):
    monkeypatch.setattr(llm_review, '_request_ollama',
                        lambda *args: review(bad_score, naming('a')))
    result = llm_review.analyze_readability({'source': 'int main(){int a=1;return a;}'}, {})
    assert result['scoring_method'] == 'python_fallback'
    assert result['score'] == result['evidence']['python_score']


def test_llm_low_score_without_evidence_triggers_python_fallback(monkeypatch):
    monkeypatch.setattr(llm_review, '_request_ollama',
                        lambda *args: review(5, naming('does_not_exist')))
    result = llm_review.analyze_readability({'source': 'int main(){return 0;}'}, {})
    assert result['scoring_method'] == 'python_fallback'


def test_valid_llm_score_ignores_duplicate_suggestions(monkeypatch):
    monkeypatch.setattr(llm_review, '_request_ollama', lambda *args: review(
        8.2, naming('a'), naming('a'), naming('does_not_exist')))
    result = llm_review.analyze_readability({'source': 'int main(){int a=1;return a;}'}, {})
    assert result['score'] == 0.82
    assert result['evidence']['ai']['rejected_issues'] == 2
    assert len(result['evidence']['ai']['issues']) == 1


def test_formatting_fallback_when_llm_explicitly_disabled():
    src = 'int main(void) {  \n \treturn 0;\n    // ' + 'x' * 45 + '\n}\n'
    result = llm_review.analyze_readability({'source': src}, {
        'max_line_length': 40, 'penalty_per_issue': 0.1,
        'require_comments': True, 'use_llm': False,
    })
    assert result['score'] == 0.7
    assert result['scoring_method'] == 'python_configured'
    assert result['evidence']['issue_counts'] == {
        'long_lines': 1, 'trailing_whitespace': 1,
        'mixed_indentation': 1, 'missing_comments': 0,
    }


def test_comment_within_string_does_not_satisfy_require_comments():
    src = 'int main(void) { puts("// not a comment"); return 0; }'
    result = llm_review.analyze_readability({'source': src}, {
        'require_comments': True, 'use_llm': False})
    assert result['evidence']['issue_counts']['missing_comments'] == 1
    assert result['score'] == 0.95


def test_empty_source_zero():
    result = llm_review.analyze_readability({'source': ' \n'}, {})
    assert result['score'] == 0
    assert result['scoring_method'] == 'python_fallback'


def test_cache_reuses_llm_grade_on_same_source_and_comment_policy(monkeypatch):
    calls = []
    def fake_llm(*args):
        calls.append(args)
        return review(8.0, naming('a'))
    monkeypatch.setattr(llm_review, '_request_ollama', fake_llm)
    source = 'int main(){int a=1;return a;}'
    first = llm_review.analyze_readability({'source': source}, {})
    cached = first['evidence']['ai']
    second = llm_review.analyze_readability({'source': source, 'cached_ai': cached}, {})
    assert second['score'] == 0.8
    assert second['evidence']['ai']['cached'] is True
    assert len(calls) == 1
    changed = llm_review.analyze_readability({
        'source': source, 'cached_ai': cached}, {'require_comments': True})
    assert changed['evidence']['ai']['cached'] is False
    assert len(calls) == 2


def test_cache_not_shared_between_models(monkeypatch):
    calls = []
    monkeypatch.setattr(llm_review, '_request_ollama',
                        lambda source, model, require_comments=False: calls.append(model) or review())
    source = 'int main(void){return 0;}'
    first = llm_review.analyze_readability({'source': source}, {'model': 'llama3.2:1b'})
    second = llm_review.analyze_readability({
        'source': source, 'cached_ai': first['evidence']['ai']}, {'model': 'qwen2.5-coder:3b'})
    assert calls == ['llama3.2:1b', 'qwen2.5-coder:3b']
    assert second['evidence']['ai']['cached'] is False


@pytest.mark.parametrize('params', [
    {'max_line_length': 20}, {'max_line_length': True},
    {'max_line_length': 301}, {'penalty_per_issue': -0.1},
    {'penalty_per_issue': 1.1}, {'penalty_per_issue': float('nan')},
    {'require_comments': 'yes'}, {'use_llm': 1},
    {'model': 'other-model'}, {'ai_weight': 0.31}, {'ai_weight': -0.1},
    {'ai_weight': True}, {'ai_weight': float('inf')},
])
def test_invalid_params(params):
    with pytest.raises(ValueError):
        llm_review.analyze_readability({'source': 'int main() {}'}, params)


def test_source_only_after_smoke_timeout(monkeypatch):
    monkeypatch.setattr(llm_review, '_request_ollama', lambda *args: review())
    first_pass = {'status': 'blocked', 'smoke': {'status': 'timeout'}}
    result = dispatch_analyzers(first_pass, [{
        'name': 'readability', 'analyzer': 'llm_review', 'params': {},
    }], HANDLERS, {'source': b'int main(void){return 0;}'})
    assert result['status'] == 'partial'
    assert result['flags'] == ['suspected_infinite_loop']
    assert result['analyzers'][0]['result']['score'] == 1.0


def test_ollama_request_fixed_seed_and_structured_score(monkeypatch):
    observed = {}
    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, n):
            assert n == 32768
            return json.dumps({'response': json.dumps(review(8, naming('a')))}).encode()
    def fake_urlopen(request, timeout):
        observed['body'] = json.loads(request.data.decode())
        observed['timeout'] = timeout
        observed['url'] = request.full_url
        return FakeResponse()
    monkeypatch.setattr(llm_review, 'urlopen', fake_urlopen)
    result = llm_review._request_ollama('int main(){int a=1;}', 'qwen2.5-coder:3b')
    assert result['score'] == 8
    assert observed['body']['format'] == llm_review.REVIEW_SCHEMA
    assert observed['body']['options']['temperature'] == 0
    assert observed['body']['options']['seed'] == 42
    assert observed['body']['options']['num_ctx'] == 4096
    assert observed['url'] == 'http://127.0.0.1:11434/api/generate'


def test_http_404_uses_python_fallback_with_specific_reason(monkeypatch):
    from urllib.error import HTTPError
    def missing(*args):
        raise HTTPError('http://127.0.0.1:11434/api/generate', 404, 'not found', {}, None)
    monkeypatch.setattr(llm_review, '_request_ollama', missing)
    result = llm_review.analyze_readability({'source': 'int main(void){return 0;}'}, {})
    assert result['scoring_method'] == 'python_fallback'
    assert 'ollama pull qwen2.5-coder:3b' in result['evidence']['ai']['message']
