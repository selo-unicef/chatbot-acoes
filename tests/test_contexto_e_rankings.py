"""Perguntas da conversa real: nenhum total ou ranking a partir de seis exemplos."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from nuca_data import (clarification_request, conversation_context_question, compute_stats,
                       compute_action_details, compute_theme_breakdown, compute_text_quantities,
                       detect_filters, filtered_actions, identify_action_topic,
                       is_followup_question, metric_intent, normalize_records, text_quantity_intent)


def build_rows():
    data=[]
    for i in range(12):
        data.append({'municipio':'Penedo','uf':'Alagoas (AL)','tema':'Resiliência Climática - #EntreNoClimaUNICEF',
                     'descricao': 'Foram plantadas 10 mudas no município.' if i==0 else 'Discussão sobre clima e juventude',
                     'status':'Aprovado','ano_acao':2026})
    for i in range(20):
        data.append({'municipio':'Recife','uf':'Pernambuco (PE)', 'tema':'Prevenção às violências',
                     'descricao':'Roda de conversa sobre violência e direitos',
                     'status':'Em validação/Aprovado','ano_acao':2026})
    for i in range(5):
        data.append({'municipio':'Olinda','uf':'Pernambuco (PE)', 'tema':'Empoderamento de meninas',
                     'descricao':'Atividade de empoderamento e igualdade','status':'Aprovado','ano_acao':2025})
    for i in range(3):
        data.append({'municipio':'Penedo','uf':'São Paulo (SP)', 'tema':'Cultura',
                     'descricao':'Oficina artística','status':'Aprovado','ano_acao':2026})
    for i in range(2):
        data.append({'municipio':'Salvador','uf':'Bahia (BA)', 'tema':'Prevenção às violências',
                     'descricao':'Debate comunitário','status':'Aprovado','ano_acao':2026})
    data.append({'municipio':'Recife','uf':'Pernambuco (PE)', 'tema':'Prevenção às violências',
                 'descricao':'Aguardando análise','status':'Em validação','ano_acao':2026})
    return normalize_records(data,require_status=True)

ROWS=build_rows()

def hist(*turns):
    return [{'role':'user','content':t} for t in turns]


def test_count_after_planting_resets_topic_explicitly():
    past=hist('Quantas ações de plantio no Brasil?', 'E quantas mudas foram plantadas?')
    q='E quantas ações totais foram feitas em Alagoas?'
    prev=conversation_context_question(q,past,ROWS)
    answer=compute_stats(ROWS,q,prev)
    assert '12 ações' in answer,answer
    assert 'plantio' not in prev
    assert 'AL' in prev


def test_ambiguous_total_asks_clarification_and_next_message_works():
    past=hist('Quantas mudas de plantas foram plantadas nas ações?')
    q='Certo, agora quero saber o total de alagoas'
    ask=clarification_request(q,past)
    assert 'total de ações' in ask and 'mudas' in ask
    # Não inventar um total enquanto aguarda a escolha do usuário.
    prev=conversation_context_question('ações',past+hist(q),ROWS)
    assert 'Quantas ações' in prev and 'AL' in prev,prev
    assert '**12 ações**' in compute_stats(ROWS,'ações',prev)
    prev2=conversation_context_question('mudas',past+hist(q),ROWS)
    assert 'AL' in prev2
    assert text_quantity_intent('mudas')=='mudas'
    assert '10 mudas' in compute_text_quantities(ROWS,'mudas',prev2),prev2


def test_examples_in_the_state_retain_pe_and_topic():
    past=hist('Quais os temas com mais acoes em Pernambuco?')
    q='De exemplos de algumas acoes no estado sobre Prevenção às violências'
    prev=conversation_context_question(q,past,ROWS)
    answer=compute_action_details(ROWS,q,prev)
    assert 'PE' in prev and 'Prevenção' in prev,prev
    assert '**20 ações registradas**' in answer,answer
    assert 'Recife' in answer
    assert 'Salvador' not in answer and 'Penedo' not in answer


def test_state_switch_without_stale_municipality():
    past=hist('Quantas ações em Penedo, Alagoas?')
    q='E em Pernambuco?'
    prev=conversation_context_question(q,past,ROWS)
    assert 'PE' in prev and 'Penedo' not in prev,prev
    assert '**25 ações**' in compute_stats(ROWS,q,prev)
    ff=detect_filters(q,'Quantas ações em Penedo em AL?')
    assert ff['municipio'] is None


def test_ranking_singular_is_full_set_not_retrieved_sample():
    q='Qual municipio do pais fez mais acoes?'
    assert metric_intent(q)=='ranking'
    answer=compute_stats(ROWS,q)
    assert 'Recife (PE)' in answer
    assert '**20 ações**' in answer, answer
    assert 'dois' not in answer


def test_city_names_in_other_states_are_not_merged():
    q='Quais municípios do país fizeram mais ações?'
    answer=compute_stats(ROWS,q)
    assert 'Penedo (AL)' in answer and 'Penedo (SP)' in answer
    assert '12 ações' in answer and '3 ações' in answer


def test_ranking_respects_explicit_state_and_topic():
    q='Qual município de Pernambuco tem mais ações na categoria Prevenção às violências?'
    answer=compute_stats(ROWS,q)
    assert 'Recife (PE)' in answer and '20 ações' in answer, answer
    assert 'Penedo' not in answer


def test_thematic_breakdown_calculated_on_all_rows():
    q='Quais os temas com mais acoes em Pernambuco?'
    answer=compute_theme_breakdown(ROWS,q)
    assert 'Prevenção às violências' in answer and '20 ações' in answer
    assert 'Empoderamento de meninas' in answer and '5 ações' in answer


def test_unexpected_topic_cannot_return_statewide_total():
    q='De exemplos de acoes no estado sobre robótica'
    p=conversation_context_question(q,hist('Quantas ações em Pernambuco?'),ROWS)
    found,_,topic=filtered_actions(ROWS,q,p)
    assert topic is not None and found==[]
    answer=compute_action_details(ROWS,q,p)
    assert '**0 ações registradas**' in answer


def test_independent_rank_has_no_stale_filter():
    h=hist('Quais os temas de Pernambuco?', 'De exemplos de algumas ações sobre Prevenção às violências')
    q='Qual município do país fez mais ações?'
    assert not is_followup_question(q)
    assert conversation_context_question(q,h,ROWS)==''
    assert '**20 ações**' in compute_stats(ROWS,q)


def test_followup_quantities_keeps_state_after_multiple_turns():
    h=hist('Quantas mudas foram plantadas nas ações?', 'E em Alagoas?')
    p=conversation_context_question('Pode detalhar?',h,ROWS)
    assert 'Quantas mudas' in p and 'AL' in p, p
    assert '10 mudas' in compute_text_quantities(ROWS,'Pode detalhar?',p,unit=text_quantity_intent(p))


def test_independent_question_does_not_inherit_thematic_scope():
    q='Quantas ações foram registradas no Brasil?'
    assert not is_followup_question(q)
    assert '**42 ações**' in compute_stats(ROWS,q),compute_stats(ROWS,q)
