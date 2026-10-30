// Streamlit v1 component transport; the original workbench owns all UI.
(() => {
  let current = null, ready = false;
  const queue = [];
  const send = (type, payload = {}) => parent.postMessage({isStreamlitMessage: true, type, ...payload}, '*');
  function pump() {
    if (!ready || current || !queue.length) return;
    current = queue.shift();
    send('streamlit:setComponentValue', {value: current.request, dataType: 'json'});
  }
  function rpc(path, data) {
    return new Promise((resolve, reject) => {
      queue.push({request: {id: crypto.randomUUID(), path, data}, resolve, reject});
      pump();
    });
  }
  window.addEventListener('message', event => {
    if (event.source !== parent || event.data.type !== 'streamlit:render') return;
    ready = true;
    const response = event.data.args.response;
    if (current && response && response.id === current.request.id) {
      const task = current; current = null;
      response.error ? task.reject(Error(response.error)) : task.resolve(response.value);
    }
    pump();
  });
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
      const file = await rpc(link.getAttribute('href'));
      const bytes = Uint8Array.from(atob(file.base64), c => c.charCodeAt(0));
      const url = URL.createObjectURL(new Blob([bytes], {type:'application/octet-stream'}));
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
