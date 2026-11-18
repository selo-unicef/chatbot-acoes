/* Chat NUCA v2: streamed SSE sobre fetch POST, markdown seguro (sem HTML de terceiros). */
const i18n = {
  pt: {
    title:'O que você gostaria de descobrir?',
    subtitle:'Descubra iniciativas, compare resultados e conheça o trabalho dos adolescentes. Pergunte do seu jeito.',
    status:'Pronto para conversar',waiting:'Pensando na resposta...',error:'Não foi possível concluir a resposta.',
    input:'Pergunte sobre ações, municípios ou resultados...',
    hint:'As respostas são baseadas nos registros dos NUCAs e podem conter imprecisões.',
    copy:'Copiar resposta',copied:'Copiado!',sources:'Registros relacionados',stop:'Interromper',send:'Enviar',
    q1:'Quais ações sobre "Resiliência Climática - #EntreNoClimaUNICEF" foram realizadas?',
    q2:'Quais os tipos de ações os NUCAs mais realizaram?',
    q3:'Quantas ações foram registradas no Brasil?',
    q4:'Me dê ideias para mobilizar adolescentes nos NUCAs.'
  },
  en: {
    title:'What would you like to discover?',
    subtitle:'Explore initiatives, compare results and learn what young people are doing. Ask in your own words.',
    status:'Ready to chat',waiting:'Thinking...',error:'Unable to complete the answer.',
    input:'Ask about activities, municipalities or results...',
    hint:'Answers are based on NUCA records and may contain inaccuracies.',
    copy:'Copy answer',copied:'Copied!',sources:'Related activity records',stop:'Stop',send:'Send',
    q1:'Which environmental activities were carried out?',
    q2:'What have NUCAs done in Pernambuco?',
    q3:'How many activities were recorded in Brazil?',
    q4:'Give me ideas to engage adolescents.'
  }
};
const $ = sel => document.querySelector(sel);
const state = {language:'pt',history:[],loading:false,abort:null,epoch:0};
const ui = {form:$('#chatForm'),input:$('#messageInput'),send:$('#sendButton'),conversation:$('#conversation'),messages:$('#messages'),welcome:$('#welcomePanel'),status:$('#statusText'),reset:$('#resetButton')};
const tx = k => i18n[state.language][k];
function scrollDown(){ ui.conversation.scrollTop = ui.conversation.scrollHeight; }
function resize(){ui.input.style.height='auto';ui.input.style.height=`${Math.min(ui.input.scrollHeight,140)}px`;}
function setLanguage(lang){state.language=lang==='en'?'en':'pt';document.documentElement.lang=lang==='en'?'en':'pt-BR';
  $('#welcomeTitle').textContent=tx('title');$('#welcomeSubtitle').textContent=tx('subtitle');ui.input.placeholder=tx('input');$('#composerHint').textContent=tx('hint');if(!state.loading) ui.status.textContent=tx('status');
  document.querySelectorAll('[data-i18n]').forEach(node=>node.textContent=tx(node.dataset.i18n));
  document.querySelectorAll('.lang-btn').forEach(b=>{const active=b.dataset.language===state.language;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));});
}
function setBusy(v){state.loading=v;ui.form.classList.toggle('busy',v);ui.status.textContent=v?tx('waiting'):tx('status');ui.send.setAttribute('aria-label',v?tx('stop'):tx('send'));ui.input.disabled=v;}
function makeMessage(role,content=''){
  const wrap=document.createElement('article');wrap.className=`message ${role}`;
  if(role==='user') {const body=document.createElement('div');body.className='message-content';body.textContent=content;wrap.append(body);}
  else {const avatar=document.createElement('img');avatar.className='mini-avatar';avatar.src='/static/img/avatar-nuca.svg';avatar.alt='';
    const main=document.createElement('div');main.className='message-main';const name=document.createElement('div');name.className='assistant-name';name.textContent='NUCA';
    const body=document.createElement('div');body.className='message-text';main.append(name,body);wrap.append(avatar,main);wrap.body=body;wrap.main=main;
  }
  ui.messages.append(wrap);scrollDown();return wrap;
}
function typingStart(node){const typing=document.createElement('span');typing.className='typing';typing.setAttribute('role','status');typing.setAttribute('aria-label',tx('waiting'));
  for(let i=0;i<3;i++)typing.append(document.createElement('span'));node.body.append(typing);return typing;}
function appendInline(parent,text){
  // Markdown reduzido, construído com DOM seguro (sem innerHTML para conteúdo da IA).
  const token=/(\*\*[^*]+\*\*|\[[^\]]+\]\(https:\/\/[^\s)]+\)|\*[^*\n]+\*)/g;
  let pos=0, m;
  while((m=token.exec(text))!==null){
    if(m.index>pos) parent.append(document.createTextNode(text.slice(pos,m.index)));
    const part=m[0];
    if(part.startsWith('**')){const strong=document.createElement('strong');strong.textContent=part.slice(2,-2);parent.append(strong);}
    else if(part.startsWith('*')){const em=document.createElement('em');em.textContent=part.slice(1,-1);parent.append(em);}
    else {const mm=part.match(/^\[([^\]]+)\]\((https:\/\/[^\s)]+)\)$/);
      if(mm){const a=document.createElement('a');a.textContent=mm[1];a.href=mm[2];a.rel='noopener noreferrer';a.target='_blank';parent.append(a);}
      else parent.append(document.createTextNode(part));
    }
    pos=m.index+part.length;
  }
  if(pos<text.length) parent.append(document.createTextNode(text.slice(pos)));
}
function renderMarkdown(target,raw){
  const frag=document.createDocumentFragment(), lines=raw.split('\n');let list=null,ordered=false;
  for(let i=0;i<lines.length;i++){
    const line=lines[i];if(!line.trim()){list=null;continue;}
    if(/^\s*\|.+\|\s*$/.test(line)&&i+1<lines.length && /^\s*\|?\s*:?-{3,}:?/.test(lines[i+1])){
      const table=document.createElement('table');const head=document.createElement('thead');const hr=document.createElement('tr');
      const cells=str=>str.trim().replace(/^\||\|$/g,'').split('|').map(x=>x.trim());
      for(const value of cells(line)){const th=document.createElement('th');appendInline(th,value);hr.append(th);}head.append(hr);table.append(head);
      const tbody=document.createElement('tbody');i++;
      while(i+1<lines.length && /^\s*\|.+\|\s*$/.test(lines[i+1])){
        const row=document.createElement('tr');for(const value of cells(lines[++i])){const td=document.createElement('td');appendInline(td,value);row.append(td);}tbody.append(row);
      }table.append(tbody);const container=document.createElement('div');container.className='table-scroll';container.append(table);frag.append(container);list=null;continue;
    }
    const heading=line.match(/^\s*(#{1,4})\s+(.+)/);
    if(heading){const h=document.createElement(heading[1].length<3?'h3':'h4');appendInline(h,heading[2]);frag.append(h);list=null;continue;}
    const bullet=line.match(/^\s*([-*]|\d+\.)\s+(.+)$/);
    if(bullet){const nextOrdered=!['-','*'].includes(bullet[1]);if(!list||ordered!==nextOrdered){list=document.createElement(nextOrdered?'ol':'ul');frag.append(list);ordered=nextOrdered;}
      const li=document.createElement('li');appendInline(li,bullet[2]);list.append(li);continue;}
    list=null;const p=document.createElement('p');appendInline(p,line);frag.append(p);
  }
  target.replaceChildren(frag);
}
function addSources(msg,sources){sources=(sources||[]).filter(s=>{try{const url=new URL(s.url);return url.protocol==='https:' && !url.hostname.endsWith('.pages.dev') && !url.pathname.endsWith('/manifest.json');}catch{return false;}});if(!sources.length)return;const container=document.createElement('div');container.className='source-list';
  const caption=document.createElement('div');caption.className='source-caption';caption.textContent=tx('sources');container.append(caption);
  for(const s of sources){const chip=document.createElement(s.url?'a':'div');chip.className='source-chip';if(s.url){chip.href=s.url;chip.target='_blank';chip.rel='noopener noreferrer';}
    const n=document.createElement('span');n.className='source-index';n.textContent=`[${s.ref}]`;const label=document.createElement('span');label.textContent=s.title||'Registro';
    chip.title=[s.title,s.municipality,s.uf,s.year].filter(Boolean).join(' · ');chip.append(n,label);container.append(chip);
  }msg.main.append(container);
}
function addCopy(msg,content){const tools=document.createElement('div');tools.className='message-tools';const btn=document.createElement('button');btn.type='button';btn.className='copy-btn';btn.textContent=tx('copy');
  btn.onclick=async()=>{try{await navigator.clipboard.writeText(content);btn.textContent=tx('copied');setTimeout(()=>{btn.textContent=tx('copy');},1500);}catch{btn.textContent=tx('copy');}};
  tools.append(btn);msg.main.append(tools);
}
// Parsing SSE por delimitador de evento (resiste a chunks TCP arbitrários).
async function consumeStream(response,onEvent){if(!response.body)throw new Error('O navegador não suporta streaming.');
  const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';
  function handle(raw){const lines=raw.replace(/\r/g,'').split('\n');let type='message',parts=[];for(const line of lines){if(line.startsWith('event:'))type=line.slice(6).trim();if(line.startsWith('data:'))parts.push(line.slice(5).trim());}
    if(parts.length){try{onEvent(type,JSON.parse(parts.join('\n')));}catch(e){if(e?.message!=='STOP_EVENT')console.warn('Evento ignorado',e);}}
  }
  try{while(true){const {done,value}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});buffer=buffer.replace(/\r\n/g,'\n');let idx;while((idx=buffer.indexOf('\n\n'))!==-1){handle(buffer.slice(0,idx));buffer=buffer.slice(idx+2);}}buffer+=decoder.decode();if(buffer.trim())handle(buffer);}finally{reader.releaseLock();}
}
async function send(raw){const q=String(raw||'').trim();if(!q||state.loading)return;const currentEpoch=state.epoch;const history=state.history.slice(-10);
  ui.welcome.hidden=true;makeMessage('user',q);ui.input.value='';resize();
  const answer=makeMessage('assistant');const typing=typingStart(answer);setBusy(true);state.abort=new AbortController();let whole='',sources=[],hadError=false;
  try{
    const response=await fetch('/api/chat/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:q,language:state.language,history}),signal:state.abort.signal});
    if(!response.ok){const body=await response.json().catch(()=>({}));throw new Error(body.error||tx('error'));}
    await consumeStream(response,(event,data)=>{
      if(event==='meta'){sources=data.sources||[];}
      else if(event==='delta'){whole+=data.text||'';typing.remove();renderMarkdown(answer.body,whole);scrollDown();}
      else if(event==='error'){hadError=true;whole+='\n\n'+(data.message||tx('error'));typing.remove();renderMarkdown(answer.body,whole);}
    });
    if(!whole&&!hadError)whole=tx('error');
  }catch(e){typing.remove();if(e.name==='AbortError'){whole=whole|| (state.language==='pt'?'Resposta interrompida.':'Answer stopped.');}
    else{hadError=true;whole=whole?`${whole}\n\n${e.message}`:e.message||tx('error');}}
  finally{if(state.epoch!==currentEpoch)return;typing.remove();renderMarkdown(answer.body,whole);addSources(answer,sources);addCopy(answer,whole);
    if(!hadError){state.history.push({role:'user',content:q},{role:'assistant',content:whole});state.history=state.history.slice(-14);}
    state.abort=null;setBusy(false);ui.input.focus();scrollDown();}
}
ui.form.addEventListener('submit',e=>{e.preventDefault();if(state.loading){state.abort?.abort();return;}send(ui.input.value);});
ui.input.addEventListener('input',resize);ui.input.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();if(!state.loading)send(ui.input.value);}});
document.querySelectorAll('.suggestion').forEach(b=>b.addEventListener('click',()=>send(tx(b.dataset.key))));
document.querySelectorAll('.lang-btn').forEach(b=>b.addEventListener('click',()=>setLanguage(b.dataset.language)));
ui.reset.addEventListener('click',()=>{state.epoch++;state.abort?.abort();state.loading=false;state.abort=null;ui.form.classList.remove('busy');ui.input.disabled=false;state.history=[];ui.messages.replaceChildren();ui.welcome.hidden=false;ui.input.value='';resize();ui.input.focus();});
setLanguage('pt');resize();
