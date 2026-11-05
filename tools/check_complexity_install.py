"""Check that THIS AutoGrader checkout has the integrated complexity pipeline.

Usage from the project root:
    python tools/check_complexity_install.py
    python tools/check_complexity_install.py --rules path/to/rules.json

This check never runs a student's program, calls Ollama, or modifies the database.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rules', type=Path, default=ROOT / 'rules' / 'callgrind_growth_example.json')
    args = parser.parse_args()
    print('Project root:', ROOT)
    print('Python:', sys.executable)
    print('Working directory:', Path.cwd())
    errors = []
    try:
        from services import rules_service
        from services import first_pass_service
        from agent.analyzers import HANDLERS
        from agent.coordinator import EXECUTION_ANALYZERS
        from services.report_service import METRICS
    except (ImportError, ModuleNotFoundError) as exc:
        print('FAIL: Integration files or project dependencies missing:', exc)
        print('Install project dependencies, then apply the COMPLETE complexity integration ZIP.')
        return 1

    print('Rules validator:', Path(rules_service.__file__).resolve())
    print('Evaluation service:', Path(first_pass_service.__file__).resolve())
    for path in (rules_service.__file__, first_pass_service.__file__):
        if not Path(path).resolve().is_relative_to(ROOT):
            errors.append('Python is importing a different AutoGrader checkout: ' + path)
    required = 'complexity_estimator'
    if required in rules_service.INACTIVE_ANALYZERS:
        errors.append('Old rules_service.py is active: complexity_estimator remains reserved.')
    if required not in rules_service.SUPPORTED_PARAMS:
        errors.append('Old rules_service.py is active: complexity params are unknown.')
    if required not in HANDLERS or required not in EXECUTION_ANALYZERS:
        errors.append('Analyzer registration missing in agent/analyzers/__init__.py or agent/coordinator.py.')
    if not callable(getattr(first_pass_service, 'attach_complexity_grading', None)):
        errors.append('services/first_pass_service.py does not attach complexity results.')
    if not any(key == required and field == 'complexity_grading' for key, field, _ in METRICS):
        errors.append('Student report service has no complexity metric mapping.')

    if args.rules.exists():
        try:
            rules = rules_service.validate_rules(args.rules.read_bytes())
            warnings = rules_service.rules_warnings(rules)
            for warning in warnings:
                if (required in warning and ('reserved' in warning or 'unsupported and ignored' in warning)):
                    errors.append('Rule validator is stale: ' + warning)
            print('Rules file:', args.rules.resolve())
            print('Rules validated:', len(rules.get('metrics', [])), 'metrics')
            for warning in warnings:
                print('Rule warning:', warning)
        except (OSError, ValueError) as exc:
            errors.append(f'Invalid rules file {args.rules}: {exc}')
    else:
        print('Note: no optional example rules found at', args.rules)

    valgrind = shutil.which('valgrind')
    gcc = shutil.which('gcc')
    header = any(Path(path).is_file() for path in (
        '/usr/include/valgrind/callgrind.h',
        '/usr/local/include/valgrind/callgrind.h',
    ))
    print('C compiler:', gcc or 'NOT FOUND')
    print('Callgrind:', valgrind or 'NOT FOUND')
    print('Callgrind headers:', 'present' if header else 'missing')
    if os.name == 'nt' or not (gcc and valgrind and header):
        print('Profiler status: unavailable here. Use Linux/WSL2 and install build-essential valgrind.')
        print('No numerical complexity marks will be awarded on this machine.')
    else:
        print('Profiler prerequisites found (live profiling not verified by this script).')

    if errors:
        for error in errors:
            print('FAIL:', error)
        print('Apply the COMPLETE integration ZIP to the Project root printed above,')
        print('restart Streamlit from that same directory, and rerun this check.')
        return 1
    print('PASS: complexity rule validation, registry, evaluation and reports are connected.')
    print('For existing submissions click "Run first-pass checks" again to create a new report.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
