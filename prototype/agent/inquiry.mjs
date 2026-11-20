import {createInterface} from 'node:readline/promises';
import {stdin, stdout} from 'node:process';
import {runAgent, toolRegistry} from './run.mjs';

async function main() {
  if (!stdin.isTTY) throw new Error('请在本机交互式终端打开询价，人工确认需要操作员在场。');
  const terminal = createInterface({input: stdin, output: stdout});
  try {
    const inquiry = process.argv.slice(2).join(' ') || await terminal.question('请输入客户询价原文：');
    const registry = toolRegistry({
      async request({reason, signal}) {
        console.log('\n客户询价原文：', inquiry, '\n\n', reason);
        const answer = await terminal.question('请对照原文核实路线、商品、数量和服务。输入“确认试算”继续，其他输入停止：', {signal});
        return answer.trim() === '确认试算' ? 'allowed-once' : 'denied';
      }
    });
    const result = await runAgent({prompt: inquiry, registry, customerInquiry:true,
      model:process.env.FREIGHT_MODEL || 'qwen3.5-9b-mlx',
      baseUrl:process.env.FREIGHT_MODEL_URL || 'http://127.0.0.1:1234/v1'});
    if (result.estimate?.status === 'pending_review_estimate') {
      const quote = result.estimate;
      console.log('\n报价草稿（合成运价）\n基础运费：', quote.calculation);
      for (const fee of quote.selected_fees) console.log(fee.code, fee.amount, fee.currency);
      console.log('依据：', quote.source.file, quote.source.sheet, quote.source.price_cell);
      console.log('最终应付：待核定。', quote.unresolved.join('\n'));
    } else {
      console.log('尚未生成报价草稿。', result.reason || (result.text ? '千问的询价反馈（待核对）：'+result.text : result.status));
    }
  } finally {
    terminal.close();
  }
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
