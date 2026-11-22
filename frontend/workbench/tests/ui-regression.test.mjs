import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import * as core from '../dist/core.js';
const rules=JSON.parse(readFileSync(new URL('../dist/demo-rates.json',import.meta.url))).rules;
const text='目的国：德国，货名：塑料收纳盒，货物类别：普货，3CBM，400kg，20箱，40天，派送需求：到门，清关需求：代理清关，税费需求：DAP';
const source=readFileSync(new URL('../dist/app.js',import.meta.url),'utf8').replace(/^import .*\n/,'').replace(/render\(\);\s*$/,'');
function harness(){
 const c=core.newCase('合成回归');c.text=text;c.fields=core.extract(text).fields;c.selected='海运';core.confirmClassification(c,'测试');
 const nodes={'#fields-form':{values:{...c.fields}},'#case-name':{value:c.name},'#inquiry':{value:c.text},'#toast':{style:{}}};
 const context=vm.createContext({...core,document:{querySelector:s=>nodes[s]??null},localStorage:{getItem:()=>JSON.stringify({schema:1,cases:[c],rules,training:[],actor:'测试'}),setItem(){}},FormData:class{constructor(form){return Object.entries(form.values)}},window:{setTimeout:()=>0},setTimeout:()=>0,clearTimeout(){}});
 vm.runInContext(source+'\nrender=()=>{globalThis.renderCount=(globalThis.renderCount||0)+1};globalThis.run=action;globalThis.getCase=current;',context);
 return {context,nodes};
}
test('UI-01 all visible fields, name and raw inquiry block stale approval without erasing edits',async()=>{
 for(const key of [...Object.keys(core.FIELDS),'name','text']){
  const {context,nodes}=harness();
  if(key==='name')nodes['#case-name'].value='新名称';else if(key==='text')nodes['#inquiry'].value+=' 新要求';else nodes['#fields-form'].values[key]='800';
  await assert.rejects(context.run('request'),/未保存修改/);
  assert.equal(context.getCase().approval,'未申请');assert.equal(context.getCase().locked,false);assert.equal(context.getCase().snapshot,undefined);assert.equal(context.renderCount,undefined);
  if(key==='weight')assert.equal(nodes['#fields-form'].values.weight,'800');
 }
});
test('UI-01 saving requires reclassification and then snapshots the new weight',async()=>{
 const {context,nodes}=harness();nodes['#fields-form'].values.weight='800';
 await context.run('save-fields');assert.equal(context.getCase().classification,'候选分类');
 await assert.rejects(context.run('request'),/先人工确认分类/);
 await context.run('confirm-class');await context.run('request');assert.equal(context.getCase().snapshot.fields.weight,'800');assert.equal(context.getCase().approval,'待审批');
});
test('UI-02 invalid original box tokens retain evidence and block extracted-form approval',async()=>{
 for(const token of ['1/2箱','1e3箱','1e-3箱','1／2箱','0x20箱','9007199254740993箱','1.5箱','-2箱','−2箱','1，5箱','1,5箱','1．5箱','.5箱','0箱','1.5 cartons','1-2箱','20箱、30箱']){
  const {context,nodes}=harness();nodes['#inquiry'].value=text.replace('20箱',token);await context.run('extract');
  const c=context.getCase();assert.equal(c.fields.boxes,'',token);assert(core.missing(c.fields).includes('boxes'));
  assert(c.evidence.boxes.includes(token),c.evidence.boxes);nodes['#fields-form'].values={...c.fields};
  await context.run('confirm-class');await assert.rejects(context.run('request'),/不能直接计算/);assert.equal(c.approval,'未申请');assert.equal(c.snapshot,undefined);
 }
});
test('UI-02 normal integer with punctuation remains valid and yields 1260 CNY',async()=>{
 for(const token of ['20箱',' 20 箱','20 cartons']){
  const {context,nodes}=harness();nodes['#inquiry'].value=text.replace('20箱',token);await context.run('extract');const c=context.getCase();assert.equal(c.fields.boxes,'20');nodes['#fields-form'].values={...c.fields};await context.run('confirm-class');await context.run('request');await context.run('approve');assert.equal(c.snapshot.row.total,1260);
 }
});

test('UI-02 manual box entries enforce the same positive safe decimal integer rule',async()=>{
 for(const value of ['9007199254740993','9007199254740992','1e3','1/2','0x20','1.0','-2','0']){
  const {context,nodes}=harness();nodes['#fields-form'].values.boxes=value;
  await context.run('save-fields');await context.run('confirm-class');
  assert(core.missing(context.getCase().fields).includes('boxes'),value);
  await assert.rejects(context.run('request'),/不能直接计算/);
  assert.equal(context.getCase().snapshot,undefined);assert.equal(context.getCase().locked,false);
 }
 for(const value of ['1','20','9007199254740991'])assert.equal(core.validBoxes(value),true);
});

test('backend export uses the current quote, version and matching approval',()=>{
 const {context}=harness(), c=context.getCase();
 c.serverDraft={version:3,input:{product_name:'收纳盒',volume_cbm:'3',weight_kg:'400'},
  quote:{base_freight_usd:'2460.00',calculation:'3 × 820',source:{file:'rate-A-v1.xlsx',sheet:'海运',price_cell:'E5',sha256:'a'.repeat(64)},unresolved:['保险费待核定']},
  approval:{case:{id:c.id,version:3},scope:'internal_quote_review',state:'REJECTED',approval_id:'review-3'}};
 const report=()=>vm.runInContext("report(current(),'内部试算记录')",context);
 for(const expected of ['v3','2460.00 USD','海运!E5','保险费待核定','内部草稿审核：已拒绝','review-3'])assert.ok(report().includes(expected));
 c.serverDraft.version=4;c.serverDraft.quote=null;
 assert.match(report(),/本版尚无审批记录/);assert.doesNotMatch(report(),/2460|review-3|已拒绝/);
});

test('switching freight preserves prior estimates and requires a new approval before order',()=>{
 const {context}=harness(), c=context.getCase();
 const oldId=c.id,oldFields={...c.fields};
 core.requestApproval(c,rules,'测试');core.decide(c,true,'测试');
 const first=c.snapshot;
 core.reselectChannel(c,'测试');
 assert.equal(c.id,oldId);assert.deepEqual({...c.fields},oldFields);
 assert.equal(c.previousTrials[0],first);assert.equal(c.snapshot,null);
 assert.throws(()=>core.placeOrder(c,'测试',true));
 assert.throws(()=>core.requestApproval(c,rules,'测试'));
 c.selected='空运';core.requestApproval(c,rules,'测试');
 assert.equal(c.approval,'待审批');core.decide(c,true,'测试');
 assert.notEqual(c.snapshot.row.total,first.row.total);
 core.placeOrder(c,'测试',true);assert.throws(()=>core.reselectChannel(c,'测试'));
});

test('channel comparison exports only completed snapshots with original amounts and transit estimates',()=>{
 const {context}=harness(), c=context.getCase();
 core.requestApproval(c,rules,'测试');core.decide(c,true,'测试');core.reselectChannel(c,'测试');
 const compareReport=()=>vm.runInContext("report(current(),'货运方案对比')",context);
 c.selected='空运';core.requestApproval(c,rules,'测试');
 assert.match(compareReport(),/历史方案 · v1 · 海运/);
 assert.doesNotMatch(compareReport(),/当前方案|空运/);
 core.decide(c,true,'测试');
 const output=compareReport();
 assert.match(output,/当前方案 · v2 · 空运/);
 assert.match(output,new RegExp('预计 '+c.snapshot.row.rule.transitDays+' 天'));
 assert.ok(output.indexOf('当前方案')<output.indexOf('历史方案'));
 assert.ok(output.includes(c.previousTrials[0].row.rule.cell));
});
