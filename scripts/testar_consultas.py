"""Testes rápidos do motor local com uma base fictícia; não usa Groq nem internet."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nuca_data import normalize_records, compute_stats, compute_advanced_stats, compute_generic_text_quantity

rows = normalize_records([
    {'status':'Aprovado','uf':'Alagoas (AL)','municipio':'Penedo','tema':'Cultura','descricao':'Foram distribuídos 120 livros.','publico':50,'adoles_parcipantes':20},
    {'status':'Aprovado','uf':'Alagoas (AL)','municipio':'Penedo','tema':'Clima','descricao':'Foram plantadas 30 mudas.','publico':60,'adoles_parcipantes':25},
    {'status':'Aprovado','uf':'Pernambuco (PE)','municipio':'Recife','tema':'Saúde','descricao':'Campanha comunitária.','publico':100,'adoles_parcipantes':40},
], require_status=True)

questions = [
    'Quantas ações foram registradas em Alagoas?',
    'Qual município teve mais adolescentes envolvidos?',
    'Quantos livros foram distribuídos nas ações em Alagoas?',
]
for q in questions:
    answer = (compute_advanced_stats(rows, q)
              or compute_generic_text_quantity(rows, q)
              or compute_stats(rows, q))
    print('\n>', q)
    print(answer)
