# Publicar o engine (para o botão "Gerar dossiê" do CRM)

O engine é FastAPI. O CRM chama `POST /api/search/cnpj`. Para funcionar,
o engine precisa de uma URL pública. Caminho recomendado: **Render (free)**.

## Render (grátis, deploy do GitHub)
1. Acesse https://render.com e faça login (GitHub).
2. **New > Blueprint** e selecione o repositório `VF_GRAPH_INTELLIGENCE`.
   O `render.yaml` já configura tudo (Docker, health check).
3. Em **Environment**, defina `TAVILY_API_KEY` = sua chave Tavily.
4. Deploy. Ao final, você recebe uma URL tipo
   `https://vf-graph-intelligence.onrender.com`.
5. Teste: `GET /health` deve retornar `{"status":"ok"}`.

Alternativas equivalentes: Railway, Fly.io, Google Cloud Run (todos leem o Dockerfile).

## Ligar ao CRM
Depois de ter a URL, defina no CRM (Vercel) a variável:
`NEXT_PUBLIC_ENGINE_URL=https://vf-graph-intelligence.onrender.com`
O botão "Gerar dossiê" passa a chamar o engine completo (Tavily + OSINT + grafo)
em vez da versão leve da Receita, e ingere a projeção no Supabase.

## Local (teste rápido)
```
python -m pip install -e ".[research]"
export TAVILY_API_KEY=sua-chave
uvicorn vf_osint.api:app --reload --port 8000
```
