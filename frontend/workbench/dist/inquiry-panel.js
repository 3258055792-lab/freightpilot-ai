const panel=document.createElement('dialog');
panel.innerHTML='<h2>本地千问询价</h2><label>客户询价原文<textarea rows="4" maxlength="8000" style="width:100%"></textarea></label><pre style="white-space:pre-wrap" aria-live="polite"></pre><button data-send>开始模拟询价</button> <button data-yes hidden>确认本次试算</button> <button data-no hidden>拒绝</button> <button data-close>关闭</button>';
panel.style.cssText='width:min(600px,90vw);border:1px solid #ddd;border-radius:12px;padding:24px';
document.body.append(panel);
const field=panel.querySelector('textarea'),output=panel.querySelector('pre');
const button=name=>panel.querySelector('[data-'+name+']');
let jobId, timer;
document.addEventListener('click',e=>{if(e.target.closest('#local-inquiry'))panel.showModal();});
button('close').onclick=()=>panel.close();
async function api(path,body){
  const response=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
  const data=await response.json();
  if(!response.ok)throw Error(data.error || '询价请求失败');
  return data;
}
function display(job){
  jobId=job.id;
  const busy=['running','review'].includes(job.status);
  field.disabled=busy;button('send').disabled=busy;
  button('yes').hidden=button('no').hidden=job.status!=='review';
  output.textContent=job.detail;
  clearTimeout(timer);
  if(busy)timer=setTimeout(()=>refresh(job.id),800);
}
let generation=0;
async function refresh(id){
  const previous=generation;
  if(id!==jobId)return;
  try{const job=await api('/inquiries/'+id);if(previous===generation)display(job);}
  catch(e){if(previous===generation)output.textContent=e.message;}
}
button('send').onclick=async()=>{
  button('send').disabled=true;
  try{display(await api('/inquiries',{prompt:field.value}));}
  catch(e){output.textContent=e.message;button('send').disabled=false;}
};
for(const [name,decision] of [['yes','approve'],['no','reject']]){
  button(name).onclick=async()=>{
    button('yes').hidden=button('no').hidden=true;
    try{const job=await api('/inquiries/'+jobId,{decision});generation++;display(job);}
    catch(e){output.textContent=e.message;}
  };
}
