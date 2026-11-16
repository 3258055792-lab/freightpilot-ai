import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {apply, definitions, runFreight} from './freight-plugin.mjs';

const sourceSha = createHash('sha256').update(await readFile(new URL('../source/synthetic-demo.xlsx', import.meta.url))).digest('hex');
const calculation = () => ({product_name:'塑料泳池',category:'普货三类',volume_cbm:'5',weight_kg:'100'});
const execution = signal => ({agent:{id:'test-agent'},callId:'test-call',signal:signal ?? new AbortController().signal});
function registered(approval, withGet=true) {
  const tools = new Map();
  apply({tools:{register(tool) { tools.set(tool.name,tool); }},
    ...(withGet ? {get(name) { return name === 'approval' ? approval : undefined; }} : {})});
  return tools;
}
const call = async (tools, name, args, exec=execution()) => JSON.parse(await tools.get(name).execute(args,exec));
function assertNoAmounts(value) {
  const json = JSON.stringify(value);
  for (const field of ['base_freight_usd','unit_price_usd_per_cbm','known_subtotals','total_payable']) {
    assert.equal(Object.hasOwn(value,field),false,field);
  }
  assert.doesNotMatch(json,/5500\.00|1100\.00|820|930/);
}

test('registers all three tools with required product and source schema', () => {
  const tools = registered();
  assert.deepEqual([...tools.keys()],['freight_lookup','freight_classify','freight_calculate']);
  assert.equal(definitions.length,3);
  const classify = tools.get('freight_classify').parameters;
  assert.equal(classify.properties.product_name.type,'string');
  assert.ok(classify.required.includes('product_name'));
  assert.ok(!classify.required.includes('source_sha256'));
  assert.equal(classify.properties.source_sha256.type,'string');
  const calculate = tools.get('freight_calculate').parameters;
  for (const name of ['product_name','category','volume_cbm','weight_kg']) assert.ok(calculate.required.includes(name));
  assert.equal(Object.hasOwn(calculate.properties,'confirmed'),false);
});

test('real plugin subprocess returns exact candidate and original source, without prices', async () => {
  const result = await call(registered(),'freight_classify',{product_name:'塑料收纳盒'});
  assert.equal(result.status,'candidates_found');
  assert.equal(result.classification_status,'pending_review');
  assert.equal(result.classification_confirmed,false);
  assert.equal(result.usable_for_quote,false);
  assert.equal(result.source.sha256,sourceSha);
  assert.equal(result.source.sheet,'巴西海运双清包税');
  assert.equal(result.candidates[0].category,'普货一类');
  assert.equal(result.candidates[0].category_cell,'A5');
  assert.equal(result.candidates[0].evidence_cell,'B5');
  assertNoAmounts(result);
});

test('broad names require clarification and duplicate source matches stay ambiguous', async () => {
  const broad = JSON.parse(await runFreight('freight_classify',{product_name:'塑料制品'}));
  assert.equal(broad.status,'needs_clarification');
  assert.deepEqual(broad.candidates,[]);
  assertNoAmounts(broad);
  const ambiguous = await call(registered(),'freight_classify',{product_name:'泳镜'});
  assert.equal(ambiguous.status,'ambiguous');
  assert.deepEqual(ambiguous.candidates.map(item => item.evidence_cell),['B6','B7']);
  assert.equal(ambiguous.classification_confirmed,false);
  assertNoAmounts(ambiguous);
});

test('schema and Python validation reject invalid classification inputs', async () => {
  const tools = registered();
  await assert.rejects(() => call(tools,'freight_classify',{}));
  await assert.rejects(() => call(tools,'freight_classify',{product_name:123}));
  for (const args of [{product_name:'塑料'}, {product_name:'塑料收纳盒',source_sha256:'not-a-sha'}]) {
    const result = await call(tools,'freight_classify',args);
    assert.equal(result.usable_for_quote,false);
    assertNoAmounts(result);
  }
  const invalid = await call(tools,'freight_classify',{product_name:' '});
  assert.equal(invalid.status,'error');
  const missingSource = await call(tools,'freight_classify',{product_name:'塑料收纳盒',source_sha256:'f'.repeat(64)});
  assert.equal(missingSource.status,'error');
});

test('legacy lookup still retrieves original cells and lists all sheets', async () => {
  const tools = registered();
  const list = await call(tools,'freight_lookup',{});
  assert.equal(list.sheets.length,17);
  const result = await call(tools,'freight_lookup',{sheet:'巴西海运双清包税',cell:'B21'});
  assert.equal(result.matches,1);
  assert.match(result.results[0].raw_text,/300KG/);
  assert.equal(result.source.sha256,sourceSha);
});

test('direct exported calculation helper never grants model supplied confirmation', async () => {
  for (const args of [calculation(),{...calculation(),confirmed:true},{...calculation(),classification_confirmed:true}]) {
    const result = JSON.parse(await runFreight('freight_calculate',args));
    assert.equal(result.status,'blocked');
    assert.equal(result.classification_confirmed,false);
    assertNoAmounts(result);
  }
});

test('missing approval channel or execution context fails closed', async () => {
  const cases = [
    [registered(),execution()],
    [registered(undefined,false),execution()],
    [registered({request:async () => 'allowed-once'}),{}],
    [registered({request:async () => 'allowed-once'}),{agent:{}}],
  ];
  for (const [tools,exec] of cases) {
    const result = await call(tools,'freight_calculate',calculation(),exec);
    assert.equal(result.status,'blocked');
    assertNoAmounts(result);
  }
});

test('only allowed-once grants; rejected, unavailable, cancelled and malformed answers block', async () => {
  for (const outcome of ['rejected','unavailable','cancelled','allow',true,{confirmed:true},undefined]) {
    let requested = 0;
    const tools = registered({request:async () => {requested++;return outcome;}});
    const result = await call(tools,'freight_calculate',calculation());
    assert.equal(requested,1);
    assert.equal(result.status,'blocked');
    assert.equal(result.classification_confirmed,false);
    assertNoAmounts(result);
  }
});

test('a throwing approval channel fails closed', async () => {
  const result = await call(registered({request:async () => {throw new Error('no reviewer');}}),
    'freight_calculate',calculation());
  assert.equal(result.status,'blocked');
  assertNoAmounts(result);
});

test('unknown confirmation or bypass parameters cannot reach the approval service', async () => {
  let requested = 0;
  const tools = registered({request:async () => {requested++;return 'allowed-once';}});
  for (const extra of [{confirmed:true},{classification_confirmed:true},{approval:'allowed-once'},{database:'/tmp/other.db'}]) {
    const result = await call(tools,'freight_calculate',{...calculation(),...extra});
    assert.equal(result.status,'blocked');
    assertNoAmounts(result);
  }
  assert.equal(requested,0);
});

test('missing product, broad product, mismatched category and invalid version cannot request approval', async () => {
  let requested = 0;
  const tools = registered({request:async () => {requested++;return 'allowed-once';}});
  const {product_name,...missingProduct} = calculation();
  await assert.rejects(() => call(tools,'freight_calculate',missingProduct));
  for (const patch of [
    {product_name:'塑料制品'}, {category:'普货一类'}, {product_name:' '},
    {source_sha256:'f'.repeat(64)}, {source_sha256:'invalid'},
  ]) {
    const result = await call(tools,'freight_calculate',{...calculation(),...patch});
    assert.equal(result.status,'blocked');
    assertNoAmounts(result);
  }
  assert.equal(requested,0);
});

test('trusted single-call approval preserves legacy estimate, binds SHA and records reviewed inputs', async () => {
  let request;
  const exec = execution();
  const tools = registered({request:async req => {request=req;return 'allowed-once';}});
  const result = await call(tools,'freight_calculate',calculation(),exec);
  assert.equal(request.agent,exec.agent);
  assert.equal(request.signal,exec.signal);
  assert.equal(request.callId,exec.callId);
  assert.equal(request.toolName,'freight_calculate');
  for (const text of ['塑料泳池','普货三类','A7','B7','Excel 来源：synthetic-demo.xlsx','运输渠道：巴西海运双清包税','总体积：5 CBM','总重量：100 KG','熏蒸商检未选择','合页 0 件','卡脚 0 件','仅批准本次分类使用和待审核试算，不审批运价。']) assert.ok(request.reason.includes(text));
  assert.doesNotMatch(request.reason, /[{}]|sha256|source_sha|volume_cbm|weight_kg|classification_review|[a-f0-9]{64}/i);
  assert.equal(result.status,'pending_review_estimate');
  assert.equal(result.base_freight_usd,'5500.00');
  assert.equal(result.source.sha256,sourceSha);
  assert.equal(result.usable_for_quote,false);
  assert.equal(result.total_payable,null);
  assert.equal(result.classification_confirmed,true);
  assert.equal(result.classification_review.scope,'this_call_only');
  assert.equal(result.classification_review.input.source_sha256,sourceSha);
});

test('approval always repeats per call; no cached model or session confirmation', async () => {
  let requested=0;
  const tools=registered({request:async () => (++requested === 1 ? 'allowed-once' : 'rejected')});
  const first=await call(tools,'freight_calculate',calculation());
  const second=await call(tools,'freight_calculate',calculation());
  assert.equal(first.status,'pending_review_estimate');
  assert.equal(second.status,'blocked');
  assertNoAmounts(second);
  assert.equal(requested,2);
});

test('ambiguous candidate review includes both original alternatives', async () => {
  let reason;
  const tools=registered({request:async req => {reason=req.reason;return 'rejected';}});
  const result=await call(tools,'freight_calculate',{...calculation(),product_name:'泳镜',category:'普货二类'});
  for (const text of ['普货二类','普货三类','B6','B7','多个候选']) assert.ok(reason.includes(text));
  assert.equal(result.status,'blocked');
  assertNoAmounts(result);
});

test('approval binds a snapshot despite argument mutation while waiting', async () => {
  const args=calculation();
  const tools=registered({request:async () => {
    args.category='普货一类';args.volume_cbm='10';args.source_sha256='f'.repeat(64);args.product_name='塑料收纳盒';
    return 'allowed-once';
  }});
  const result=await call(tools,'freight_calculate',args);
  assert.equal(result.category,'普货三类');
  assert.equal(result.base_freight_usd,'5500.00');
  assert.equal(result.source.sha256,sourceSha);
  assert.equal(result.classification_review.product_name,'塑料泳池');
  assert.equal(result.classification_review.input.volume_cbm,'5');
});

test('cancellation after approval prevents calculation', async () => {
  const controller=new AbortController();
  const tools=registered({request:async () => {controller.abort();return 'allowed-once';}});
  const result=await call(tools,'freight_calculate',calculation(),execution(controller.signal));
  assert.equal(result.status,'blocked');
  assertNoAmounts(result);
});

test('plugin queries and approved estimate preserve original database and Excel bytes', async () => {
  const files=['../output/freight_quotes.db','../source/synthetic-demo.xlsx']
    .map(path => fileURLToPath(new URL(path,import.meta.url)));
  const hash=async path => createHash('sha256').update(await readFile(path)).digest('hex');
  const before=await Promise.all(files.map(hash));
  const tools=registered({request:async () => 'allowed-once'});
  await call(tools,'freight_classify',{product_name:'塑料收纳盒'});
  await call(tools,'freight_lookup',{sheet:'巴西海运双清包税',cell:'B21'});
  await call(tools,'freight_calculate',calculation());
  assert.deepEqual(await Promise.all(files.map(hash)),before);
});


test('approval summary shows selected extra services without technical payload', async () => {
  let reason;
  const tools=registered({request:async req => {reason=req.reason;return 'allowed-once';}});
  const result=await call(tools,'freight_calculate',{
    ...calculation(),fumigation:true,hinge_count:2,pallet_foot_count:3,
  });
  for (const text of ['熏蒸商检已选择','合页 2 件','卡脚 3 件']) assert.ok(reason.includes(text));
  assert.doesNotMatch(reason, /[{}]|sha256|hinge_count|pallet_foot_count|[a-f0-9]{64}/i);
  assert.deepEqual(result.known_subtotals,{USD:'5500.00',CNY:'1180.00'});
  assert.equal(result.classification_review.input.source_sha256,sourceSha);
});
