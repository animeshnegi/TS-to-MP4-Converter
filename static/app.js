const input = document.getElementById('fileInput');
const dropzone = document.getElementById('dropzone');
const uploadBtn = document.getElementById('uploadBtn');
const clearBtn = document.getElementById('clearBtn');
const themeToggle = document.getElementById('themeToggle');
const queueEl = document.getElementById('queue');
const errorEl = document.getElementById('error');
const totalCount = document.getElementById('totalCount');
const activeCount = document.getElementById('activeCount');
const queueCount = document.getElementById('queueCount');
const doneCount = document.getElementById('doneCount');

const jobs = new Map();
let selectedFiles = [];

function applyTheme(theme) {
  const light = theme === 'light';
  document.documentElement.classList.toggle('light', light);
  if (themeToggle) {
    const icon = themeToggle.querySelector('.theme-icon');
    const label = themeToggle.querySelector('.theme-label');
    if (icon) icon.textContent = light ? '☾' : '☀';
    if (label) label.textContent = light ? 'Dark' : 'Light';
    themeToggle.setAttribute('aria-label', light ? 'Switch to dark theme' : 'Switch to light theme');
    themeToggle.setAttribute('title', light ? 'Switch to dark theme' : 'Switch to light theme');
  }
}

applyTheme(localStorage.getItem('ts-theme') || 'light');
themeToggle?.addEventListener('click', () => {
  const next = document.documentElement.classList.contains('light') ? 'dark' : 'light';
  localStorage.setItem('ts-theme', next);
  applyTheme(next);
});

input.addEventListener('change', () => addFiles([...input.files]));
['dragenter', 'dragover'].forEach(type => dropzone.addEventListener(type, e => { e.preventDefault(); dropzone.classList.add('drag'); }));
['dragleave', 'drop'].forEach(type => dropzone.addEventListener(type, e => { e.preventDefault(); dropzone.classList.remove('drag'); }));
dropzone.addEventListener('drop', e => addFiles([...e.dataTransfer.files]));
uploadBtn.addEventListener('click', uploadSelected);
clearBtn.addEventListener('click', clearCompleted);

function addFiles(files) {
  const valid = files.filter(f => f.name.toLowerCase().endsWith('.ts'));
  const invalid = files.length - valid.length;
  if (invalid) showError(`${invalid} file(s) skipped. Only .ts files are supported.`);
  selectedFiles.push(...valid);
  renderSelected();
}

function renderSelected() {
  uploadBtn.disabled = selectedFiles.length === 0;
  uploadBtn.textContent = selectedFiles.length > 1 ? `Upload ${selectedFiles.length} files` : 'Upload selected file';
  input.value = '';
}

async function uploadSelected() {
  const files = selectedFiles.splice(0);
  renderSelected();
  files.forEach(file => {
    const id = `local-${crypto.randomUUID()}`;
    jobs.set(id, { id, file, status: 'uploading', uploadProgress: 0, progress: 0, speed: '-', eta: '-', filename: file.name });
    renderJob(id);
    uploadOne(id).catch(err => failJob(id, err.message || 'Upload failed.'));
  });
}

function uploadOne(id) {
  return new Promise((resolve, reject) => {
    const job = jobs.get(id);
    const data = new FormData();
    data.append('file', job.file);
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/upload');
    xhr.upload.onprogress = e => {
      if (e.lengthComputable) {
        job.uploadProgress = e.loaded / e.total * 100;
        renderJob(id);
      }
    };
    xhr.onload = () => {
      let body = {};
      try { body = JSON.parse(xhr.responseText); } catch (_) {}
      if (xhr.status !== 200) return reject(new Error(body.error || 'Upload failed.'));
      job.serverId = body.job_id;
      job.status = 'queued';
      job.uploadProgress = 100;
      renderJob(id);
      poll(id);
      resolve();
    };
    xhr.onerror = () => reject(new Error('Network error during upload.'));
    xhr.send(data);
  });
}

async function poll(localId) {
  const job = jobs.get(localId);
  if (!job?.serverId) return;
  try {
    const res = await fetch(`/status/${job.serverId}`, { cache: 'no-store' });
    if (!res.ok) throw new Error('Status request failed');
    const server = await res.json();
    job.status = server.status;
    job.progress = Number(server.progress || 0);
    job.speed = server.speed || '-';
    job.duration = Number(server.duration || 0);
    job.queuePosition = Number(server.queue_position || 0);
    job.mode = server.mode || '';
    job.download = server.download || '';
    job.outputFilename = server.output_filename || `${job.filename.replace(/\.ts$/i, '')}.mp4`;
    if (server.status === 'complete') {
      job.progress = 100;
      renderJob(localId);
      return;
    }
    if (server.status === 'error') {
      job.error = server.error || 'Conversion failed.';
      renderJob(localId);
      return;
    }
    renderJob(localId);
    setTimeout(() => poll(localId), 350);
  } catch (_) {
    setTimeout(() => poll(localId), 1000);
  }
}

function renderJob(id) {
  const job = jobs.get(id);
  if (!job) return;
  let card = document.getElementById(`job-${id}`);
  if (!card) {
    card = document.createElement('article');
    card.id = `job-${id}`;
    card.className = 'job-card';
    queueEl.prepend(card);
  }
  const upload = Math.round(job.uploadProgress || 0);
  const conversion = Math.round(job.progress || 0);
  let status = job.status;
  let label = status === 'uploading' ? `Uploading ${upload}%` : status === 'queued' ? (job.queuePosition ? `Queued · #${job.queuePosition}` : 'Queued') : status === 'converting' ? `Converting · ${job.mode === 'audio-repair' ? 'audio repair' : job.mode === 'full-transcode' ? 'full transcode' : 'turbo'}` : status === 'complete' ? 'Complete' : 'Error';
  const eta = estimateEta(job);
  card.innerHTML = `
    <div class="job-head"><div class="filename" title="${escapeHtml(job.filename)}">${escapeHtml(job.filename)}</div><div class="job-status ${status}">${label}</div></div>
    <div class="stage"><div class="stage-label"><span>Upload</span><b>${upload}%</b></div><div class="progress"><div style="width:${upload}%"></div></div></div>
    <div class="stage"><div class="stage-label"><span>Conversion</span><b>${conversion}%</b></div><div class="progress"><div style="width:${conversion}%"></div></div></div>
    <div class="job-details"><span>${job.status === 'converting' ? `Speed: ${escapeHtml(job.speed)}` : job.status === 'complete' ? 'Ready' : 'Waiting'}</span><span>${eta}</span></div>
    ${job.status === 'complete' ? `<a class="download" href="${job.download}">Download ${escapeHtml(job.outputFilename)}</a>` : ''}
    ${job.status === 'error' ? `<div class="job-error">${escapeHtml(job.error || 'Conversion failed.')}</div>` : ''}
  `;
  updateSummary();
}

function estimateEta(job) {
  if (job.status !== 'converting' || !job.duration || !job.speed || job.speed === '-') return '';
  const m = String(job.speed).match(/([0-9.]+)x/i);
  if (!m || !job.progress) return 'ETA: —';
  const seconds = job.duration * (100 - job.progress) / 100 / Number(m[1]);
  return `ETA: ${formatTime(seconds)}`;
}

function updateSummary() {
  const values = [...jobs.values()];
  totalCount.textContent = values.length;
  activeCount.textContent = values.filter(j => j.status === 'uploading' || j.status === 'converting').length;
  queueCount.textContent = values.filter(j => j.status === 'queued').length;
  doneCount.textContent = values.filter(j => j.status === 'complete').length;
  clearBtn.disabled = !values.some(j => j.status === 'complete' || j.status === 'error');
}

function clearCompleted() {
  for (const [id, job] of jobs) {
    if (job.status === 'complete' || job.status === 'error') {
      document.getElementById(`job-${id}`)?.remove();
      jobs.delete(id);
    }
  }
  updateSummary();
}

function failJob(id, message) {
  const job = jobs.get(id);
  if (!job) return;
  job.status = 'error';
  job.error = message;
  renderJob(id);
}

function showError(message) { errorEl.textContent = message; errorEl.classList.remove('hidden'); setTimeout(() => errorEl.classList.add('hidden'), 5000); }
function escapeHtml(value) { return String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); }
function formatTime(s) { s = Math.max(0, Math.round(s)); const m = Math.floor(s / 60), sec = s % 60; return `${m}m ${String(sec).padStart(2, '0')}s`; }
updateSummary();
