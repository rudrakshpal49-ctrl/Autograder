"""Tests for the simpler PDF -> Ollama -> teacher approval workflow."""
import pytest

from agent import test_generator
from analyzers.correctness import evaluate_correctness, outputs_equal
from services.testcase_service import merge_cases, validate_input_cases


def test_ollama_suggests_cases_without_input_contract(monkeypatch):
    captured = []

    def pretend_ollama(prompt, **kwargs):
        captured.append(prompt)
        return {"cases": [
            {"input": "1 2\n", "expected": "3\n", "category": "normal"},
            {"input": "1 2\n", "expected": "wrong\n", "category": "duplicate"},
            {"input": "0 0\n", "expected": "0\n", "category": "boundary"},
            {"input": "5 7\n"},                # missing expected output
            {"input": 123, "expected": "123"},  # malformed stdin field
            {"input": "-3 1\n", "expected": "-2\n", "uncertain": True},
        ]}

    monkeypatch.setattr(test_generator, '_ask_ollama', pretend_ollama)
    result = test_generator.generate_inputs('Read two integers, print their sum.')
    assert [case['input'] for case in result] == ['1 2\n', '0 0\n']
    assert result[0]['expected'] == '3\n'
    assert result[0]['expected_source'] == 'llm'
    assert 'COMPLETE stdin' in captured[0]
    assert 'Read two integers, print their sum.' in captured[0]
    assert 'input contract' not in captured[0].lower()


def test_all_invalid_generated_candidates_fail_safely(monkeypatch):
    monkeypatch.setattr(test_generator, '_ask_ollama',
                        lambda *a, **k: {"cases": [{"expected": "missing stdin"}]})
    with pytest.raises(ValueError, match='usable input/output pairs'):
        test_generator.generate_inputs('Read an array and sort it.')


def test_teacher_can_override_llm_prediction():
    generated = [{"input": "1 2\n", "expected": "wrong\n", "category": "normal", "expected_source": "llm"}]
    teacher = [{"input": "1 2\n", "expected": "3\n", "category": "teacher"}]
    combined = merge_cases(generated, teacher)
    assert len(combined) == 1
    assert combined[0]['expected'] == '3\n'
    assert combined[0]['expected_source'] == 'teacher'


def test_multiple_output_comparison_options():
    assert outputs_equal('1 2\n', '1   2\r\n', {'mode': 'tokens'})
    assert not outputs_equal('1 2\n', '1   2\n', {'mode': 'exact'})
    assert outputs_equal('3.14\n', '3.14001\n', {'mode': 'float_tokens', 'abs_tol': 0.001})
    assert outputs_equal('red\nblue\n', 'blue\nred\n', {'mode': 'unordered_lines'})
    cases = validate_input_cases([{'input': '3\n', 'expected': '2 1 3\n'}])
    execution = {'status': 'ok', 'results': [{'status': 'passed', 'stdout': '2   1 3\n', 'stderr': ''}]}
    assert evaluate_correctness(cases, execution, {'mode': 'tokens'})['score'] == 1.0


def test_string_problem_does_not_normalize_stdin():
    monkeypatch_input = '  one two  \n'
    cases = validate_input_cases([{'input': monkeypatch_input, 'expected': '  owt eno  \n'}])
    assert cases[0]['input'] == monkeypatch_input
