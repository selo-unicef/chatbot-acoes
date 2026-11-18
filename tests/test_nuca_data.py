import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from nuca_data import (applies, detect_filters, compute_stats, normalize_records,
                       search_snapshot, source, metric_intent)

RECS = [
    {'id':1,'titulo':'Ação de vacinação','descricao':'Campanha de saúde com jovens','municipio':'Recife','uf':'PE','ano':2026,'tema':'Saúde','pessoas_mobilizadas':120,'status':'Aprovado'},
    {'id':2,'titulo':'Horta e reciclagem','descricao':'Mutirão de meio ambiente','municipio':'Olinda','uf':'PE','ano':2026,'tema':'Meio ambiente','pessoas_mobilizadas':80,'status':'Aprovado'},
    {'id':3,'titulo':'Oficina de teatro','descricao':'Arte na escola','municipio':'Maceió','uf':'AL','ano':2025,'tema':'Cultura','pessoas_mobilizadas':45,'status':'Aprovado'},
]

def test_filters_year_and_uf():
    assert detect_filters('Quais ações de saúde em Pernambuco em 2026?')['uf']=='PE'
    assert detect_filters('Quais ações em AL?')['uf']=='AL'
    assert detect_filters('O que fizeram para mobilizar jovens?')['uf'] is None
    assert detect_filters('Quais ações no Pará?')['uf']=='PA'
    assert detect_filters('E em 2025?', 'Quais ações em Pernambuco em 2026?') == {'year':'2025','uf':'PE','municipio':None}

def test_filters_safe_on_missing():
    assert not applies({'titulo':'sem uf'},{'year':'2026','uf':'PE','municipio':None})
    assert applies(RECS[0], {'year':'2026','uf':'PE','municipio':None})

def test_approved_only():
    rows=normalize_records({'actions':RECS+[{'id':4,'status':'Em validação','titulo':'Não publicada'}]})
    assert len(rows)==3

def test_count_and_people():
    assert '**2 ações' in compute_stats(RECS,'Quantas ações em PE em 2026?')
    assert '200 pessoas mobilizadas' in compute_stats(RECS,'Quantas pessoas participaram das ações em PE?')
    assert 'não uma contagem de pessoas únicas' in compute_stats(RECS,'Quantas pessoas participaram das ações em PE?')

def test_missing_people_never_guess():
    rows=RECS+[{'id':5,'titulo':'Sem número','municipio':'Recife','uf':'PE','ano':2026,'tema':'Saúde','status':'Aprovado'}]
    answer=compute_stats(rows,'Quantas pessoas foram mobilizadas em PE?')
    assert 'Não é possível calcular um total completo' in answer

def test_no_invented_source():
    assert source(RECS[0],1)['id']=='1'
    assert source({'titulo':'sem id'},10)['id'] is None

def test_lexical_search_and_continuation():
    hits=search_snapshot(RECS,'O que fizeram sobre meio ambiente em PE?')
    assert hits[0]['record']['id']==2
    hits=search_snapshot(RECS,'E em 2025?','Quais ações em AL em 2026?')
    assert hits[0]['record']['id']==3

def test_metrics():
    assert metric_intent('Quais ações existem?') is None
    assert metric_intent('Quantos municípios participaram?')=='municipios'
    assert metric_intent('Ranking de municípios com mais ações')=='ranking'

def test_do_not_confuse_adolescents_with_all_mobilized_people():
    answer = compute_stats(RECS,'Quantos adolescentes participaram das ações em PE?')
    assert 'Não é possível calcular um total completo' in answer
    assert 'adolescentes participantes' in answer

def test_synonym_environmental_and_number_format():
    from nuca_data import num
    assert search_snapshot(RECS,'Ações ambientais em PE?')[0]['record']['id'] == 2
    assert num('1.234') == 1234
    assert num('1.234,50') == 1234.5
