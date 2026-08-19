# Arquitetura do VF OSINT

## Objetivo

Transformar processo, movimentação e evento econômico em um grafo fiscal auditável. O dossiê e a visão comercial são projeções desse grafo, condicionadas à qualidade da evidência.

## Fluxo

1. **Origem processual**: processo e movimentação entram com fonte, trecho, data e empresa afetada. Sem processo, a consulta é apenas enriquecimento de entidade.
2. **Resolução da entidade**: CNPJ é a chave empresarial; razão social e nome fantasia são aliases que precisam permanecer coerentes com a fonte processual.
3. **Descoberta**: depois do processo, o CNPJ gera consultas separadas por estrutura societária, contexto institucional, contatos, exposição fiscal, documentos oficiais, finanças, governança e eventos recentes. Resultados indexados são filtrados pela identidade antes da extração, com preferência por registros, tribunais, diários, CVM, B3, CARF, PGFN e sites corporativos.
4. **Mapeamento e crawling**: o crawler visita apenas URLs permitidas, observa `robots.txt`, limita profundidade, volume e frequência e não contorna autenticação, captcha ou bloqueios.
5. **Extração em grafo**: cada achado gera entidade, relação e evidência. Data de publicação, captura e evento permanecem separadas.
6. **Reconciliação**: fonte oficial pode confirmar; duas fontes independentes podem corroborar; divergência vira conflito; derivação fica como hipótese.
7. **Snapshot**: cada ingestão ou feedback produz versão imutável; o estado anterior não é sobrescrito.
8. **Dossiê e CRM**: são projeções do grafo. O score mede qualidade e contexto da oportunidade, não probabilidade de fechamento.
9. **Abordagem**: a mensagem usa apenas fatos confirmados/corroborados. Sem isso, gera um roteiro interno de investigação.
10. **Aprendizagem**: feedback comercial vira nova evidência operacional, jamais reescreve regras protegidas.

## Contrato do dossiê de referência

| Bloco | Campos obrigatórios |
|---|---|
| Entidade | razão social, CNPJ, situação, capital e data/fonte |
| Processo | número CNJ, tribunal, origem, papel da empresa, natureza, fase, evento, data do evento e data da publicação |
| Exposição | valor atualizado, obrigação, beneficiário, prazo e gatilho |
| Garantia | localizada, não localizada ou não confirmável; modalidade, vigência e clausulado quando houver |
| Pessoas | nome, papel profissional público, LinkedIn indexado, origem, data e condição de interlocutor/decisor potencial |
| Contatos | e-mail e telefone empresariais publicados, tipo, fonte, trecho, confiança e condição de uso |
| Mercado | hipótese de produto, validações, capacidade, contragarantia e apetite vigente |
| Comercial | urgência comprovada, persona, mensagem, classificação e próxima ação |
| Evidência | URL, título, trecho, hash, captura, classe, confiança, status e conflitos |

O produto executivo expõe os mesmos achados em 12 blocos fixos: Empresa; Grupo Econômico; Decisores; Contatos Públicos; Estrutura Financeira; Sinais Tributários; Sinais Judiciais; Garantias Identificadas; Eventos Recentes; Hipóteses Securitárias; Lacunas; e Próximo Movimento Comercial.

## Regras de negócio

- O CRM define funil, requisitos para avançar, linguagem, personas e revisão humana.
- Administrador, diretor ou sócio encontrado em fonte pública é interlocutor/decisor potencial, não poder decisório confirmado.
- Score legado é preservado como histórico e não participa do score de evidência.
- Abordagem externa exige revisão humana e fonte oficial do evento processual citado.

## Regras de mercado

- Aderência securitária depende da obrigação, fase, valor, prazo, gatilho, beneficiário e objetivo do tributarista.
- Apetite de seguradora é temporal e deve ter fonte e data.
- Direcionamento à Zurich ou qualquer outra seguradora nunca é automático.
- Não há promessa de aceitação judicial, aprovação de crédito ou emissão de apólice.

## Privacidade e limites de coleta

- Apenas informação pública necessária à finalidade empresarial legítima.
- Pessoas são tratadas por papel profissional; dados pessoais sensíveis e contatos pessoais não são coletados.
- LinkedIn e redes sociais com acesso condicionado ficam fora do crawler padrão.
- Links de compartilhamento social são descartados; somente páginas `/company/` e `/in/` indexadas entram como pistas.
- E-mails e telefones só são extraídos quando o contexto local resolve o CNPJ/nome ou quando a página pertence a um site corporativo já resolvido.
- O motor não burla captcha, login, rate limit, bloqueio técnico ou `robots.txt`.
- Conteúdo bruto é armazenado por hash e pode ser submetido a política de retenção.
