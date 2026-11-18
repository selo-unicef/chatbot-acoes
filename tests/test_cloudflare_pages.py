"""Testes determinísticos de integração com o formato real do manifesto da Cloudflare Pages."""
import json
import sys
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nuca_data import CloudflarePagesStore, compute_profile_stats, compute_stats, search_snapshot

SID = '82ead3d505dcd68023a3635f'
BASE = 'https://nucas-dados.pages.dev'

ACTIONS = [
    {'status':'Aprovado','uf':'Pernambuco (PE)','municipio':'Recife','tema':'Saúde',
     'descricao':'Mutirão de prevenção da dengue','ano_acao':2026,
     'publico':80,'adoles_parcipantes':12,'link_acao':'https://exemplo.org/acao1'},
    {'status':'Em validação/Aprovado','uf':'Pernambuco (PE)','municipio':'Olinda',
     'tema':'Cultura','descricao':'Oficina de teatro','ano_acao':2026,
     'publico':40,'adoles_parcipantes':8},
    {'status':'Em validação','uf':'Pernambuco (PE)','municipio':'Recife',
     'tema':'Educação','descricao':'Ainda não confirmada','ano_acao':2026},
]
GENERO = [
    {'uf':'UF','municipio':'Município','feminino':0,'masculino':0,'nao_binario':0,'total_membros':0,'status':'NUCA criado?'},
    {'uf':'Pernambuco (PE)','municipio':'Recife','feminino':14,'masculino':8,'nao_binario':1,'total_membros':23},
]
RACIAL = [{'uf':'Pernambuco (PE)','municipio':'Recife','indigenas':2,'quilombolas':1,'ribeirinhos':0,
           'amarela':0,'branca':4,'parda':10,'preta':9,'adolescentes':23}]
DATA = {'acoes_nuca': ACTIONS, 'detalhes_nucas': GENERO, 'detalhes_adolescentes': RACIAL}

def manifest(sid=SID):
    return {'schema_version':1, 'snapshot_id':sid,'updated_at':'2026-11-16T19:49:57+00:00',
            'tables':{key:{'rows':len(rows), 'parts':[f'snapshots/{sid}/{key}/parte-001.json']}
                      for key,rows in DATA.items()}}

class Response:
    def __init__(self, value, status=200):
        self.value=value
        self.status_code=status
    def __enter__(self):return self
    def __exit__(self, *args):return False
    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError('HTTP %d'%self.status_code)
    def iter_content(self, size):
        data=json.dumps(self.value,ensure_ascii=False).encode('utf-8')
        yield data

class Session:
    def __init__(self):
        self.calls=[]
        self.sid=SID
        self.broken=False
    def get(self,url,**kwargs):
        self.calls.append(url)
        if '/manifest.json' in url:
            return Response(manifest(self.sid))
        for name,rows in DATA.items():
            if url == f'{BASE}/snapshots/{SID}/{name}/parte-001.json':
                return Response(rows)
        return Response(None,404)

def test_reads_all_datasets_and_statuses():
    session=Session()
    store=CloudflarePagesStore(BASE, ttl=60, session=session)
    datasets=store.get_datasets()
    assert store.version == SID
    assert len(datasets['acoes_nuca']) == 2  # apenas Aprovado e Em validação/Aprovado
    assert len(datasets['detalhes_nucas']) == 1  # descarta cabeçalho UF/Município
    assert len(datasets['detalhes_adolescentes']) == 1
    assert 'Membros' in compute_profile_stats(datasets,'Qual é o perfil dos integrantes dos NUCAs?')
    assert '23' in compute_profile_stats(datasets,'Qual o perfil de gênero dos integrantes em Pernambuco?')
    assert '2' in compute_profile_stats(datasets,'Quantos indígenas em PE?')
    assert '**2 ações' in compute_stats(store.get(),'Quantas ações em Pernambuco em 2026?')
    assert '120 pessoas mobilizadas' in compute_stats(store.get(),'Quantas pessoas foram mobilizadas em PE?')
    assert '20 participações de adolescentes' in compute_stats(store.get(),'Quantos adolescentes participaram das ações em PE?')
    assert search_snapshot(store.get(),'Ações de dengue em Recife')[0]['record']['tema']=='Saúde'
    assert 'Cultura' in store.get()[1]['tema']
    assert len(session.calls) == 4

def test_manifest_only_when_revalidating_same_snapshot():
    session=Session()
    store=CloudflarePagesStore(BASE, ttl=60, session=session)
    assert store.get() is not None
    store.expires=0
    assert store.get() is not None
    assert len(session.calls)==5   # 1 manifest, 3 parts, 1 manifest

def test_preserve_valid_data_when_new_snapshot_fails():
    session=Session()
    store=CloudflarePagesStore(BASE,ttl=60,session=session)
    assert store.get()
    old=store.get_datasets()
    store.expires=0
    session.sid='a'*24
    assert store.get_datasets() is old
    assert store.version==SID
    assert store.error

def test_reject_traversal_in_manifest():
    session=Session()
    original_get=session.get
    def malicious(url,**kwargs):
        if '/manifest.json' in url:
            payload=manifest()
            payload['tables']['acoes_nuca']['parts']=['../../.env']
            return Response(payload)
        return original_get(url,**kwargs)
    session.get=malicious
    store=CloudflarePagesStore(BASE,session=session)
    assert store.get() is None
    assert 'Caminho inválido' in store.error
