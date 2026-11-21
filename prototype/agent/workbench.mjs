import {execFile} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {createServer} from 'node:http';
import {randomUUID} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {runAgent,toolRegistry} from './run.mjs';

const port=Number(process.env.FREIGHT_PORT || 4178);
const origin='http://127.0.0.1:'+port;
const jobs=new Map();
const files=new Map(Object.entries({'index.html':'text/html','style.css':'text/css','app.js':'text/javascript','core.js':'text/javascript','inquiry-panel.js':'text/javascript','demo-rates.json':'application/json'}));
function snapshot(job) {
  const {id,status,detail}=job;
  return {id,status,detail};
}
async function processInquiry(job,prompt) {
  const registry=toolRegistry({request:({reason,signal})=>new Promise(resolve=>{
    job.status='review';
    job.detail=reason;
    const finish=decision=>{
      signal.removeEventListener('abort',expire);
      delete job.decide;
      job.status='running';
      job.detail='正在处理决定';
      resolve(decision);
    };
    const expire=()=>finish('denied');
    job.decide=finish;
    if(signal.aborted) expire();
    else signal.addEventListener('abort',expire,{once:true});
  })});
  try {
    const r=await runAgent({prompt,customerInquiry:true,registry});
    const q=r.estimate;
    job.status='done';
    job.detail=q ? ['基础运费：'+q.calculation,'来源：'+q.source.file+' / '+q.source.sheet+' / '+q.source.price_cell,...q.selected_fees.map(f=>f.code+' '+f.amount+' '+f.currency),'最终应付：待核定',...q.unresolved].join('\n') : (r.reason || r.text || r.status);
  } catch(error) {job.status='error';job.detail=error.message;}
  finally {setTimeout(()=>jobs.delete(job.id),30*60*1000).unref();}
}
async function payload(req) {
  if(req.headers.origin!==origin || req.headers['content-type']!=='application/json') throw Error('请求来源无效');
  let size=0;const chunks=[];
  for await (const chunk of req) {size+=chunk.length;if(size>40000)throw Error('询价过长');chunks.push(chunk);}
  return JSON.parse(Buffer.concat(chunks).toString());
}
createServer(async(req,res)=>{
  const json=(status,value)=>{res.writeHead(status,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(value));};
  try {
    if(req.headers.host!=='127.0.0.1:'+port)return json(403,{error:'仅本机使用'});
    const path=new URL(req.url,origin).pathname;
    if(req.method==='POST' && path==='/drafts'){
      const input=await payload(req);
      const result=await new Promise((resolve,reject)=>{
        const child=execFile(process.env.FREIGHT_PYTHON || (process.platform==='win32'?'python':'python3'),
          [fileURLToPath(new URL('../workbench_draft.py',import.meta.url))],{timeout:15000},
          (err,out)=>err?reject(Error('审批服务暂不可用')):resolve(out));
        child.stdin.on('error',()=>{});
        child.stdin.end(JSON.stringify(input));
      });
      const data=JSON.parse(result);
      return json(data.status || 200,data);
    }
    if(req.method==='POST' && path==='/inquiries'){
      const {prompt}=await payload(req);
      if(typeof prompt!=='string'||!prompt.trim()||prompt.length>8000)return json(400,{error:'请输入有效询价'});
      if([...jobs.values()].some(j=>['running','review'].includes(j.status)))return json(409,{error:'请先完成当前询价'});
      const job={id:randomUUID(),status:'running',detail:'本机千问正在读取客户询价'};
      jobs.set(job.id,job);void processInquiry(job,prompt);return json(202,snapshot(job));
    }
    if(path.startsWith('/inquiries/')){
      const job=jobs.get(path.slice('/inquiries/'.length));
      if(!job)return json(404,{error:'询价不存在或已过期'});
      if(req.method==='POST'){
        const {decision}=await payload(req);
        if(!job.decide||!['approve','reject'].includes(decision))return json(409,{error:'当前无法确认'});
        job.decide(decision==='approve'?'allowed-once':'denied');
      }else if(req.method!=='GET')return json(405,{error:'请求无效'});
      return json(200,snapshot(job));
    }
    const file=path==='/'?'index.html':path.slice(1);
    if(req.method!=='GET'||!files.has(file))return json(404,{error:'不存在'});
    let body=await readFile(new URL('../../frontend/workbench/dist/'+file,import.meta.url),'utf8');
    if(file==='index.html')body=body.replace('</head>','<meta name="local-inquiries" content="enabled"><script type="module" src="inquiry-panel.js"></script></head>');
    res.writeHead(200,{'Content-Type':files.get(file)+'; charset=utf-8','Cache-Control':'no-store'});res.end(body);
  }catch(error){json(400,{error:error.message});}
}).listen(port,'127.0.0.1',()=>console.log('工作台 '+origin));
