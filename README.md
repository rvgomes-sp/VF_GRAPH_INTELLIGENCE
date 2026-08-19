# V&F Graph Intelligence System

Sistema local de inteligência relacional para oportunidades de Seguro Garantia Judicial Fiscal. O processo e sua movimentação são a origem; a empresa, os decisores, os documentos e os contatos são entidades conectadas; a hipótese securitária é um destino sujeito a validação jurídica, técnica e de mercado.

## O que já está implementado

- grafo process-first com nós para processo, evento, empresa, pessoa, cargo, documento, domínio, contato, garantia, hipótese e evidência;
- relações auditáveis: cada ligação carrega fonte, trecho, data, score, classificação e justificativa;
- snapshots temporais imutáveis em SQLite e feedback comercial como nova versão do grafo;
- score de oportunidade explicável, sem pontuar ausência de garantia por falta de localização;
- 12 visões: grafos corporativo, processual, influência, contatos e garantias; timeline; eventos; decisores; evidências; hipótese; lacunas; próxima ação;
- exportação Neo4j em Cypher parametrizado, sem exigir credenciais ou servidor para executar localmente;
- endpoint process-first e interface que começa por processo, evento e fonte;
- crawler inteligente de HTML e PDF com `robots.txt`, rate limit, profundidade e orçamento;
- classificação de fontes e score por confiabilidade;
- separação entre fato confirmado, corroborado, hipótese, lacuna e conflito;
- separação de `captured_at`, `published_at` e `observed_event_at`;
- trilha SQLite de documentos, claims, dossiês e feedback;
- score explicável de evidência — o score legado não é reaproveitado como verdade;
- gerador de abordagem por persona com termos protegidos;
- aprendizagem guardada por feedback, sem mudança autônoma das regras jurídicas ou securitárias;
- API FastAPI, interface web por CNPJ e CLI;
- descoberta multi-OSINT com Tavily, fontes oficiais, sites corporativos e páginas profissionais públicas indexadas;
- extração de e-mails, telefones, LinkedIn e interlocutores/decisores potenciais, sempre com fonte e condição de evidência;
- relatório em Markdown, JSON e PDF.
- pré-validação CNPJ–razão social com bloqueio de pesquisa profunda em caso de conflito corroborado;
- pesquisa em oito camadas: estrutura societária, contexto institucional, contatos públicos, litigância fiscal, documentos oficiais, demonstrações financeiras, governança e eventos recentes;
- blacklist global de agregadores, compatibilidade entre fonte e camada e limite de repetição por domínio;
- cobertura por camada e estado de resolução da identidade visíveis na interface, no JSON e no PDF.
- produto final estruturado em 12 blocos: empresa, grupo econômico, decisores, contatos, finanças, sinais tributários, sinais judiciais, garantias, eventos recentes, hipóteses securitárias, lacunas e próximo movimento comercial.

## Fluxo canônico

```text
Processo -> Movimentação -> Evento econômico -> Hipótese de garantia
         -> Empresa -> Decisores e influenciadores -> Evidências -> Canais
         -> Abordagem consultiva
```

A consulta apenas por CNPJ continua disponível como `ENTITY_ENRICHMENT_ONLY`.
Ela não transforma uma empresa em oportunidade sem origem processual.

## Instalação

```powershell
python -m pip install -e ".[dev]"
```

Para habilitar a descoberta Tavily no motor:

```powershell
python -m pip install -e ".[dev,research]"
$env:TAVILY_API_KEY = 'sua-chave'
```

A chave fica somente no ambiente. Ela não é gravada no banco, nos relatórios ou nos logs do projeto.

## Construção do grafo no navegador

Inicie o servidor:

```powershell
$env:PYTHONPATH = 'src'
uvicorn vf_osint.api:app --reload --port 8000
```

Abra `http://127.0.0.1:8000/`. O formulário principal recebe processo, tribunal, empresa, CNPJ, evento, data, valor, URL e trecho da fonte. O enriquecimento Tavily é opcional e ocorre somente depois que o processo foi estruturado. A consulta isolada por CNPJ permanece em uma seção auxiliar.

O resultado mostra contatos e interlocutores e oferece **Abrir relatório PDF**. Um cargo localizado é apresentado como decisor/interlocutor potencial até existir evidência suficiente do poder decisório.

## Pesquisa por CNPJ na linha de comando

```powershell
python -m vf_osint.cli --database data\casos_osint.db search-cnpj `
  --cnpj 07.640.726/0001-38 `
  --legal-name "INFOTEL IMPORTACAO E DISTRIBUICAO LTDA" `
  --state SP `
  --deep `
  --output-dir output\casos
```

Cada execução produz o conjunto `{cnpj}_dossie.md`, `{cnpj}_dossie.json` e `{cnpj}_dossie.pdf`.

## Ingestão process-first na linha de comando

Copie e revise `examples/process_graph_input.example.json`, mantendo sempre a
URL e o trecho originais da fonte:

```powershell
python -m vf_osint.cli --database data\casos_osint.db ingest-process-graph `
  --input examples\process_graph_input.example.json `
  --output-dir output\grafos
```

Para enriquecer a empresa depois da criação do processo:

```powershell
python -m vf_osint.cli --database data\casos_osint.db ingest-process-graph `
  --input examples\process_graph_input.example.json `
  --enrich-tavily `
  --output-dir output\grafos
```

São produzidos o snapshot completo e o lote parametrizado para Neo4j.

## Caso INFOTEL sem coleta externa

```powershell
python -m vf_osint.cli --database data\vf_osint.db ingest-legacy `
  --seed examples\infotel_seed.json `
  --input examples\infotel_legacy_excerpt.txt

python -m vf_osint.cli --database data\vf_osint.db build-dossier `
  --seed examples\infotel_seed.json `
  --output output\infotel_dossier.md
```

O resultado inicial deve ficar como `MONITORAR`: o painel legado é uma pista, não prova do ato processual.

## Coleta pública

Revise `examples/infotel_sources.json` e execute:

```powershell
python -m vf_osint.cli --database data\vf_osint.db crawl `
  --seed examples\infotel_seed.json `
  --sources examples\infotel_sources.json
```

Fontes bloqueadas por política, `robots.txt`, conteúdo não suportado ou resposta excessiva aparecem em `rejected`; o motor não tenta contornar a restrição.

## API

```powershell
uvicorn vf_osint.api:app --reload
```

- `POST /api/investigations`
- `POST /api/search/cnpj`
- `POST /api/graph/processes`
- `GET /api/graphs/{graph_id}`
- `GET /api/graphs/process/{process_number}`
- `GET /api/graphs/{graph_id}/neo4j`
- `GET /api/graphs/{graph_id}/crm-projection`
- `POST /api/graphs/{graph_id}/feedback`
- `GET /api/dossiers/{dossier_id}`
- `GET /api/dossiers/{dossier_id}/report.pdf`
- `POST /api/dossiers/{dossier_id}/feedback`

## Camadas OSINT

- **Validação de identidade:** conflito corroborado entre CNPJ e razão social interrompe a pesquisa profunda e bloqueia a abordagem; ausência de confirmação permanece `UNRESOLVED`.
- **Estrutura societária:** grupo econômico, controle, controladas, subsidiárias, coligadas, filiais e unidades operacionais, sem presumir vínculo além do trecho publicado.
- **Contexto institucional:** atos, contratos públicos, licitações, incentivos e relações com órgãos públicos.
- **Contatos públicos:** e-mails, telefones, formulários, RI, jurídico e referências em procurações ou documentos protocolados.
- **Litigância fiscal:** crédito tributário, auto de infração, tributos, depósito, penhora e garantia judicial.
- **Documentos oficiais:** somente fontes governamentais, registros e tribunais nessa camada.
- **Demonstrações financeiras:** balanços, notas explicativas, contingências, provisões e relatórios anuais.
- **Governança e decisores:** financeiro, jurídico, compliance, tesouraria, RI e assessores tributários; LinkedIn é aceito apenas aqui.
- **Eventos recentes:** decisões, publicações e movimentos públicos com data do evento separada da data de captura.
- **Descoberta indexada:** Tavily executa consultas curtas e separadas por objetivo, com blacklist enviada à API e reaplicada localmente.
- **Fontes oficiais:** domínios `.gov.br` e `.jus.br` recebem classificação própria, sem promover agregadores a fontes oficiais.
- **Site corporativo:** map/crawl focado em contato, equipe, diretoria, jurídico, financeiro e governança.
- **LinkedIn:** somente URLs e trechos profissionais públicos/indexados; o crawler não tenta acessar conteúdo condicionado a login.
- **Crawler direto:** HTML e PDF, `robots.txt`, intervalo por domínio, limite de tamanho, profundidade e total de páginas.
- **Reconciliação:** um contato ou cargo heurístico não é confirmado apenas porque apareceu em uma página oficial; duas fontes independentes podem corroborar e divergências permanecem visíveis.
- **Atribuição local:** processo, telefone, e-mail e pessoa exigem CNPJ, razão social exata ou todos os elementos distintivos no mesmo trecho; documentos longos não combinam sobrenomes ou contatos dispersos.
- **Garantia:** uma menção sem número de processo e fonte oficial permanece hipótese; o motor não presume existência, modalidade, substituição ou aceitação judicial.

## Produto final em 12 blocos

O JSON, o Markdown, a interface e o PDF organizam a leitura em: Empresa; Grupo Econômico; Decisores; Contatos Públicos; Estrutura Financeira; Sinais Tributários; Sinais Judiciais; Garantias Identificadas; Eventos Recentes; Hipóteses Securitárias; Lacunas; e Próximo Movimento Comercial. Cada evidência estruturada preserva URL, título, tipo da fonte, data, trecho original, confiança e classificação.

## Estrutura de regras

- `config/business_rules.yaml`: avanço, abordagem e governança comercial.
- `config/market_rules.yaml`: aderência, subscrição, clausulado, capacidade e limites de promessa.
- `config/source_registry.json`: catálogo inicial de fontes oficiais e sua finalidade.

Veja [docs/ARQUITETURA.md](docs/ARQUITETURA.md) para o contrato completo do dossiê.
Veja também [docs/GRAPH_INTELLIGENCE.md](docs/GRAPH_INTELLIGENCE.md) para o modelo process-first, scores, relações, histórico e limites de implementação.
