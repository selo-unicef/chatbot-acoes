"""Regressões com perguntas reais enviadas por usuários do chatbot."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from nuca_data import (compute_action_details, compute_profile_stats, compute_stats,
                       filtered_actions, identify_action_topic, is_followup_question,
                       normalize_records)

ROWS = [
    {'status':'Aprovado','uf':'Pernambuco (PE)','municipio':'Recife',
     'tema':'Saúde integral e integrada de adolescentes','descricao':'Mutirão de vacinação e saúde','ano_acao':2026},
    {'status':'Em validação/Aprovado','uf':'Pernambuco (PE)','municipio':'Olinda',
     'tema':'Resiliência Climática - #EntreNoClimaUNICEF','descricao':'Plantio de árvores nativas e doação de mudas','ano_acao':2026},
    {'status':'Aprovado','uf':'Alagoas (AL)','municipio':'Maceió',
     'tema':'Empoderamento de meninas','descricao':'Encontro sobre direitos e igualdade de gênero','ano_acao':2026},
    {'status':'Aprovado','uf':'Alagoas (AL)','municipio':'Penedo',
     'tema':'Empoderamento de meninas','descricao':'Roda de conversa sobre empoderamento','ano_acao':2026},
    {'status':'Aprovado','uf':'Bahia (BA)','municipio':'Salvador',
     'tema':'Resiliência Climática - #EntreNoClimaUNICEF','descricao':'Ação de plantio no parque','ano_acao':2026},
    {'status':'Em validação','uf':'Alagoas (AL)','municipio':'Arapiraca',
     'tema':'Empoderamento de meninas','descricao':'Ainda aguardando aprovação','ano_acao':2026},
]
APPROVED = normalize_records(ROWS,require_status=True)
DATASETS={'detalhes_nucas':[{'uf':'Alagoas (AL)','municipio':'Maceió','feminino':1742,'masculino':1000,'nao_binario':0,'total_membros':2742}],
          'detalhes_adolescentes':[]}


def test_estado_total_e_continuacao_preserva_o_total():
    assert '**2 ações**' in compute_stats(APPROVED,'Quantas ações foram realizadas em Pernambuco?')
    detail=compute_action_details(APPROVED,'Pode detalhar?', 'Quantas ações foram realizadas em Pernambuco?')
    assert '**2 ações registradas**' in detail
    assert 'Recife' in detail and 'Olinda' in detail
    assert 'municípios' in detail


def test_plantio_nao_retorna_total_de_toda_base():
    answer=compute_stats(APPROVED,'Quantas ações de plantio foram feitas no Brasil?')
    assert '**2 ações**' in answer,answer
    assert 'plantio' in answer
    assert '**5 ações**' not in answer


def test_tema_empoderamento_nao_e_genero_feminino():
    q='Qual o total de ações de Alagoas na categoria de Empoderamento de Meninas?'
    assert compute_profile_stats(DATASETS,q) is None
    assert '**2 ações**' in compute_stats(APPROVED,q)
    assert 'Empoderamento de meninas' in compute_stats(APPROVED,q)


def test_detalhes_por_categoria_e_recorte():
    q='Quantas ações na categoria de Empoderamento de meninas em Alagoas?'
    detail=compute_action_details(APPROVED,'Pode detalhar?',q)
    assert '**2 ações registradas**' in detail
    assert 'Maceió' in detail and 'Penedo' in detail
    assert 'Salvador' not in detail


def test_perguntas_independentes_nao_herdam_estado():
    assert not is_followup_question('Quantas ações de plantio foram feitas no Brasil?')
    assert is_followup_question('Pode detalhar?')
    # O backend passa prev vazio em perguntas independentes.
    matches,_,_=filtered_actions(APPROVED,'Quantas ações de plantio foram feitas no Brasil?', '')
    assert len(matches)==2


def test_tema_desconhecido_nao_gera_total_geral():
    question='Quantas ações de robótica espacial foram realizadas em Pernambuco?'
    answer=compute_stats(APPROVED,question)
    assert '**0 ações**' in answer


def test_aprovado_e_em_validacao_aprovado_entram_na_conta():
    assert len(APPROVED)==5
    assert 'Arapiraca' not in str(APPROVED)

def test_nome_da_categoria_termina_em_adolescentes_mas_pede_numero_de_acoes():
    q='Qual o total de ações na categoria Saúde integral e integrada de adolescentes em Pernambuco?'
    assert '**1 ação**' in compute_stats(APPROVED,q)

def test_continuacao_e_em_outro_estado_preserva_tema_e_atualiza_geografia():
    from nuca_data import conversation_context_question
    hist=[{'role':'user','content':'Quantas ações de plantio em Pernambuco?'},
          {'role':'assistant','content':'1 ação'},
          {'role':'user','content':'E em Alagoas?'},
          {'role':'assistant','content':'0 ações'}]
    context=conversation_context_question('Pode detalhar?',hist,APPROVED)
    assert 'AL' in context and 'PE' not in context
    assert 'plantio' in context
    ans=compute_action_details(APPROVED,'Pode detalhar?',context)
    assert '**0 ações registradas**' in ans


def test_mais_um_filtro_herdado_em_continuacao():
    from nuca_data import conversation_context_question
    hist=[{'role':'user','content':'Quantas ações em Pernambuco?'},
          {'role':'assistant','content':'2 ações'}]
    prev=conversation_context_question('E em Alagoas?',hist,APPROVED)
    assert '**2 ações**' in compute_stats(APPROVED,'E em Alagoas?',prev)
