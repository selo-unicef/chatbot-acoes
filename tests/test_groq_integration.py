"""Testes isolados: não chamam API, não precisam de chaves reais."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llm import DEFAULT_GROQ_MODEL, groq_options, produce, search_vector


def test_groq_default_and_reasoning_options(monkeypatch):
    monkeypatch.delenv('GROQ_REASONING_EFFORT', raising=False)
    monkeypatch.delenv('MAX_OUTPUT_TOKENS', raising=False)
    monkeypatch.delenv('GROQ_TEMPERATURE', raising=False)
    assert DEFAULT_GROQ_MODEL == 'openai/gpt-oss-20b'
    assert groq_options(DEFAULT_GROQ_MODEL) == {
        'reasoning_effort': 'low', 'include_reasoning': False,
        'temperature': 0.5, 'max_completion_tokens': 1200,
    }
    assert 'reasoning_effort' not in groq_options('modelo-sem-raciocinio')
    assert 'include_reasoning' not in groq_options('modelo-sem-raciocinio')


def test_groq_reasoning_invalid(monkeypatch):
    monkeypatch.setenv('GROQ_REASONING_EFFORT', 'none')
    with pytest.raises(ValueError, match='low, medium ou high'):
        groq_options(DEFAULT_GROQ_MODEL)


def test_groq_stream_with_history_and_no_reasoning(monkeypatch):
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='Olá'))])
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='!'))])
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None))])

    class FakeGroq:
        def __init__(self, **kwargs):
            captured['client_options'] = kwargs
            self.chat = SimpleNamespace(completions=FakeCompletions())

        def close(self):
            captured['closed'] = True

    monkeypatch.setitem(sys.modules, 'groq', SimpleNamespace(Groq=FakeGroq))
    monkeypatch.setenv('GROQ_API_KEY', 'test-only')
    monkeypatch.setenv('GROQ_MODEL', 'openai/gpt-oss-20b')
    monkeypatch.delenv('GROQ_REASONING_EFFORT', raising=False)
    chunks = list(produce('pt', 'E em PE?', [{'role': 'user', 'content': 'Em AL?'}], 'FONTE [1] - ação pública', provider='groq'))
    assert chunks == ['Olá', '!']
    assert captured['client_options']['api_key'] == 'test-only'
    assert captured['stream'] is True
    assert captured['model'] == 'openai/gpt-oss-20b'
    assert captured['messages'][0]['role'] == 'system'
    assert captured['messages'][1] == {'role': 'user', 'content': 'Em AL?'}
    assert 'FONTE [1]' in captured['messages'][-1]['content']
    assert captured['closed']


def test_groq_errors_before_api_call(monkeypatch):
    monkeypatch.delenv('GROQ_API_KEY', raising=False)
    with pytest.raises(RuntimeError, match='GROQ_API_KEY ausente'):
        list(produce('pt', 'Oi', [], 'Dados'))
    monkeypatch.setenv('GROQ_API_KEY', 'test-only')
    with pytest.raises(RuntimeError, match='somente Groq'):
        list(produce('pt', 'Oi', [], 'Dados', provider='gemini'))


def test_vector_optional_without_key(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('OPENAI_VECTOR_STORE_ID', raising=False)
    assert search_vector('Saúde') == ('', [])


def test_vector_search_http_optional(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'legacy-key')
    monkeypatch.setenv('OPENAI_VECTOR_STORE_ID', 'vs_test')

    def fake_post(url, **kwargs):
        assert url.endswith('/vector_stores/vs_test/search')
        assert kwargs['json']['query'] == 'ação cultural'
        assert kwargs['headers']['Authorization'] == 'Bearer legacy-key'
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {'data': [{'filename': 'acoes.txt', 'content': [{'type': 'text', 'text': 'Ação pública'}]}]},
        )

    monkeypatch.setattr('requests.post', fake_post)
    context, sources = search_vector('ação cultural')
    assert '[1]' in context
    assert 'Ação pública' in context
    assert sources[0]['title'] == 'acoes.txt'
