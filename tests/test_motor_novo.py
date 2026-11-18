from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from descricao_generica import infer_quantity_spec, extract_generic_quantities
from nuca_data import (compute_advanced_stats, compute_generic_text_quantity,
                       corpus_overview, normalize_records)
from semantic_router import should_route_question

ROWS = normalize_records([
    {'status':'Aprovado','uf':'Alagoas (AL)','municipio':'Penedo','tema':'Cultura','descricao':'Foram distribuídos 120 livros aos estudantes.','publico':50,'adoles_parcipantes':20},
    {'status':'Aprovado','uf':'Alagoas (AL)','municipio':'Penedo','tema':'Clima','descricao':'Ação ambiental','publico':60,'adoles_parcipantes':30},
    {'status':'Em validação/Aprovado','uf':'Alagoas (AL)','municipio':'Maceió','tema':'Cultura','descricao':'Foram distribuídos 80 livros em uma oficina.','publico':70,'adoles_parcipantes':40},
    {'status':'Aprovado','uf':'Pernambuco (PE)','municipio':'Recife','tema':'Saúde','descricao':'Campanha arrecadou 350 quilos de alimentos.','publico':100,'adoles_parcipantes':25},
    {'status':'Aprovado','uf':'Pernambuco (PE)','municipio':'Recife','tema':'Saúde','descricao':'Outra ação','publico':90,'adoles_parcipantes':15},
    {'status':'Aprovado','uf':'Pernambuco (PE)','municipio':'Olinda','tema':'Cultura','descricao':'Outra ação','publico':20,'adoles_parcipantes':10},
], require_status=True)


def test_generic_quantity_books():
    spec = infer_quantity_spec('Quantos livros foram distribuídos nas ações em Alagoas?')
    assert spec is not None
    assert [m.count for m in extract_generic_quantities('Foram distribuídos 120 livros.', spec)] == [120.0]
    answer = compute_generic_text_quantity(ROWS, 'Quantos livros foram distribuídos nas ações em Alagoas?')
    assert '200 livros' in answer
    assert 'total parcial' in answer


def test_generic_quantity_food_kg():
    answer = compute_generic_text_quantity(ROWS, 'Quantos quilos de alimentos foram arrecadados em Pernambuco?')
    assert '350 quilos de alimentos' in answer


def test_threshold_municipalities_full_base():
    answer = compute_advanced_stats(ROWS, 'Quantos municípios fizeram pelo menos 2 ações?')
    assert '2 municípios' in answer
    assert 'Penedo' in answer and 'Recife' in answer


def test_compare_states():
    answer = compute_advanced_stats(ROWS, 'Quem tem mais ações, Alagoas ou Pernambuco?')
    assert '**AL**: 3 ações' in answer
    assert '**PE**: 3 ações' in answer


def test_rank_adolescents_by_municipality():
    answer = compute_advanced_stats(ROWS, 'Qual município teve mais adolescentes envolvidos nas ações?')
    assert 'Penedo (AL)' in answer
    assert '50' in answer


def test_overview_uses_entire_scope():
    text = corpus_overview(ROWS, 'Quais ações existem em Pernambuco?')
    assert 'Total de ações: 3' in text
    assert 'Municípios distintos: 2' in text


def test_router_for_followup_and_complex_queries():
    history = [{'role':'user','content':'Quantas ações em Pernambuco?'}]
    assert should_route_question('E em Alagoas?', history)
    assert should_route_question('Qual município teve mais adolescentes?', [])


def test_structured_router_returns_canonical(monkeypatch):
    import json
    from types import SimpleNamespace
    import sys
    from semantic_router import route_question

    captured = {}
    payload = {
        'action':'canonical', 'intent':'count_actions',
        'canonical_question':'Quantas ações foram registradas em Alagoas?',
        'clarification':'', 'search_terms':[], 'confidence':96,
    }

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])

    class FakeGroq:
        def __init__(self, **kwargs):
            captured['client'] = kwargs
            self.chat = SimpleNamespace(completions=FakeCompletions())
        def close(self):
            captured['closed'] = True

    monkeypatch.setitem(sys.modules, 'groq', SimpleNamespace(Groq=FakeGroq))
    monkeypatch.setenv('GROQ_API_KEY', 'test-only')
    out = route_question('E em Alagoas?', [{'role':'user','content':'Quantas ações em Pernambuco?'}],
                         'Quantas ações em Pernambuco?', ['Saúde','Cultura'], ['Recife — Pernambuco (PE)'])
    assert out['canonical_question'].endswith('Alagoas?')
    assert captured['response_format']['type'] == 'json_schema'
    assert captured['response_format']['json_schema']['strict'] is True
    assert captured['closed']
