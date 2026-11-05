"""Diagnose Ollama connectivity/model availability from the active Python host.

    python tools/check_ollama.py
    python tools/check_ollama.py --probe
    python tools/check_ollama.py --url http://127.0.0.1:11434

The optional probe calls both endpoints used by the application. It does not
change models, files, the database, or test banks.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

MODEL = 'qwen2.5-coder:3b'


def call_json(url: str, payload=None, timeout: float = 10):
    data = None if payload is None else json.dumps(payload).encode('utf-8')
    request = urllib.request.Request(
        url, data=data, headers={'Content-Type': 'application/json'} if data else {},
        method='POST' if data else 'GET')
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:11434',
                        help='Ollama server base URL, without /api/...')
    parser.add_argument('--model', default=MODEL)
    parser.add_argument('--probe', action='store_true',
                        help='Run a short model request through /api/generate and /api/chat')
    args = parser.parse_args()
    base = args.url.rstrip('/')
    if base.endswith('/api') or '/api/' in base:
        print('Supply a base URL, not an /api/... endpoint.')
        return 2
    print('Ollama server:', base)
    try:
        payload = call_json(base + '/api/tags')
        names = [row.get('name') for row in payload.get('models', []) if isinstance(row, dict)]
        print('Available models:', ', '.join(names) or '(none)')
        if args.model not in names:
            print(f'Model missing: {args.model}. Pull it on THIS Ollama instance.')
            return 1
        print('Model available:', args.model)
        if args.probe:
            requests = {
                '/api/generate': {'model': args.model, 'prompt': 'Reply OK', 'stream': False,
                                  'options': {'num_predict': 6}},
                '/api/chat': {'model': args.model,
                              'messages': [{'role': 'user', 'content': 'Reply OK'}],
                              'stream': False, 'options': {'num_predict': 6}},
            }
            for endpoint, req in requests.items():
                response = call_json(base + endpoint, req, timeout=120)
                if not isinstance(response, dict) or response.get('error'):
                    raise RuntimeError(f'{endpoint}: unexpected response {response!r}')
                print(endpoint, 'OK')
    except urllib.error.HTTPError as exc:
        body = exc.read().decode('utf-8', errors='replace')[:350]
        print('HTTP error:', exc.code, body)
        return 1
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
        print('Ollama check failed:', exc)
        return 1
    print('Ollama check passed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
