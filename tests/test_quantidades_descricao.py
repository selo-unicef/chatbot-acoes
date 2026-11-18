"""Teste de relatos de plantio, inclusive termos que NÃO podem ser somados."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from descricao_quantidades import extract_planting_quantities
from nuca_data import (compute_stats, compute_text_quantities, compute_theme_breakdown,
                       text_quantity_intent, conversation_context_question, normalize_records)


def amounts(text, unit='mudas'):
    return [m.count for m in extract_planting_quantities(text, unit)]


@pytest.mark.parametrize('description,expected', [
    ('Foram plantadas 120 mudas nativas.', [120]),
    ('Realizamos plantio de 50 mudas com 150 participantes.', [50]),
    ('Em 2026, 30 mudas foram plantadas na praça.', [30]),
    ('Plantamos 2 mil mudas.', [2000]),
    ('Foi realizado o plantio de 1.200 mudas.', [1200]),
    ('Plantio de 60 mudas e distribuição de 300 mudas.', [60]),
    ('Foram plantadas 40 mudas. Foram plantadas 40 mudas.', [40]),
    ('Doamos 40 mudas e plantamos 35 mudas.', [35]),
    ('Distribuímos 30 mudas e plantamos 60 mudas.', [60]),
    ('Doamos 200 mudas aos moradores.', []),
    ('Recebemos 80 mudas nativas.', []),
    ('A meta é o plantio de 400 mudas.', []),
    ('O projeto prevê o plantio de 300 mudas.', []),
    ('Serão plantadas 70 mudas.', []),
    ('Cerca de 50 mudas foram plantadas.', []),
    ('Foram plantadas aproximadamente 25 mudas.', []),
    ('Houve conversa sobre biodiversidade com 60 participantes.', []),
    ('Plantamos 30 árvores e doamos 20 mudas.', []),
    ('Participaram 300 adolescentes no plantio de mudas.', []),
    ('Foram plantadas 2,5 mil mudas.', []),
    ('Foram distribuídas 150 mudas, sendo 100 plantadas e 50 doadas.', []),
])
def test_extractor_conservador(description, expected):
    assert amounts(description) == expected


def test_arvores_e_mudas_nao_confundidas():
    text = 'Plantamos 30 árvores e 50 mudas nativas.'
    assert amounts(text, 'arvores') == [30]
    assert amounts(text, 'mudas') == [50]


def test_unidade_perguntada_e_nao_acoes():
    assert text_quantity_intent('Quantas ações de plantio de mudas?') is None
    assert text_quantity_intent('E quantas mudas foram plantadas?') == 'mudas'
    assert text_quantity_intent('Qual o total de árvores plantadas nas atividades?') == 'arvores'
    assert text_quantity_intent('Quantos participantes estiveram presentes?') is None


SAMPLE = normalize_records([
    {'status':'Aprovado','uf':'Alagoas (AL)','municipio':'Penedo','tema':'Resiliência Climática',
     'descricao':'Foram plantadas 100 mudas; participaram 30 adolescentes.'},
    {'status':'Em validação/Aprovado','uf':'Alagoas (AL)','municipio':'Penedo','tema':'Resiliência Climática',
     'descricao':'Plantio de 75 mudas e doação de 150 mudas.'},
    {'status':'Aprovado','uf':'Alagoas (AL)','municipio':'Maceió','tema':'Saúde',
     'descricao':'A meta é plantar 800 mudas. Foram distribuídas 100 mudas.'},
    {'status':'Aprovado','uf':'Bahia (BA)','municipio':'Salvador','tema':'Resiliência Climática',
     'descricao':'Plantamos 50 árvores, além da doação de 40 mudas.'},
    {'status':'Em validação','uf':'Alagoas (AL)','municipio':'Penedo','tema':'Resiliência Climática',
     'descricao':'Foram plantadas 900 mudas.'},
], require_status=True)


def test_pergunta_plantio_e_depois_mudas_no_contexto():
    history = [
        {'role':'user', 'content':'Quantas ações voltadas a plantio de mudas em Alagoas?'},
        {'role':'assistant', 'content':'São 3 ações.'}
    ]
    q = 'E quantas mudas foram plantadas?'
    prev = conversation_context_question(q, history, SAMPLE)
    response = compute_text_quantities(SAMPLE, q, prev)
    assert '**175 mudas plantadas**' in response, response
    assert '**2 ações**' in response
    assert '**900**' not in response
    assert 'Penedo (AL)' in response
    assert '150 mudas' not in response
    assert 'parcial' in response


def test_uma_atividade_nao_significa_quantidade_de_mudas():
    assert '**' in compute_stats(SAMPLE, 'Quantas ações de plantio em Alagoas?')
    response = compute_text_quantities(SAMPLE, 'Quantas mudas foram plantadas em Maceió?')
    assert 'não encontrei uma quantidade explícita' in response
    assert '800 mudas' not in response


def test_tematicas_recontadas_em_todos_os_registros():
    ans = compute_theme_breakdown(SAMPLE, 'Quais os principais temas das ações em Penedo?')
    assert '**2 ações**' in ans
    assert 'Resiliência Climática**: 2 ações' in ans
    assert 'Saúde' not in ans


def test_respostas_variam_sem_alterar_o_numero_ou_criar_perguntas():
    responses = [compute_stats(SAMPLE, 'Quantas ações foram registradas em Alagoas?', style=i) for i in range(4)]
    assert len(set(responses)) == 4
    assert all('**3 ações**' in value for value in responses)
    assert all('Cloudflare' not in value and '?' not in value for value in responses)
    assert all('municípios' in value for value in responses)
