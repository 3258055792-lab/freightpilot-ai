export const CHANNELS=['海运','空运','国际快递','专线','空派','海派'];
export const FIELDS={country:'目的国',goods:'货物名称',category:'货物类别',volume:'体积（m³）',weight:'重量（kg）',boxes:'箱数',days:'时效（天）',delivery:'派送需求',customs:'清关需求',tax:'税费需求'};
export const emptyFields=()=>Object.fromEntries(Object.keys(FIELDS).map(k=>[k,'']));
export function extract(text){
 const f=emptyFields(), evidence={};
 const patterns={country:/(?:目的国|国家)\s*[:：]\s*([^\n，,；;]+)/,goods:/(?:货物名称|货名)\s*[:：]\s*([^\n，,；;]+)/,category:/货物类别\s*[:：]\s*(普货|敏感货|危险品|待核验)/,volume:/(\d+(?:\.\d+)?)\s*(?:m³|m3|立方米|CBM|方)/i,weight:/(\d+(?:\.\d+)?)\s*(?:kg|公斤|千克)/i,boxes:/([+＋\-−]?\s*(?:[.．]\d+|\d+(?:[.,，．]\s*\d+)*))\s*(?:箱|cartons)/i,days:/(\d+)\s*天/,delivery:/派送需求\s*[:：]\s*(到门|自提)/,customs:/清关需求\s*[:：]\s*(代理清关|自行清关)/,tax:/税费需求\s*[:：]\s*(DDP|DAP|双清包税|待核验)/i};
 for(const[k,re]of Object.entries(patterns)){const matches=[...text.matchAll(new RegExp(re.source,re.flags+'g'))];if(matches.length===1){const m=matches[0],numeric=['weight','volume','boxes','days'].includes(k),ambiguous=numeric&&(/(?:\d[,，]\s*|\d\s*[-~～至到]\s*|[-−]\s*)$/.test(text.slice(0,m.index))||/每箱|单箱|每件|单件/.test(text));if(k==='boxes'&&(!/^\d+$/.test(m[1].trim())||!Number.isSafeInteger(Number(m[1]))||Number(m[1])<=0)){evidence[k]='无效箱数，须人工确认正整数：'+text}else if(ambiguous){evidence[k]='数量范围、分隔符或单件数据，须人工确认总量：'+text}else{f[k]=m[1].trim();evidence[k]=m[0]}}else if(matches.length>1){evidence[k]='多处数值或描述，需要人工确认：'+(k==='boxes'?text:matches.map(m=>m[0]).join(' / '))}}
 return {fields:f,evidence};
}
export function missing(f){return Object.keys(FIELDS).filter(k=>!String(f[k]??'').trim() || f[k]==='待核验' || ['volume','weight','boxes','days'].includes(k)&&(!Number.isFinite(+f[k])||+f[k]<=0) || k==='boxes'&&!Number.isInteger(+f[k]));}
export function validateRule(r){
 const required=['id','country','category','channel','delivery','customs','tax','currency','basis','version','sheet','cell'];
 if(required.some(k=>!String(r[k]??'').trim()))throw Error('规则缺少标识、适用条件或来源字段');
 if(!CHANNELS.includes(r.channel)||!['kg','m3','shipment'].includes(r.basis)||!['CNY','USD','HKD','EUR'].includes(r.currency))throw Error('渠道、币种或计费单位不支持');
 for(const k of ['rate','min','maxWeight','maxVolume','transitDays'])if(!Number.isFinite(r[k])||r[k]<0)throw Error('价格和适用范围必须是非负数字');
 if(r.maxWeight===0||r.maxVolume===0||r.transitDays===0)throw Error('范围与时效必须大于零');
 if(!Array.isArray(r.surcharges)||r.surcharges.some(x=>!x.name||!Number.isFinite(x.amount)||x.amount<0))throw Error('附加费格式错误');
 if(!Array.isArray(r.pending)||r.pending.some(x=>typeof x!=='string'))throw Error('待确认项必须为文字数组');
 if(r.volumetricFactor!==undefined&&(!Number.isFinite(r.volumetricFactor)||r.volumetricFactor<=0))throw Error('体积计重系数必须大于零');
 return r;
}
export function compare(f,rules){return CHANNELS.map(channel=>{
 if(f.category==='危险品')return{channel,status:'不支持',reason:'当前工作台未配置危险品承运能力'};
 const gaps=missing(f);if(gaps.length)return{channel,status:'需人工询价',reason:'待补充 / 待核验：'+gaps.map(k=>FIELDS[k]).join('、')};
 const candidates=rules.filter(r=>r.channel===channel&&['country','category','delivery','customs','tax'].every(k=>r[k]===f[k])&&+f.weight<=r.maxWeight&&+f.volume<=r.maxVolume&&r.transitDays<=+f.days);
 if(!candidates.length)return{channel,status:'需人工询价',reason:'无完全匹配的已核验规则；不推断渠道不承运'};
 const r=candidates.find(r=>r.verified&&r.pending.length===0);
 if(!r)return{channel,status:'需人工询价',reason:'来源待核验或存在待确认项',rule:candidates[0]};
 const quantity=r.basis==='kg'?Math.max(+f.weight,+f.volume*(r.volumetricFactor||0)):r.basis==='m3'?+f.volume:1;
 const base=Math.max(quantity*r.rate,r.min),extra=r.surcharges.reduce((s,x)=>s+x.amount,0);
 return{channel,status:'可直接计算',reason:r.demo?'演示规则 · 非真实运价':'已人工核验的导入规则',rule:r,quantity,base,extra,total:Math.round((base+extra)*100)/100};
 });}
export function newCase(name='新客户询价'){return{id:crypto.randomUUID(),name,created:new Date().toISOString(),version:1,text:'',fields:emptyFields(),evidence:{},classification:'候选分类',trial:'未生成',quote:'草稿',order:'未下单',exception:'无异常',approval:'未申请',logs:[],events:[],selected:'',locked:false};}
export function log(c,action,actor,details=''){c.logs.push({at:new Date().toISOString(),action,actor,details,version:c.version});}
export function edit(c,fields,actor){if(c.locked)throw Error('版本已锁定，请复制为新案例');const before=JSON.stringify(c.fields);c.fields={...fields};c.classification='候选分类';c.version++;log(c,'人工修改',actor,JSON.stringify({before:JSON.parse(before),after:fields}));}
export function confirmClassification(c,actor){if(c.locked||c.approval!=='未申请'||c.classification==='已确认')throw Error('流程不可重复');if(!['普货','敏感货','危险品'].includes(c.fields.category))throw Error('请先确认货物类别');c.classification='已确认';log(c,'分类确认',actor,c.fields.category);}
export function requestApproval(c,rules,actor){if(c.locked||c.approval!=='未申请'||c.classification!=='已确认')throw Error('须先人工确认分类，且每版本仅允许一次审批');const row=compare(c.fields,rules).find(x=>x.channel===c.selected);if(!row||row.status!=='可直接计算')throw Error('所选渠道尚不能直接计算');c.snapshot=JSON.parse(JSON.stringify({fields:c.fields,row,version:c.version}));c.locked=true;c.approval='待审批';log(c,'申请试算审批',actor,'输入、规则、来源与版本已锁定');}
export function decide(c,approved,actor){if(c.approval!=='待审批')throw Error('审批已处理或尚未申请');c.approval=approved?'已确认':'已拒绝';if(!approved){c.classification='已拒绝';c.trial='已停止';c.quote='已拒绝';c.order='已停止';log(c,'拒绝并终止',actor);return;}c.trial='已生成';c.quote='试算待核验';log(c,'批准并生成试算',actor);}
export function placeOrder(c,actor,customerConfirmed){if(!customerConfirmed||c.approval!=='已确认'||c.trial!=='已生成'||c.order!=='未下单')throw Error('需已生成试算、人工记录客户确认且尚未下单');c.order='待订舱';c.quote='客户已确认';c.events.push({at:new Date().toISOString(),node:'客户下单',location:'待补充',note:'工作台登记；未向承运商下单'});log(c,'登记客户确认与订单',actor);}
export function cloneCase(c){const n=newCase(c.name+' · 副本');n.text=c.text;n.fields={...c.fields};n.evidence={...c.evidence};n.parentId=c.id;log(n,'复制案例','操作员',c.id);return n;}
export function addEvent(c,event,actor){if(['未下单','已停止'].includes(c.order))throw Error('请先登记订单');if(!event.node||!event.location)throw Error('节点和当前位置必填');c.events.push({...event,at:new Date().toISOString()});c.order=event.node;c.exception=event.exception?'异常待处理':'无异常';log(c,'物流节点更新',actor,JSON.stringify(event));}
