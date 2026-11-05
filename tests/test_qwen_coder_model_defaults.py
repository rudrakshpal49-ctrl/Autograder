"""Regression checks: the complexity extension uses the project's Ollama model."""
import json
from pathlib import Path

from agent.analyzers import size_aware_stdin
from agent.analyzers import time_growth_bridge


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL = 'qwen2.5-coder:3b'


def test_input_grammar_model_default():
    assert size_aware_stdin.DEFAULT_MODEL == MODEL


def test_complexity_rule_example_model_default():
    rules = json.loads((PROJECT_ROOT / 'rules' / 'callgrind_growth_example.json').read_text())
    assert rules['metrics'][0]['params']['ollama_model'] == MODEL


def test_bridge_uses_project_model_default(monkeypatch):
    params_seen = {}
    monkeypatch.setattr(time_growth_bridge.shutil, 'which', lambda program: '/usr/bin/valgrind')
    monkeypatch.setattr(time_growth_bridge.profiler, '_callgrind_header', lambda: '/usr/include/valgrind/callgrind.h')

    def fake_evaluate(source_path, params=None, limits=None):
        params_seen.update(params or {})
        return {'score': None, 'evidence': {'detected_complexity': 'inconclusive', 'fit': {}}, 'feedback': 'unavailable'}

    monkeypatch.setattr(time_growth_bridge.profiler, 'evaluate', fake_evaluate)
    result = time_growth_bridge.analyze_time_growth(
        {'source': b'#include <stdio.h>\nint main(void){int n;scanf("%d",&n);return 0;}\n',
         'sample_input': '10\n', 'time_limit_s': 1},
        {'required': 'O(N)'},
    )
    assert params_seen['ollama_model'] == MODEL
    assert result['status'] == 'insufficient_evidence'
