// Streamlit v1 component transport; the original workbench owns all UI.
(() => {
  let current = null, ready = false;
  const queue = [];
  let sequence = 0;
  const requestId = () => (globalThis.crypto?.randomUUID ? crypto.randomUUID() : Date.now().toString(36)+'-'+(++sequence)+'-'+Math.random().toString(36).slice(2));
  if (!globalThis.structuredClone) globalThis.structuredClone = value => JSON.parse(JSON.stringify(value));
  const showStatus = text => {const el=document.getElementById('status');if(el)el.textContent=text;};
  const send = (type, payload = {}) => parent.postMessage({isStreamlitMessage: true, type, ...payload}, '*');
  function pump() {
    if (!ready || current || !queue.length) return;
    current = queue.shift();
    const task = current;
    task.timer = setTimeout(() => {
      if (current !== task) return;
      current = null;
      task.reject(Error('云端响应超时，请检查网络后重试；仍无响应请刷新页面重新上传。'));
      pump();
    }, 120000);
    send('streamlit:setComponentValue', {value: current.request, dataType: 'json'});
  }
  function rpc(path, data) {
    return new Promise((resolve, reject) => {
      queue.push({request: {id: requestId(), path, data}, resolve, reject});
      pump();
    });
  }
  window.addEventListener('message', event => {
    if (event.source !== parent || event.data.type !== 'streamlit:render') return;
    ready = true;
    const response = event.data.args.response;
    if (current && response && response.id === current.request.id) {
      const task = current; clearTimeout(task.timer); current = null;
      response.error ? task.reject(Error(response.error)) : task.resolve(response.value);
    }
    pump();
  });
  setTimeout(() => {
    if (!ready) {
      showStatus('云端连接未建立，请刷新页面，或使用 Chrome / Edge 打开。');
      while(queue.length) queue.shift().reject(Error('云端连接未建立，请刷新页面重试'));
    }
  }, 30000);
  const encodeBlob = blob => new Promise((resolve,reject)=>{
    const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);
    reader.onerror=()=>reject(Error('读取文件失败'));reader.readAsDataURL(blob);
  });
  window.cloudUpload = async files => {
    const total = files.reduce((sum,file)=>sum+file.size,0);
    const upload = await rpc('/api/upload/begin',{files:files.map(file=>({name:file.name,size:file.size}))});
    let sent=0;
    try {
      for(let index=0;index<files.length;index++) {
        const file=files[index];
        for(let offset=0;offset<file.size;offset+=upload.chunk_size) {
          const blob=file.slice(offset,offset+upload.chunk_size);
          await rpc('/api/upload/chunk',{upload:upload.upload,index,offset,base64:await encodeBlob(blob)});
          sent+=blob.size;
          const percent=total?Math.floor(sent/total*100):100;
          showStatus(`正在上传：${percent}%（${(sent/1048576).toFixed(1)} / ${(total/1048576).toFixed(1)} MB）`);
          document.getElementById('progress-wrap').classList.remove('hidden');
          document.getElementById('task-progress').value=percent;
          document.getElementById('progress-label').textContent='上传 '+percent+'%';
        }
      }
      showStatus('上传完成，正在识别图层…');
      return await rpc('/api/upload/finish',{upload:upload.upload});
    } catch(error) {
      rpc('/api/upload/abort',{upload:upload.upload}).catch(()=>{});
      throw error;
    }
  };
  window.fetch = async (path, options = {}) => {
    const value = await rpc(path, options.body ? JSON.parse(options.body) : undefined);
    return {ok:true, json:async()=>value};
  };
  document.addEventListener('click', async event => {
    const link = event.target.closest('a');
    if (!link || !link.getAttribute('href')?.startsWith('/api/jobs/')) return;
    event.preventDefault();
    if (link.dataset.loading) return;
    link.dataset.loading = 'true';
    const label = link.textContent;
    link.textContent = '正在准备下载…';
    try {
      const path=link.getAttribute('href');
      const file = await rpc(path,{metadata:true});
      const chunks=[];
      for(let offset=0;offset<file.size;offset+=file.chunk_size) {
        const part=await rpc(path,{offset});
        chunks.push(Uint8Array.from(atob(part.base64), c=>c.charCodeAt(0)));
        link.textContent=`正在下载 ${Math.min(100,Math.floor((offset+file.chunk_size)/file.size*100))}%…`;
      }
      const url = URL.createObjectURL(new Blob(chunks, {type:'application/octet-stream'}));
      const download = document.createElement('a');
      download.href=url; download.download=file.name; download.click();
      setTimeout(()=>URL.revokeObjectURL(url),60000);
    } catch(e) {document.getElementById('status').textContent='下载失败：'+e.message;}
    finally {link.textContent=label;delete link.dataset.loading;}
  });
  function resize(){send('streamlit:setFrameHeight',{height:Math.max(1000,document.documentElement.scrollHeight)});}
  window.addEventListener('load',()=>{resize();new ResizeObserver(resize).observe(document.body);});
  send('streamlit:componentReady',{apiVersion:1});
})();
