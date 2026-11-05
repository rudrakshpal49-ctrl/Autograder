"""Regression checks for the most common partial-install failure."""
import json
import subprocess
import sys
from pathlib import Path

from services.rules_service import validate_rules, rules_warnings, SUPPORTED_PARAMS, INACTIVE_ANALYZERS
from agent.analyzers import HANDLERS

ROOT = Path(__file__).resolve().parents[1]


def test_complexity_metric_is_active_and_params_are_supported():
    assert 'complexity_estimator' in SUPPORTED_PARAMS
    assert 'complexity_estimator' not in INACTIVE_ANALYZERS
    assert 'complexity_estimator' in HANDLERS
    payload = json.loads((ROOT / 'rules/callgrind_growth_example.json').read_text())
    metric = payload['metrics'][0]
    settings = validate_rules(json.dumps(payload).encode())
    assert metric['analyzer'] == 'complexity_estimator'
    assert not any('reserved analyzer' in warning or 'is unsupported and ignored' in warning
                   for warning in rules_warnings(settings))
    assert not any('final combined weighted grade is not implemented yet' in warning
                   for warning in rules_warnings(settings))


def test_preflight_succeeds_and_shows_actual_project_root():
    checker = ROOT / 'tools/check_complexity_install.py'
    result = subprocess.run([sys.executable, str(checker)], cwd=ROOT,
                            capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr
    assert str(ROOT) in result.stdout
    assert 'PASS: complexity rule validation' in result.stdout


def test_weight_warnings_only_when_weights_do_not_total_one():
    valid = dict(time_limit_s=1, memory_limit_mb=64,
                 metrics=[dict(analyzer='complexity_estimator', name='complexity',
                               weight=1.0, params={'required': 'O(N)'})])
    assert not any('weights' in x.lower() for x in rules_warnings(valid))
    valid['metrics'][0]['weight'] = 0.5
    assert any('total 50%' in x for x in rules_warnings(valid))
