import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';

const directory = process.env.FP_QA_CANDIDATE_DIST;
assert.ok(directory, '请将 FP_QA_CANDIDATE_DIST 指向已核对 SHA 的候选 dist 目录');
const corePath = resolve(directory, 'core.js');
const ratesPath = resolve(directory, 'demo-rates.json');
const core = await import(pathToFileURL(corePath).href);
const ratesBytes = await readFile(ratesPath);
const rules = JSON.parse(ratesBytes).rules;
const input = '目的国：德国，货名：塑料收纳盒，货物类别：普货，3CBM，400kg，20箱，40天，派送需求：到门，清关需求：代理清关，税费需求：DAP';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
console.log(JSON.stringify({ coreSha256: hash(await readFile(corePath)), sourceSha256: hash(ratesBytes) }));

function attempt(fields) {
  const c = core.newCase('FP-03 合成数值回归');
  c.fields = fields;
  c.selected = '海运';
  core.confirmClassification(c, '模拟分类核验人');
  let error = null;
  try { core.requestApproval(c, rules, '模拟发起人'); }
  catch (e) { error = e.message; }
  return { c, error };
}

test('正常20箱可申请，1260仅为已锁定的预计算金额', () => {
  const { c, error } = attempt(core.extract(input).fields);
  assert.equal(error, null);
  assert.equal(c.approval, '待审批');
  assert.equal(c.trial, '未生成');
  assert.equal(c.snapshot.row.total, 1260);
});

for (const token of ['1/2箱', '1e3箱']) {
  test(`${token}不能截取尾数形成可审批输入`, t => {
    const parsed = core.extract(input.replace('20箱', token));
    const { c, error } = attempt(parsed.fields);
    t.diagnostic(JSON.stringify({ input: token, boxes: parsed.fields.boxes,
      evidence: parsed.evidence.boxes, missing: core.missing(parsed.fields),
      approval: c.approval, snapshotBoxes: c.snapshot?.fields.boxes, error }));
    assert.deepEqual({ boxes: parsed.fields.boxes,
      invalid: core.missing(parsed.fields).includes('boxes'), blocked: error !== null },
    { boxes: '', invalid: true, blocked: true });
  });
}

test('手工箱数超安全整数必须在申请前被拒绝', t => {
  const fields = { ...core.extract(input).fields, boxes: '9007199254740993' };
  const { c, error } = attempt(fields);
  t.diagnostic(JSON.stringify({ boxes: fields.boxes, numericValue: Number(fields.boxes),
    missing: core.missing(fields), approval: c.approval,
    snapshotBoxes: c.snapshot?.fields.boxes, error }));
  assert.deepEqual({ invalid: core.missing(fields).includes('boxes'), blocked: error !== null },
    { invalid: true, blocked: true });
});
