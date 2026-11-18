"""Extração conservadora de quantidades explícitas em descrições de ações.

Complementa o extrator especializado de mudas/árvores. É usado para perguntas
inesperadas como 'quantos livros foram distribuídos?' ou 'quantos quilos foram
arrecadados?'. Só soma números associados à unidade e à ação pedidas no mesmo
contexto textual; por isso o resultado é sempre apresentado como parcial.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


def plain(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in value if not unicodedata.combining(c)).lower()


@dataclass(frozen=True)
class QuantitySpec:
    unit_label: str
    unit_tokens: tuple[str, ...]
    action_stems: tuple[str, ...]


@dataclass(frozen=True)
class GenericMention:
    count: float
    excerpt: str


STOP_UNITS = {"de", "do", "da", "dos", "das", "em", "no", "na", "nas", "nos", "total", "numero", "quantidade"}
ACTION_MAP = {
    "plant": ("plant", "replant"),
    "arrecad": ("arrecad", "colet", "recolh"),
    "distribu": ("distribu",),
    "doa": ("doa",),
    "entreg": ("entreg",),
    "produz": ("produz", "confeccion", "fabric"),
    "colet": ("colet", "recolh"),
    "recolh": ("recolh", "colet"),
    "retir": ("retir", "remov"),
    "recicl": ("recicl", "reaproveit"),
}
PLANNED = re.compile(r"\b(?:meta|previsao|prev[eê]|planej|pretend|objetivo|futur|sera|serao|seriam|irao|vao|previst|expectativa|proposta)\w*\b")
APPROX = re.compile(r"\b(?:cerca de|aproximadamente|aprox|mais de|menos de|quase|em torno de|por volta de|pelo menos|ate)\b")
SPLIT = re.compile(r"[;\n]+|(?<=[.?!])\s+")
NUM = re.compile(r"(?<![\d,.])(\d{1,3}(?:[. ]\d{3})+|\d{1,8})(?:\s+(mil))?(?![\d,.])")


def infer_quantity_spec(question: str) -> QuantitySpec | None:
    q = plain(question)
    # Pessoas/adolescentes/ações têm campos estruturados e não devem cair aqui.
    if re.search(r"\b(?:acoes?|atividades?|pessoas?|participantes?|adolescentes?|municipios?|cidades?)\b", q):
        # Permitir 'nas ações' apenas quando a unidade física aparece antes.
        unit_before = re.search(r"(?:quant[oa]s?|total de|numero de)\s+(.{2,45}?)\s+(?:foram|foi|ficaram|sao|eram|houve)\b", q)
        if not unit_before or re.search(r"\b(?:acoes?|pessoas?|adolescentes?|municipios?)\b", unit_before.group(1)):
            return None
    m = re.search(r"(?:quant[oa]s?|total\s+de|numero\s+de|quantidade\s+de)\s+(.{2,50}?)\s+(?:foram|foi|ficaram|sao|eram|houve)\s+([a-z]+)", q)
    if not m:
        return None
    unit_phrase = m.group(1).strip(" ?.,!")
    action_word = m.group(2)
    content = [w for w in re.findall(r"[a-z0-9]+", unit_phrase) if w not in STOP_UNITS and len(w) > 2]
    if not content:
        return None
    # 'quilos de alimentos' exige que ao menos 'quilo' ou 'kg' esteja próximo do número;
    # os demais tokens ajudam a rejeitar frases irrelevantes.
    label = unit_phrase
    stems = ()
    for key, values in ACTION_MAP.items():
        if action_word.startswith(key):
            stems = values
            break
    if not stems:
        # Particípios frequentes que podem ter flexão.
        root = re.sub(r"(?:ados?|adas?|idos?|idas?|ou|am|aram|eram)$", "", action_word)
        stems = (root[:7],) if len(root) >= 4 else ()
    if not stems:
        return None
    return QuantitySpec(unit_label=label, unit_tokens=tuple(content[:4]), action_stems=tuple(stems))


def _unit_near(sentence: str, n_end: int, tokens: tuple[str, ...]) -> bool:
    window = sentence[n_end:n_end + 70]
    # Primeiro token é o núcleo da unidade. Aceita flexão simples por prefixo.
    first = tokens[0]
    aliases = [first]
    if first in ("quilos", "quilo", "quilogramas", "quilograma"):
        aliases += ["kg"]
    if first in ("toneladas", "tonelada"):
        aliases += ["ton"]
    return any(re.search(r"\b" + re.escape(a[:5] if len(a) > 5 else a) + r"\w*\b", window) for a in aliases)


def extract_generic_quantities(description: str, spec: QuantitySpec) -> list[GenericMention]:
    found: list[GenericMention] = []
    seen: set[float] = set()
    for raw in SPLIT.split(str(description or "")):
        sentence = plain(raw)
        if PLANNED.search(sentence) or APPROX.search(sentence):
            continue
        if not any(stem and stem in sentence for stem in spec.action_stems):
            continue
        # Se houver substantivos adicionais ('quilos de alimentos'), exija ao menos um deles.
        extras = spec.unit_tokens[1:]
        if extras and not any(tok[:5] in sentence for tok in extras):
            continue
        for match in NUM.finditer(sentence):
            if not _unit_near(sentence, match.end(), spec.unit_tokens):
                continue
            raw_num = match.group(1).replace(".", "").replace(" ", "")
            value = float(int(raw_num) * (1000 if match.group(2) else 1))
            if value <= 0 or value > 100_000_000 or value in seen:
                continue
            # O verbo deve estar razoavelmente próximo do número/unidade.
            left = sentence[max(0, match.start() - 90):match.start()]
            right = sentence[match.end():match.end() + 120]
            if not any(stem in left or stem in right for stem in spec.action_stems):
                continue
            seen.add(value)
            excerpt = re.sub(r"\s+", " ", raw.strip())
            if len(excerpt) > 220:
                excerpt = excerpt[:217].rstrip() + "…"
            found.append(GenericMention(value, excerpt))
    return found
