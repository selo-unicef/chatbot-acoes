"""Chatbot NUCAs v2 — Flask, snapshot/CDN, RAG, respostas em streaming."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict, defaultdict, deque
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from llm import produce, search_vector
from nuca_data import (CloudflarePagesStore, SnapshotStore, compute_profile_stats, compute_stats,
                       compute_action_details, compute_text_quantities, compute_generic_text_quantity,
                       compute_theme_breakdown, compute_advanced_stats, text_quantity_intent, is_details_request, is_followup_question,
                       should_summarize_actions, conversation_context_question, clarification_request,
                       evidence, search_snapshot, corpus_overview, known_themes, municipality_samples)
from semantic_router import route_question, should_route_question

load_dotenv(Path(__file__).resolve().parent / '.env')
logging.basicConfig(level=os.getenv('LOG_LEVEL','INFO'), format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger('nuca')

app = Flask(__name__)
app.json.ensure_ascii = False
app.config['MAX_CONTENT_LENGTH'] = 32_768
app.config['JSON_SORT_KEYS'] = False

# Fonte padrão: os mesmos snapshots públicos utilizados pelo frontend NUCA.
# Para voltar a um JSON legado local, configure DATA_SOURCE_MODE=legacy.
if os.getenv('DATA_SOURCE_MODE', 'cloudflare').lower() == 'legacy':
    store = SnapshotStore(
        path=os.getenv('SNAPSHOT_PATH', ''),
        url=os.getenv('SNAPSHOT_URL', ''),
        ttl=int(os.getenv('SNAPSHOT_TTL_SECONDS', '7200')),
    )
else:
    store = CloudflarePagesStore(
        base_url=os.getenv('CLOUDFLARE_PAGES_URL', 'https://nucas-dados.pages.dev'),
        ttl=int(os.getenv('CLOUDFLARE_CACHE_SECONDS', '600')),
    )

# Cota básica por processo (em várias instâncias, utilizar rate limiting no proxy/CDN).
_rate_lock = threading.Lock()
_rate_hits = defaultdict(deque)
_cache_lock = threading.Lock()
_cache: OrderedDict = OrderedDict()


def t(language: str, pt: str, en: str) -> str:
    return en if language == 'en' else pt


def throttle() -> bool:
    if os.getenv('DISABLE_RATE_LIMIT','0') == '1':
        return False
    identifier = request.remote_addr or 'unknown'
    now = time.monotonic()
    cap = int(os.getenv('RATE_LIMIT_PER_MINUTE','20'))
    with _rate_lock:
        hits = _rate_hits[identifier]
        while hits and hits[0] < now - 60:
            hits.popleft()
        if len(hits) >= cap:
            return True
        hits.append(now)
        if len(_rate_hits) > 3000:
            for key in list(_rate_hits)[:500]:
                if not _rate_hits[key] or _rate_hits[key][-1] < now - 60:
                    del _rate_hits[key]
    return False


def clean_history(raw: Any, question: str) -> list[dict]:
    if not isinstance(raw, list):
        return []
    items = []
    for item in raw[-12:]:
        if not isinstance(item, dict):
            continue
        role, content = item.get('role'), item.get('content')
        if role in ('user','assistant') and isinstance(content, str) and content.strip():
            limit = 1400 if role == 'user' else 1800
            items.append({'role':role,'content':content.strip()[:limit]})
    # Evita duplicar a pergunta atual (bug do projeto anterior).
    if items and items[-1]['role'] == 'user' and items[-1]['content'] == question:
        items.pop()
    return items[-10:]


def valid_payload():
    raw = request.get_json(silent=True)
    if not isinstance(raw, dict):
        return None, ('Corpo JSON inválido.', 400)
    q = raw.get('message')
    if not isinstance(q, str) or not q.strip() or len(q) > 1600:
        return None, ('Digite uma pergunta de até 1.600 caracteres.', 400)
    q = q.strip()
    lang = 'en' if str(raw.get('language','pt')).lower().startswith('en') else 'pt'
    return {'question':q,'language':lang,'history':clean_history(raw.get('history'),q)}, None


def _try_direct(records: list[dict], datasets: dict[str, list[dict]] | None,
                question: str, previous: str, language: str, style: int) -> str | None:
    """Executa cálculos locais sobre a base inteira, sem delegar números ao LLM."""
    quantity_unit = text_quantity_intent(question)
    if not quantity_unit and is_followup_question(question) and not re.search(
            r'\b(?:acoes|atividades|pessoas|adolescentes|municipios|cidades)\b', question, re.I):
        quantity_unit = text_quantity_intent(previous)
    if quantity_unit:
        answer = compute_text_quantities(records, question, previous, language,
                                         unit=quantity_unit, detailed=is_details_request(question))
        if answer is not None:
            return answer

    # Quantidades não previstas originalmente (livros distribuídos, quilos arrecadados etc.).
    generic = compute_generic_text_quantity(records, question, previous, language,
                                            detailed=is_details_request(question))
    if generic is not None:
        return generic

    if should_summarize_actions(question, previous):
        answer = compute_action_details(records, question, previous, language)
        if answer is not None:
            return answer

    themes = compute_theme_breakdown(records, question, previous, language)
    if themes is not None:
        return themes

    advanced = compute_advanced_stats(records, question, previous, language)
    if advanced is not None:
        return advanced

    # Perfis agregados só entram quando a pergunta não é sobre ações/atividades.
    if datasets:
        profile = compute_profile_stats(datasets, question, previous, language)
        if profile is not None:
            return profile

    return compute_stats(records, question, previous, language, style=style)


def _router_plan(question: str, history: list[dict], previous: str,
                 records: list[dict]) -> dict | None:
    return route_question(
        question, history, previous,
        themes=known_themes(records),
        municipalities_sample=municipality_samples(records, 24),
    )


def prepare(question: str, history: list[dict], language: str) -> dict:
    records = store.get()
    if records is None:
        if os.getenv('OPENAI_VECTOR_STORE_ID') and os.getenv('OPENAI_API_KEY'):
            context, refs = search_vector(question)
            return {'direct':None, 'context':context, 'sources':refs,
                    'data_mode':'vector', 'snapshot_count':None}
        return {'direct':t(language,
            'Não consegui acessar as informações dos NUCAs agora. Tente novamente em instantes.',
            'I could not access the NUCA information right now. Please try again shortly.'),
            'context':'','sources':[],'data_mode':'none','snapshot_count':None}

    datasets = store.get_datasets() if hasattr(store, 'get_datasets') else None
    previous = conversation_context_question(question, history, records)
    style = sum(m['role'] == 'user' for m in history)

    # Confirmações determinísticas simples continuam rápidas e não consomem IA.
    clarify = clarification_request(question, history, language)
    if clarify:
        return {'direct':clarify, 'context':'', 'sources':[],
                'data_mode':'clarification', 'snapshot_count':len(records)}

    routed = None
    engine_question, engine_previous = question, previous
    # Continuações, pronomes e comparações são os casos em que o parser antigo
    # mais carregava filtros incorretos. Neles usamos Structured Outputs da Groq
    # apenas para INTERPRETAR a frase; os cálculos continuam locais.
    if should_route_question(question, history):
        routed = _router_plan(question, history, previous, records)
        if routed and routed.get('action') == 'clarify' and routed.get('clarification'):
            return {'direct':str(routed['clarification']), 'context':'', 'sources':[],
                    'data_mode':'clarification', 'snapshot_count':len(records)}
        if routed and routed.get('action') == 'canonical' and int(routed.get('confidence') or 0) >= 55:
            canonical = str(routed.get('canonical_question') or '').strip()
            if canonical:
                engine_question, engine_previous = canonical, ''

    direct = _try_direct(records, datasets, engine_question, engine_previous, language, style)
    if direct is not None:
        return {'direct':direct, 'context':'', 'sources':[],
                'data_mode':'data', 'snapshot_count':len(records)}

    # Uma pergunta independente pode ser perfeitamente compreensível para uma
    # pessoa, mas não caber nos padrões locais. Nesse caso o roteador tem uma
    # segunda chance de transformá-la em consulta executável sobre a base toda.
    if routed is None and os.getenv('ENABLE_QUERY_ROUTER', 'true').lower() == 'true':
        routed = _router_plan(question, history, previous, records)
        if routed and routed.get('action') == 'clarify' and routed.get('clarification'):
            return {'direct':str(routed['clarification']), 'context':'', 'sources':[],
                    'data_mode':'clarification', 'snapshot_count':len(records)}
        if routed and routed.get('action') == 'canonical' and int(routed.get('confidence') or 0) >= 55:
            canonical = str(routed.get('canonical_question') or '').strip()
            if canonical and canonical != engine_question:
                engine_question, engine_previous = canonical, ''
                direct = _try_direct(records, datasets, engine_question, '', language, style)
                if direct is not None:
                    return {'direct':direct, 'context':'', 'sources':[],
                            'data_mode':'data', 'snapshot_count':len(records)}

    # Perguntas descritivas recebem dois tipos de contexto:
    # (1) um RESUMO calculado em todos os registros do recorte; e
    # (2) exemplos textuais ranqueados. Assim exemplos nunca viram "total".
    results = search_snapshot(records, engine_question, engine_previous,
                              int(os.getenv('MAX_EVIDENCE','10')))
    evidence_context, refs = evidence(results, max_chars=1050)
    overview = corpus_overview(records, engine_question, engine_previous)
    context = '\n\n'.join(filter(None, [overview, evidence_context]))

    vector_enabled = (os.getenv('RETRIEVAL_MODE','auto') in ('hybrid','vector')
                      or (not results and os.getenv('RETRIEVAL_MODE','auto')=='auto'))
    if vector_enabled and os.getenv('OPENAI_VECTOR_STORE_ID'):
        vcontext, vrefs = search_vector(engine_question)
        if vcontext:
            offset = len(refs)
            if offset:
                for num in range(len(vrefs),0,-1):
                    vcontext = re.sub(r'\['+str(num)+r'\]', '['+str(num+offset)+']',vcontext)
            for item in vrefs:
                item['ref'] += offset
            context='\n\n'.join(filter(None,[context,vcontext]))
            refs += vrefs

    for ref in refs:
        url = ref.get('url')
        if url and ('pages.dev' in url or '/manifest.json' in url):
            ref['url'] = None
    return {'direct':None, 'context':context, 'sources':refs,
            'data_mode':'data', 'snapshot_count':len(records)}


def sse(event: str, data: Any) -> str:
    return f'event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'


def cache_get(key: str):
    if os.getenv('ENABLE_RESPONSE_CACHE','false').lower() != 'true':
        return None
    with _cache_lock:
        value = _cache.get(key)
        if value and value[0] > time.monotonic():
            _cache.move_to_end(key)
            return value[1]
        if value:
            del _cache[key]
    return None


def cache_set(key: str, answer: str):
    if os.getenv('ENABLE_RESPONSE_CACHE','false').lower() != 'true':
        return
    with _cache_lock:
        _cache[key] = (time.monotonic()+1800, answer)
        _cache.move_to_end(key)
        while len(_cache) > 150:
            _cache.popitem(last=False)


def answer_chunks(payload: dict, plan: dict):
    if plan['direct'] is not None:
        yield plan['direct']
        return
    provider = os.getenv('AI_PROVIDER','groq')
    cache_key = json.dumps((provider, os.getenv('GROQ_MODEL', 'openai/gpt-oss-20b'), payload['language'],payload['question'].lower(),store.version),ensure_ascii=False)
    if not payload['history']:
        cached = cache_get(cache_key)
        if cached is not None:
            yield cached
            return
    output = []
    for delta in produce(payload['language'], payload['question'],payload['history'], plan['context'],provider):
        output.append(delta)
        yield delta
    if not payload['history'] and output:
        cache_set(cache_key, ''.join(output))


@app.get('/')
def index():
    return render_template('index.html')


@app.get('/health')
def health():
    key_available = bool(os.getenv('GROQ_API_KEY')) and os.getenv('AI_PROVIDER', 'groq') == 'groq'
    has_dataset = store.configured() or bool(os.getenv('OPENAI_VECTOR_STORE_ID') and os.getenv('OPENAI_API_KEY'))
    return jsonify({'status': 'ok' if key_available and has_dataset else 'configuration_required',
                    'ai_provider':os.getenv('AI_PROVIDER','groq'),
                    'model':os.getenv('GROQ_MODEL','openai/gpt-oss-20b'),
                    'dataset': 'cloudflare' if isinstance(store, CloudflarePagesStore) else ('snapshot' if store.configured() else ('vector' if has_dataset else 'missing')),
                    'snapshot_loaded':len(store.records) if store.records is not None else None,
                    'snapshot_id': store.version or None,
                    'data_error': store.error or None}), (200 if key_available and has_dataset else 503)




@app.get('/api/data/status')
def data_status():
    datasets = store.get_datasets() if hasattr(store, 'get_datasets') else None
    if datasets is None and not hasattr(store, 'get_datasets'):
        records = store.get()
        datasets = {'acoes_nuca': records} if records is not None else None
    if datasets is None:
        return jsonify({'status':'unavailable','source':store.url,'message':store.error or 'A fonte ainda não foi carregada'}), 503
    return jsonify({
        'status':'ok', 'source':store.url,
        'snapshot_id':store.version or None,
        'updated_at':getattr(store, 'updated_at', '') or None,
        'records':{k:len(v) for k,v in datasets.items()},
        'note':'Ações elegíveis incluem Aprovado e Em validação/Aprovado; Em validação não entra nos resultados.'
    })

@app.post('/api/chat')
def chat_legacy():
    payload, error = valid_payload()
    if error:
        return jsonify({'error':error[0]}), error[1]
    if throttle():
        return jsonify({'error':'Limite temporário de mensagens atingido. Tente novamente em um minuto.'}), 429
    try:
        plan = prepare(payload['question'],payload['history'],payload['language'])
        answer = ''.join(answer_chunks(payload,plan))
        return jsonify({'answer':answer, 'sources':plan['sources'], 'data_mode':plan['data_mode']})
    except Exception:
        logger.exception('Falha em /api/chat')
        return jsonify({'error':'Não foi possível concluir a resposta. Confira a chave e os limites de uso da Groq e tente novamente.'}), 502


@app.post('/api/chat/stream')
def chat_stream():
    payload, error = valid_payload()
    if error:
        return jsonify({'error':error[0]}), error[1]
    if throttle():
        return jsonify({'error':'Limite temporário de mensagens atingido. Tente novamente em um minuto.'}), 429

    def generate():
        try:
            plan = prepare(payload['question'],payload['history'],payload['language'])
            yield sse('meta', {'sources':plan['sources'], 'data_mode':plan['data_mode']})
            count = 0
            for chunk in answer_chunks(payload,plan):
                count += len(chunk)
                yield sse('delta', {'text':chunk})
            if count == 0:
                yield sse('delta', {'text':t(payload['language'],
                    'Não consegui gerar uma resposta desta vez. Pode reformular a pergunta?',
                    'I could not generate an answer this time. Could you rephrase the question?')})
            yield sse('done', {'ok': True})
        except Exception:
            logger.exception('Falha ao transmitir resposta')
            yield sse('error', {'message':'Não foi possível responder no momento. Verifique se o modelo e a chave Groq estão disponíveis.'})

    return Response(stream_with_context(generate()),mimetype='text/event-stream',headers={
        'Cache-Control':'no-cache, no-store', 'X-Accel-Buffering':'no', 'X-Content-Type-Options':'nosniff',
    })


@app.after_request
def security_headers(resp):
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    resp.headers['X-Frame-Options'] = 'SAMEORIGIN'
    resp.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'self'"
    return resp

if __name__ == '__main__':
    app.run(host='0.0.0.0',port=int(os.getenv('PORT','5000')),debug=os.getenv('FLASK_DEBUG')=='1',threaded=True)
