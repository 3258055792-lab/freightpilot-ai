import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';

export async function readIntakeSection() {
  const file='SOP-01-v1.md';
  const dir=new URL('../../data/sample/fp01/',import.meta.url);
  const raw=await readFile(new URL(file,dir));
  const sha=createHash('sha256').update(raw).digest('hex');
  const manifest=await readFile(new URL('SHA256SUMS',dir),'utf8');
  if(!manifest.split(/\r?\n/).includes(sha+'  '+file))throw Error('操作规范来源校验失败');
  const section=raw.toString('utf8').match(/^## (§1 [^\r\n]+)\r?\n\s*([^#]+?)(?=^## |(?![\s\S]))/m);
  if(!section)throw Error('操作规范条款缺失');
  return {file,sha,title:section[1],sop:section[2].trim()};
}

// Only answer questions whose entire scope is covered by the cited section.
const coveredQuestions = [
  /^(?:巴西海运)?(?:基础运费)?(?:试算|询价)(?:时)?(?:需要|要)(?:提供|填写|准备)?(?:哪些|什么)资料$/,
  /^(?:巴西海运)?(?:基础运费)?(?:试算|询价)资料(?:缺失|不全)(?:时)?(?:怎么办|怎么处理)$/,
  /^箱数(?:能否|可以|能|是否可以)替代总体积(?:吗)?$/,
];

export async function lookupSop(question) {
  if(typeof question !== 'string' || !question.trim() || question.length > 500)
    throw Error('请输入 1 至 500 字的问题');
  const normalized = question.trim().replace(/[？?。]$/u, '');
  if(!coveredQuestions.some(pattern => pattern.test(normalized)))
    return {status:'insufficient', message:'现有合成操作规范无法确认这个问题，请交操作员核对。'};
  return {status:'cited', source:await readIntakeSection()};
}
