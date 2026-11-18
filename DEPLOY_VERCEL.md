# Deploy no GitHub e na Vercel

## 1. Criar um novo repositório

Crie um repositório vazio no GitHub, por exemplo:

```text
chatbot-nuca
```

Na pasta deste projeto:

```bash
git init
git add .
git commit -m "Versão inicial do Assistente NUCA"
git branch -M main
git remote add origin https://github.com/SEU-USUARIO/chatbot-nuca.git
git push -u origin main
```

Antes do `git add .`, confirme que `.env` não aparece em:

```bash
git status
```

O `.gitignore` já bloqueia `.env`, `.venv`, caches e arquivos locais da Vercel.

## 2. Importar na Vercel

1. Acesse o painel da Vercel.
2. Clique em **Add New → Project**.
3. Importe o novo repositório do GitHub.
4. Não altere o diretório raiz.
5. A Vercel deve reconhecer automaticamente a aplicação Python/Flask em `app.py`.

Não é necessário configurar Build Command ou Start Command manualmente.

## 3. Environment Variables

Em **Settings → Environment Variables**, adicione pelo menos:

```text
GROQ_API_KEY = sua chave Groq
AI_PROVIDER = groq
GROQ_MODEL = openai/gpt-oss-20b
ENABLE_QUERY_ROUTER = true
QUERY_ROUTER_MODEL = openai/gpt-oss-20b
DATA_SOURCE_MODE = cloudflare
CLOUDFLARE_PAGES_URL = https://nucas-dados.pages.dev
CLOUDFLARE_CACHE_SECONDS = 600
MAX_EVIDENCE = 10
ENABLE_RESPONSE_CACHE = true
```

Aplique as variáveis a **Production**, **Preview** e **Development** se quiser o mesmo comportamento em todos os ambientes.

## 4. Deploy

Clique em **Deploy**.

Depois teste:

```text
https://SEU-PROJETO.vercel.app/
https://SEU-PROJETO.vercel.app/health
https://SEU-PROJETO.vercel.app/api/data/status
```

## 5. Perguntas para validar

Teste conversas completas, não apenas perguntas isoladas:

```text
Quantas ações foram registradas no Brasil?
E em Pernambuco?
Quais os principais temas nesse estado?
Dê exemplos sobre Prevenção às violências.
Agora quero o total de ações de Alagoas.
Qual município do país fez mais ações?
Quantos municípios fizeram pelo menos 3 ações?
Quantas mudas foram plantadas nas ações?
Quantos livros foram distribuídos nas ações?
```

## Observação sobre Vercel

O Flask é detectado diretamente pela plataforma e o runtime Python suporta respostas em streaming. Em instâncias novas, o primeiro acesso pode precisar carregar os arquivos de dados externos; depois, a instância reutilizada mantém cache em memória enquanto permanecer ativa.

O projeto não grava arquivos persistentes no servidor, o que combina com o modelo serverless da Vercel.
