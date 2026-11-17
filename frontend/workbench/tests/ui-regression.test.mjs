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
 for(const token of ['1.5箱','-2箱','−2箱','1，5箱','1,5箱','1．5箱','.5箱','0箱','1.5 cartons','1-2箱','20箱、30箱']){
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
