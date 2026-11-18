"""Interpretação estruturada de perguntas usando Groq somente quando necessário.

O modelo NÃO calcula resultados. Ele transforma linguagem natural em uma pergunta
canônica que o motor local executa sobre todos os registros.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any


ROUTER_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["canonical", "clarify", "general"]},
        "intent": {"type": "string", "enum": [
            "count_actions", "sum_people", "sum_adolescents", "count_municipalities",
            "theme_ranking", "location_ranking", "examples", "details",
            "text_quantity", "search", "general"
        ]},
        "canonical_question": {"type": "string"},
        "clarification": {"type": "string"},
        "search_terms": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
    },
    "required": ["action", "intent", "canonical_question", "clarification", "search_terms", "confidence"],
    "additionalProperties": False,
}


def should_route_question(question: str, history: list[dict]) -> bool:
    """Usa o interpretador em continuações/anáforas, onde regex costuma errar."""
    if os.getenv("ENABLE_QUERY_ROUTER", "true").lower() != "true":
        return False
    q = str(question or "").strip().lower()
    # Comparações, rankings por métricas e perguntas analíticas complexas ganham
    # uma normalização mesmo quando são independentes da conversa.
    if re.search(r"\b(?:compar|maior|menor|mais|menos|media|promedio|percentual|proporcao|ranking|lidera|liderou|pelo menos|no minimo|ao menos)\w*\b", q):
        return True
    if not history:
        return False
    # Continuações explícitas ou referências ao que veio antes.
    if re.search(r"^(e\b|mas\b|agora\b|certo\b|entao\b|e se\b|dess[ea]s?\b|ness[ea]s?\b|del[ea]s?\b)", q):
        return True
    if re.search(r"\b(nesse estado|nesse municipio|nessa cidade|nessas acoes|essas acoes|delas|deles|o total|esse total|isso|ali|la)\b", q):
        return True
    # Perguntas muito curtas com contexto quase sempre precisam ser resolvidas em conjunto.
    if len(re.findall(r"\w+", q)) <= 6 and re.search(r"\b(quant|qual|quais|onde|como|total|exemplos?|detalh)\w*\b", q):
        return True
    return False


def route_question(question: str, history: list[dict], previous_context: str,
                   themes: list[str], municipalities_sample: list[str] | None = None) -> dict[str, Any] | None:
    """Transforma a pergunta em uma consulta canônica, sem calcular ou responder."""
    if os.getenv("ENABLE_QUERY_ROUTER", "true").lower() != "true":
        return None
    key = os.getenv("GROQ_API_KEY")
    if not key:
        return None
    try:
        from groq import Groq

        last_users = [m.get("content", "") for m in history if m.get("role") == "user"][-4:]
        theme_text = "; ".join(themes[:40])
        municipality_hint = "; ".join((municipalities_sample or [])[:20])
        system = """Você é um interpretador de consultas para uma base de ações dos NUCAs do Selo UNICEF.
Sua única tarefa é reescrever a pergunta atual como uma pergunta CANÔNICA e autossuficiente para um motor de dados. Não responda a pergunta e não calcule números.

Regras:
- Preserve filtros explícitos da pergunta atual (estado, município, ano, tema) e eles substituem filtros incompatíveis anteriores.
- Só herde contexto anterior quando a frase atual fizer referência clara a ele: 'e em Alagoas?', 'nesse estado', 'dessas ações', 'pode detalhar?', 'e o total?'.
- Se a pessoa disser 'ações totais', 'todas as ações' ou equivalente, remova assunto temático anterior como plantio/mudas.
- Diferencie quantidade de AÇÕES de quantidade de OBJETOS descritos nas ações (mudas, árvores, livros, quilos etc.).
- 'Empoderamento de meninas' pode ser um tema de ação; não interprete como contagem de gênero se a pergunta fala em ações.
- Para ranking, explicite se é de municípios, estados ou temas.
- Para exemplos, mantenha todos os filtros do recorte pedido.
- Se houver duas interpretações realmente plausíveis e nenhuma puder ser escolhida com segurança, use action='clarify' e escreva uma pergunta curta de confirmação em português natural.
- Se for uma pergunta geral que não depende dos dados, use action='general'.
- canonical_question deve ser uma frase em português clara e autossuficiente; não use termos técnicos de infraestrutura.
"""
        user = (
            f"Perguntas anteriores do usuário: {json.dumps(last_users, ensure_ascii=False)}\n"
            f"Contexto reconstruído pelo sistema: {previous_context or 'nenhum'}\n"
            f"Temas existentes na base (referência): {theme_text or 'não informado'}\n"
            f"Alguns municípios conhecidos (apenas para grafia): {municipality_hint or 'não informado'}\n"
            f"Pergunta atual: {question}"
        )
        client = Groq(api_key=key, timeout=float(os.getenv("QUERY_ROUTER_TIMEOUT", "18")), max_retries=1)
        model = os.getenv("QUERY_ROUTER_MODEL", os.getenv("GROQ_MODEL", "openai/gpt-oss-20b"))
        options: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
            "max_completion_tokens": 450,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "nuca_query_plan", "strict": True, "schema": ROUTER_SCHEMA},
            },
        }
        if model in ("openai/gpt-oss-20b", "openai/gpt-oss-120b"):
            options["reasoning_effort"] = "low"
            options["include_reasoning"] = False
        try:
            response = client.chat.completions.create(**options)
            content = response.choices[0].message.content or "{}"
            result = json.loads(content)
        finally:
            client.close()
        if not isinstance(result, dict):
            return None
        return result
    except Exception:
        # O roteador é um aperfeiçoamento, nunca um ponto único de falha.
        return None
