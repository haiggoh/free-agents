#!/usr/bin/env python3
"""Smoke test NVIDIA NIM models serially with various configurations."""

import urllib.request
import json
import os
import time

url = 'https://integrate.api.nvidia.com/v1/chat/completions'
key = os.environ.get('NVIDIA_API_KEY') or open(os.path.expanduser('~/.api_keys/nvidia')).read().strip()

RESULTS_FILE = '/Users/bra0002h/ClaudeWorkspace/local-agents/config/nvidia-model-test-results.json'

MODELS = [
    'moonshotai/kimi-k3',
    'moonshotai/kimi-k2.6',
    'deepseek-ai/deepseek-v4.1-flash',
    'deepseek-ai/deepseek-coder-6.7b-instruct',
    'z-ai/glm-5.3-flash',
    'z-ai/glm-5.3',
    'poolside/laguna-xs-2.1',
    'openai/gpt-oss-20b',
    'meta/muse-glimmer-30b',
]

def load_results():
    with open(RESULTS_FILE) as f:
        return json.load(f)

def save_results(data):
    with open(RESULTS_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def test_model(model, thinking=False, streaming=False, max_tokens=100, temperature=0.7, effort=None):
    """Test a model with given configuration.

    effort mapping:
      low: 8192, medium: 16384, high: 65536, xhigh: 131072, max: 262144
    """
    # Determine max_tokens from effort if provided
    if effort:
        effort_map = {'low': 8192, 'medium': 16384, 'high': 65536, 'xhigh': 131072, 'max': 262144}
        max_tokens = effort_map.get(effort, max_tokens)

    body = {
        'model': model,
        'max_tokens': max_tokens,
        'temperature': temperature,
        'chat_template_kwargs': {'enable_thinking': thinking},
        'messages': [{'role': 'user', 'content': 'Reply with OK'}],
        'stream': streaming
    }
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {key}'})
    try:
        start = time.time()
        with urllib.request.urlopen(req, timeout=180) as r:
            if streaming:
                chunks = []
                for line in r:
                    line = line.decode().strip()
                    if line.startswith('data: '):
                        data = line[6:]
                        if data != '[DONE]':
                            chunks.append(data)
                elapsed = time.time() - start
                return {'ok': True, 'elapsed': elapsed, 'chunks': len(chunks)}
            else:
                elapsed = time.time() - start
                result = json.loads(r.read().decode())
                content = result['choices'][0]['message'].get('content', '')
                reasoning = result['choices'][0]['message'].get('reasoning_content', '')
                return {'ok': bool(content or reasoning), 'elapsed': elapsed, 'content': content[:100], 'reasoning': reasoning[:100] if reasoning else None}
    except Exception as e:
        elapsed = time.time() - start if 'start' in locals() else 0
        return {'ok': False, 'elapsed': elapsed, 'error': str(e)}

def run_tests():
    results = load_results()

    for model in MODELS:
        print(f'\n=== Testing {model} ===')
        model_results = {}

        # Test 1: thinking=false, no streaming, no effort
        print(f'  thinking=false, stream=false, no effort...', end=' ', flush=True)
        r = test_model(model, thinking=False, streaming=False)
        model_results['thinking_false_stream_false'] = r
        print(f"ok={r.get('ok')} ({r.get('elapsed',0):.1f}s) - {r.get('error') or r.get('content') or r.get('reasoning')}")

        # Test 2: thinking=true, no streaming, no effort
        print(f'  thinking=true,  stream=false, no effort...', end=' ', flush=True)
        r = test_model(model, thinking=True, streaming=False)
        model_results['thinking_true_stream_false'] = r
        print(f"ok={r.get('ok')} ({r.get('elapsed',0):.1f}s) - {r.get('error') or r.get('content') or r.get('reasoning')}")

        # Test 3: thinking=false, streaming, no effort
        print(f'  thinking=false, stream=true, no effort...', end=' ', flush=True)
        r = test_model(model, thinking=False, streaming=True)
        model_results['thinking_false_stream_true'] = r
        print(f"ok={r.get('ok')} ({r.get('elapsed',0):.1f}s, {r.get('chunks',0)} chunks)")

        # Test 5: thinking=false, stream=false, effort=low
        print(f'  thinking=false, stream=false, effort=low...', end=' ', flush=True)
        r = test_model(model, thinking=False, streaming=False, effort='low')
        model_results['thinking_false_stream_false_effort_low'] = r
        print(f"ok={r.get('ok')} ({r.get('elapsed',0):.1f}s) - {r.get('error') or r.get('content') or r.get('reasoning')}")

        # Test 6: thinking=true, stream=false, effort=low
        print(f'  thinking=true,  stream=false, effort=low...', end=' ', flush=True)
        r = test_model(model, thinking=True, streaming=False, effort='low')
        model_results['thinking_true_stream_false_effort_low'] = r
        print(f"ok={r.get('ok')} ({r.get('elapsed',0):.1f}s) - {r.get('error') or r.get('content') or r.get('reasoning')}")

        # Test 7: thinking=false, stream=false, effort=max
        print(f'  thinking=false, stream=false, effort=max...', end=' ', flush=True)
        r = test_model(model, thinking=False, streaming=False, effort='max')
        model_results['thinking_false_stream_false_effort_max'] = r
        print(f"ok={r.get('ok')} ({r.get('elapsed',0):.1f}s) - {r.get('error') or r.get('content') or r.get('reasoning')}")

        # Test 8: thinking=true, stream=false, effort=max
        print(f'  thinking=true,  stream=false, effort=max...', end=' ', flush=True)
        r = test_model(model, thinking=True, streaming=False, effort='max')
        model_results['thinking_true_stream_false_effort_max'] = r
        print(f"ok={r.get('ok')} ({r.get('elapsed',0):.1f}s) - {r.get('error') or r.get('content') or r.get('reasoning')}")

        # Test 9: temperature=0.0, thinking=false, stream=false
        print(f'  temp=0.0, thinking=false, stream=false...', end=' ', flush=True)
        r = test_model(model, thinking=False, streaming=False, temperature=0.0)
        model_results['temp_0.0_thinking_false_stream_false'] = r
        print(f"ok={r.get('ok')} ({r.get('elapsed',0):.1f}s) - {r.get('error') or r.get('content') or r.get('reasoning')}")

        # Test 10: temperature=1.5, thinking=false, stream=false
        print(f'  temp=1.5, thinking=false, stream=false...', end=' ', flush=True)
        r = test_model(model, thinking=False, streaming=False, temperature=1.5)
        model_results['temp_1.5_thinking_false_stream_false'] = r
        print(f"ok={r.get('ok')} ({r.get('elapsed',0):.1f}s) - {r.get('error') or r.get('content') or r.get('reasoning')}")

        # Test 11: temperature=0.0, thinking=true, stream=false
        print(f'  temp=0.0, thinking=true, stream=false...', end=' ', flush=True)
        r = test_model(model, thinking=True, streaming=False, temperature=0.0)
        model_results['temp_0.0_thinking_true_stream_false'] = r
        print(f"ok={r.get('ok')} ({r.get('elapsed',0):.1f}s) - {r.get('error') or r.get('content') or r.get('reasoning')}")

        results['models'][model] = model_results
        save_results(results)

        # Wait 60 seconds between models to avoid rate limiting
        if model != MODELS[-1]:
            print('  Waiting 60s before next model...')
            time.sleep(60)

    print('\n=== All tests complete ===')

if __name__ == '__main__':
    run_tests()