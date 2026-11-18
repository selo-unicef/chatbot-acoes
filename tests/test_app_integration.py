"""Smoke tests das rotas Flask, executados onde Flask estiver instalado."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip('flask')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as app_module


def test_health_groq_configured_with_snapshot(monkeypatch, tmp_path):
    snapshot = tmp_path / 'actions.json'
    snapshot.write_text('{"actions": []}', encoding='utf-8')
    monkeypatch.setenv('GROQ_API_KEY', 'test-only')
    monkeypatch.setenv('AI_PROVIDER', 'groq')
    # O objeto de dados foi criado no import; substituição isolada, sem rede.
    from nuca_data import SnapshotStore
    monkeypatch.setattr(app_module, 'store', SnapshotStore(path=str(snapshot)))
    response = app_module.app.test_client().get('/health')
    assert response.status_code == 200
    assert response.json['ai_provider'] == 'groq'
    assert response.json['model'] == 'openai/gpt-oss-20b'


def test_api_chat_uses_groq_streaming(monkeypatch, tmp_path):
    from nuca_data import SnapshotStore
    snapshot = tmp_path / 'actions.json'
    snapshot.write_text('{"actions": [{"id":"1", "titulo":"Oficina de arte", "descricao":"Teatro", "municipio":"Maceió", "uf":"AL", "ano":2026, "status":"Aprovado"}]}', encoding='utf-8')
    monkeypatch.setattr(app_module, 'store', SnapshotStore(path=str(snapshot)))
    monkeypatch.setenv('AI_PROVIDER', 'groq')
    monkeypatch.setenv('GROQ_API_KEY', 'test-only')
    monkeypatch.setenv('DISABLE_RATE_LIMIT', '1')

    def fake_produce(language, question, history, context, provider):
        assert provider == 'groq'
        assert question == 'Descreva a oficina'
        assert 'Oficina de arte' in context
        yield 'Foi realizada '
        yield 'uma oficina [1].'

    monkeypatch.setattr(app_module, 'produce', fake_produce)
    response = app_module.app.test_client().post('/api/chat/stream', json={'message': 'Descreva a oficina', 'history': []})
    assert response.status_code == 200
    output = response.get_data(as_text=True)
    assert 'event: meta' in output
    assert 'event: delta' in output
    assert 'Foi realizada ' in output
    assert 'uma oficina [1].' in output
    assert 'event: done' in output
