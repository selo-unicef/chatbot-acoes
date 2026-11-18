"""Valida dados do Cloudflare Pages sem usar Groq nem Supabase."""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nuca_data import CloudflarePagesStore

load_dotenv(Path(__file__).resolve().parents[1] / '.env')

url = os.getenv('CLOUDFLARE_PAGES_URL', 'https://nucas-dados.pages.dev')
store = CloudflarePagesStore(url, ttl=60)
datasets = store.get_datasets()
if datasets is None:
    print('ERRO: não foi possível carregar o CDN:', store.error)
    raise SystemExit(1)
print('Cloudflare Pages:', url)
print('Snapshot:', store.version)
print('Atualização:', store.updated_at)
for table, records in datasets.items():
    print(f'{table}: {len(records)} registros elegíveis')
print('OK: as três tabelas foram lidas com sucesso.')
