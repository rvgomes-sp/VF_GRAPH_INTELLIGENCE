# V&F Graph Intelligence System - Guia de publicação no GitHub

Este pacote contém o código-fonte publicável do VF OSINT, suas regras, testes,
documentação técnica e exemplos sem credenciais.

## Finalidade

O sistema organiza processos, movimentações, empresas, pessoas, documentos,
contatos e hipóteses securitárias em um grafo auditável. O processo é a origem;
o CNPJ orienta o enriquecimento posterior em oito camadas:

1. estrutura societária e grupo econômico;
2. contexto institucional;
3. contatos públicos;
4. sinais de litigância fiscal;
5. documentos oficiais;
6. demonstrações financeiras;
7. governança e decisores;
8. eventos recentes.

O dossiê consolida a investigação em 12 blocos auditáveis, preservando para
cada evidência a URL, o tipo de fonte, a data, o trecho original, a confiança e
a classificação.

O pacote também contém snapshots temporais locais, exportação Cypher
parametrizada para Neo4j e projeção operacional pronta para futura sincronização
com um CRM/Supabase. Nenhuma conexão externa é publicada no pacote.

Os resultados são classificados como `CONFIRMADO`, `CORROBORADO`, `HIPÓTESE`,
`CONFLITO` ou `LACUNA`. Nenhuma hipótese libera automaticamente uma abordagem
comercial.

## Conteúdo do pacote

- `src/vf_osint/`: motor, API, interface, coleta e geração dos dossiês;
- `tests/`: testes de identidade, atribuição, evidência, API e persistência;
- `config/`: regras de negócio, mercado e catálogo de fontes;
- `docs/`: arquitetura e limites do sistema;
- `examples/`: exemplos de entrada sem credenciais;
- `tools/`: utilitários auxiliares;
- `README.md`: instalação e utilização;
- `pyproject.toml`: dependências e configuração do pacote.

## Arquivos deliberadamente excluídos

Por segurança e privacidade, este pacote não inclui:

- bancos SQLite ou checkpoints locais;
- relatórios e PDFs de empresas pesquisadas;
- diretórios `output/`, `data/`, `.tmp/`, `tmp/` e `.artifacts/`;
- `.git/`, caches, bytecode e metadados de instalação;
- chaves Tavily ou outras credenciais.

Mantenha essas exclusões ao publicar o repositório.

## Instalação

No PowerShell, dentro da pasta clonada:

```powershell
python -m pip install -e ".[dev,research]"
$env:TAVILY_API_KEY = 'sua-chave-local'
```

A chave deve existir somente no ambiente da máquina ou em um secret do serviço
de hospedagem. Nunca grave o valor no GitHub.

## Executar os testes

```powershell
$env:PYTHONPATH = 'src'
python -m pytest -q
```

## Executar a interface local

```powershell
$env:PYTHONPATH = 'src'
uvicorn vf_osint.api:app --reload --port 8000
```

Abra `http://127.0.0.1:8000/`.

## Publicar em um repositório novo

Crie primeiro um repositório vazio no GitHub. Depois execute, dentro desta
pasta, substituindo a URL de exemplo:

```powershell
git init
git add .
git commit -m "Publica V&F Graph Intelligence v0.5.0"
git branch -M main
git remote add origin https://github.com/SEU-USUARIO/vf-osint.git
git push -u origin main
```

Antes do `git push`, confirme:

```powershell
git status
git diff --cached --stat
git ls-files | Select-String -Pattern '\.db$|\.sqlite|output/|\.env$'
```

O último comando não deve retornar bancos, relatórios operacionais ou arquivos
de ambiente.

## Regras que não devem ser removidas

- conflito corroborado entre CNPJ e razão social bloqueia a pesquisa profunda;
- identidade não confirmada mantém a oportunidade como não enviável;
- agregadores não promovem fatos a confirmados;
- LinkedIn é fonte profissional pública, não comprovação autônoma de vínculo;
- processos e contatos exigem identidade no mesmo trecho da evidência;
- menção a garantia sem número de processo e fonte oficial permanece hipótese;
- consulta apenas por CNPJ é enriquecimento e não cria oportunidade processual;
- toda relação do grafo exige fonte, trecho, score e justificativa;
- feedback comercial gera novo snapshot e não sobrescreve a história;
- o motor não contorna login, CAPTCHA, `robots.txt` ou restrições de acesso;
- seguro garantia depende de validação jurídica, técnica e de mercado.
