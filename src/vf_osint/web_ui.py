from __future__ import annotations


SEARCH_PAGE = r"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>V&F — Grafo de Oportunidade Fiscal</title>
  <style>
    :root { --ink:#1b1820; --purple:#75628a; --coral:#b56e5a; --paper:#f7f4ef; --line:#d9d2c9; --muted:#6c6872; --ok:#2f7658; }
    * { box-sizing:border-box; }
    body { margin:0; color:var(--ink); background:linear-gradient(135deg,#f7f4ef 0%,#ece4ef 100%); font-family:Inter,Segoe UI,Arial,sans-serif; min-height:100vh; }
    header { padding:24px clamp(20px,5vw,72px); border-bottom:1px solid var(--line); background:rgba(247,244,239,.9); }
    header span { color:var(--purple); font-size:12px; font-weight:800; letter-spacing:.16em; }
    header h1 { margin:8px 0 0; font-size:clamp(24px,4vw,42px); letter-spacing:-.035em; }
    main { width:min(1120px,calc(100% - 32px)); margin:32px auto 64px; display:grid; grid-template-columns:minmax(300px,420px) 1fr; gap:24px; align-items:start; }
    .panel { background:rgba(255,255,255,.92); border:1px solid var(--line); border-radius:18px; box-shadow:0 16px 40px rgba(50,38,60,.08); padding:24px; }
    h2 { margin:0 0 8px; font-size:20px; } p { color:var(--muted); line-height:1.5; }
    label { display:block; margin:16px 0 6px; font-size:12px; font-weight:800; text-transform:uppercase; letter-spacing:.08em; color:var(--purple); }
    input[type=text],input[type=url],input[type=number],input[type=date],select,textarea { width:100%; border:1px solid var(--line); border-radius:10px; padding:11px 12px; font-size:14px; background:white; outline:none; font-family:inherit; }
    textarea { min-height:88px; resize:vertical; }
    input:focus { border-color:var(--purple); box-shadow:0 0 0 3px rgba(117,98,138,.12); }
    .row { display:grid; grid-template-columns:1fr 90px; gap:12px; }
    .check { margin:18px 0; display:flex; gap:10px; align-items:flex-start; color:var(--ink); font-size:14px; line-height:1.35; }
    button,.button { display:inline-flex; align-items:center; justify-content:center; width:100%; border:0; border-radius:11px; padding:14px 18px; background:var(--ink); color:white; font-weight:800; text-decoration:none; cursor:pointer; }
    button:hover,.button:hover { background:var(--purple); }
    button:disabled { opacity:.55; cursor:wait; }
    .status { margin-top:16px; padding:12px; border-radius:10px; background:var(--paper); color:var(--muted); font-size:13px; }
    .status.busy { color:var(--purple); } .status.ok { color:var(--ok); }
    .result { min-height:420px; }
    .empty { display:grid; place-items:center; min-height:370px; text-align:center; color:var(--muted); }
    .hero { background:var(--ink); color:white; border-radius:14px; padding:20px; }
    .hero small { color:#d8cbe2; } .hero h2 { font-size:25px; margin:6px 0; } .hero p { color:#e9e2ec; margin:0; }
    .metrics { display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin:14px 0; }
    .metric { padding:13px; border:1px solid var(--line); border-radius:12px; background:white; }
    .metric b { display:block; font-size:22px; } .metric span { color:var(--muted); font-size:11px; text-transform:uppercase; }
    .section { margin-top:18px; } .section h3 { font-size:14px; text-transform:uppercase; letter-spacing:.08em; color:var(--purple); }
    .identity { margin:14px 0; padding:13px; border-radius:12px; background:#eef6f1; border:1px solid #b9d8c8; }
    .identity.conflict { background:#fff0ed; border-color:#e0a79b; color:#7d2e26; }
    .layers { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
    .layer { padding:10px; border:1px solid var(--line); border-radius:10px; background:var(--paper); font-size:12px; }
    .layer b { display:block; margin-bottom:4px; }
    .chips { display:flex; flex-wrap:wrap; gap:8px; }
    .chip { max-width:100%; padding:8px 10px; background:var(--paper); border:1px solid var(--line); border-radius:999px; font-size:13px; overflow-wrap:anywhere; }
    .person { border-left:3px solid var(--coral); padding:8px 12px; margin:8px 0; background:var(--paper); }
    .actions { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin-top:20px; }
    .secondary { background:var(--purple); }
    details { margin-top:22px; border-top:1px solid var(--line); padding-top:18px; }
    summary { cursor:pointer; color:var(--purple); font-weight:800; }
    @media(max-width:850px){ main{grid-template-columns:1fr}.metrics{grid-template-columns:1fr 1fr}.result{min-height:0} }
  </style>
</head>
<body>
  <header><span>V&F GRAPH INTELLIGENCE SYSTEM</span><h1>Grafo de Oportunidade Fiscal</h1></header>
  <main>
    <section class="panel">
      <h2>Processo como origem</h2>
      <p>Registre a movimentação com sua fonte. O sistema conecta evento, empresa, decisores, documentos, canais e hipótese securitária.</p>
      <form id="graph-form">
        <label for="process-number">Número do processo</label>
        <input id="process-number" type="text" placeholder="5001234-22.2026.4.03.6100" required>
        <div class="row">
          <div><label for="tribunal">Tribunal</label><input id="tribunal" type="text" placeholder="TRF3"></div>
          <div><label for="source-system">Origem</label><select id="source-system"><option>DATAJUD</option><option>DJEN</option><option>TRIBUNAL</option><option>CARF</option><option>DIARIO_OFICIAL</option></select></div>
        </div>
        <label for="company-name">Empresa afetada</label>
        <input id="company-name" type="text" placeholder="Razão social" required>
        <label for="graph-cnpj">CNPJ</label>
        <input id="graph-cnpj" type="text" inputmode="numeric" placeholder="00.000.000/0000-00" required maxlength="18">
        <div class="row">
          <div><label for="event-type">Evento</label><select id="event-type"><option>Penhora</option><option>Bloqueio Judicial</option><option>Depósito Judicial</option><option>Execução Fiscal</option><option>Intimação</option><option>Sentença</option><option>Recurso</option><option>Despacho</option></select></div>
          <div><label for="event-date">Data</label><input id="event-date" type="date"></div>
        </div>
        <label for="amount">Valor discutido</label>
        <input id="amount" type="number" min="0" step="0.01" placeholder="0,00">
        <label for="source-url">URL da fonte</label>
        <input id="source-url" type="url" placeholder="https://..." required>
        <label for="source-excerpt">Trecho original da evidência</label>
        <textarea id="source-excerpt" required placeholder="Cole o trecho que conecta processo, evento e empresa."></textarea>
        <label class="check"><input id="enrich-tavily" type="checkbox"><span>Depois de estruturar o processo, enriquecer empresa, governança, documentos e contatos públicos com Tavily.</span></label>
        <button id="graph-submit" type="submit">Construir grafo de oportunidade</button>
      </form>
      <details>
        <summary>Enriquecimento auxiliar somente por CNPJ</summary>
        <p>Este fluxo não cria oportunidade processual; apenas prepara a entidade para futura conexão ao grafo.</p>
      <form id="search-form">
        <label for="cnpj">CNPJ</label>
        <input id="cnpj" name="cnpj" type="text" inputmode="numeric" placeholder="00.000.000/0000-00" required maxlength="18">
        <div class="row">
          <div><label for="legal-name">Razão social (opcional)</label><input id="legal-name" type="text" placeholder="Ajuda a refinar"></div>
          <div><label for="state">UF</label><input id="state" type="text" maxlength="2" placeholder="SP"></div>
        </div>
        <label class="check"><input id="deep" type="checkbox" checked><span>Pesquisa ampliada: busca por múltiplos ângulos, extração avançada e crawling focado em contato, equipe, diretoria, jurídico e financeiro.</span></label>
        <button id="submit" type="submit">Pesquisar e gerar relatório</button>
      </form>
      </details>
      <div id="status" class="status">Pronto para iniciar.</div>
    </section>
    <section id="result" class="panel result"><div class="empty"><div><h2>O grafo aparecerá aqui</h2><p>Processo é a origem; oportunidade securitária é o destino.</p></div></div></section>
  </main>
  <script>
    const form=document.querySelector('#search-form'), graphForm=document.querySelector('#graph-form'), statusBox=document.querySelector('#status'), result=document.querySelector('#result'), button=document.querySelector('#submit'), graphButton=document.querySelector('#graph-submit');
    const esc=(v)=>String(v??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
    const fmt=(value)=>{const d=value.replace(/\D/g,'').slice(0,14); return d.replace(/^(\d{2})(\d)/,'$1.$2').replace(/^(\d{2})\.(\d{3})(\d)/,'$1.$2.$3').replace(/\.(\d{3})(\d)/,'.$1/$2').replace(/(\d{4})(\d)/,'$1-$2')};
    document.querySelector('#cnpj').addEventListener('input',e=>e.target.value=fmt(e.target.value));
    document.querySelector('#graph-cnpj').addEventListener('input',e=>e.target.value=fmt(e.target.value));
    function contacts(d,bucket){return (d.contacts?.[bucket]||[]).map(x=>`<span class="chip">${esc(x.value)} · ${esc(x.status)}</span>`).join('')||'<span class="chip">NAO LOCALIZADO</span>'}
    function render(payload){const d=payload.dossier, p=d.provenance||{}, people=(d.interlocutors||[]).filter(x=>x.name), identity=d.identity_resolution||{}, layers=Object.values(d.evidence_layers||{}), blocks=Object.values(d.intelligence_blocks||{}); result.innerHTML=`
      <div class="hero"><small>${esc(d.organization.cnpj)}</small><h2>${esc(d.organization.legal_name)}</h2><p>${esc(d.executive_reading.thesis)}</p></div>
      <div class="metrics"><div class="metric"><b>${esc(d.executive_reading.evidence_score)}</b><span>evidência</span></div><div class="metric"><b>${p.confirmed_count||0}</b><span>confirmados</span></div><div class="metric"><b>${p.corroborated_count||0}</b><span>corroborados</span></div><div class="metric"><b>${people.length}</b><span>pessoas</span></div></div>
      <div class="identity ${identity.blocked?'conflict':''}"><b>Identidade: ${esc(identity.status||'UNRESOLVED')}</b><br>${esc(identity.rationale||'Validação pendente.')}</div>
      <div class="section"><h3>Cobertura por camadas</h3><div class="layers">${layers.map(x=>`<div class="layer"><b>${esc(x.label)}</b>${esc(x.coverage)} · ${x.documents||0} docs · ${(x.domains||[]).length} fontes</div>`).join('')}</div></div>
      <div class="section"><h3>Produto final em 12 blocos</h3><div class="layers">${blocks.map(x=>`<div class="layer"><b>${esc(x.title)}</b>${esc(x.status||'LACUNA')}</div>`).join('')}</div></div>
      <div class="section"><h3>E-mails públicos</h3><div class="chips">${contacts(d,'emails')}</div></div>
      <div class="section"><h3>Telefones públicos</h3><div class="chips">${contacts(d,'phones')}</div></div>
      <div class="section"><h3>LinkedIn</h3><div class="chips">${contacts(d,'linkedin')}</div></div>
      <div class="section"><h3>Formulários públicos</h3><div class="chips">${contacts(d,'forms')}</div></div>
      <div class="section"><h3>Decisores / interlocutores potenciais</h3>${people.map(x=>`<div class="person"><b>${esc(x.name)}</b><br>${esc(x.public_role)} · ${esc(x.evidence_status)}</div>`).join('')||'<div class="person">NAO LOCALIZADO</div>'}</div>
      <div class="actions"><a class="button" href="${esc(payload.report_pdf)}" target="_blank">Abrir relatório PDF</a><a class="button secondary" href="/api/dossiers/${esc(d.dossier_id)}" target="_blank">Ver dados JSON</a></div>`}
    function renderGraph(payload){const g=payload.graph, counts={};(g.nodes||[]).forEach(n=>counts[n.node_type]=(counts[n.node_type]||0)+1);const views=Object.keys(g.views||{});result.innerHTML=`
      <div class="hero"><small>${esc(g.origin_status)}</small><h2>${esc(g.opportunity_classification)}</h2><p>${esc(g.next_action)}</p></div>
      <div class="metrics"><div class="metric"><b>${esc(g.opportunity_score)}</b><span>score</span></div><div class="metric"><b>${(g.nodes||[]).length}</b><span>nós</span></div><div class="metric"><b>${(g.relationships||[]).length}</b><span>relações</span></div><div class="metric"><b>${(g.critical_events||[]).length}</b><span>eventos críticos</span></div></div>
      <div class="section"><h3>Nós estruturados</h3><div class="chips">${Object.entries(counts).map(([k,v])=>`<span class="chip">${esc(k)} · ${v}</span>`).join('')}</div></div>
      <div class="section"><h3>12 visões de decisão</h3><div class="layers">${views.map(k=>`<div class="layer"><b>${esc(k.replace(/^\d+_/,'').replaceAll('_',' '))}</b>estrutura calculada</div>`).join('')}</div></div>
      <div class="section"><h3>Lacunas de validação</h3><div class="chips">${(g.validation_gaps||[]).map(x=>`<span class="chip">${esc(x)}</span>`).join('')||'<span class="chip">SEM LACUNAS BLOQUEADORAS</span>'}</div></div>
      <div class="actions"><a class="button" href="${esc(payload.graph_url)}" target="_blank">Abrir grafo JSON</a><a class="button secondary" href="${esc(payload.neo4j_export_url)}" target="_blank">Exportar Neo4j</a><a class="button secondary" href="${esc(payload.crm_projection_url)}" target="_blank">Projeção CRM</a></div>`}
    graphForm.addEventListener('submit',async(e)=>{e.preventDefault();const cnpj=document.querySelector('#graph-cnpj').value.replace(/\D/g,'');if(cnpj.length!==14){statusBox.textContent='Digite os 14 números do CNPJ.';return}graphButton.disabled=true;statusBox.className='status busy';statusBox.textContent='Estruturando processo, evento, evidências e hipótese securitária…';const date=document.querySelector('#event-date').value;const amount=document.querySelector('#amount').value;const payload={enrich_tavily:document.querySelector('#enrich-tavily').checked,deep:true,process:{process_number:document.querySelector('#process-number').value,tribunal:document.querySelector('#tribunal').value||null,amount:amount?Number(amount):null,active:true,company_cnpj:cnpj,company_legal_name:document.querySelector('#company-name').value,events:[{event_type:document.querySelector('#event-type').value,event_date:date?date+'T12:00:00Z':null,description:document.querySelector('#source-excerpt').value}],source_system:document.querySelector('#source-system').value,source_url:document.querySelector('#source-url').value,source_excerpt:document.querySelector('#source-excerpt').value,evidence_score:75}};try{const response=await fetch('/api/graph/processes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const data=await response.json();if(!response.ok)throw new Error(data.detail||'Falha ao construir grafo');renderGraph(data);statusBox.className='status ok';statusBox.textContent='Grafo process-first criado e persistido como snapshot auditável.'}catch(error){statusBox.className='status';statusBox.textContent=error.message}finally{graphButton.disabled=false}});
    form.addEventListener('submit',async(e)=>{e.preventDefault(); const digits=document.querySelector('#cnpj').value.replace(/\D/g,''); if(digits.length!==14){statusBox.textContent='Digite os 14 números do CNPJ.';return} button.disabled=true;statusBox.className='status busy';statusBox.textContent='Pesquisando fontes, extraindo evidências e preparando o PDF…';
      try{const response=await fetch('/api/search/cnpj',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cnpj:digits,legal_name:document.querySelector('#legal-name').value||null,state:document.querySelector('#state').value.toUpperCase()||null,deep:document.querySelector('#deep').checked,use_tavily:true})}); const payload=await response.json(); if(!response.ok)throw new Error(payload.detail||'Falha na pesquisa'); render(payload); statusBox.className='status ok'; statusBox.textContent=`Concluído: ${payload.collection.documents} documentos e ${payload.collection.claims_extracted} achados extraídos.`}catch(error){statusBox.className='status';statusBox.textContent=error.message}finally{button.disabled=false}});
  </script>
</body>
</html>"""
