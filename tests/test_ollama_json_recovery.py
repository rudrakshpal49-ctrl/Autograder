"""Regression tests for incomplete Ollama JSON and small-batch generation."""
import json

from agent import test_generator


class FakeHTTPResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return json.dumps(self.payload).encode('utf-8')


def test_retry_when_ollama_stops_in_middle_of_string(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        request_payload = json.loads(request.data.decode('utf-8'))
        calls.append(request_payload)
        if len(calls) == 1:
            return FakeHTTPResponse({'response': '{"cases":[{"input":"5 7\\n","expected":"12'})
        return FakeHTTPResponse({'response': json.dumps({
            'cases': [{'input': '5 7\n', 'expected': '12\n', 'category': 'normal'}]
        })})

    monkeypatch.setattr(test_generator.urllib.request, 'urlopen', fake_urlopen)
    result = test_generator._ask_ollama('Return candidate cases', schema=test_generator.CASE_SCHEMA)
    assert result['cases'][0]['expected'] == '12\n'
    assert len(calls) == 2
    assert calls[0]['format']['properties']['cases']['maxItems'] == 3
    assert calls[0]['options']['num_predict'] < calls[1]['options']['num_predict']


def test_generation_in_three_small_batches_deduplicates(monkeypatch):
    calls = []

    def fake_ollama(prompt, model=None, *, schema=None):
        calls.append(prompt)
        assert schema['properties']['cases']['maxItems'] == 3
        if len(calls) == 1:
            return {'cases': [
                {'input': '1 2\n', 'expected': '3\n', 'category': 'normal'},
                {'input': '0 0\n', 'expected': '0\n', 'category': 'normal'},
            ]}
        if len(calls) == 2:
            return {'cases': [
                {'input': '1 2\n', 'expected': 'wrong\n', 'category': 'duplicate'},
                {'input': '-1 4\n', 'expected': '3\n', 'category': 'boundary'},
            ]}
        return {'cases': [
            {'input': '4 -4\n', 'expected': '0\n', 'category': 'negative'},
        ]}

    monkeypatch.setattr(test_generator, '_ask_ollama', fake_ollama)
    cases = test_generator.generate_inputs('Read two integers and print sum.', num_cases=9)
    assert len(calls) == 3
    assert [case['input'] for case in cases] == ['1 2\n', '0 0\n', '-1 4\n', '4 -4\n']
    assert cases[0]['expected'] == '3\n'
    assert all(case['expected_source'] == 'llm' for case in cases)


def test_expected_output_prediction_is_batched(monkeypatch):
    lengths = []

    def fake_ollama(prompt, model=None, *, schema=None):
        inputs = json.loads(prompt.split('INPUTS:\n', 1)[1])
        lengths.append(len(inputs))
        return {'cases': [{'number': i['number'], 'expected': str(i['number']) + '\n',
                           'uncertain': False} for i in inputs]}

    monkeypatch.setattr(test_generator, '_ask_ollama', fake_ollama)
    result = test_generator.generate_expected_outputs('Print a number.',
                [{'input': f'{i}\n'} for i in range(7)])
    assert lengths == [3, 3, 1]
    assert result == ['1\n', '2\n', '3\n', '4\n', '5\n', '6\n', '7\n']
