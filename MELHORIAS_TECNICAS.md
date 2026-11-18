# Melhorias técnicas desta versão

## Problemas encontrados na versão anterior

1. Continuação de conversa baseada principalmente em regex.
2. Alguns filtros anteriores sobreviviam quando a nova pergunta mudava de assunto.
3. Perguntas abertas recebiam apenas poucos registros e o modelo podia tratá-los como cobertura total.
4. Consultas fora dos padrões conhecidos caíam rápido demais no LLM.
5. Extração de quantidades nas descrições era especializada apenas em mudas e árvores.
6. O pacote continha uma pasta `v1`, arquivos de histórico, caches e metadados do macOS que não pertenciam a um repositório novo.

## Novo fluxo

```text
Pergunta
  ↓
Contexto da conversa
  ↓
É uma continuação/consulta complexa?
  ├─ não → motor determinístico
  └─ sim → Groq Structured Output → pergunta canônica
                                      ↓
                              motor determinístico
                                      ↓
                          cálculo sobre toda a base
```

Se não houver uma operação determinística adequada:

```text
pergunta canônica
  ↓
resumo agregado do recorte completo
  +
busca textual BM25 nos relatos
  ↓
Groq redige a resposta
```

## Regra fundamental

A IA ajuda a **entender** e **redigir**. Totais e rankings são calculados pelo Python.

## Consultas cobertas diretamente

- total de ações;
- total de municípios;
- pessoas mobilizadas;
- participações de adolescentes;
- ranking de municípios e estados por ações;
- ranking por pessoas/adolescentes;
- principais temas;
- comparação entre estados;
- municípios com pelo menos N ações;
- média de ações por município;
- filtros por UF, município e ano;
- exemplos e detalhamento;
- perfil agregado de gênero e raça;
- mudas e árvores plantadas;
- outras quantidades explícitas encontradas em descrições.
