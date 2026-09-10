const form = document.getElementById('uploadForm');
const input = document.getElementById('fileInput');
const dropzone = document.getElementById('dropzone');
const label = document.getElementById('fileLabel');
const button = document.getElementById('convertBtn');
const panel = document.getElementById('progressPanel');
const bar = document.getElementById('bar');
const percent = document.getElementById('percent');
const statusText = document.getElementById('statusText');
const speed = document.getElementById('speed');
const eta = document.getElementById('eta');
const result = document.getElementById('result');
const download = document.getElementById('download');
const error = document.getElementById('error');

let selectedFile = null;
let startedAt = 0;

function chooseFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.ts')) return showError('Please choose a .ts file.');
  selectedFile = file;
  label.textContent = `${file.name} (${formatBytes(file.size)})`;
  button.disabled = false;
  error.classList.add('hidden');
}

input.addEventListener('change', () => chooseFile(input.files[0]));
['dragenter', 'dragover'].forEach(e => dropzone.addEventListener(e, ev => { ev.preventDefault(); dropzone.classList.add('drag'); }));
['dragleave', 'drop'].forEach(e => dropzone.addEventListener(e, ev => { ev.preventDefault(); dropzone.classList.remove('drag'); }));
dropzone.addEventListener('drop', ev => chooseFile(ev.dataTransfer.files[0]));

form.addEventListener('submit', async ev => {
  ev.preventDefault();
  if (!selectedFile) return;
  button.disabled = true;
  panel.classList.remove('hidden');
  result.classList.add('hidden');
  error.classList.add('hidden');
  setProgress(0, 'Uploading…');
  startedAt = performance.now();

  const data = new FormData();
  data.append('file', selectedFile);
  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/upload');
  xhr.upload.onprogress = e => {
    if (e.lengthComputable) setProgress((e.loaded / e.total) * 10, 'Uploading…');
  };
  xhr.onload = () => {
    if (xhr.status !== 200) return showError(JSON.parse(xhr.responseText).error || 'Upload failed.');
    const job = JSON.parse(xhr.responseText);
    poll(job.job_id);
  };
  xhr.onerror = () => showError('Network error during upload.');
  xhr.send(data);
});

async function poll(id) {
  try {
    const res = await fetch(`/status/${id}`, { cache: 'no-store' });
    const job = await res.json();
    if (job.status === 'queued') setProgress(10, 'Queued…');
    else if (job.status === 'converting') {
      const p = 10 + (Number(job.progress || 0) * 0.9);
      setProgress(p, 'Converting…');
      speed.textContent = `Speed: ${job.speed || '—'}`;
      const elapsed = (performance.now() - startedAt) / 1000;
      const remaining = Number(job.progress) > 0 ? elapsed * (100 - Number(job.progress)) / Number(job.progress) : 0;
      eta.textContent = `ETA: ${remaining ? formatTime(remaining) : '—'}`;
    } else if (job.status === 'complete') {
      setProgress(100, 'Complete');
      speed.textContent = 'Ready to download';
      eta.textContent = '';
      download.href = job.download;
      result.classList.remove('hidden');
      button.disabled = false;
      return;
    } else if (job.status === 'error') return showError(job.error || 'Conversion failed.');
    setTimeout(() => poll(id), 400);
  } catch (_) {
    setTimeout(() => poll(id), 1000);
  }
}

function setProgress(value, text) {
  const v = Math.max(0, Math.min(100, value));
  bar.style.width = `${v}%`;
  percent.textContent = `${Math.round(v)}%`;
  statusText.textContent = text;
}
function showError(message) { error.textContent = message; error.classList.remove('hidden'); button.disabled = false; }
function formatBytes(bytes) { const units = ['B','KB','MB','GB','TB']; let i=0; while(bytes >= 1024 && i < units.length-1){bytes/=1024;i++;} return `${bytes.toFixed(i?1:0)} ${units[i]}`; }
function formatTime(s) { s = Math.max(0, Math.round(s)); const m=Math.floor(s/60), sec=s%60; return `${m}m ${String(sec).padStart(2,'0')}s`; }
