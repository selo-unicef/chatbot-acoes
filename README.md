# Assistente NUCA

Chatbot para consultar ações e indicadores dos NUCAs do Selo UNICEF em linguagem natural.

Esta versão foi preparada para um **novo repositório GitHub** e para deploy direto na **Vercel** com Flask/Python.

## O que mudou nesta versão

O projeto não depende mais apenas de palavras-chave fixas ou de poucos exemplos recuperados.

### 1. Cálculos sobre a base inteira
Perguntas de total, soma, ranking, temas, municípios, pessoas mobilizadas e participações de adolescentes são calculadas pelo Python usando todos os registros que atendem ao recorte.

Exemplos:
- `Quantas ações foram registradas em Alagoas?`
- `Quais os temas com mais ações em Pernambuco?`
- `Qual município fez mais ações no Brasil?`
- `Quantos municípios fizeram pelo menos 3 ações?`
- `Qual município teve mais adolescentes envolvidos?`
- `Compare o total de ações de Alagoas e Pernambuco.`

### 2. Interpretador de perguntas com Structured Outputs
Quando a pergunta é uma continuação, usa pronomes ou foge dos padrões locais, a Groq é usada somente para **interpretar o pedido** e convertê-lo em uma consulta canônica.

A Groq não calcula os totais. Depois da interpretação, os números são calculados localmente na base completa.

Isso reduz erros como:
- herdar `plantio` depois de o usuário pedir `ações totais`;
- esquecer que `nesse estado` se refere a Pernambuco;
- interpretar poucos exemplos como o total do estado;
- confundir `Empoderamento de meninas` com dados de gênero.

### 3. Perguntas abertas com contexto global
Quando a resposta precisa ser redigida pela IA, ela recebe:
1. um resumo calculado sobre **todo o recorte**;
2. os registros textuais mais relevantes.

Assim, os exemplos servem para explicar e ilustrar, não para inventar totais.

### 4. Quantidades encontradas nas descrições
Além de mudas e árvores, o chatbot tenta responder perguntas inesperadas quando a quantidade está escrita claramente nas descrições.

Exemplos:
- `Quantas mudas foram plantadas?`
- `Quantos livros foram distribuídos?`
- `Quantos quilos de alimentos foram arrecadados?`
- `Quantas cestas básicas foram entregues?`

Esses resultados são apresentados como **totais parciais**, porque somente números explicitamente registrados nos relatos são somados.

## Estrutura

```text
.
├── app.py
├── nuca_data.py
├── llm.py
├── semantic_router.py
├── descricao_quantidades.py
├── descricao_generica.py
├── requirements.txt
├── requirements-dev.txt
├── .python-version
├── .env.example
├── templates/
├── static/
├── scripts/
├── tests/
└── .github/workflows/tests.yml
```

## Rodar localmente

Requer Python 3.12.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

Edite `.env` e informe:

```env
GROQ_API_KEY=sua_chave
```

Depois:

```bash
python app.py
```

Abra:

```text
http://127.0.0.1:5000
```

## Testes

```bash
pytest -q
```

A versão entregue foi validada com **77 testes aprovados** e 1 teste opcional ignorado.

Para testar somente a leitura dos dados públicos:

```bash
python scripts/verificar_dados.py
```

## Variáveis de ambiente importantes

| Variável | Padrão | Uso |
|---|---|---|
| `GROQ_API_KEY` | — | obrigatória para respostas geradas e interpretação semântica |
| `GROQ_MODEL` | `openai/gpt-oss-20b` | modelo de resposta |
| `ENABLE_QUERY_ROUTER` | `true` | ativa interpretação estruturada quando necessário |
| `QUERY_ROUTER_MODEL` | `openai/gpt-oss-20b` | modelo usado para interpretar consultas |
| `CLOUDFLARE_PAGES_URL` | `https://nucas-dados.pages.dev` | origem dos dados |
| `CLOUDFLARE_CACHE_SECONDS` | `600` | cache em memória por instância |
| `MAX_EVIDENCE` | `10` | número máximo de exemplos textuais enviados à IA |
| `RATE_LIMIT_PER_MINUTE` | `20` | limite básico por IP/processo |

## Deploy na Vercel

Veja [DEPLOY_VERCEL.md](DEPLOY_VERCEL.md).

O projeto usa `app.py` com uma instância Flask chamada `app`, formato reconhecido diretamente pela Vercel. Não é necessário criar uma pasta `/api` ou converter o backend para JavaScript.

## Segurança

- Nunca envie `.env` para o GitHub.
- `GROQ_API_KEY` deve ficar nas Environment Variables da Vercel.
- O projeto só apresenta ações com status `Aprovado` ou `Em validação/Aprovado`.
- Dados técnicos da origem não são mostrados nas respostas normais do chatbot.
