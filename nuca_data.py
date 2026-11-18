"""Busca local e estatísticas sobre um snapshot público e desidentificado de ações.

Nenhum SQL ou cálculo de contagens globais é delegado a um LLM.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from descricao_quantidades import extract_planting_quantities
from descricao_generica import infer_quantity_spec, extract_generic_quantities

import requests

log = logging.getLogger(__name__)
NORMALIZE_SYNONYMS = {'ambientais':'ambiente','ambiental':'ambiente','ecologicas':'ecologia','culturais':'cultura','cultural':'cultura','educacionais':'educacao','educacional':'educacao','escolares':'escola','sanitarias':'saude','mobilizacao':'mobilizar','mobilizacoes':'mobilizar','jovens':'adolescentes'}
STOP = set('a o as os e em de da do das dos que quais qual quantas quantos quantos para por com um uma uns umas no na nos nas sobre me fale foram feitas feitos foi tem ter sua seu seus suas esse essa desses dessas como quais existem existe realizadas realizado realizadas por favor sobre mais onde quais ha há'.split())
FIELD_ALIASES = {
    'id': ('id', 'id_acao', 'id_acao_nuca', 'acao_id', 'codigo_acao', 'codigo'),
    'titulo': ('titulo', 'nome_acao', 'titulo_acao', 'nome', 'title'),
    'descricao': ('descricao', 'descricao_acao', 'resumo', 'relato', 'texto', 'description', 'conteudo'),
    'municipio': ('municipio', 'nm_mun', 'nm_municipio', 'nome_municipio', 'cidade'),
    'uf': ('uf', 'sg_uf', 'estado', 'sigla_uf'),
    'ano': ('ano', 'ano_acao', 'year', 'exercicio'),
    'tema': ('tema', 'temas', 'tematica', 'categoria', 'area', 'eixo'),
    'pessoas': ('pessoas_mobilizadas', 'total_pessoas_mobilizadas', 'quantidade_pessoas_mobilizadas', 'qtd_pessoas_mobilizadas', 'publico', 'participantes', 'total_participantes'),
    'adolescentes': ('adolescentes_mobilizados', 'total_adolescentes', 'qtd_adolescentes', 'adoles_parcipantes', 'adoles_participantes', 'adolescentes_participantes', 'quantidade_adolescentes'),
    'status': ('status', 'status_validacao', 'situacao', 'situacao_acao'),
    'url': ('url', 'link_acao', 'url_acao', 'link', 'rede_social'),
    'nuca': ('nuca_id', 'id_nuca', 'nuca'),
}
STATES = {
    'acre':'AC','alagoas':'AL','amapa':'AP','amazonas':'AM','bahia':'BA','ceara':'CE',
    'distrito federal':'DF','espirito santo':'ES','goias':'GO','maranhao':'MA','mato grosso':'MT',
    'mato grosso do sul':'MS','minas gerais':'MG','para':'PA','paraiba':'PB','parana':'PR',
    'pernambuco':'PE','piaui':'PI','rio de janeiro':'RJ','rio grande do norte':'RN',
    'rio grande do sul':'RS','rondonia':'RO','roraima':'RR','santa catarina':'SC',
    'sao paulo':'SP','sergipe':'SE','tocantins':'TO',
}

def norm(value: Any) -> str:
    text = unicodedata.normalize('NFKD', str(value or ''))
    return ''.join(c for c in text if not unicodedata.combining(c)).lower().strip()

def words(s: Any) -> list[str]:
    return [NORMALIZE_SYNONYMS.get(w, w) for w in re.findall(r'[a-z0-9]+', norm(s)) if len(w) > 2 and w not in STOP]

def field(record: dict, name: str) -> Any:
    for alias in FIELD_ALIASES[name]:
        value = record.get(alias)
        if value is not None and value != '':
            return value
    return None

def string_field(record: dict, name: str) -> str:
    value = field(record, name)
    if isinstance(value, (dict, list)):
        return ', '.join(map(str, value)) if isinstance(value, list) else json.dumps(value, ensure_ascii=False)
    return str(value or '').strip()

def num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    if value is None:
        return None
    s = re.sub(r'[^\d,.-]', '', str(value).strip())
    if not s:
        return None
    if ',' in s and '.' in s:
        s = s.replace('.', '').replace(',', '.')
    elif re.fullmatch(r'\d{1,3}(?:\.\d{3})+', s):
        s = s.replace('.', '')
    elif ',' in s:
        s = s.replace(',', '.')
    try:
        out = float(s)
        return out if math.isfinite(out) else None
    except ValueError:
        return None

def source(rec: dict, position: int) -> dict:
    identifier = string_field(rec, 'id')
    u = string_field(rec, 'url')
    title = string_field(rec, 'titulo') or ' — '.join(filter(None, [string_field(rec, 'tema'), string_field(rec, 'municipio')])) or ('Ação ' + identifier if identifier else f'Registro {position}')
    return {
        'id': identifier or None,
        'title': title[:140],
        'municipality': string_field(rec, 'municipio')[:90],
        'uf': string_field(rec, 'uf')[:30],
        'year': string_field(rec, 'ano')[:8],
        'url': u if urlparse(u).scheme == 'https' else None,
    }

def normalize_records(payload: Any, require_status: bool = False) -> list[dict]:
    if isinstance(payload, dict):
        for key in ('actions', 'acoes', 'data', 'registros', 'records'):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
    if not isinstance(payload, list):
        raise ValueError('Snapshot deve ser um array de ações, ou objeto {"actions": [...]}')
    # A fonte publicada precisa estar filtrada e sem dados pessoais; não presumir status inexistentes.
    records = [r for r in payload if isinstance(r, dict)]
    # Se status existir, publicar SOMENTE registros aprovados; ausência de status indica feed já filtrado.
    approved = {'aprovado','aprovada','approved','validado','validada','publicado','publicada', 'em validacao/aprovado'}
    return [r for r in records if not _is_header(r) and ((not require_status and not string_field(r, 'status')) or re.sub(r'\s*/\s*', '/', norm(string_field(r,'status'))) in approved)]

def _is_header(record: dict) -> bool:
    """Ignora a linha de cabeçalho eventualmente exportada pela planilha."""
    return norm(string_field(record, 'uf')) == 'uf' and norm(string_field(record, 'municipio')) in ('municipio', 'municipality')


class CloudflarePagesStore:
    """Carrega as três tabelas do manifesto Pages, usando um snapshot atômico em cache.

    Revalida o manifesto periodicamente, mas só baixa as partes se o snapshot mudou.
    Em caso de falha, mantém o último snapshot COMPLETO, nunca uma versão parcial.
    """
    TABLES = ('acoes_nuca', 'detalhes_adolescentes', 'detalhes_nucas')

    def __init__(self, base_url: str = 'https://nucas-dados.pages.dev', ttl: int = 600, session=None):
        base_url = base_url.strip().rstrip('/')
        if not base_url.startswith('https://') or not urlparse(base_url).hostname:
            raise ValueError('CLOUDFLARE_PAGES_URL deve ser um endereço HTTPS válido')
        self.base_url = base_url
        self.url = base_url + '/manifest.json'
        self.ttl = max(60, int(ttl))
        self.session = session or requests
        self._lock = threading.RLock()
        self.records: list[dict] | None = None
        self.datasets: dict[str, list[dict]] | None = None
        self.version = ''
        self.updated_at = ''
        self.expires = 0.0
        self.error = ''

    def configured(self) -> bool:
        return True

    def _json(self, url: str, byte_limit: int) -> Any:
        with self.session.get(url, timeout=30, stream=True, allow_redirects=False) as response:
            response.raise_for_status()
            chunks = []
            size = 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > byte_limit:
                    raise ValueError('Arquivo público excede o limite de segurança')
                chunks.append(chunk)
            return json.loads(b''.join(chunks))

    def get_datasets(self) -> dict[str, list[dict]] | None:
        if self.datasets is not None and time.time() < self.expires:
            return self.datasets
        with self._lock:
            if self.datasets is not None and time.time() < self.expires:
                return self.datasets
            try:
                manifest = self._json(self.url + '?v=' + str(int(time.time() // 60)), 256_000)
                if not isinstance(manifest, dict) or manifest.get('schema_version') != 1:
                    raise ValueError('manifest.json inválido ou incompatível')
                sid = str(manifest.get('snapshot_id', ''))
                if not re.fullmatch(r'[a-f0-9]{24,64}', sid):
                    raise ValueError('snapshot_id inválido')
                tables = manifest.get('tables')
                if not isinstance(tables, dict) or not all(name in tables for name in self.TABLES):
                    raise ValueError('manifest.json sem as três tabelas necessárias')
                if self.datasets is not None and sid == self.version:
                    self.expires = time.time() + self.ttl
                    self.error = ''
                    return self.datasets
                fresh = {}
                for name in self.TABLES:
                    info = tables[name]
                    if not isinstance(info, dict) or not isinstance(info.get('parts'), list):
                        raise ValueError('Catálogo de dados incompleto: ' + name)
                    paths = info['parts']
                    if not paths or len(paths) > 100:
                        raise ValueError('Número de partes inválido: ' + name)
                    rows = []
                    for part in paths:
                        if not isinstance(part, str) or not re.fullmatch(
                            rf'snapshots/{re.escape(sid)}/{re.escape(name)}/parte-\d{{3}}\.json', part
                        ):
                            raise ValueError('Caminho inválido no manifesto: ' + name)
                        payload = self._json(self.base_url + '/' + part, 16 * 1024 * 1024)
                        if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
                            raise ValueError('Parte JSON não é uma lista de registros: ' + name)
                        rows.extend(payload)
                        if len(rows) > 200_000:
                            raise ValueError('Quantidade de registros fora do limite: ' + name)
                    if len(rows) != info.get('rows'):
                        raise ValueError(f'Contagem inconsistente em {name}: {len(rows)} != {info.get("rows")}')
                    if name == 'acoes_nuca':
                        fresh[name] = normalize_records(rows, require_status=True)
                    else:
                        fresh[name] = [r for r in rows if not _is_header(r)]
                # Troca atômica: somente depois de baixar e conferir todas as tabelas.
                self.datasets = fresh
                self.records = fresh['acoes_nuca']
                self.version = sid
                self.updated_at = str(manifest.get('updated_at', ''))
                self.expires = time.time() + self.ttl
                self.error = ''
                log.info('Cloudflare Pages: snapshot %s carregado (%d ações elegíveis, %d gênero, %d racial)',
                         sid, len(self.records), len(fresh['detalhes_nucas']), len(fresh['detalhes_adolescentes']))
            except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
                self.error = str(exc)
                self.expires = time.time() + 60
                log.warning('Falha ao ler Cloudflare Pages; mantendo último snapshot válido: %s', exc)
            return self.datasets

    def get(self) -> list[dict] | None:
        datasets = self.get_datasets()
        return datasets.get('acoes_nuca') if datasets is not None else None


def _uf_match(value: Any, uf: str | None) -> bool:
    if not uf:
        return True
    ru = norm(value)
    return bool(ru and (ru == norm(uf) or STATES.get(ru) == uf or ru.endswith('(' + norm(uf) + ')')))


def compute_profile_stats(datasets: dict[str, list[dict]], question: str, prev: str = '', language: str = 'pt') -> str | None:
    """Somas diretas para perguntas explícitas sobre dados agregados de gênero ou raça.

    Não soma categorias de raça e de pertencimento territorial, pois podem se sobrepor.
    """
    q = norm(question)
    # 'Ações de Empoderamento de Meninas' é um tema, não uma pergunta sobre gênero.
    if re.search(r'\b(acoes|atividades|iniciativas|projetos)\b', q):
        return None
    if not re.search(r'\b(quantos?|quantas?|total|distribuicao|numero|dados|perfil|composicao)\b', q):
        return None
    group = None
    race_terms = {'indigenas':'indigenas', 'indigena':'indigenas', 'quilombola':'quilombolas',
                  'quilombolas':'quilombolas', 'ribeirinho':'ribeirinhos', 'ribeirinhos':'ribeirinhos',
                  'parda':'parda', 'pardos':'parda', 'pardas':'parda', 'preta':'preta',
                  'pretos':'preta', 'pretas':'preta', 'branca':'branca', 'brancos':'branca',
                  'brancas':'branca', 'amarela':'amarela', 'amarelos':'amarela',
                  'amarelas':'amarela'}
    gender_terms = {'feminino':'feminino','feminina':'feminino','meninas':'feminino',
                    'masculino':'masculino','masculina':'masculino','meninos':'masculino',
                    'nao binario':'nao_binario','nao binarios':'nao_binario'}
    selected_races = [key for word,key in race_terms.items() if re.search(r'\b'+re.escape(word)+r'\b',q)]
    selected_genders = [key for word,key in gender_terms.items() if re.search(r'\b'+re.escape(word)+r'\b',q)]
    if selected_races or re.search(r'\b(raciais|racial|raca|etnia|etnico|quilombola|indigena)\b',q):
        group = 'detalhes_adolescentes'
    elif selected_genders or re.search(r'\b(genero|generos|membros|integrantes|nucas criados)\b',q):
        group = 'detalhes_nucas'
    if not group:
        return None
    filters = detect_filters(question, prev)
    rows = [r for r in datasets.get(group, []) if _uf_match(r.get('uf'), filters.get('uf'))
            and (not filters.get('municipio') or filters['municipio'] in norm(r.get('municipio')))]
    if not rows:
        return ('No matching municipalities were found in the public dataset.' if language=='en'
                else 'Não encontrei municípios correspondentes na base pública.')
    # Se a pergunta exige determinado ano, não atribuir o período a dados sem referência temporal.
    if filters.get('year'):
        return ('These aggregated records have no year field to support a year-specific calculation.' if language=='en'
                else 'Esses indicadores não estão separados por ano, então não consigo informar esse recorte com segurança.')
    keys = list(dict.fromkeys(selected_races if group=='detalhes_adolescentes' else selected_genders))
    if not keys:
        keys = (['feminino','masculino','nao_binario','total_membros'] if group=='detalhes_nucas'
                else ['branca','preta','parda','amarela','indigenas','quilombolas','ribeirinhos'])
    # Não soma campos faltantes como zero: reporta incompletude.
    labels = {'feminino':'gênero feminino','masculino':'gênero masculino','nao_binario':'não binários',
              'total_membros':'membros','branca':'branca','preta':'preta','parda':'parda',
              'amarela':'amarela','indigenas':'indígenas','quilombolas':'quilombolas','ribeirinhos':'ribeirinhos'}
    totals=[]
    for key in keys:
        values=[num(r.get(key)) for r in rows]
        valid=[v for v in values if v is not None]
        if len(valid)!=len(rows):
            return (f'Não consigo somar **{labels[key]}**: {len(valid)} de {len(rows)} registros têm valor numérico.'
                    if language=='pt' else f'Cannot sum {key}: {len(valid)} of {len(rows)} records have a numeric value.')
        totals.append((key, sum(valid)))
    lines = [(f'- **{labels[k].capitalize()}**: {v:,.0f}'.replace(',', '.') if language=='pt'
              else f'- **{k}**: {v:,.0f}') for k,v in totals]
    intro = (f'Nos NUCAs de **{len(rows)} municípios**, os números registrados são:' if language=='pt'
             else f'NUCA figures from **{len(rows)} municipalities**:')
    note = ('\n\nAs categorias de raça/cor e pertencimentos territoriais não devem ser somadas entre si: podem se sobrepor.'
            if group=='detalhes_adolescentes' and not selected_races else '')
    return intro + '\n' + '\n'.join(lines) + note


class SnapshotStore:
    def __init__(self, path: str = '', url: str = '', ttl: int = 7200):
        self.path, self.url, self.ttl = path, url, max(60, ttl)
        self._lock = threading.RLock()
        self.records: list[dict] | None = None
        self.expires = 0.0
        self.version = ''
        self.etag = ''
        self.modified = ''
        self.error = ''

    def configured(self) -> bool:
        return bool(self.path or self.url)

    def get(self) -> list[dict] | None:
        if not self.configured():
            return None
        now = time.time()
        if self.records is not None and now < self.expires:
            return self.records
        with self._lock:
            if self.records is not None and time.time() < self.expires:
                return self.records
            try:
                if self.path:
                    data = Path(self.path).read_bytes()
                    if len(data) > 32 * 1024 * 1024:
                        raise ValueError('Snapshot maior que 32 MB')
                    payload = json.loads(data)
                else:
                    if not self.url.startswith('https://'):
                        raise ValueError('SNAPSHOT_URL deve ser https://')
                    headers = {}
                    if self.etag:
                        headers['If-None-Match'] = self.etag
                    if self.modified:
                        headers['If-Modified-Since'] = self.modified
                    with requests.get(self.url, timeout=12, headers=headers, stream=True, allow_redirects=False) as response:
                        if response.status_code == 304 and self.records is not None:
                            self.expires = time.time() + self.ttl
                            return self.records
                        response.raise_for_status()
                        chunks, length = [], 0
                        for chunk in response.iter_content(65536):
                            length += len(chunk)
                            if length > 32 * 1024 * 1024:
                                raise ValueError('Snapshot remoto maior que 32 MB')
                            chunks.append(chunk)
                        payload = json.loads(b''.join(chunks))
                        self.etag = response.headers.get('ETag', '')
                        self.modified = response.headers.get('Last-Modified', '')
                self.records = normalize_records(payload)
                self.version = self.etag or str(len(self.records)) + ':' + str(int(time.time() // self.ttl))
                self.expires = time.time() + self.ttl
                self.error = ''
                log.info('Snapshot carregado: %s ações', len(self.records))
            except Exception as exc:
                self.error = str(exc)
                self.expires = time.time() + 60  # Evita consulta remota a cada mensagem em caso de falha.
                log.warning('Falha ao atualizar snapshot (mantendo último snapshot válido): %s', exc)
            return self.records


def detect_filters(query: str, prev: str = '') -> dict:
    """Filtros só são usados se o campo existir; contexto anterior herda apenas o que falta."""
    def extract(q: str) -> dict:
        plain = norm(q)
        year = re.search(r'\b(20\d{2})\b', plain)
        uf = None
        for name, abbr in sorted(STATES.items(), key=lambda kv: -len(kv[0])):
            if name == 'para' and not re.search(r'\bpará\b', q, re.I):
                continue  # Não confundir a preposição 'para' com o estado Pará.
            if re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', plain):
                uf = abbr
                break
        if not uf:
            for m in re.finditer(r'\b[A-Z]{2}\b', q):
                if m.group() in STATES.values():
                    uf = m.group()
                    break
        municipio = None
        m = re.search(r'(?:municipio de|cidade de|em)\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\-\']{2,50}?)(?=\s+(?:em|no|na|durante|sobre|para|de 20\d{2})\b|[?.,!]|$)', q, re.I)
        if m:
            candidate = norm(m.group(1)).strip()
            if (len(candidate.split()) <= 4 and candidate not in STATES
                and candidate not in {'acoes','nucas','todos os municipios','acoes de saude','meio ambiente','todo o brasil'}
                and (m.group(0).lower().startswith(('municipio de', 'cidade de')) or m.group(1)[0].isupper())):
                municipio = candidate
        return {'year': year.group(1) if year else None, 'uf': uf, 'municipio': municipio}
    new, old = extract(query), extract(prev)
    if re.search(r'\b(?:no|do|em|todo o) brasil\b', norm(query)):
        old['uf'] = old['municipio'] = None
    if new['uf'] and new['uf'] != old['uf'] and not new['municipio']:
        old['municipio'] = None
    return {k: new[k] or old[k] for k in new}


def applies(rec: dict, filters: dict) -> bool:
    y = filters.get('year')
    if y and y != str(string_field(rec, 'ano'))[:4]:
        return False
    uf = filters.get('uf')
    if uf:
        ru = norm(string_field(rec, 'uf'))
        if not ru or (ru != norm(uf) and STATES.get(ru) != uf and not ru.endswith('(' + norm(uf) + ')')):
            return False
    mun = filters.get('municipio')
    if mun:
        rm = norm(string_field(rec, 'municipio'))
        if not rm or mun not in rm:
            return False
    return True


def search_snapshot(records: list[dict], query: str, prev: str = '', limit: int = 6) -> list[dict]:
    filters = detect_filters(query, prev)
    # Perguntas curtas de continuação herdam palavras relevantes da última pergunta.
    continuation = (len(words(query)) < 4 or norm(query).startswith(('e ', 'nesses', 'dessas', 'desses', 'quais delas')))
    tokens = words(query + (' ' + prev if continuation else ''))
    special = set(words('nuca nucas adolescente adolescentes ação ações projeto projetos município municípios quantos realizadas houve'))
    tokens = [t for t in tokens if t not in special and not re.fullmatch(r'20\d{2}', t)]
    tokens = [t for t in tokens if t not in {norm(v) for v in STATES.values()}]
    tokens = [t for t in tokens if t not in set(norm(x) for x in STATES)]
    if not tokens and not any(filters.values()):
        # Recuperar ações ilustrativas gerais: não calcular totais a partir delas.
        return [{'record': r, 'score': 0.1, 'position': i} for i, r in enumerate(records[:limit], 1)]
    topic = identify_action_topic(records, query, prev)
    filtered = [(i, r) for i, r in enumerate(records, 1) if applies(r, filters) and (not topic or topic_matches(r, topic))]
    if not filtered:
        return []
    document_tokens = []
    frequencies = Counter()
    for _, r in filtered:
        title = words(string_field(r, 'titulo'))
        fields = words(' '.join(string_field(r,k) for k in ('descricao','tema','municipio','uf','ano')))
        counter = Counter(title * 3 + fields)
        document_tokens.append(counter)
        frequencies.update(counter.keys())
    n = len(filtered)
    avg = sum(sum(c.values()) for c in document_tokens) / max(n, 1)
    ranked = []
    for (i, r), c in zip(filtered, document_tokens):
        total = sum(c.values())
        s = 0.0
        for t in set(tokens):
            f = c[t]
            if f:
                idf = math.log(1 + (n - frequencies[t] + 0.5) / (frequencies[t] + 0.5))
                s += idf * (f * 2.2) / (f + 1.2 * (0.25 + 0.75 * total / max(avg,1)))
        if s or (not tokens and filters):
            ranked.append({'record':r, 'score':round(s,4), 'position':i})
    return sorted(ranked, key=lambda x: x['score'], reverse=True)[:limit]


def metric_intent(query: str) -> str | None:
    q = norm(query)
    # Ranking de municípios e estados: não confundir com ranking de temas.
    if (re.search(r'\b(municipios?|cidades?|estados?|ufs?|localidades?)\b', q)
            and re.search(r'\b(mais acoes|mais atividades|ranking|lideram?|maior numero de acoes|maior quantidade de acoes|mais realizou|mais fizeram|mais fez|mais registrou|mais registraram)\b', q)):
        return 'ranking'
    if not re.search(r'\b(quantos?|quantas?|total|numero|somatorio|soma)\b',q):
        return None
    # A unidade explicitamente pedida prevalece sobre palavras presentes no nome
    # da categoria. "Ações de Saúde ... de adolescentes" pede AÇÕES, não pessoas.
    explicit = (
        ('adolescentes', r'\b(?:quantos?|quantas?|total\s+de|numero\s+de)\s+(?:adolescentes|jovens)\b'),
        ('pessoas', r'\b(?:quantos?|quantas?|total\s+de|numero\s+de)\s+(?:pessoas|participantes|mobilizados|mobilizadas)\b'),
        ('municipios', r'\b(?:quantos?|quantas?|total\s+de|numero\s+de)\s+(?:municipios|cidades)\b'),
        ('acoes', r'\b(?:quantos?|quantas?|total\s+de|numero\s+de)\s+(?:acoes|atividades|iniciativas|projetos|registros)\b'),
    )
    for intent, pattern in explicit:
        if re.search(pattern,q):return intent
    if re.search(r'\b(pessoas|participantes|mobilizados|mobilizadas)\b',q):return 'pessoas'
    if re.search(r'\b(adolescentes|jovens)\b',q) and not re.search(r'\b(acoes|atividades)\b',q):return 'adolescentes'
    if re.search(r'\b(municipios|cidades)\b',q) and not re.search(r'\b(acoes|atividades)\b',q):return 'municipios'
    if re.search(r'\b(acoes|atividades|iniciativas|projetos|registros)\b',q):return 'acoes'
    return None


def _contains(text: str, pattern: str) -> bool:
    return bool(re.search(r'(?<![a-z0-9])' + re.escape(pattern) + r'(?![a-z0-9])', text))


# Temas reais da planilha têm precedência; os termos abaixo são usados apenas
# quando a pessoa pergunta por um assunto livre, e nunca para retornar o total geral.
ACTIVITY_TERMS = {
    'plantio': ('plantio', 'plantios', 'plantando', 'plantaram', 'plantar',
                'plantacao', 'plantacoes', 'mudas', 'reflorestamento', 'reflorestar'),
    'meio ambiente': ('meio ambiente', 'ambiental', 'ambientais', 'sustentabilidade',
                      'reciclagem', 'ecologia', 'clima', 'climaticas', 'resiliencia climatica'),
    'saude': ('saude', 'vacinacao', 'dengue', 'saude mental'),
    'educacao': ('educacao', 'educacional', 'escola', 'ensino', 'aprendizagem'),
    'violencia': ('violencia', 'violencias', 'protecao', 'abuso'),
    'cultura': ('cultura', 'cultural', 'arte', 'musica', 'teatro'),
    'esporte': ('esporte', 'esportiva', 'futebol'),
    'gravidez': ('gravidez', 'gestacao', 'gravidez na adolescencia'),
}


def identify_action_topic(records: list[dict], query: str, prev: str = '') -> dict | None:
    """Retorna um recorte temático SEM inventar correspondências.

    Categorias existentes são comparadas em 'tema' (nunca com números de gênero).
    Para assuntos livres, pesquisa termos na descrição e no tema.
    """
    q = norm(query)
    themes = [t for t in {string_field(r, 'tema') for r in records} if t and norm(t) not in ('tema', 'nao informado')]
    # Prioriza categorias expressas na pergunta, da mais específica à mais genérica.
    for theme in sorted(themes, key=lambda t: len(norm(t)), reverse=True):
        nt = norm(theme)
        if len(nt) > 3 and _contains(q, nt):
            return {'kind': 'category', 'value': nt, 'label': theme}
    # 'categoria/tema de X' significa literalmente a coluna tema da planilha.
    explicit = re.search(r'\b(?:categoria|tematica|tema)\s+(?:de|da|do|sobre)\s+(.+)', q)
    if explicit:
        name = re.split(r'\s+(?:em|no|na|durante|entre|ano|para|com|que|foram|realizadas)\b', explicit.group(1))[0].strip(' ?.!,')
        for theme in sorted(themes, key=lambda t: len(norm(t)), reverse=True):
            if name and (norm(theme) == name or (len(name) >= 5 and name in norm(theme))):
                return {'kind': 'category', 'value': norm(theme), 'label': theme}
        if name:
            return {'kind': 'category', 'value': name, 'label': explicit.group(1).strip(' ?.!,')}
    # Conhecimento lexical delimitado por assunto, para reconhecer 'plantio', etc.
    for name, variants in ACTIVITY_TERMS.items():
        if _contains(q, name) or (name == 'meio ambiente' and _contains(q, 'acoes ambientais')):
            return {'kind': 'terms', 'value': variants, 'label': name}
    # Assunto desconhecido depois de 'ações de/sobre...' ou 'ações no estado
    # sobre...' NÃO pode cair no total geral. O termo livre será buscado nas
    # descrições, inclusive quando não houver categoria homônima na tabela.
    match = re.search(r'\b(?:acoes|atividades|iniciativas|projetos)(?:\s+(?:no|na)\s+(?:estado|municipio|cidade))?\s+'
                      r'(?:de|sobre|relacionadas?\s+a|ligadas?\s+a)\s+(.+)', q)
    if match:
        phrase = re.split(r'\s+(?:foram|feitas|realizadas|registradas|aconteceram|tem|ha|em|no|na|nos|nas|pelo|pela|durante|entre|para|que)\b', match.group(1))[0].strip(' ?.,!')
        # Não tratar nomes de estados ou 'ações de 2026' como temas.
        if phrase and phrase not in STATES and phrase not in {norm(x) for x in STATES.values()} and not re.fullmatch(r'20\d{2}', phrase):
            terms = [word for word in words(phrase) if word not in ('acao','acoes','atividade','atividades')]
            if terms:
                return {'kind': 'free', 'value': terms, 'label': phrase}
    # 'Quantas ações de plantio' usa o dicionário mesmo se a palavra aparecer em outro formato.
    return None


def topic_matches(rec: dict, topic: dict) -> bool:
    if topic['kind'] == 'category':
        theme = norm(string_field(rec, 'tema'))
        return theme == topic['value'] or (len(topic['value']) >= 5 and topic['value'] in theme)
    text = norm(' '.join(string_field(rec, k) for k in ('tema', 'titulo', 'descricao')))
    if topic['kind'] == 'terms':
        return any(_contains(text, norm(term)) for term in topic['value'])
    # Todos os termos importantes devem aparecer para evitar contar qualquer ação.
    return all(_contains(text, term) or (len(term) >= 5 and bool(re.search(r'\b'+re.escape(term[:5])+r'\w*', text)))
               for term in topic['value'])


def _topic_with_context(records: list[dict], query: str, prev: str = '') -> dict | None:
    topic = identify_action_topic(records, query)
    return topic if topic is not None else (identify_action_topic(records, prev) if prev else None)


def filtered_actions(records: list[dict], query: str, prev: str = '') -> tuple[list[dict], dict, dict | None]:
    filters = detect_filters(query, prev)
    topic = _topic_with_context(records, query, prev)
    matches = [r for r in records if applies(r, filters) and (topic is None or topic_matches(r, topic))]
    return matches, filters, topic


def _format_count(value: int | float) -> str:
    return f'{value:,.0f}'.replace(',', '.')


def _scope_label(filters: dict, topic: dict | None) -> str:
    parts = []
    if topic:
        parts.append('sobre ' + topic['label'] if topic['kind'] != 'category' else 'na categoria ' + topic['label'])
    if filters.get('municipio'):
        parts.append('no município de ' + filters['municipio'].title() + (', em ' + next((name.title() for name, uf in STATES.items() if uf == filters['uf']), filters['uf']) if filters.get('uf') else ''))
    elif filters.get('uf'):
        long_state = next((name.title() for name, uf in STATES.items() if uf == filters['uf']), filters['uf'])
        parts.append('em ' + long_state)
    if filters.get('year'):
        parts.append('em ' + filters['year'])
    return ' '.join(parts)


def compute_theme_breakdown(records: list[dict], question: str, prev: str = '',
                            language: str = 'pt') -> str | None:
    """Calcula os temas em TODO o recorte, nunca a partir de 6 exemplos."""
    q = norm(question)
    if not re.search(r'\b(?:principais temas|temas (?:com mais acoes|que mais tiveram acoes|mais (?:frequentes|comuns|registrados))|quais (?:sao )?(?:os )?temas|distribuicao por tema|temas das acoes|temas das atividades)\b', q):
        return None
    rows, filters, topic = filtered_actions(records, question, prev)
    scope = _scope_label(filters, topic)
    if not rows:
        return ('Não encontrei ações para identificar os temas nesse recorte.'
                if language == 'pt' else 'I could not find actions to summarize their themes.')
    themes = Counter(string_field(r, 'tema') or 'Tema não informado' for r in rows)
    first = f'Encontrei **{_format_count(len(rows))} ações**' + (f' {scope}' if scope else ' nos NUCAs') + '.'
    if language == 'en':
        return f'{len(rows)} activities were recorded. Main themes:\n' + '\n'.join(
            f'- {theme}: {total}' for theme, total in themes.most_common(5))
    out = [first, '', '**Os temas mais frequentes são:**']
    out.extend(f'- **{theme}**: {_format_count(total)} ' + ('ação' if total == 1 else 'ações')
               for theme, total in themes.most_common(5))
    if len(themes) > 5:
        out.append(f'\nOutros {len(themes)-5} temas aparecem com menor frequência.')
    return '\n'.join(out)


def text_quantity_intent(question: str) -> str | None:
    """Reconhece uma quantidade de mudas/árvores, não uma contagem de ações.

    "Quantas ações de plantio de mudas?" continua sendo pergunta por ações.
    "Quantas mudas foram plantadas nessas ações?" pede unidades físicas.
    """
    q = norm(question)
    if q in ('mudas', 'de mudas', 'quantidade de mudas'):
        return 'mudas'
    if q in ('arvores', 'de arvores', 'quantidade de arvores'):
        return 'arvores'
    if re.search(r'\bquantas?\s+(?:(?:dessas|desses|de|das|dos|as|os)\s+)?mudas?\b', q) or re.search(r'\b(?:quantidade|total|numero)\s+de\s+mudas?\b', q):
        return 'mudas'
    if re.search(r'\bquantas?\s+(?:(?:dessas|desses|de|das|dos|as|os)\s+)?arvores?\b', q) or re.search(r'\b(?:quantidade|total|numero)\s+de\s+arvores?\b', q):
        return 'arvores'
    return None


def compute_text_quantities(records: list[dict], question: str, prev: str = '',
                            language: str = 'pt', unit: str | None = None,
                            detailed: bool = False) -> str | None:
    """Soma unidades explícitas presentes nas descrições, com cobertura indicada.

    Números de adolescentes, participantes, mudas doadas ou metas de plantio
    não são contabilizados como mudas/árvores efetivamente plantadas.
    """
    unit = unit or text_quantity_intent(question)
    if unit not in ('mudas', 'arvores'):
        return None
    rows, filters, topic = filtered_actions(records, question, prev)
    scope = _scope_label(filters, topic)
    label = 'mudas' if unit == 'mudas' else 'árvores'
    if not rows:
        if language == 'en':
            return 'I could not find matching activities in this area or topic.'
        return f'Não encontrei ações correspondentes' + (f' {scope}' if scope else '') + '. Assim, não há como informar a quantidade plantada.'
    found = []
    for row in rows:
        mentions = extract_planting_quantities(string_field(row, 'descricao'), unit)
        if mentions:
            found.append((row, mentions))
    count_with_values = len(found)
    if not found:
        if language == 'en':
            return (f'I found {len(rows)} relevant activities, but their descriptions do not give an '
                    f'explicit count of planted {label}. I cannot calculate a reliable total.')
        return (f'Examinei **{_format_count(len(rows))} relatos de ações**' + (f' {scope}' if scope else '') +
                f', mas não encontrei uma quantidade explícita de **{label} efetivamente plantadas**. '
                'Por isso, não consigo calcular esse número com segurança.')
    total = sum(sum(m.count for m in mentions) for _, mentions in found)
    if language == 'en':
        return (f'Across **{count_with_values} activities**, the descriptions explicitly report '
                f'**{total:,} planted {label}**. This is a partial sum of written reports, not a verified overall total.')
    recorded = f'**{_format_count(total)} {label} plantadas**' if total != 1 else '**1 muda plantada**' if unit == 'mudas' else '**1 árvore plantada**'
    lines = [
        f'Nas descrições de **{_format_count(count_with_values)} ações**' +
        (f' {scope}' if scope else '') +
        f', encontrei a informação de {recorded}.',
        '',
        'Esse é um **total parcial**, pois só considera as quantidades expressamente informadas nos relatos. '
        + (f'Outras **{_format_count(len(rows) - count_with_values)} ações** não indicam claramente o número plantado. '
           if len(rows) > count_with_values else '')
        + 'Uma mesma atividade também pode ter sido relatada mais de uma vez.'
    ]
    # Exibir exemplos reais sem links de infraestrutura e sem alegar cobertura total.
    lines.extend(['', '**Exemplos de atividades:**'])
    for row, mentions in found[:4 if detailed else 2]:
        city = string_field(row, 'municipio') or 'Município não informado'
        uf = string_field(row, 'uf')
        n = sum(m.count for m in mentions)
        abbr = re.search(r'\(([A-Z]{2})\)', uf)
        short_uf = abbr.group(1) if abbr else STATES.get(norm(uf), uf)
        place = city + (f' ({short_uf})' if uf and norm(uf) not in norm(city) else '')
        lines.append(f'- **{place}**: relato de plantio de {_format_count(n)} {label}.')
    return '\n'.join(lines)



def compute_generic_text_quantity(records: list[dict], question: str, prev: str = '',
                                  language: str = 'pt', detailed: bool = False) -> str | None:
    """Soma quantidades explícitas de outras unidades citadas nas descrições.

    É propositalmente conservador: só funciona quando a pergunta informa uma
    unidade e uma ação ('livros distribuídos', 'quilos arrecadados' etc.).
    """
    spec = infer_quantity_spec(question)
    if spec is None:
        return None
    rows, filters, topic = filtered_actions(records, question, prev)
    scope = _scope_label(filters, topic)
    found = []
    for row in rows:
        mentions = extract_generic_quantities(string_field(row, 'descricao'), spec)
        if mentions:
            found.append((row, mentions))
    if not found:
        if language == 'en':
            return None
        return (f'Examinei **{_format_count(len(rows))} relatos de ações**' + (f' {scope}' if scope else '') +
                f', mas não encontrei uma quantidade explícita de **{spec.unit_label}** associada à ação pedida. '
                'Por isso, não consigo calcular esse total com segurança.')
    total = sum(sum(m.count for m in mentions) for _, mentions in found)
    total_text = _format_count(total)
    lines = [
        ('Na descrição de **1 ação**' if len(found) == 1 else f'Nas descrições de **{_format_count(len(found))} ações**') + (f' {scope}' if scope else '') +
        f', encontrei **{total_text} {spec.unit_label}** explicitamente informados no contexto pedido.',
        '',
        'Esse é um **total parcial**: só entram relatos em que a quantidade aparece de forma clara junto da unidade e da atividade mencionada.'
    ]
    if len(rows) > len(found):
        missing_count = len(rows) - len(found)
        missing_label = '**1 ação**' if missing_count == 1 else f'**{_format_count(missing_count)} ações**'
        lines[-1] += f' Em {missing_label} do mesmo recorte não encontrei um número explícito compatível.'
    lines.extend(['', '**Exemplo:**' if len(found) == 1 else '**Alguns exemplos:**'])
    for row, mentions in found[:4 if detailed else 2]:
        city = string_field(row, 'municipio') or 'Município não informado'
        uf = string_field(row, 'uf')
        abbr = re.search(r'\(([A-Z]{2})\)', uf)
        short_uf = abbr.group(1) if abbr else STATES.get(norm(uf), uf)
        place = city + (f' ({short_uf})' if short_uf else '')
        value = sum(m.count for m in mentions)
        lines.append(f'- **{place}**: {_format_count(value)} {spec.unit_label}.')
    return '\n'.join(lines)


def known_themes(records: list[dict]) -> list[str]:
    """Lista categorias reais para ajudar o interpretador sem enviar a base."""
    counts = Counter(string_field(r, 'tema') for r in records if string_field(r, 'tema'))
    return [name for name, _ in counts.most_common() if norm(name) not in ('tema', 'nao informado')]


def municipality_samples(records: list[dict], limit: int = 30) -> list[str]:
    """Amostra de grafias de municípios para o interpretador; não é usada em cálculos."""
    counts = Counter()
    for r in records:
        city, uf = string_field(r, 'municipio'), string_field(r, 'uf')
        if city:
            counts[f'{city} — {uf}'] += 1
    return [name for name, _ in counts.most_common(limit)]


def corpus_overview(records: list[dict], question: str, prev: str = '') -> str:
    """Resumo calculado sobre TODO o recorte para acompanhar evidências textuais.

    Assim o LLM pode escrever uma resposta descritiva sem confundir dez exemplos
    recuperados com a cobertura total da consulta.
    """
    rows, filters, topic = filtered_actions(records, question, prev)
    if not rows:
        return 'RESUMO CALCULADO DO RECORTE: nenhuma ação correspondente.'
    places = Counter()
    states = Counter()
    themes = Counter()
    years = Counter()
    for r in rows:
        city = string_field(r, 'municipio')
        uf = string_field(r, 'uf')
        theme = string_field(r, 'tema')
        year = str(string_field(r, 'ano'))[:4]
        if city:
            places[(city, uf)] += 1
        if uf:
            states[uf] += 1
        if theme:
            themes[theme] += 1
        if re.fullmatch(r'20\d{2}', year):
            years[year] += 1
    lines = [
        'RESUMO CALCULADO DO RECORTE (use para totais; não estime a partir dos exemplos):',
        f'- Total de ações: {len(rows)}',
        f'- Municípios distintos: {len(places)}',
        f'- Estados/UF distintos: {len(states)}',
    ]
    if themes:
        lines.append('- Temas mais frequentes: ' + '; '.join(f'{k} ({v})' for k, v in themes.most_common(6)))
    if years:
        ordered = sorted(years)
        lines.append(f'- Período com registros neste recorte: {ordered[0]} a {ordered[-1]}')
    if places:
        lines.append('- Municípios com mais ações neste recorte: ' + '; '.join(
            f'{city} / {uf} ({count})' for (city, uf), count in places.most_common(6)))
    return '\n'.join(lines)


def _all_ufs_in_text(text: str) -> list[str]:
    """Extrai todas as UFs citadas, preservando a ordem e evitando 'para' preposição."""
    found = []
    raw, q = str(text or ''), norm(text)
    positions = []
    for name, abbr in STATES.items():
        if name == 'para' and not re.search(r'\bpará\b', raw, re.I):
            continue
        m = re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', q)
        if m:
            positions.append((m.start(), abbr))
    for m in re.finditer(r'\b[A-Z]{2}\b', raw):
        if m.group() in STATES.values():
            positions.append((m.start(), m.group()))
    for _, uf in sorted(positions):
        if uf not in found:
            found.append(uf)
    return found


def compute_advanced_stats(records: list[dict], question: str, prev: str = '',
                           language: str = 'pt') -> str | None:
    """Consultas analíticas que exigem varrer e agrupar a base inteira."""
    q = norm(question)

    # Comparação direta entre dois ou mais estados: "AL ou PE, quem tem mais ações?".
    ufs = _all_ufs_in_text(question)
    if len(ufs) >= 2 and re.search(r'\b(?:compar|mais acoes|menos acoes|quantas acoes|total de acoes|acoes em)\w*', q):
        year = detect_filters(question, prev).get('year')
        topic = _topic_with_context(records, question, prev)
        counts = []
        for uf in ufs:
            subset = [r for r in records if applies(r, {'year':year, 'uf':uf, 'municipio':None})
                      and (topic is None or topic_matches(r, topic))]
            counts.append((uf, len(subset)))
        counts.sort(key=lambda x: (-x[1], x[0]))
        if language == 'en':
            return '\n'.join(f'- **{uf}**: {n} actions' for uf, n in counts)
        scope = (' na categoria ' + topic['label']) if topic and topic['kind']=='category' else (' sobre ' + topic['label'] if topic else '')
        lines = [f'**Comparação de ações{scope}:**']
        lines += [f'- **{uf}**: {_format_count(n)} ações' for uf, n in counts]
        if counts[0][1] != counts[-1][1]:
            diff = counts[0][1] - counts[-1][1]
            lines.append(f'\n**{counts[0][0]}** tem {_format_count(diff)} ações a mais que **{counts[-1][0]}** nesse recorte.')
        return '\n'.join(lines)

    # Quantos municípios alcançaram um patamar de ações.
    threshold = re.search(r'\b(?:pelo menos|no minimo|ao menos|mais de|acima de)\s+(\d{1,4})\s+(?:acoes|atividades)\b', q)
    if threshold and re.search(r'\b(?:municipios?|cidades?)\b', q):
        n = int(threshold.group(1))
        filters = detect_filters(question, prev)
        # Município é a dimensão do agrupamento, portanto não filtrar um município isolado.
        filters['municipio'] = None
        topic = _topic_with_context(records, question, prev)
        counts = Counter()
        labels = {}
        for r in records:
            if not applies(r, filters) or (topic and not topic_matches(r, topic)):
                continue
            city, uf = string_field(r, 'municipio'), string_field(r, 'uf')
            if not city:
                continue
            key = (norm(city), norm(uf))
            counts[key] += 1
            labels[key] = (city, uf)
        if 'mais de' in q or 'acima de' in q:
            selected = [(k,v) for k,v in counts.items() if v > n]
            op = f'mais de {n}'
        else:
            selected = [(k,v) for k,v in counts.items() if v >= n]
            op = f'pelo menos {n}'
        selected.sort(key=lambda kv: (-kv[1], labels[kv[0]][0].casefold()))
        if language == 'en':
            return f'**{len(selected)} municipalities** have {op} recorded actions.'
        scope = _scope_label(filters, topic)
        lines = [f'Encontrei **{_format_count(len(selected))} municípios** com **{op} ações registradas**' + (f' {scope}' if scope else '') + '.']
        if selected:
            lines.extend(['', '**Entre os que têm mais ações:**'])
            for key,value in selected[:5]:
                city, uf = labels[key]
                lines.append(f'- **{city}** ({uf}): {_format_count(value)} ações')
        return '\n'.join(lines)

    # Ranking por pessoas mobilizadas ou participações de adolescentes.
    metric = None
    if re.search(r'\b(?:mais|maior numero|maior quantidade|ranking|lidera|liderou)\b', q):
        if re.search(r'\b(?:adolescentes?|jovens?)\b', q):
            metric = 'adolescentes'
        elif re.search(r'\b(?:pessoas?|publico|participantes?|mobilizad)\w*\b', q):
            metric = 'pessoas'
    if metric and re.search(r'\b(?:municipios?|cidades?|estados?|ufs?)\b', q):
        by_state = bool(re.search(r'\b(?:estados?|ufs?)\b', q))
        filters = detect_filters(question, prev)
        # Agrupamento nacional/por UF não deve ficar preso a um município anterior.
        if by_state:
            filters['uf'] = None
        filters['municipio'] = None
        topic = _topic_with_context(records, question, prev)
        totals = Counter(); missing = Counter(); labels = {}
        for r in records:
            if not applies(r, filters) or (topic and not topic_matches(r, topic)):
                continue
            city, uf_raw = string_field(r, 'municipio'), string_field(r, 'uf')
            abbr = re.search(r'\(([A-Z]{2})\)', uf_raw)
            uf = abbr.group(1) if abbr else STATES.get(norm(uf_raw), uf_raw)
            if by_state:
                if not uf: continue
                key = (norm(uf),); labels[key] = uf_raw or uf
            else:
                if not city or not uf: continue
                key = (norm(city), norm(uf)); labels[key] = f'{city} ({uf})'
            value = num(field(r, metric))
            if value is None:
                missing[key] += 1
            else:
                totals[key] += value
        if not totals:
            return 'Não encontrei valores numéricos suficientes para montar esse ranking.' if language=='pt' else None
        ranked = sorted(totals.items(), key=lambda kv: (-kv[1], labels[kv[0]].casefold()))
        noun = 'Estados' if by_state else 'Municípios'
        metric_label = 'participações de adolescentes' if metric=='adolescentes' else 'pessoas mobilizadas'
        if re.search(r'\bqual\s+(?:municipio|cidade|estado)\b', q) and not re.search(r'\b(?:ranking|quais|top)\b', q):
            key, value = ranked[0]
            subject = 'O estado' if by_state else 'O município'
            result = f'{subject} com maior soma de {metric_label} é **{labels[key]}**, com **{_format_count(value)}**.'
            if missing[key]:
                result += f' Há {missing[key]} ações desse local sem esse número preenchido, então o valor pode estar subestimado.'
            else:
                result += ' Esse total soma as participações informadas nas ações e não representa necessariamente pessoas únicas.'
            return result
        lines = [f'**{noun} com maior soma de {metric_label}:**']
        for key,value in ranked[:7]:
            note = f' · {missing[key]} ações sem número informado' if missing[key] else ''
            lines.append(f'- **{labels[key]}**: {_format_count(value)}{note}')
        if any(missing.values()):
            lines.append('\nO ranking soma apenas os valores preenchidos nas ações; locais com registros sem esse número podem estar subestimados.')
        else:
            lines.append('\nOs valores representam participações registradas nas ações, não necessariamente pessoas únicas.')
        return '\n'.join(lines)

    # Média de ações por município em um recorte.
    if re.search(r'\b(?:media|promedio)\b.*\b(?:acoes|atividades)\b.*\b(?:municipio|cidade)', q):
        rows, filters, topic = filtered_actions(records, question, prev)
        places = {(norm(string_field(r,'municipio')), norm(string_field(r,'uf'))) for r in rows if string_field(r,'municipio')}
        if not places:
            return 'Não encontrei municípios nesse recorte.' if language=='pt' else None
        avg = len(rows) / len(places)
        if language == 'en':
            return f'The average is **{avg:.1f} actions per municipality**.'
        return (f'A média é de **{avg:.1f} ações por município**' + (f' {_scope_label(filters, topic)}' if _scope_label(filters, topic) else '') +
                f'. O cálculo considera {_format_count(len(rows))} ações em {_format_count(len(places))} municípios.')
    return None

def compute_stats(records: list[dict], query: str, prev: str = '', language: str = 'pt', style: int = 0) -> str | None:
    intent = metric_intent(query)
    if not intent and prev and is_followup_question(query):
        intent = metric_intent(prev)
    if not intent:
        return None
    matches, filters, topic = filtered_actions(records, query, prev)
    scope = _scope_label(filters, topic)
    count = len(matches)
    if intent == 'acoes':
        if language == 'en':
            return f'The NUCAs recorded **{count:,} actions**' + (f' {scope}' if scope else '') + '. These are activities reported in the available records.'
        scope_text = ' ' + scope if scope else ' nos NUCAs'
        amount = f'**{_format_count(count)} ações**' if count != 1 else '**1 ação**'
        starters = (
            f'Encontrei {amount}{scope_text}.',
            f'Os registros mostram {amount}{scope_text}.',
            f'No recorte que você pediu, foram registradas {amount}{scope_text}.',
            f'Ao todo, os NUCAs contabilizam {amount}{scope_text}.',
        )
        intro = starters[style % len(starters)]
        if not matches:
            return intro + ' Não encontrei atividades que atendam a esses critérios.'
        places = {(norm(string_field(r, 'municipio')), norm(string_field(r, 'uf')))
                  for r in matches if string_field(r, 'municipio')}
        states = {norm(string_field(r, 'uf')) for r in matches if string_field(r, 'uf')}
        themes = Counter(string_field(r, 'tema') for r in matches if string_field(r, 'tema') and norm(string_field(r, 'tema')) not in ('outro','nao informado','tema'))
        info = []
        if len(places) > 1:
            info.append(f'As atividades aparecem em **{_format_count(len(places))} municípios**.')
        elif not filters.get('municipio') and len(states) > 1:
            info.append(f'Os registros abrangem **{_format_count(len(states))} estados**.')
        if themes and not topic:
            top = themes.most_common(2)
            if len(top) == 1:
                info.append(f'O tema mais registrado é **{top[0][0]}**, com {_format_count(top[0][1])} ações.')
            elif top[0][1] > top[1][1]:
                info.append(f'O tema mais frequente é **{top[0][0]}** ({_format_count(top[0][1])} ações).')
            else:
                info.append(f'Entre os temas com mais ações estão **{top[0][0]}** e **{top[1][0]}**.')
        elif not info:
            years = sorted({str(string_field(r, 'ano'))[:4] for r in matches if re.fullmatch(r'20\d{2}', str(string_field(r, 'ano'))[:4])})
            if len(years) > 1 and not filters.get('year'):
                info.append(f'Os registros vão de {years[0]} a {years[-1]}.')
        if topic and topic['kind'] != 'category':
            info.append('Para esse assunto, considerei as atividades que fazem menção a ele nos temas ou nas descrições.')
        return intro + (' ' + ' '.join(info[:2]) if info else '')
    if intent == 'municipios':
        places = {(norm(string_field(r, 'municipio')), norm(string_field(r, 'uf')))
                  for r in matches if string_field(r,'municipio')}
        if language == 'en':
            return f'**{len(places)} municipalities** have matching registered actions.'
        return f'**{_format_count(len(places))} municípios** têm ações registradas' + (f' {scope}' if scope else '') + '.'
    if intent in ('pessoas', 'adolescentes'):
        column = 'adolescentes' if intent == 'adolescentes' else 'pessoas'
        vals = [num(field(r, column)) for r in matches]
        valid = [v for v in vals if v is not None]
        if count == 0:
            return 'Não encontrei ações com esses critérios.' if language=='pt' else 'No matching actions were found.'
        if len(valid) != count:
            label = 'adolescentes participantes' if column == 'adolescentes' else 'pessoas mobilizadas'
            return (f'Não é possível calcular um total completo: apenas **{len(valid)} de {count} ações** '
                    f'correspondentes têm o campo numérico de **{label}** preenchido. '
                    'Não vou extrapolar os registros ausentes.') if language=='pt' else 'A complete total cannot be calculated because some records lack the required numbers.'
        total = sum(valid)
        label = 'participações de adolescentes' if column == 'adolescentes' else 'pessoas mobilizadas'
        if language == 'en':
            return f'The {count} matching actions report **{_format_count(total)} {label}**. Participants may appear in more than one action.'
        return (f'As **{_format_count(count)} ações** correspondentes registram **{_format_count(total)} {label}**' +
                (f' {scope}' if scope else '') + '. É a soma informada nas atividades, e **não uma contagem de pessoas únicas**; '
                'alguém pode ter participado de mais de uma atividade.')
    if intent == 'ranking':
        q = norm(query)
        if not re.search(r'\b(municipios?|cidades?|estados?|ufs?|localidades?)\b',q):
            return None
        by_state = bool(re.search(r'\b(estados?|ufs?)\b', q))
        group = Counter()
        names = {}
        for row in matches:
            city = string_field(row, 'municipio').strip()
            uf_raw = string_field(row, 'uf').strip()
            abbr = re.search(r'\(([A-Z]{2})\)', uf_raw)
            uf = abbr.group(1) if abbr else STATES.get(norm(uf_raw), uf_raw.upper() if len(uf_raw) == 2 else uf_raw)
            if by_state:
                if not uf:
                    continue
                key = (norm(uf),)
                names.setdefault(key, uf_raw or uf)
            else:
                if not city or not uf:
                    continue
                # Homônimos em estados diferentes não são somados indevidamente.
                key = (norm(city), norm(uf))
                names.setdefault(key, f'{city} ({uf})')
            group[key] += 1
        if not group:
            return 'Não encontrei ações com esses critérios.' if language == 'pt' else 'No matching actions found.'
        top = sorted(group.items(), key=lambda item: (-item[1], names[item[0]].casefold()))
        leader_count = top[0][1]
        leaders = [names[k] for k, value in top if value == leader_count]
        scope_without_topic = _scope_label(filters, topic)
        where = f' {scope_without_topic}' if scope_without_topic else ' no Brasil'
        if len(leaders) > 1:
            label = 'estados' if by_state else 'municípios'
            shown = ', '.join(f'**{name}**' for name in leaders[:5])
            quantity_label = 'ação' if leader_count == 1 else 'ações'
            return (f'Há empate entre {label} com mais ações registradas{where}: {shown}. '
                    f'Cada um tem **{_format_count(leader_count)} {quantity_label}**.'
                    + (f' Outros {len(leaders)-5} também estão empatados.' if len(leaders)>5 else ''))
        if re.search(r'\b(quais|ranking|lista|top\s*\d+|10\s+mais|cinco\s+mais)\b', q):
            noun = 'Estados' if by_state else 'Municípios'
            return (f'**{noun} com mais ações registradas{where}:**\n'
                    + '\n'.join(f'- **{names[key]}**: {_format_count(value)} ações'
                                for key, value in top[:7]))
        noun = 'O estado' if by_state else 'O município'
        quantity_label = 'ação' if leader_count == 1 else 'ações'
        return (f'{noun} com mais ações registradas{where} é '
                f'**{leaders[0]}**, com **{_format_count(leader_count)} {quantity_label}**. '
                'O resultado considera todos os registros de ações que atendem ao recorte pedido.')
    return None


def is_followup_question(question: str) -> bool:
    """Detecta referência ao contexto, sem herdar filtros em perguntas independentes."""
    q = norm(question).strip(' ?!.')
    if re.match(r'^(?:e\s+|e quanto|nessas? |nesses? |dessas? |desses? |delas? |deles? |'
                r'pode detalhar|detalhe|me conte mais|fale mais|explique melhor|mais exemplos|'
                r'pode dar exemplos|me mostre|mostre exemplos|what about|tell me more|'
                r'could you elaborate|certo[, ]+agora|agora quero saber)', q):
        return True
    if re.search(r'\b(?:nesse|neste|naquele|nesse mesmo|no|deste) estado\b|'
                 r'\b(?:nessa|nesta|naquela|na mesma) cidade\b|'
                 r'\b(?:nesse|neste|naquele) municipio\b|'
                 r'\b(?:nessas|nesses|dessas|desses) acoes\b', q):
        return True
    # Respostas muito curtas a uma pergunta de confirmação ("ações", "de mudas").
    if q in ('acoes', 'de acoes', 'as acoes', 'todas as acoes', 'mudas', 'de mudas',
             'quantidade de mudas', 'pessoas', 'pessoas mobilizadas', 'de pessoas'):
        return True
    return q in ('pode detalhar', 'pode explicar melhor', 'quais foram', 'me mostre',
                 'pode citar exemplos', 'mais detalhes', 'mais exemplos', 'em alagoas')


def _explicit_action_total(question: str) -> bool:
    """'Quantas ações TOTAIS?' não herda plantio, gênero ou outros temas."""
    q = norm(question)
    return bool(re.search(r'\b(?:todas as acoes|total de acoes|acoes totais|todas as atividades|'
                          r'total de atividades|quantas acoes no total|quantas acoes totais)\b', q))


def clarification_request(question: str, history: list[dict], language: str = 'pt') -> str | None:
    """Pede confirmação somente quando falta a unidade de um total solicitado."""
    q = norm(question)
    if not re.search(r'\b(?:total|quantidade|numero)\b', q):
        return None
    if not re.search(r'\b(?:alagoas|pernambuco|bahia|estado|municipio|cidade|brasil)\b',q):
        return None
    # Uma pergunta explícita nunca precisa desta confirmação.
    if re.search(r'\b(?:acoes|atividades|mudas|arvores|adolescentes|participantes|pessoas|'
                 r'municipios|cidades|nucas|integrantes|meninas|meninos|temas|categorias)\b', q):
        return None
    # Exemplo: "agora quero saber o total de Alagoas" após falar de mudas.
    filters = detect_filters(question)
    location = next((name.title() for name, abbr in STATES.items() if abbr == filters.get('uf')), None)
    place = ('em ' + location) if location else ('no Brasil' if 'brasil' in q else 'nesse local')
    if language == 'en':
        return f'Do you mean the total number of activities {place}, or the number of seedlings planted?'
    return (f'Só para confirmar: você quer saber o **total de ações registradas {place}** '
            f'ou **quantas mudas foram plantadas nas ações {place}**?')


def conversation_context_question(question: str, history: list[dict], records: list[dict]) -> str:
    """Reconstrói o recorte da conversa a partir das mensagens do usuário.

    Passa apenas os filtros compatíveis à pergunta atual; trocar de UF limpa o
    município anterior, 'ações totais' limpa o tema, e perguntas independentes
    nunca herdam automaticamente a categoria de uma consulta anterior.
    """
    if not is_followup_question(question):
        return ''
    user_turns = [str(m.get('content', '')) for m in history
                  if m.get('role') == 'user' and isinstance(m.get('content'), str)
                  and m.get('content').strip()]
    if not user_turns:
        return ''
    # Reconstrói somente a sequência desde a última pergunta independente.
    start = next((idx for idx in range(len(user_turns)-1, -1, -1)
                  if not is_followup_question(user_turns[idx])), 0)
    history_turns = user_turns[start:]
    state = {'uf':None, 'municipio':None, 'year':None, 'metric':None,
             'unit':None, 'topic':None}
    for turn in history_turns + [question]:
        q = norm(turn)
        previous_uf = state['uf']
        fresh = detect_filters(turn)
        if re.search(r'\b(?:no|do|em|todo o|pelo) brasil\b|\bdo pais\b|\bnacional\b',q):
            state['uf'] = None
            state['municipio'] = None
        if fresh.get('uf'):
            if previous_uf != fresh['uf']:
                state['municipio'] = None
            state['uf'] = fresh['uf']
        if fresh.get('municipio'):
            state['municipio'] = fresh['municipio']
        if fresh.get('year'):
            state['year'] = fresh['year']
        new_topic = identify_action_topic(records, turn)
        if new_topic:
            state['topic'] = new_topic
        elif _explicit_action_total(turn):
            state['topic'] = None
        unit = text_quantity_intent(turn)
        metric = metric_intent(turn)
        if unit:
            state['unit'] = unit
            state['metric'] = None
            if not re.search(r'\b(?:nessas|nesses|dessas|desses) acoes\b',q):
                # Consulta por mudas independente da atividade anterior,
                # salvo quando a pessoa disser 'nessas ações'.
                if not (new_topic or state['topic'] and is_followup_question(turn)):
                    state['topic'] = None
        elif metric:
            state['metric'] = metric
            state['unit'] = None
        elif q in ('acoes', 'de acoes', 'as acoes', 'todas as acoes'):
            state['metric'] = 'acoes'; state['unit'] = None
            state['topic'] = None
        elif q in ('mudas', 'de mudas', 'quantidade de mudas'):
            state['unit'] = 'mudas'; state['metric'] = None
    topic = state['topic']
    core = {'acoes':'Quantas ações', 'adolescentes':'Quantos adolescentes',
            'pessoas':'Quantas pessoas mobilizadas', 'municipios':'Quantos municípios',
            'ranking':'Qual município tem mais ações'}.get(state['metric'], 'Quais ações')
    if state['unit']:
        core = 'Quantas ' + ('mudas' if state['unit']=='mudas' else 'árvores') + ' foram plantadas'
    if topic:
        core += (' na categoria de ' if topic['kind']=='category' else ' sobre ') + topic['label']
    if state['municipio']:
        core += ' no município de ' + state['municipio'].title()
    if state['uf']:
        core += ' em ' + state['uf']
    if state['year']:
        core += ' em ' + state['year']
    return core + '?'


def is_details_request(question: str) -> bool:
    q = norm(question)
    return any(x in q for x in ('detalhar', 'detalhes', 'explique melhor', 'fale mais', 'conte mais',
                                  'mais exemplos', 'exemplos', 'quais foram', 'quais municipios',
                                  'quais acoes', 'o que fizeram', 'o que os nucas fizeram', 'me mostre',
                                  'how many examples', 'elaborate', 'tell me more'))


def compute_action_details(records: list[dict], question: str, prev: str = '', language: str = 'pt') -> str | None:
    """Resumo calculado em TODOS os registros do recorte, sem confundir exemplos com total."""
    if not is_details_request(question):
        return None
    ref = prev or question
    # Só explica ações quando a pergunta atual ou o contexto se referem a elas.
    if not (re.search(r'\b(acoes|atividades|iniciativas|projetos|registradas)\b', norm(ref + ' ' + question))
            or 'o que os nucas fizeram' in norm(ref + ' ' + question)):
        return None
    rows, filters, topic = filtered_actions(records, question, prev)
    count = len(rows)
    scope = _scope_label(filters,topic)
    if language=='en':
        head = f'**{count:,} matching activities**' + (f' {scope}' if scope else '') + '.'
    else:
        head = f'**{_format_count(count)} ' + ('ação registrada' if count == 1 else 'ações registradas') + '**' + (f' {scope}' if scope else ' nos NUCAs') + '.'
    if not rows:
        return head + (' Não encontrei exemplos com esses critérios.' if language=='pt' else ' There are no examples matching those filters.')
    places = {(norm(string_field(r, 'municipio')), norm(string_field(r, 'uf')))
              for r in rows if string_field(r, 'municipio')}
    themes = Counter(string_field(r,'tema') or 'Tema não informado' for r in rows)
    municipalities = Counter(string_field(r,'municipio') for r in rows if string_field(r,'municipio'))
    lines = [head, '', f'As atividades aparecem em **{_format_count(len(places))} municípios**.' if language=='pt' else f'Activities were reported in **{len(places)} municipalities**.']
    if topic and topic['kind'] != 'category':
        lines.append('A busca considerou menções ao assunto nos temas e descrições; alguns registros relacionados podem usar outras palavras.')
    if len(themes)>1:
        lines.extend(['', '**Temas mais frequentes:**' if language=='pt' else '**Main themes:**'])
        lines += [f'- {key}: **{_format_count(val)}**' for key,val in themes.most_common(4)]
    elif themes:
        lines.extend(['', f'**Tema:** {next(iter(themes))}'])
    if 'quais municipios' in norm(question) or 'which municipalities' in norm(question):
        lines.extend(['', '**Municípios com mais atividades:**'])
        lines += [f'- {name}: {_format_count(val)}' for name,val in municipalities.most_common(6)]
    else:
        # Exemplos reais e variados; NÃO afirmar que eles representam todas as ações.
        chosen = []
        seen = set()
        for r in sorted(rows, key=lambda row: bool(string_field(row,'descricao')),reverse=True):
            place = (norm(string_field(r,'municipio')),norm(string_field(r,'uf')))
            if place in seen:
                continue
            chosen.append(r);seen.add(place)
            if len(chosen)==3:break
        if chosen:
            lines.extend(['', '**Alguns exemplos:**' if language=='pt' else '**A few examples:**'])
            for r in chosen:
                city = string_field(r, 'municipio') or 'Município não informado'
                theme = string_field(r,'tema') or 'Tema não informado'
                description = re.sub(r'\s+',' ',string_field(r,'descricao'))
                description = description[:165].rsplit(' ',1)[0].rstrip(' ,;')+'…' if len(description)>165 else description
                lines.append(f'- **{city}** — {theme}' + (f': {description}' if description else ''))
    return '\n'.join(lines)


def should_summarize_actions(question: str, prev: str = '') -> bool:
    q = norm(question)
    if not is_details_request(question):
        return False
    return bool(re.search(r'\b(acoes|atividades|iniciativas|projetos|registradas)\b',q+' '+norm(prev))
                or 'o que os nucas fizeram' in q+' '+norm(prev))


def evidence(items: list[dict], max_chars: int = 1400) -> tuple[str, list[dict]]:
    blocks, refs = [], []
    for i, item in enumerate(items, 1):
        r = item['record']
        src = source(r, item['position'])
        src['ref'] = i
        refs.append(src)
        lines = [f'FONTE [{i}]', f'ID da ação: {src["id"] or "não informado"}',
                 f'Título: {src["title"]}', f'Município: {src["municipality"]}',
                 f'UF: {src["uf"]}', f'Ano: {src["year"]}',
                 f'Tema: {string_field(r, "tema")}',
                 f'Descrição: {string_field(r, "descricao")[:max_chars]}']
        blocks.append('\n'.join(lines)[:max_chars + 350])
    return '\n\n'.join(blocks), refs
