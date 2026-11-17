import test from 'node:test';
import assert from 'node:assert/strict';
import {runAgent,toolRegistry} from './run.mjs';
const call=(name,args,id='1')=>({role:'assistant',tool_calls:[{id,type:'function',function:{name,arguments:JSON.stringify(args)}}]});
function model(...answers){let requests=[];return {requests,fetchImpl:async(url,options)=>{requests.push(JSON.parse(options.body));assert(answers.length,'unexpected extra model request');return {ok:true,json:async()=>({choices:[{message:answers.shift(),finish_reason:'stop'}]})}}}}
test('model calls real Python classification; exact evidence is returned into next model round',async()=>{
 const m=model(call('freight_classify',{product_name:'塑料收纳盒'}),{content:'候选待人工核验'});
 const r=await runAgent({prompt:'测试商品分类',...m});assert.equal(r.status,'completed');
 const e=r.events.find(e=>e.type==='tool_result');assert.equal(e.result.candidates[0].category,'普货一类');assert.equal(e.result.classification_confirmed,false);
 assert.equal(JSON.parse(m.requests[1].messages.at(-1).content).source.sha256,e.result.source.sha256);
});
test('model supplied approval marker fails closed and does not get another round',async()=>{
 const m=model(call('freight_calculate',{product_name:'塑料收纳盒',category:'普货一类',volume_cbm:'3',weight_kg:'400',confirmed:true}));
 const r=await runAgent({prompt:'测试',...m});assert.equal(r.status,'blocked');assert.equal(m.requests.length,1);
});
test('calculation without a human approval channel blocks and stops the loop',async()=>{
 const m=model(call('freight_calculate',{product_name:'塑料收纳盒',category:'普货一类',volume_cbm:'3',weight_kg:'400'}));
 const r=await runAgent({prompt:'测试',...m});assert.equal(r.status,'blocked');assert.match(r.reason,/确认通道/);assert.equal(r.events.at(-1).result.base_freight_usd,undefined);
});
test('unknown tools and malformed arguments never execute',async()=>{
 for(const message of [call('exec',{command:'anything'}),{tool_calls:[{id:'1',type:'function',function:{name:'freight_lookup',arguments:'not json'}}]}])await assert.rejects(runAgent({prompt:'测试',...model(message)}));
});
test('bounded model loop and no-tool reply are not reported as tool execution',async()=>{
 const m=model(call('freight_lookup',{}));assert.equal((await runAgent({prompt:'测试',maxRounds:1,...m})).status,'budget_exhausted');
 assert.equal((await runAgent({prompt:'测试',...model({content:'我已经完成'})})).status,'no_tool_call');
});
test('external endpoint is refused before sending inquiry',async()=>{
 await assert.rejects(runAgent({prompt:'测试',baseUrl:'https://example.com/v1',fetchImpl:()=>assert.fail()}),/local model/);
});
