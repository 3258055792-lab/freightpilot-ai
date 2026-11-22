import {CHANNELS,FIELDS,extract,missing,customerFollowup,validateRule,compare,newCase,edit,confirmClassification,requestApproval,decide,reselectChannel,placeOrder,cloneCase,addEvent,log} from './core.js';
const KEY='freightpilot-workbench-v1';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const $=s=>document.querySelector(s), money=(n,c='CNY')=>new Intl.NumberFormat('zh-CN',{style:'currency',currency:c}).format(n), date=t=>new Date(t).toLocaleString('zh-CN',{hour12:false});
let saved;try{saved=JSON.parse(localStorage.getItem(KEY))}catch{}
let state=saved?.schema===1?saved:{schema:1,cases:[],rules:[],training:[],actor:'本机操作员',demoLoaded:false};
let view='overview',active=state.cases[0]?.id,filter={},demoStep=0,questionIndex=0,answer=-1,result=null;
const current=()=>state.cases.find(c=>c.id===active);
const persist=()=>{try{localStorage.setItem(KEY,JSON.stringify(state))}catch{throw Error('浏览器存储已满或被禁用，请立即导出备份');}};
const toast=t=>{$('#toast').textContent=t;$('#toast').style.display='block';clearTimeout(window.toastTimer);window.toastTimer=setTimeout(()=>$('#toast').style.display='none',4500)};
const badge=s=>`<span class="badge ${/拒绝|异常|不支持|停止/.test(s)?'bad':/待|人工询价|候选|草稿|未/.test(s)?'warn':/确认|已生成|可直接/.test(s)?'ok':'neutral'}">${esc(s)}</span>`;
const heading=(title,sub,buttons='')=>`<div class="heading"><div><div class="eyebrow">FREIGHT AGENT WORKSPACE</div><h1>${title}</h1><p>${sub}</p></div><div class="actions">${buttons}</div></div>`;
const btn=(text,action,primary=false,attrs='')=>`<button data-action="${action}" class="${primary?'primary':''}" ${attrs}>${text}</button>`;
const card=(title,body,right='')=>`<section class="card"><div class="card-head"><h2>${title}</h2>${right}</div><div class="card-body">${body}</div></section>`;
const selectOptions=(items,value,blank='待补充')=>`<option value="">${blank}</option>`+items.map(x=>`<option ${x===value?'selected':''} value="${esc(x)}">${esc(x)}</option>`).join('');
function brazilEstimate(c){
 const d=c?.serverDraft;
 if(!d?.quote || c.fields.country!=='巴西' || !['A','B'].includes(d.rate_source))return null;
 return d;
}
const baseChannel='巴西海运（基础运费）';
function selectedBase(c){
 const d=brazilEstimate(c);
 return !!d && c.baseSelectionVersion===d.version && internalReview(c)!=='内部草稿已拒绝';
}
function selectChannel(c,value){
 if(c.locked)throw Error('当前渠道已锁定');
 if(value===baseChannel){
  const d=brazilEstimate(c);
  if(!d || internalReview(c)==='内部草稿已拒绝')throw Error('当前版本没有可选的基础试算');
  c.selected='';
  c.baseSelectionVersion=d.version;
 }else{
  delete c.baseSelectionVersion;
  c.selected=value;
 }
}
function internalReview(c){
 const d=c.serverDraft,a=d.approval;
 if(a?.scope!=='internal_quote_review' || a.case?.id!==c.id || a.case.version!==d.version)return '内部草稿待送审';
 return ({PENDING:'内部草稿待审核',APPROVED:'内部草稿已批准',REJECTED:'内部草稿已拒绝'})[a.state]||'内部草稿待核对';
}
function steps(c){const labels=['客户询价','需求识别','渠道推荐','报价审批','生成试算','客户下单','物流跟踪'];const n=!c?0:c.events.length>1?6:c.order==='待订舱'?5:(c.trial==='已生成'||brazilEstimate(c))?4:c.approval==='待审批'?3:c.classification==='已确认'?2:c.text?1:0;return `<div class="steps">${labels.map((s,i)=>`<div class="step ${i<=n?'on':''}"><i>${i+1}</i>${s}</div>`).join('')}</div>`}
function caseTable(cases){return cases.length?`<div class="table-wrap"><table><thead><tr><th>案例 / 客户</th><th>目的国 · 渠道</th><th>分类</th><th>试算 / 报价</th><th>订单</th><th></th></tr></thead><tbody>${cases.map(c=>`<tr><td><button class="link-button" data-open="${c.id}">${esc(c.name)}</button><small>${c.demo?'演示案例 · ':''}${esc(c.id.slice(0,8))} · v${c.version}</small></td><td>${esc(c.fields.country||'待补充')}<small>${esc(selectedBase(c)?baseChannel:c.selected||'渠道待选择')}</small></td><td>${badge(c.classification)}</td><td>${badge(c.quote)}<small>${esc(c.trial)}</small></td><td>${badge(c.order)}</td><td><button class="quiet" data-clone="${c.id}" title="复制为新案例">↗ 复制</button></td></tr>`).join('')}</tbody></table></div>`:`<div class="empty">还没有案例。新建询价，或载入一组明确标注的演示数据。</div>`}
function overview(){const cases=state.cases, pending=cases.filter(c=>c.approval==='待审批').length;return heading('让每一次报价，都有据可循。','从一句客户询价，到一条可解释、可追踪、由人掌控的业务链路。',btn('＋ 新建询价','new',true)+(document.querySelector('meta[name=local-inquiries]')?'<button id="local-inquiry">本地千问询价</button>':''))+`<div class="hero"><div><span class="hero-tag">BUILT FOR YOUR NEXT DEMO</span><h2>货代全流程 Agent，准备就绪</h2><p>需求识别、渠道试算与物流跟踪集中管理。让不确定性可见，让每个关键判断可回溯。</p></div>${btn('进入比赛演示 ↗','demo')}</div><div class="stats">${[['询价案例',cases.length,'当前浏览器内的所有案例','▤'],['等待人工审批',pending,'每个版本仅允许一次审批','◷'],['已登记订单',cases.filter(c=>!['未下单','已停止'].includes(c.order)).length,'本地登记，不向承运商发送','⇢'],['异常待处理',cases.filter(c=>c.exception!=='无异常').length,'物流节点中人工标记的异常','⚑']].map(([t,n,h,i])=>`<div class="stat"><div class="stat-label">${t}<span>${i}</span></div><div class="stat-value">${n.toString().padStart(2,'0')}</div><div class="stat-foot">${h}</div></div>`).join('')}</div><div class="grid"><div><section class="card"><div class="card-head"><h2>最近的询价与订单</h2>${btn('查看全部 →','cases')}</div>${caseTable(cases.slice(0,5))}</section>${card('从识别到交付，关键节点由人掌控',steps(cases[0])+`<div class="notice green">分类确认 → 锁定版本 → 一次审批 → 生成试算。拒绝立即终止，已锁定内容通过复制新案例继续。</div>`)}</div><div>${card('工作台状态',`<div class="mini-route"><div class="route-icon">⌁</div><div class="grow"><h3>报价来源库</h3><div class="tiny">${state.rules.length} 条规则 · ${state.rules.filter(r=>!r.demo&&r.verified).length} 条真实来源已核验</div></div>${btn('管理','sources')}</div><div class="mini-route"><div class="route-icon">◎</div><div class="grow"><h3>人工控制点</h3><div class="tiny">分类确认 / 试算审批 / 客户确认记录</div></div>${badge('已启用')}</div><div class="mini-route"><div class="route-icon">↥</div><div class="grow"><h3>本机数据</h3><div class="tiny">保存在当前浏览器，可导出备份</div></div>${btn('导出','backup')}</div>`)}${card('先用演示案例走一遍',`<p>一票发往德国的普货，包含完整询价与六种渠道的示例规则。每个批准动作都需要你亲自点击。</p><div class="actions spaced">${btn(state.demoLoaded?'打开演示询价':'载入演示数据','load-demo',true)}${btn('销售训练','training')}</div>`)}<div class="tiny">当前版本：浏览器本地工作台。未连接真实运价系统、承运商或定位服务。审批日志为本地操作记录，不是身份认证或不可篡改审计。</div></div></div>`;}
function caseList(){let list=state.cases.filter(c=>(!filter.q||c.name.includes(filter.q)||c.text.includes(filter.q))&&(!filter.country||c.fields.country===filter.country)&&(!filter.order||c.order===filter.order)&&(!filter.channel||c.selected===filter.channel)&&(!filter.from||c.created.slice(0,10)>=filter.from)&&(!filter.to||c.created.slice(0,10)<=filter.to));return heading('询价与案例库','按客户、国家、订单状态、渠道和时间筛选。锁定版本可复制为新案例。',btn('＋ 新建询价','new',true)+(document.querySelector('meta[name=local-inquiries]')?'<button id="local-inquiry">本地千问询价</button>':''))+`<div class="filters"><input aria-label="搜索客户询价" data-filter="q" placeholder="搜索客户或询价文本" value="${esc(filter.q||'')}"><select aria-label="国家筛选" data-filter="country">${selectOptions([...new Set(state.cases.map(c=>c.fields.country).filter(Boolean))],filter.country,'所有国家')}</select><select aria-label="订单状态筛选" data-filter="order">${selectOptions([...new Set(state.cases.map(c=>c.order))],filter.order,'所有订单状态')}</select><select aria-label="渠道筛选" data-filter="channel">${selectOptions(CHANNELS,filter.channel,'所有渠道')}</select><input aria-label="开始日期" type="date" data-filter="from" value="${esc(filter.from||'')}"><input aria-label="结束日期" type="date" data-filter="to" value="${esc(filter.to||'')}">${btn('清除筛选','clear-filters')}</div><section class="card">${caseTable(list)}</section><p>显示 ${list.length} / ${state.cases.length} 个案例。报价单、订单和演示案例都通过「复制」建立独立的新流程。</p>`}
function followupPanel(c){
 if(!c.text?.trim()||c.locked)return '';
 const draft=customerFollowup(c.fields,c.text);
 if(!draft)return '<p>目前没有需要向客户补问的事项。</p>';
 return '<div class="spaced"><label>给客户的补充询问<textarea id="customer-followup" rows="4">'+esc(draft)+'</textarea></label><div class="actions">'+btn('复制补问给客户','copy-followup')+'</div></div>';
}
function customerNotes(c){
 const clauses=(c.text||'').split(/[，,。；;！？!?\n]+/).map(part=>part.trim()).filter(Boolean);
 const notes=clauses.filter(part=>/海运|空运|保险|包装|报[价價]|美元|欧元|人民币|费用/.test(part));
 return notes.length?'<details open><summary>运输与报价要求（客户原话）</summary><ul>'+notes.map(note=>'<li>'+esc(note)+'</li>').join('')+'</ul></details>':'';
}
function fieldInput(c,k){const value=c.fields[k],disabled=c.locked?'disabled':'';const opts={category:['普货','敏感货','危险品','待核验'],delivery:['到门','自提'],customs:['代理清关','自行清关'],tax:['DAP','DDP','双清包税','待核验']};return `<label>${FIELDS[k]}${opts[k]?`<select name="${k}" ${disabled}>${selectOptions(opts[k],value)}</select>`:`<input name="${k}" value="${esc(value)}" placeholder="待补充" ${['volume','weight','boxes','days'].includes(k)?'type="number" min="0.001" step="any"':''} ${disabled}>`}<span class="form-help">${c.evidence[k]?'原文：'+esc(c.evidence[k]):!value?'待补充':'人工填写 · 需核验'}</span></label>`}
function previousTrials(c){
 if(!c.previousTrials?.length)return '';
 const rows=c.previousTrials.map(({row,version})=>'<li>v'+esc(version)+' · '+esc(row.channel)+' · '+esc(money(row.total,row.rule.currency))+' '+esc(row.rule.currency)+'</li>');
 return '<div class="notice"><strong>之前的试算</strong><ul>'+rows.join('')+'</ul></div>';
}
function channels(c){const rows=compare(c.fields,state.rules);
 const estimate=brazilEstimate(c);
 const baseRow=estimate?`<tr><td><b>巴西海运</b><small>基础运费 · 模拟</small></td><td>${badge('已试算')}</td><td>${esc(estimate.quote.calculation)}<small>${esc(internalReview(c))} · 到门时效、派送及清关费用待核</small></td><td><label><input type="radio" aria-label="选择巴西海运基础运费" name="channel" value="${baseChannel}" ${selectedBase(c)?'checked':''} ${c.locked||internalReview(c)==='内部草稿已拒绝'?'disabled':''}>选择基础运费</label>${btn('查看试算记录','export')}</td></tr>`:'';
 return `<div class="table-wrap"><table><thead><tr><th>渠道</th><th>可计算性</th><th>规则覆盖 / 说明</th><th>选择</th></tr></thead><tbody>${baseRow}${rows.map(r=>`<tr><td><b>${r.channel}</b>${r.rule?`<small>${r.rule.transitDays} 天 · ${r.rule.currency}</small>`:''}</td><td>${badge(r.status)}</td><td><span class="tiny">${esc(r.reason)}</span>${r.rule?`<small>${esc(r.rule.file)} · ${esc(r.rule.sheet)}!${esc(r.rule.cell)}</small>`:''}</td><td><input type="radio" aria-label="选择${r.channel}" name="channel" value="${r.channel}" ${c.selected===r.channel?'checked':''} ${c.locked||r.status!=='可直接计算'?'disabled':''}></td></tr>`).join('')}</tbody></table></div><p class="tiny">比较目的国、货类、体积、重量、时效及派送/清关/税费需求。无法匹配时交由人工询价；不同币种不做未声明汇率的排名。</p>`}
function sourceTrace(c){const s=c.snapshot;if(!s)return `<div class="empty">申请审批后，输入、来源与计算规则会锁定到当前版本。</div>`;const r=s.row.rule;return `<dl class="sources"><dt>来源文件 · 工作表 · 单元格</dt><dd>${esc(r.file)} · ${esc(r.sheet)}!${esc(r.cell)}</dd><dt>来源版本 · SHA-256</dt><dd>${esc(r.version)}<br>${esc(r.sha)}</dd><dt>原始报价文件 · 原始 SHA-256</dt><dd>${esc(r.originalFile||r.file)}<br>${esc(r.originalSha||r.sha)}</dd><dt>规则 · 币种 · 附加费</dt><dd>计费量 × ${r.rate} ${r.currency}/${r.basis}；最低 ${r.min} ${r.currency}${r.basis==='kg'?`；计费重量=max(实重, 体积×${r.volumetricFactor||0})`:''}<br>${r.surcharges.map(x=>`${esc(x.name)} ${money(x.amount,r.currency)}`).join('；')||'无附加费'}</dd><dt>待确认项</dt><dd>${r.pending.length?r.pending.map(esc).join('；'):'无已登记项；实际业务仍需核验来源适用性'}</dd></dl><details><summary>查看锁定输入快照 · v${s.version}</summary><pre>${esc(JSON.stringify(s.fields,null,2))}</pre></details>`}
const localDrafts=()=>!!document.querySelector('meta[name=local-inquiries]');
const draftValues=f=>({product_name:f.goods,volume_cbm:f.volume,weight_kg:f.weight});
async function draftCall(c,action,body={}){
 const response=await fetch('/drafts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({case:c.id,action,body})});
 const result=await response.json();
 if(!response.ok)throw Error(result.error || '草稿服务暂不可用');
 c.serverDraft=result;
}
function quoteHistoryLines(draft){
 const labels={PENDING:'待审核',APPROVED:'已批准',CONSUMED:'已使用',REJECTED:'已拒绝',INVALIDATED:'已失效'};
 return (draft.quote_history||[]).map(record=>{
  const source=record.source;
  const reviewed=record.decision==='APPROVE' && record.review_state==='INVALIDATED'?'曾批准，现已失效':labels[record.review_state]||'未申请';
  return ['v'+record.case_version+' · '+source.file+' · '+record.calculation,
   source.sheet+'!'+source.band_cell+' / '+source.price_cell+' · SHA-256 '+source.sha256,
   '试算许可：'+labels[record.trial_state]+' · '+record.trial_id,
   '草稿审核：'+reviewed+(record.review_id?' · '+record.review_id:'')].join('\n');
 });
}
function draftCard(c){
 if(!localDrafts() || c.locked)return '';
 const d=c.serverDraft,a=d?.approval;
 const statuses={APPROVED:'已批准',REJECTED:'已拒绝',PENDING:'待审批',INVALIDATED:'旧批准或申请已失效',CONSUMED:'已使用'};
 const info=d?.input ? '服务端版本 '+d.version+' · '+esc(d.input.product_name)+' · '+esc(d.input.volume_cbm)+' CBM · '+esc(d.input.weight_kg)+' kg' : '请先填写货物名称、体积和重量，保存为后端草稿。';
 const controls=[btn('读取服务端状态','draft-read')];
 const selected=['A','B'].includes(d?.rate_source)?d.rate_source:'';
 const picker='<label>合成巴西海运运价<select id="draft-source"><option value="">仅分类演示</option>'+['A','B'].map(k=>'<option value="'+k+'" '+(k===selected?'selected':'')+'>运价表 '+k+' · v1</option>').join('')+'</select></label>';
 if(!d?.input)controls.push(btn('用本地千问识别客户原文','case-intake'));
 if(!d?.input)controls.push(btn('保存为后端草稿','draft-save'));
 else if(!a || a.case.version!==d.version)controls.push(btn('申请本版审批','draft-request'));
 else if(selected && a.state==='APPROVED' && a.scope==='classification_trial')controls.push(btn('生成内部报价草稿','draft-calculate'));
 else if(selected && a.state==='CONSUMED' && d.quote)controls.push(btn('申请内部草稿审核','draft-review'));
 else if(a.state==='PENDING')controls.push(btn('批准本版','draft-approve'),btn('拒绝本版','draft-reject'));
 const history=quoteHistoryLines(d||{});
 return card('后端分类试算审批（合成）',(c.intake?'<p>'+esc(c.intake.status)+'</p><details><summary>依据：SOP-01 §1</summary>'+esc(c.intake.sop)+'<p>'+esc(c.intake.file)+' · '+esc(c.intake.sha)+'</p></details>':'')+picker+'<p>'+info+'</p>'+(d?.quote?'<p>基础运费 '+esc(d.quote.calculation)+' · '+esc(d.quote.source.file)+' / '+esc(d.quote.source.price_cell)+'</p><p>来源 SHA-256：'+esc(d.quote.source.sha256)+'</p><p>'+d.quote.unresolved.map(esc).join(' ')+'</p>':'')+'<p>'+ (a && a.case.version===d?.version?(a.scope==='internal_quote_review'?'内部草稿审核 · ':'试算许可 · '):'')+esc(statuses[a?.state] || '尚未申请')+'</p><div class="actions">'+controls.join('')+'</div>'+(history.length?'<details><summary>历版试算与审核（'+history.length+'）</summary>'+history.map(line=>'<pre>'+esc(line)+'</pre>').join('')+'</details>':'')+'<small>绑定商品、总体积与总重。修改这些字段后点击“保存人工修改”会更新后端版本。</small>');
}
async function saveBackend(c,fields){
 const rate=$('#draft-source')?.value;
 if(rate && c.intake && (fields.country!=='巴西'||c.intake.mode!=='海运'))throw Error('本样例只支持巴西海运，请核对原文后重新识别');
 if(!rate && ['A','B'].includes(c.serverDraft?.rate_source))throw Error('请选择运价表 A 或 B');
 await draftCall(c,'save',{...(rate?{rate_source:rate}:{}),expected_version:c.serverDraft?.version || 1,input:draftValues(fields)});
}
function caseDetail(){const c=current();if(!c)return heading('请选择一个案例','从案例库打开询价。',btn('返回案例库','cases'));const stopped=c.approval==='已拒绝',gaps=missing(c.fields);return heading(esc(c.name),`${c.demo?'演示案例 · ':''}${esc(c.id.slice(0,8))} · v${c.version} · ${c.locked?'版本已锁定':'可编辑'}`,`${btn('← 案例库','cases')}${btn('复制新案例','clone')}${btn('导出材料','export')}`)+steps(c)+draftCard(c)+`<div class="status-grid">${[['分类',c.classification],['试算',brazilEstimate(c)?'基础运费已生成':c.trial],['报价',brazilEstimate(c)?internalReview(c):c.quote],['订单',c.order],['异常',c.exception]].map(([k,v])=>`<div class="status-box"><div class="tiny">${k}状态</div>${badge(v)}</div>`).join('')}</div>${stopped?'<div class="notice">已拒绝：当前流程已停止，不能再次审批、生成试算或登记订单。可复制为新案例重新识别。</div>':''}<div class="grid"><div>${card('01 / 客户询价',`<label>客户 / 案例名称<input id="case-name" value="${esc(c.name)}" ${c.locked?'disabled':''}></label><label class="spaced">粘贴客户询价<textarea id="inquiry" ${c.locked?'disabled':''} placeholder="例如：目的国：德国，货名：收纳盒，货物类别：普货，2方，320kg，20箱，40天，派送需求：到门，清关需求：代理清关，税费需求：DAP">${esc(c.text)}</textarea></label><div class="actions spaced">${btn('提取需求','extract',true,c.locked?'disabled':'')}<span class="tiny">${localDrafts()?'自然语言询价可调用本机千问；数量按原文核对。':'规则提取 · 未明确表达的字段保留待补充'}</span></div>`)}${card('02 / 需求识别与人工确认',`<div class="notice ${gaps.length?'':'green'}">${gaps.length?'待补充 / 待核验：'+gaps.map(k=>FIELDS[k]).join('、'):'字段已填齐。请核对原文和货物类别后进行人工确认。'}</div>${customerNotes(c)}${followupPanel(c)}<form id="fields-form"><div class="form-grid">${Object.keys(FIELDS).map(k=>fieldInput(c,k)).join('')}</div><div class="actions spaced">${btn('保存人工修改','save-fields',false,c.locked?'disabled':'')}${btn('确认分类','confirm-class',true,c.locked||c.classification==='已确认'?'disabled':'')}</div></form>`)}${card('03 / 渠道匹配',previousTrials(c)+channels(c),badge(selectedBase(c)?baseChannel:c.selected||(brazilEstimate(c)?'已有基础试算':'待选择')))}${selectedBase(c)?card('04 / 已选巴西海运基础运费','<p>'+esc(c.serverDraft.quote.calculation)+'</p><p>'+esc(internalReview(c))+' · 到门派送、清关及到货日期待核。</p>'+btn('查看试算记录','export')):card('04 / 一次审批与试算',`<p>分类确认后，锁定选定渠道、全部输入和来源规则。批准将生成试算；拒绝立即结束当前流程。</p><div class="actions spaced">${btn('锁定版本并申请审批','request',true,c.locked||c.classification!=='已确认'?'disabled':'')}${badge(c.approval)}</div>${c.approval==='待审批'?`<div class="notice spaced">审批只由当前操作人员点击作出；系统不会自动批准。请核对右侧来源及输入快照。</div><div class="actions">${btn('人工批准 · 生成试算','approve',true)}<button class="danger" data-action="reject">拒绝并停止</button></div>`:''}${c.trial==='已生成'?`<div class="divider"></div><span class="tiny">${c.snapshot.row.rule.demo?'演示试算 · 非真实报价':'报价试算 · 需核验实际适用性'}</span><div class="price">${money(c.snapshot.row.total,c.snapshot.row.rule.currency)} <small>${c.snapshot.row.rule.currency}</small></div><p>基础费用 ${money(c.snapshot.row.base,c.snapshot.row.rule.currency)} + 附加费用 ${money(c.snapshot.row.extra,c.snapshot.row.rule.currency)}；计费量 ${c.snapshot.row.quantity.toFixed(2)} ${c.snapshot.row.rule.basis}</p><p class="tiny">DAP 演示规则不含目的地进口税费。其他规则依照来源说明，不推断未列费用已包含。</p><div class="actions spaced">${c.order==='未下单'?btn('重选货运方式','reselect-channel'):''}${btn('报价说明 / 复盘','export')}${btn('进入订单跟踪','orders',true)}</div>`:''}`)}</div><div>${card('版本与审批控制',`<label>操作人（本地记录，非认证身份）<input id="actor" value="${esc(state.actor)}" maxlength="60"></label><p class="tiny spaced">修改已锁定输入不会覆盖原报价。每次审批绑定一个不可在界面编辑的快照；复制后重新确认。</p><div class="actions spaced">${badge(c.locked?'版本已锁定':'版本可编辑')}${badge('单次审批')}</div>`)}${card('来源追踪',sourceTrace(c))}${card('人工修改与审批日志',c.logs.length?c.logs.slice().reverse().map(l=>`<div class="log"><strong>${esc(l.action)}</strong><small>${esc(l.actor)} · v${l.version} · ${date(l.at)}</small>${l.details?`<details><summary>详情</summary><pre>${esc(l.details)}</pre></details>`:''}</div>`).join(''):'<p>尚无操作记录。</p>')}</div></div>`}
function sopSearch(){
 if(!document.querySelector('meta[name="local-inquiries"]'))return '';
 return card('巴西海运 · 合成操作规范', `<form id="sop-search"><label>查询问题<input name="question" required maxlength="500" placeholder="巴西海运试算需要哪些资料？"></label><button>查询依据</button></form><p id="sop-result" role="status"></p>`);
}
async function submitSop(event){
 event.preventDefault();
 const form=event.currentTarget, output=$('#sop-result'), button=form.querySelector('button');
 button.disabled=true;
 output.textContent='正在核对规范…';
 try{
  const response=await fetch('/sop-lookup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:form.elements.question.value})});
  const data=await response.json();
  if(!response.ok)throw Error(data.error || '查询失败');
  output.textContent=data.source ? [data.source.sop,data.source.file,data.source.title,'SHA-256 '+data.source.sha].join(' · ') : data.message;
 }catch(error){output.textContent=error.message;}finally{button.disabled=false;}
}
function sources(){return heading('报价来源库','导入结构化规则，保留文件哈希和单元格定位。未经人工核验的规则不允许计算。',btn('下载规则模板','template'))+sopSearch()+`<div class="notice">请从实际报价表整理 JSON 规则并填写原始文件名、原文件 SHA、工作表、单元格及版本。本页面记录导入 JSON 的 SHA；原始文件可另行上传核对 SHA。导入不会自动标记为已核验。</div>${card('导入与核验',`<div class="actions"><label class="file-label">导入规则 JSON<input type="file" id="import-rules" accept=".json,application/json"></label><label class="file-label">计算原始报价文件 SHA<input type="file" id="hash-file"></label>${btn('载入演示规则与案例','load-demo')}</div><p id="file-hash" class="sources spaced">所有文件均在浏览器本地处理。</p>`)}<section class="card"><div class="card-head"><h2>来源规则 · ${state.rules.length}</h2>${badge('保留版本快照')}</div><div class="table-wrap"><table><thead><tr><th>规则 / 渠道</th><th>适用范围</th><th>来源与版本</th><th>核验</th><th></th></tr></thead><tbody>${state.rules.map((r,i)=>`<tr><td><b>${esc(r.channel)}</b><small>${esc(r.id)}${r.demo?' · 演示':''}</small></td><td>${esc(r.country)} / ${esc(r.category)}<small>${r.maxWeight}kg · ${r.maxVolume}m³ · ${r.transitDays}天</small><small>${esc(r.delivery)} / ${esc(r.customs)} / ${esc(r.tax)}</small></td><td><details><summary>${esc(r.file)} · ${esc(r.version)}</summary><div class="sources">${esc(r.sheet)}!${esc(r.cell)}<br>导入 SHA: ${esc(r.sha)}<br>原文件: ${esc(r.originalFile||'演示 / 未提供')}<br>原 SHA: ${esc(r.originalSha||'未提供')}<br>规则: ${r.rate} ${r.currency}/${r.basis}，最低${r.min}<br>待确认：${esc(r.pending.join('；')||'无')}</div></details></td><td>${badge(r.verified?'已确认':'待核验')}</td><td>${!r.verified?`<button data-verify="${i}">人工核验</button>`:'<span class="tiny">核验人：'+esc(r.verifiedBy||'演示数据')+'</span>'}</td></tr>`).join('')||'<tr><td colspan="5" class="empty">暂无来源。导入规则或载入演示数据开始。</td></tr>'}</tbody></table></div></section>${card('规则模板说明',`<p>每条规则包含目的国、货类、渠道、服务需求、体积/重量上限、时效、币种、计价单位、最低价和附加费。单位支持 kg、m3、shipment；kg 规则可使用明确的体积计重系数。缺少适用范围、未确认费用或未核验来源时不生成报价。</p><p>源 Excel、PDF 不会被自动解读为有效运价。可用「下载规则模板」整理本机 AI 报价系统的输出后导入。</p>`)}`}
function orderPage(){const c=current();return heading('订单与物流跟踪','登记客户下单、订舱、报关、提舱、运输节点和异常。当前位置来自人工记录。',btn('查看全部订单','cases'))+`<select class="select-case" id="active-case" aria-label="选择订单案例">${selectOptions(state.cases.map(x=>x.id),active,'选择案例').replace(/>([a-f\d-]{36})</g,(m,id)=>'>'+esc(state.cases.find(x=>x.id===id)?.name||id)+'<')}</select>${!c?'<div class="empty">请先新建询价案例。</div>':`<div class="spaced status-grid">${[['报价',c.quote],['订单',c.order],['异常',c.exception]].map(([t,s])=>`<div class="status-box"><div class="tiny">${t}</div>${badge(s)}</div>`).join('')}</div><div class="grid"><div>${card('客户确认与订单登记',`<h3>${esc(c.name)}</h3><p>此处仅记录业务状态；不连接承运商，不会自动提交真实订单。</p><label class="check spaced"><input type="checkbox" id="customer-confirmed" ${c.order!=='未下单'?'disabled':''}>我已取得客户对当前报价版本的确认，并由本人记录。演示案例只记录演示确认。</label><div class="actions spaced">${btn('登记客户下单','place-order',true,c.approval!=='已确认'||c.order!=='未下单'?'disabled':'')}${btn('查看报价版本','detail')}</div>`)}${card('新增物流节点',`<div class="form-grid"><label>节点<select id="event-node">${['已订舱','已报关','已提舱','运输中','目的港到达','清关中','派送中','已签收'].map(x=>`<option>${x}</option>`).join('')}</select></label><label>货物当前位置<input id="event-location" placeholder="城市 / 港口 / 仓库"></label><label>说明<input id="event-note" placeholder="提单号、节点说明等"></label></div><label class="check spaced"><input type="checkbox" id="event-exception">本节点存在异常，需要跟进</label><div class="actions spaced">${btn('保存节点','add-event',true,['未下单','已停止'].includes(c.order)?'disabled':'')}${btn('标记异常已解决','resolve-exception',false,c.exception==='无异常'?'disabled':'')}</div>`)}${card('运输节点时间线',c.events.length?c.events.slice().reverse().map(e=>`<div class="log"><strong>${esc(e.node)} · ${esc(e.location)}</strong>${e.exception?' '+badge('异常待处理'):''}<small>${date(e.at)}</small><p>${esc(e.note)}</p></div>`).join(''):'<div class="empty">尚未登记订单或物流节点。</div>')}</div><div>${card('当前货物位置',`<div class="route-icon">⌖</div><h2 class="spaced">${esc(c.events.at(-1)?.location||'待补充')}</h2><p>${c.events.length?'最后更新：'+date(c.events.at(-1).at):'尚无定位记录'}</p><p class="tiny">仅显示人工登记，不代表实时 GPS 或承运商返回。</p>`)}${card('版本来源',sourceTrace(c))}</div></div>`}`}
const demoStages=[['客户询价','把客户的自然语言询价变为可核对的业务输入。','打开演示询价，检查原文。未写明的需求保持空白。'],['需求识别','让缺失信息和分类不确定性显式出现。','提取字段，核对证据，再亲自点击「确认分类」。'],['渠道推荐','比较多渠道，不把无数据误当作可报价。','检查每种渠道的适用条件与来源，选择可直接计算的渠道。'],['报价','建立一份有来源、版本和规则的报价快照。','点击「锁定版本并申请审批」，查看右侧输入和 SHA。'],['人工审批','人有最终决定权，拒绝立即停止。','本人点击批准或拒绝。拒绝后只能复制新案例，不能重试原审批。'],['下单','将客户确认与报价版本绑定。','批准生成试算后，在订单页记录客户对该版本的确认。'],['物流跟踪','用可追溯节点闭合服务链路。','记录订舱、报关、位置与异常；导出复盘用于参赛展示。']];
function demoPage(){const d=demoStages[demoStep];return heading('一票货的旅程，一次可解释的演示。','比赛演示模式 · 所有价格与案例均明确标记为演示，不触发真实业务。',btn('载入 / 打开演示案例','load-demo',true))+`<div class="demo-layout"><div class="demo-side">${demoStages.map((x,i)=>`<button data-demo="${i}" class="${i===demoStep?'active':''}">${String(i+1).padStart(2,'0')}　${x[0]}</button>`).join('')}</div><div>${card(d[0],`<div class="big-number">${String(demoStep+1).padStart(2,'0')}</div><h2>${d[1]}</h2><p class="spaced">${d[2]}</p><div class="notice green spaced">演示导航只切换讲解，不会替人批准。实际状态通过案例页的人工操作推进。</div><div class="actions spaced">${btn('打开实际操作','demo-operate',true)}${btn('上一步','demo-prev',false,demoStep===0?'disabled':'')}${btn('下一步 →','demo-next',false,demoStep===6?'disabled':'')}</div>`)}<div class="stats" style="grid-template-columns:repeat(3,1fr)">${[['可解释','原文证据 + 来源 SHA'],['人工可控','一次审批 + 拒绝终止'],['不确定性可见','待补充 + 待核验 + 人工询价']].map(([a,b])=>`<div class="stat"><h3>${a}</h3><p class="tiny">${b}</p></div>`).join('')}</div>${card('展示材料与证据',`<p>可以从实际案例导出报价说明、流程复盘和参赛讲稿。真实业务效果、节省时间和报价准确率尚未测量，材料不会填入虚构指标。</p><div class="actions spaced">${btn('导出当前案例展示材料','export',false,current()?'':'disabled')}${btn('复制当前演示案例','clone',false,current()?'':'disabled')}</div>`)}</div></div>`}
const QUESTIONS=[{topic:'普货',q:'客户只说“配件 10 箱”，可以直接确认是普货吗？',options:['可以，配件默认普货','不可以，需补充品名、材质及是否含电池/液体等信息','只看箱数即可'],correct:1,why:'笼统品名不足以支持分类。把缺失属性标为待补充，分类交由人确认。'},{topic:'敏感货',q:'客户询价明确含锂电池，但来源规则只适用普货，应如何处理？',options:['套用普货规则','加 10% 后报价','标记敏感货候选，人工确认并询价'],correct:2,why:'货物条件不匹配不能套价。敏感属性需要人工核验和适用渠道资料。'},{topic:'双清包税',q:'客户要求双清包税，但来源未列税费范围，如何报价？',options:['默认全部税费已含','记录待确认项并人工询价','把税费设为零'],correct:1,why:'服务名称不足以证明费用覆盖，需明确适用范围与除外项。'},{topic:'DAP / DDP',q:'规则标记 DAP，客户要求 DDP，当前规则可以直接匹配吗？',options:['不能，服务需求不同，需核验匹配规则','可以，价格相同','把名称改成 DDP 即可'],correct:0,why:'工作台按明确的服务需求匹配，不自动替换贸易条件或推断税费已包含。'},{topic:'附加费用',q:'基础运费 800 CNY，操作费 120 CNY，已确认派送费 80 CNY，合计多少？',options:['800 CNY','920 CNY','1,000 CNY'],correct:2,why:'800 + 120 + 80 = 1,000 CNY。每项费用应保持名称及来源。'},{topic:'多币种',q:'报价含 100 USD 和 200 CNY，但未提供已确认汇率，如何展示？',options:['直接相加为 300 CNY','分别列出币种，补充汇率来源与版本后再换算','使用猜测汇率'],correct:1,why:'不同币种不能直接相加。保留币种并标记换算条件待补充。'}];
function training(){const q=QUESTIONS[questionIndex%QUESTIONS.length],wrong=state.training.filter(x=>!x.correct);return heading('把每一次练习，变成报价的底气。','普货、敏感货、双清包税、DAP/DDP、附加费与多币种专项训练。',btn('下一题 →','next-question'))+`<div class="grid"><div>${card('销售训练 · '+q.topic,`<div class="tiny">第 ${questionIndex%QUESTIONS.length+1} / ${QUESTIONS.length} 题</div><h2 class="spaced">${q.q}</h2><div class="training-options">${q.options.map((o,i)=>`<button data-answer="${i}" class="${answer===i?'selected':''}" ${result?'disabled':''}>${String.fromCharCode(65+i)}　${o}</button>`).join('')}</div>${result?`<div class="training-result">${result.correct?'✓ 回答正确':'需要再练习'}<br>${q.why}</div>`:btn('提交答案','submit-answer',true,answer<0?'disabled':'')}`)}${card('易错点回顾',wrong.length?wrong.slice().reverse().map(x=>`<div class="log"><strong>${esc(x.topic)}</strong><p>${esc(x.why)}</p><small>${date(x.at)}</small></div>`).join(''):'<p>还没有错题记录。完成练习后自动汇总。</p>')}</div><div>${card('训练记录',`<div class="stats" style="grid-template-columns:1fr 1fr;margin:0"><div class="stat"><div class="tiny">已答题</div><div class="stat-value">${state.training.length}</div></div><div class="stat"><div class="tiny">正确率</div><div class="stat-value">${state.training.length?Math.round((state.training.length-wrong.length)/state.training.length*100):0}%</div></div></div><div class="spaced">${state.training.slice(-8).reverse().map(x=>`<div class="mini-route"><div class="grow">${esc(x.topic)}<div class="tiny">${date(x.at)}</div></div>${badge(x.correct?'已确认':'待复习')}</div>`).join('')}</div>`)}<p class="tiny">这是工作流程训练题，不提供海关、税务或承运合规结论。实货以适用规则和专业核验为准。</p></div></div>`}
function backendReport(c){
 const d=c.serverDraft, q=d.quote, a=d.approval;
 const lines=['# 内部试算记录（合成）',c.name,'案例 '+c.id+' · 后端版本 v'+d.version,
  '品名：'+d.input.product_name,'总体积：'+d.input.volume_cbm+' CBM','总毛重：'+d.input.weight_kg+' kg'];
 if(q){
  lines.push('基础运费：'+q.base_freight_usd+' USD',q.calculation,'最终应付：待核定',
   '运价来源：'+q.source.file+' / '+q.source.sheet+'!'+q.source.price_cell,
   'SHA-256：'+q.source.sha256,...q.unresolved);
 }else lines.push('本版尚未生成基础运费。');
 const states={PENDING:'待审批',APPROVED:'已批准',REJECTED:'已拒绝',CONSUMED:'已使用',INVALIDATED:'已失效'};
 if(a?.case.version===d.version && a.case.id===c.id){
  lines.push((a.scope==='internal_quote_review'?'内部草稿审核':'试算许可')+'：'+states[a.state],
   '审批记录：'+a.approval_id);
  if(a.scope!=='internal_quote_review')lines.push('本版内部草稿尚未送审。');
 }else lines.push('本版尚无审批记录。');
 const history=quoteHistoryLines(d);
 if(history.length)lines.push('','历版试算与审核',...history);
 lines.push('本记录仅供团队复核模拟业务。');
 return lines.join('\n');
}
async function refreshReport(c){
 if(!localDrafts() || !c?.serverDraft?.input)return;
 await draftCall(c,'read');
 if(current()!==c || !c.serverDraft.input)throw Error('案例或后端草稿已变化，请重新打开导出页');
}
function channelComparison(c){
 const lines=['# 货运方案对比',c.name,'案例 '+c.id,'金额按各版试算时的资料和原币种列示，时效为运价规则中的预计天数。'];
 function append(snapshot,label){
  const {row,fields,version}=snapshot,rule=row.rule;
  lines.push('',label+' · v'+version+' · '+row.channel+(rule.demo?'（演示运价）':''),
   '货物：'+fields.goods+' · '+fields.weight+' kg · '+fields.volume+' m³',
   '试算金额：'+money(row.total,rule.currency)+' '+rule.currency+' · 预计 '+rule.transitDays+' 天',
   '基础费用：'+money(row.base,rule.currency)+' · 附加费：'+money(row.extra,rule.currency),
   '来源：'+rule.file+' / '+rule.sheet+'!'+rule.cell+' · '+rule.version);
 }
 if(c.trial==='已生成' && c.snapshot)append(c.snapshot,'当前方案');
 else lines.push('当前版本尚无已完成的试算。');
 for(const previous of [...(c.previousTrials||[])].reverse())append(previous,'历史方案');
 return lines.join('\n');
}
function report(c,type){if(!c)return '请先选择案例';if(type==='货运方案对比')return channelComparison(c);if(c.serverDraft?.input)return backendReport(c);const r=c.snapshot?.row;const head=`# ${type}｜${c.name}\n\n案例 ${c.id} · v${c.version}\n${c.demo||r?.rule.demo?'【演示材料，不是真实报价或业务承诺】':'【工作台试算材料，需业务核验】'}\n\n`;const fields=Object.entries(FIELDS).map(([k,l])=>`- ${l}：${c.fields[k]||'待补充'}`).join('\n');const quotation=r&&c.trial==='已生成'?`\n\n## 报价试算\n渠道：${r.channel}\n总额：${money(r.total,r.rule.currency)}\n基础：${money(r.base,r.rule.currency)}；附加费：${money(r.extra,r.rule.currency)}\n计费量：${r.quantity} ${r.rule.basis}；单价：${r.rule.rate}；最低价：${r.rule.min}\n来源：${r.rule.file} / ${r.rule.sheet}!${r.rule.cell}\n版本：${r.rule.version}\nSHA-256：${r.rule.sha}\n原文件：${r.rule.originalFile||'未提供'} / ${r.rule.originalSha||'未提供'}\n附加费明细：${r.rule.surcharges.map(x=>x.name+' '+money(x.amount,r.rule.currency)).join('；')}\n待确认项：${r.rule.pending.join('；')||'无已登记项；实际适用性需核验'}\n规则：计费量×单价，与最低价取大，再加附加费。按原币种列示，未转换汇率。\n\n输入快照：\n${JSON.stringify(c.snapshot.fields,null,2)}`:'\n\n报价尚未生成，不应作为正式报价发送。';const audit=`\n\n## 流程与记录\n分类：${c.classification}；试算：${c.trial}；报价：${c.quote}；订单：${c.order}；异常：${c.exception}\n${c.logs.map(l=>`- ${date(l.at)} ${l.actor} ${l.action} v${l.version} ${l.details}`).join('\n')}\n\n## 物流\n${c.events.map(e=>`- ${date(e.at)} ${e.node} / ${e.location} / ${e.note}`).join('\n')||'暂无记录'}`;const pitch=type==='参赛展示材料'?'\n\n## 演示主线\n客户询价 → 需求识别 → 渠道推荐 → 报价快照 → 人工审批 → 客户下单登记 → 物流跟踪。\n创新点：来源追踪、可见的不确定性、版本锁定与单次审批。\n实测商业指标：尚未测量。真实运价、认证审批和承运商连接：尚未接入。':'';return head+fields+quotation+(type==='正式报价说明'?'':audit)+pitch+'\n\n说明：数据来自当前浏览器本地工作台；非认证审计。实际服务、费用范围与客户承诺须另行核验。';}
function exportPage(){const c=current();return heading('让过程可复盘，让成果可展示。','从当前案例生成报价说明、流程复盘或参赛材料。保留来源与真实状态。',btn('返回当前案例','detail'))+(!c?'<div class="empty">请先选择一个案例。</div>':`${card('材料类型',`<div class="actions"><select id="report-type" style="width:auto">${(c.serverDraft?.input?['内部试算记录（合成）']:['正式报价说明','流程复盘','参赛展示材料']).concat(c.previousTrials?.length?['货运方案对比']:[]).map(x=>`<option>${x}</option>`).join('')}</select>${btn('复制文字','copy-report',true)}${btn('下载 Markdown','download-report')}${btn('打印 / PDF','print')}${btn('复制案例','clone')}</div><textarea id="report" class="spaced" style="min-height:460px" readonly>${esc(report(c,'正式报价说明'))}</textarea>`)}${card('数据备份与迁移',`<p>备份包括案例、来源规则、审批及修改日志、物流节点和训练记录。请在更换浏览器前备份。导入会校验格式并要求明确确认替换当前数据。</p><div class="actions spaced">${btn('下载完整备份','backup')}<label class="file-label">导入备份<input type="file" id="restore" accept=".json"></label></div>`)}`)}
function render(){const nav=[['overview','◫','工作台概览'],['cases','▤','询价与案例'],['sources','▦','报价来源库'],['orders','⇢','订单与物流'],['demo','▷','比赛演示'],['training','◇','销售训练']];$('#app').innerHTML=`<div class="shell"><aside class="sidebar"><div class="brand"><div class="brand-icon">↗</div><div><strong>FreightPilot</strong><small>AGENT WORKSPACE</small></div></div><div class="nav-label">WORKSPACE</div><nav class="nav">${nav.map(([id,icon,t])=>`<button data-nav="${id}" class="${view===id||view==='detail'&&id==='cases'?'active':''}"><span>${icon}</span>${t}</button>`).join('')}</nav><div class="sidebar-foot"><span class="dot"></span>本机工作台<br>可解释 · 可追溯 · 人工可控<div style="margin-top:14px;color:#7e9d88">VERSION 1.0 / DEMO READY</div></div></aside><main class="main"><header class="topbar"><div>工作空间 <span style="margin:0 12px;color:#bdc7bc">/</span> <b>${nav.find(x=>x[0]===view)?.[2]||'案例详情'}</b></div><div class="top-actions"><span class="pill">仅本地存储 · 未连接实盘</span><span>${esc(state.actor)}</span><div class="avatar">FP</div></div></header><div class="content">${({overview, cases:caseList,detail:caseDetail,sources,orders:orderPage,demo:demoPage,training,export:exportPage}[view]||overview)()}<footer class="footer"><span>FREIGHTPILOT · 货代 Agent 参赛与报价工作台</span><span>所有自动化，都有清晰的人类控制点。</span></footer></div></main></div>`;bind();}
async function sha(bytes){return [...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(x=>x.toString(16).padStart(2,'0')).join('')}
async function loadDemo(){if(!state.demoLoaded){const response=await fetch('demo-rates.json');if(!response.ok)throw Error('演示来源载入失败');const bytes=await response.arrayBuffer(),data=JSON.parse(new TextDecoder().decode(bytes)),hash=await sha(bytes);state.rules.push(...data.rules.map(r=>({...validateRule(r),file:'demo-rates.json',sha:hash,verified:true,demo:true})));const c=newCase('北辰家居 · 德国普货演示');c.demo=true;c.text='目的国：德国，货名：塑料收纳盒，货物类别：普货，2方，320kg，20箱，40天，派送需求：到门，清关需求：代理清关，税费需求：DAP';const parsed=extract(c.text);c.fields=parsed.fields;c.evidence=parsed.evidence;log(c,'载入演示询价','系统','未执行分类确认或人工批准');state.cases.unshift(c);active=c.id;state.demoLoaded=true;persist()}else active=state.cases.find(c=>c.demo)?.id||active;view='detail';render()}
function download(name,text,type='application/json'){const url=URL.createObjectURL(new Blob([text],{type}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
function fieldsFromForm(){return Object.fromEntries(new FormData($('#fields-form')))}
function assertSavedInputs(c){
 if(!c||c.locked||!$('#fields-form'))return;
 const fields=fieldsFromForm();
 if(Object.keys(FIELDS).some(k=>String(fields[k]??'')!==String(c.fields[k]??''))||$('#case-name').value!==c.name||$('#inquiry').value!==c.text)throw Error('存在未保存修改，请先保存人工修改，再重新确认分类；尚未申请审批');
}
async function action(a){const c=current();if(a==='request'||a==='confirm-class')assertSavedInputs(c);switch(a){
 case 'copy-followup':{
  assertSavedInputs(c);
  await navigator.clipboard.writeText($('#customer-followup').value);toast('补问已复制');return;
 }
 case 'case-intake':{
  if(c.locked)throw Error('版本已锁定');
  const text=$('#inquiry').value;
  const fieldsBefore=JSON.stringify(fieldsFromForm());
  toast('本机千问正在识别询价');
  const res=await fetch('/case-intake',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt:text})});
  const recognized=await res.json();
  if(!res.ok)throw Error(recognized.error || '识别失败');
  if(current()!==c || !$('#inquiry') || $('#inquiry').value!==text || fieldsBefore!==JSON.stringify(fieldsFromForm()) || c.serverDraft?.input)throw Error('案例已变化，请重新识别');
  c.text=text;c.name=$('#case-name').value.trim()||c.name;
  edit(c,recognized.fields,state.actor);c.evidence=recognized.evidence;c.intake=recognized;
  log(c,'千问识别询价','本地千问',recognized.status);break;
 }
 case 'draft-read':await draftCall(c,'read');break;
 case 'draft-save':{
  const fields=fieldsFromForm();await saveBackend(c,fields);
  edit(c,fields,state.actor);c.text=$('#inquiry').value;c.name=$('#case-name').value.trim()||c.name;break;
 }
 case 'draft-calculate':case 'draft-review':case 'draft-request':case 'draft-approve':case 'draft-reject':{
  assertSavedInputs(c);const d=c.serverDraft,values=draftValues(c.fields);
  if(Object.keys(values).some(k=>values[k]!==d.input[k]))throw Error('页面和后台草稿不同，请先核对并保存字段');
  if(($('#draft-source')?.value||'')!==(['A','B'].includes(d.rate_source)?d.rate_source:''))throw Error('请先保存所选运价表');
  if(a==='draft-request'||a==='draft-review')await draftCall(c,a.slice(6),{input:values,version:d.version});
  else await draftCall(c,a.slice(6),{id:d.approval.approval_id,version:d.version,revision:d.approval.revision,hash:d.approval.snapshot_hash});
  break;
 }
 case 'new':{const n=newCase();state.cases.unshift(n);active=n.id;view='detail';break;}
 case 'overview':case 'cases':case 'sources':case 'orders':case 'demo':case 'training':case 'detail':view=a;break;
 case 'export':await refreshReport(c);view=a;break;
 case 'load-demo':await loadDemo();return;
 case 'extract':{if(c.locked)throw Error('版本已锁定');const text=$('#inquiry').value,parsed=extract(text);
  if(localDrafts() && !c.serverDraft?.input && (!parsed.fields.goods || !parsed.fields.country) && text.trim()){
   return action('case-intake');
  }
  if(c.serverDraft?.input)await saveBackend(c,parsed.fields);c.name=$('#case-name').value.trim()||'未命名询价';c.text=text;edit(c,parsed.fields,state.actor);c.evidence=parsed.evidence;log(c,'提取需求','规则提取器','仅提取明确字段，缺失项留空');break;}
 case 'save-fields':{const fields=fieldsFromForm();if(c.serverDraft?.input)await saveBackend(c,fields);edit(c,fields,state.actor);c.name=$('#case-name').value.trim()||c.name;c.text=$('#inquiry').value;toast('人工修改已保存，分类需要重新确认');break;}
 case 'confirm-class':{if(JSON.stringify(fieldsFromForm())!==JSON.stringify(c.fields))throw Error('请先保存字段修改再确认分类');confirmClassification(c,state.actor);break;}
 case 'reselect-channel':{
  reselectChannel(c,state.actor);persist();render();
  const first=$('[name=channel]:not(:disabled)');
  first?.closest('section')?.scrollIntoView({block:'start'});first?.focus({preventScroll:true});return;
 }
 case 'request':requestApproval(c,state.rules,state.actor);break;
 case 'approve':decide(c,true,state.actor);break;
 case 'reject':decide(c,false,state.actor);break;
 case 'clone':{if(!c)throw Error('请先选择案例');const n=cloneCase(c);n.demo=c.demo;state.cases.unshift(n);active=n.id;view='detail';break;}
 case 'place-order':placeOrder(c,state.actor,$('#customer-confirmed').checked);break;
 case 'add-event':addEvent(c,{node:$('#event-node').value,location:$('#event-location').value.trim(),note:$('#event-note').value.trim(),exception:$('#event-exception').checked},state.actor);break;
 case 'resolve-exception':c.exception='无异常';log(c,'人工确认异常已解决',state.actor);break;
 case 'clear-filters':filter={};break;
 case 'demo-next':demoStep=Math.min(6,demoStep+1);break;
 case 'demo-prev':demoStep=Math.max(0,demoStep-1);break;
 case 'demo-operate':if(!state.demoLoaded)await loadDemo();view=demoStep>=5?'orders':'detail';break;
 case 'next-question':questionIndex++;answer=-1;result=null;break;
 case 'submit-answer':{if(result||answer<0)throw Error('请选择一个答案');const q=QUESTIONS[questionIndex%QUESTIONS.length];result={at:new Date().toISOString(),topic:q.topic,question:q.q,answer:q.options[answer],correct:answer===q.correct,why:q.why};state.training.push(result);break;}
 case 'print':case 'copy-report':case 'download-report':{
  await refreshReport(c);
  $('#report').value=report(c,$('#report-type').value);
  if(a==='print'){window.print();return;}
  if(a==='copy-report'){await navigator.clipboard.writeText($('#report').value);toast('材料已复制');return;}
  download('freightpilot-'+c.id.slice(0,8)+'.md',$('#report').value,'text/markdown');return;
 }
 case 'backup':download('freightpilot-backup-'+new Date().toISOString().slice(0,10)+'.json',JSON.stringify(state,null,2));return;
 case 'template':{const response=await fetch('demo-rates.json');const d=await response.json();const r=d.rules[0];delete r.demo;delete r.verified;r.id='YOUR-RULE-ID';r.originalFile='请填写真实报价表.xlsx';r.originalSha='请用原文件 SHA 功能生成64位哈希';r.version='请填写版本';r.pending=['请核验所有价格、服务范围和费用后清空'];download('freightpilot-rule-template.json',JSON.stringify({rules:[r]},null,2));return;}
 }persist();render();}
function bind(){if($('#sop-search'))$('#sop-search').onsubmit=submitSop;document.querySelectorAll('[data-nav]').forEach(b=>b.onclick=()=>{view=b.dataset.nav;render()});document.querySelectorAll('[data-action]').forEach(b=>b.onclick=e=>{e.preventDefault();Promise.resolve(action(b.dataset.action)).catch(err=>toast(err.message))});document.querySelectorAll('[data-open]').forEach(b=>b.onclick=()=>{active=b.dataset.open;view='detail';render()});document.querySelectorAll('[data-clone]').forEach(b=>b.onclick=()=>{active=b.dataset.clone;action('clone').catch(e=>toast(e.message))});document.querySelectorAll('[data-filter]').forEach(e=>e.onchange=()=>{filter[e.dataset.filter]=e.value;render()});document.querySelectorAll('[name="channel"]').forEach(e=>e.onchange=()=>{
 const c=current(),wasBase=selectedBase(c);
 try{
  if(e.value===baseChannel||wasBase)assertSavedInputs(c);
  selectChannel(c,e.value);persist();
  if(e.value===baseChannel||wasBase)render();
 }catch(error){
  document.querySelectorAll('[name="channel"]').forEach(r=>r.checked=r.value===(wasBase?baseChannel:c.selected));
  toast(error.message);
 }
});document.querySelectorAll('[data-demo]').forEach(e=>e.onclick=()=>{demoStep=+e.dataset.demo;render()});document.querySelectorAll('[data-answer]').forEach(e=>e.onclick=()=>{answer=+e.dataset.answer;render()});if($('#actor'))$('#actor').onchange=e=>{state.actor=e.target.value.trim()||'本机操作员';persist()};if($('#active-case'))$('#active-case').onchange=e=>{active=e.target.value;render()};if($('#report-type'))$('#report-type').onchange=e=>$('#report').value=report(current(),e.target.value);if($('#fields-form'))$('#fields-form').onsubmit=e=>e.preventDefault();
 if($('#hash-file'))$('#hash-file').onchange=async e=>{try{const f=e.target.files[0];if(!f)return;$('#file-hash').textContent=`${f.name} · SHA-256：${await sha(await f.arrayBuffer())}`}catch(err){toast(err.message)}};
 if($('#import-rules'))$('#import-rules').onchange=async e=>{try{const f=e.target.files[0];if(!f)return;if(f.size>5*1024*1024)throw Error('规则文件上限 5 MB');const bytes=await f.arrayBuffer(),d=JSON.parse(new TextDecoder().decode(bytes));if(!Array.isArray(d.rules)||!d.rules.length||d.rules.length>1000)throw Error('需要 1–1000 条 rules');const hash=await sha(bytes);const rules=d.rules.map(r=>({...validateRule(r),file:f.name,sha:hash,verified:false,demo:false,verifiedBy:null}));if(rules.some(r=>!r.originalFile||!/^[a-f0-9]{64}$/i.test(r.originalSha||'')))throw Error('真实规则须填写原文件名 originalFile 和64位 originalSha');const ids=new Set(state.rules.map(r=>r.id+'@'+r.version));for(const r of rules){const id=r.id+'@'+r.version;if(ids.has(id))throw Error('规则 ID 与版本重复，请更新版本后导入');ids.add(id)}state.rules.push(...rules);persist();render();toast('已导入，待逐条人工核验')}catch(err){toast('导入失败：'+err.message)}};
 document.querySelectorAll('[data-verify]').forEach(e=>e.onclick=()=>{const r=state.rules[+e.dataset.verify];if(!confirm('请确认已核对原文件 SHA、单元格、计价规则、服务范围和附加费。此操作只记录本机人工核验。'))return;r.verified=true;r.verifiedBy=state.actor;r.verifiedAt=new Date().toISOString();persist();render()});
 if($('#restore'))$('#restore').onchange=async e=>{try{const f=e.target.files[0];if(!f)return;if(f.size>10*1024*1024)throw Error('备份上限 10 MB');const d=JSON.parse(await f.text());if(d.schema!==1||!Array.isArray(d.cases)||!Array.isArray(d.rules)||!Array.isArray(d.training)||typeof d.actor!=='string')throw Error('备份格式错误');d.rules.forEach(validateRule);for(const c of d.cases){if(!c.id||!c.fields||!Array.isArray(c.logs)||!Array.isArray(c.events)||!['未申请','待审批','已确认','已拒绝'].includes(c.approval))throw Error('案例结构错误');if(c.approval!=='未申请'&&(!c.locked||!c.snapshot))throw Error('锁定版本缺少快照');}if(!confirm('将替换当前浏览器全部案例和记录。请先导出备份。确认导入？'))return;state=d;active=d.cases[0]?.id;persist();view='overview';render();toast('备份已恢复')}catch(err){toast('恢复失败：'+err.message)}};
}
render();
