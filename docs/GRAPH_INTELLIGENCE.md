# V&F Graph Intelligence System

## Princípio

O sistema não parte do contato. A cadeia canônica é:

```text
PROCESSO
  -> MOVIMENTAÇÃO PROCESSUAL
  -> EVENTO ECONÔMICO
  -> HIPÓTESE DE GARANTIA
  -> EMPRESA
  -> DECISORES E INFLUENCIADORES
  -> DOCUMENTOS E EVIDÊNCIAS
  -> CANAIS DE ACESSO
  -> ABORDAGEM CONSULTIVA
```

Pesquisa por CNPJ continua disponível, mas é classificada como
`ENTITY_ENRICHMENT_ONLY`. Sem processo, o grafo recebe o estado
`AGUARDANDO_ORIGEM_PROCESSUAL` e não deve ser tratado como oportunidade.

## Verdades separadas

| Camada | Responsabilidade | Estado atual |
|---|---|---|
| Grafo | Verdade relacional entre processos, eventos, empresas, pessoas, documentos, contatos e hipóteses | Implementado com snapshots SQLite e exportação Neo4j |
| Operação comercial | Funil, tarefas, interações, usuário, proposta e resultado | Persistência local parcial; projeção Supabase ainda não conectada |
| Documentos | Conteúdo bruto, hash, URL, datas e trechos | Implementado |
| Tavily | Descoberta e extração de evidências para enriquecimento | Implementado como camada subordinada ao grafo |
| DataJud, DJEN e CARF | Origem processual e movimentos estruturados | Contrato de ingestão implementado; coletores automáticos ainda não conectados |
| Neo4j | Execução da verdade relacional em banco de grafo | Exportação Cypher parametrizada implementada; servidor não configurado |

## Nós

- `PROCESSO`
- `EVENTO_PROCESSUAL`
- `EMPRESA`
- `PESSOA`
- `CARGO`
- `ESCRITORIO`
- `DOCUMENTO`
- `DOMINIO`
- `EMAIL`
- `TELEFONE`
- `GARANTIA`
- `HIPOTESE_SECURITARIA`
- `EVIDENCIA`

Contatos são nós, nunca simples atributos da empresa. Hipóteses securitárias
também são nós explicitamente marcados como hipótese pendente de validação.

## Relações

As relações incluem `AFETA`, `GEROU`, `SUGERE`, `POSSUI`, `OCUPA`,
`REPRESENTA`, `APARECEU_EM`, `CONTEM`, `ASSOCIADO_A`, `PERTENCE_A`,
`CONFIRMA`, `SUPORTA`, `POSSUI_GARANTIA` e `VALIDADA_PELO_COMERCIAL`.

Toda relação exige:

- nó de evidência;
- URL ou origem operacional identificável;
- trecho original;
- data, quando localizada;
- score de evidência;
- classificação;
- justificativa.

O construtor rejeita relações sem esses elementos.

## Score de evidência documental

O score da fonte segue a orientação V&F e não representa probabilidade de
fechamento:

| Evidência | Score de referência |
|---|---:|
| Registro ou documento protocolado oficial | 95 |
| Procuração ou substabelecimento público | 95 |
| Site institucional ou RI | 90 |
| Relatório anual | 85 |
| Diário ou ato governamental | 80 |
| Tribunal | 75 |
| PDF ou fonte pública secundária contextual | 50-70 |
| CRM legado sem prova externa | 0 |

## Score da oportunidade

O score é explicável e limitado a componentes não cumulativos por categoria:

- processo como origem: `+10`;
- evento crítico: `+25`;
- valor superior a R$ 5 milhões: `+20`;
- decisor financeiro potencial: `+10`;
- decisor jurídico potencial: `+10`;
- influenciador tributário: `+15`;
- processo explicitamente ativo: `+15`;
- evento recente: até `+15`.

Ausência de garantia vale `0`. “Não localizada” nunca é convertida em
“inexistente”. Uma garantia só recebe a relação `POSSUI_GARANTIA` quando há
processo explícito e evidência confirmada ou corroborada.

Classificação:

- `0-40`: MONITORAR;
- `41-70`: QUALIFICAR;
- `71-100`: PRIORIDADE;
- `acima de 100`: ACAO_IMEDIATA.

## Regras de negócio e de mercado

Regras de negócio controlam funil, prioridade, revisão humana, interlocutores e
próxima ação. Regras de mercado controlam obrigação garantível, clausulado,
prazo, beneficiário, capacidade, contragarantia, apetite e aceite judicial.

Uma hipótese como `SUBSTITUICAO_DE_PENHORA` não afirma que a substituição é
juridicamente adequada. Ela apenas orienta a validação conjunta com o
tributarista e o financeiro.

## Histórico e feedback

O grafo nunca é sobrescrito. Cada ingestão ou feedback gera um novo snapshot
com `parent_graph_id`. Feedback comercial cria uma evidência operacional
separada, marcada como não pública. Ele pode validar um aprendizado comercial,
mas não transforma automaticamente esse aprendizado em prova judicial.

## Doze visões de decisão

Cada snapshot produz:

1. Grafo Corporativo;
2. Grafo Processual;
3. Grafo de Influência;
4. Grafo de Contatos;
5. Grafo de Garantias;
6. Linha do Tempo;
7. Eventos Críticos;
8. Decisores Prioritários;
9. Evidências Públicas;
10. Hipótese Securitária;
11. Lacunas de Validação;
12. Próxima Ação Comercial.

## Fluxo Tavily subordinado ao grafo

1. O processo identifica a empresa afetada.
2. Consultas especializadas e curtas descobrem URLs.
3. Resultados são filtrados por identidade, domínio e classe de fonte.
4. URLs selecionadas passam por extração focada.
5. Cada achado gera entidade, relação e evidência.
6. O grafo é recalculado e salvo como novo snapshot.

Tavily não grava “respostas”. Ele alimenta documentos e evidências estruturadas.

## Exportação Neo4j

`GET /api/graphs/{graph_id}/neo4j` produz constraints, `MERGE` de nós e
relações em Cypher parametrizado. Nenhum valor de empresa, pessoa ou evidência
é interpolado diretamente na consulta. A execução exige um servidor Neo4j e o
driver oficial configurados externamente.

## Projeção operacional do CRM

`GET /api/graphs/{graph_id}/crm-projection` produz estruturas para
`crm_empresas`, `crm_oportunidades`, `crm_decisores`, `entity_evidence` e
`crm_interacoes`. O endpoint não escreve no Supabase: ele materializa a projeção
que um sincronizador poderá consumir. Só existe linha de oportunidade quando a
origem processual foi validada; enriquecimento isolado de CNPJ não entra no
funil como oportunidade.
