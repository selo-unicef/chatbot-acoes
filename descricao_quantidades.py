"""Extrai quantitativos *explícitos* de relatos de atividades dos NUCAs.

Não é um extrator de fatos gerais nem um sistema de inferência. Reconhece somente
quantidade + unidade + verbo compatível com plantio concluído, com verificações
conservadoras para metas, distribuições, aproximações e repetições do mesmo fato.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


def _plain(s: str) -> str:
    s = unicodedata.normalize('NFKD', str(s or ''))
    return ''.join(c for c in s if not unicodedata.combining(c)).lower()


@dataclass(frozen=True)
class Mention:
    count: int
    excerpt: str


# As unidades são pesquisadas separadamente para evitar somar árvores e mudas.
UNITS = {
    'mudas': r'mudas?',
    'arvores': r'arvores?',
}
# Reconhecer ações relatadas como realizadas, não promessas ou intenções.
PLANTING = re.compile(r'\b(?:plantio|plantios|plantad[ao]s?|plantaram|plantou|plantamos|plantando|replantad[ao]s?|replantio)\b')
PLANNED = re.compile(r'\b(?:meta|previsao|preve|previam|previu|planejament[oa]|planejad[ao]s?|pretend(?:em|e|emos|iam)|objetivo|futur[oa]s?|sera[o]?|seriam|iriam|vao|previst[ao]s?|expectativa|proposta|prometid[ao]s?)\b')
OTHER_ACTIVITY = re.compile(r'\b(?:distribu(?:icao|idas?|idos?|iram|imos|ir|iu)|doa(?:cao|das?|dos?|ram|mos|r|ou)|entreg(?:a|ues?|aram|amos|ar|ou)|receb(?:eram|idas?|eu)|adquirid[ao]s?|comprad[ao]s?)\b')
APPROX = re.compile(r'(?:\b(?:cerca de|mais de|menos de|pelo menos|no minimo|ate|quase|aproximadamente|aprox|em torno de|por volta de|estimad[ao]s?)\s*|[~≈])$')
NUMBER = r'(?<![\d,.])(?P<number>\d{1,3}(?:[. ]\d{3})+|\d{1,7})(?:\s+(?P<scale>mil))?'
SPLIT = re.compile(r'[;\n]+|(?<=[.?!])\s+(?=[A-ZÁ-Ú])')


def _near(sentence: str, start: int, end: int, lookback: int = 105, lookahead: int = 75) -> bool:
    """Verifica associação contextual da unidade com plantio efetivamente realizado."""
    left = sentence[max(0, start-lookback):start]
    right = sentence[end:end+lookahead]
    prev_plants = list(PLANTING.finditer(left))
    next_plants = list(PLANTING.finditer(right))
    if not prev_plants and not next_plants:
        return False
    # Uma atividade que só distribuiu/doou mudas não é evidência de plantio.
    last_other = list(OTHER_ACTIVITY.finditer(left))
    if last_other and (not prev_plants or last_other[-1].start() > prev_plants[-1].start()):
        return False
    if next_plants:
        # Não associe um número ao plantio de OUTRA quantidade citada depois.
        between = right[:next_plants[0].start()]
        if re.search(r'\d', between):
            return False
        # "mudas distribuídas e plantadas" é ambíguo e fica fora da soma.
        other_after = OTHER_ACTIVITY.search(right[:next_plants[0].end()])
        if other_after:
            return False
    # Projetos ainda planejados não entram como resultados realizados.
    closest_before = prev_plants[-1].start() if prev_plants else -1
    earliest_after = next_plants[0].start() if next_plants else len(right)
    plan_before = PLANNED.search(left[max(0, closest_before - 65):]) if prev_plants else None
    if plan_before:
        return False
    if next_plants and PLANNED.search(right[:earliest_after + next_plants[0].end()]):
        return False
    # "serão plantadas" e "irão plantar" não representam execução concluída.
    if prev_plants and PLANNED.search(left[max(0, prev_plants[-1].start()-20):prev_plants[-1].start()]):
        return False
    if next_plants and PLANNED.search(right[max(0, next_plants[0].start()-25):next_plants[0].start()]):
        return False
    return True


def extract_planting_quantities(description: str, unit: str = 'mudas') -> list[Mention]:
    """Retorna somente quantidades expressas como plantadas, sem inferir valores.

    Repetições do mesmo número em uma ação são deduplicadas conservadoramente.
    Isto evita somar duas vezes uma atividade que reitera o mesmo número.
    """
    pattern = UNITS.get(unit)
    if pattern is None:
        return []
    regex = re.compile(NUMBER + r'\s+' + r'(?:de\s+)?' + pattern + r'\b')
    found, seen_values = [], set()
    for raw_sentence in SPLIT.split(str(description or '')):
        sentence = _plain(raw_sentence)
        for m in regex.finditer(sentence):
            n = m.group('number').replace('.', '').replace(' ', '')
            value = int(n) * (1000 if m.group('scale') else 1)
            if value <= 0 or value > 100_000_000 or value in seen_values:
                continue
            before = sentence[max(0, m.start()-48):m.start()]
            if APPROX.search(before.rstrip()):
                continue
            # Não escolher a extremidade de uma faixa ou decimal como total.
            if re.search(r'(?:\d+\s*(?:a|ate|e|[-–])\s*|\d+,)$', before):
                continue
            if not _near(sentence, m.start(), m.end()):
                continue
            seen_values.add(value)
            excerpt = re.sub(r'\s+', ' ', raw_sentence.strip())
            if len(excerpt) > 200:
                pivot = m.start()
                excerpt = '…' + excerpt[max(0, pivot - 65):min(len(excerpt), pivot + 115)].strip() + '…'
            found.append(Mention(count=value, excerpt=excerpt))
    return found
