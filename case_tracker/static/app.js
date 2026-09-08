/* 案件追蹤系統 — 前端邏輯（無框架、無外部相依）。
   注意：案件資料一律存在伺服器端的 SQLite，瀏覽器只保存「操作者姓名」這個個人偏好。 */

'use strict';

const state = {
  meta: null,
  items: [],
  today: '',
  sort: '',
  dir: 'asc',
  editingId: null,
  editingRev: null,
  importFile: null,
  importSheet: '',
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const FORM_FIELDS = ['case_no', 'contract_no', 'study_no', 'client', 'case_type',
  'stage', 'next_milestone', 'due_date', 'owner', 'status', 'notes'];

const STATUS_CLASS = {
  '進行中': 's-ongoing',
  '需留意': 's-watch',
  '已延遲': 's-late',
  '已結案': 's-closed',
};

/* ------------------------------------------------------------------ */
/* 共用工具                                                             */
/* ------------------------------------------------------------------ */

function operatorName() {
  return ($('#operator').value || '').trim();
}

async function api(path, options = {}) {
  const opts = Object.assign({ headers: {} }, options);
  opts.headers['X-Operator'] = encodeURIComponent(operatorName());
  if (opts.body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(opts.body);
  }
  const response = await fetch(path, opts);
  const text = await response.text();
  let payload = null;
  if (text) {
    try { payload = JSON.parse(text); } catch (err) { payload = null; }
  }
  if (!response.ok) {
    const error = new Error((payload && payload.error) || `HTTP ${response.status}`);
    error.status = response.status;
    error.details = (payload && payload.details) || {};
    throw error;
  }
  return payload;
}

function filterParams(extra) {
  const params = new URLSearchParams();
  const add = (key, value) => { if (value) params.set(key, value); };
  add('q', $('#search').value.trim());
  add('status', $('#filter-status').value);
  add('case_type', $('#filter-type').value);
  add('stage', $('#filter-stage').value);
  add('owner', $('#filter-owner').value);
  if ($('#filter-open').checked) params.set('open_only', '1');
  if ($('#filter-overdue').checked) params.set('overdue_only', '1');
  add('sort', state.sort);
  if (state.sort) params.set('dir', state.dir);
  Object.entries(extra || {}).forEach(([k, v]) => params.set(k, v));
  return params;
}

function toast(message, isError) {
  const el = $('#saved');
  el.textContent = message;
  el.style.color = isError ? 'var(--late)' : 'var(--accent)';
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.textContent = ''; }, 4000);
}

function fillSelect(select, values, placeholder) {
  const current = select.value;
  select.innerHTML = '';
  if (placeholder !== undefined) {
    const opt = document.createElement('option');
    opt.value = '';
    opt.textContent = placeholder;
    select.appendChild(opt);
  }
  values.forEach((value) => {
    const opt = document.createElement('option');
    opt.value = value;
    opt.textContent = value;
    select.appendChild(opt);
  });
  if (values.includes(current)) select.value = current;
}

/* ------------------------------------------------------------------ */
/* 載入與繪製                                                           */
/* ------------------------------------------------------------------ */

async function loadMeta() {
  state.meta = await api('/api/meta');
  state.today = state.meta.today;
  $('#today').textContent = `基準日 ${state.meta.today}`;

  fillSelect($('#filter-status'), state.meta.statuses, '全部');
  fillSelect($('#filter-type'), state.meta.case_types, '全部');
  fillSelect($('#filter-stage'), state.meta.stages, '全部');
  fillSelect($('#filter-owner'), state.meta.owners, '全部');

  fillSelect($('[name="case_type"]'), state.meta.case_types);
  fillSelect($('[name="stage"]'), state.meta.stages);
  fillSelect($('[name="status"]'), state.meta.statuses);

  $('#owner-list').innerHTML = state.meta.owners
    .map((o) => `<option value="${escapeAttr(o)}">`).join('');
  $('#contract-list').innerHTML = (state.meta.contracts || [])
    .map((c) => `<option value="${escapeAttr(c)}">`).join('');
  $('#client-list').innerHTML = state.meta.clients
    .map((c) => `<option value="${escapeAttr(c)}">`).join('');
}

async function loadCases() {
  const params = filterParams();
  const data = await api(`/api/cases?${params.toString()}`);
  state.items = data.items;
  state.today = data.today;
  render();
  updateExportLinks();
}

function escapeHtml(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/"/g, '&quot;');
}

function stageIndex(stage) {
  const idx = state.meta.stages.indexOf(stage);
  return idx < 0 ? '' : String(idx + 1).padStart(2, '0');
}

function rowClass(item) {
  if (item.due_state === 'overdue') return 'row-overdue';
  if (item.due_state === 'due_soon') return 'row-soon';
  if (item.status === '已結案') return 'row-closed';
  return '';
}

function dueCell(item) {
  const cls = item.due_state === 'overdue' ? 'due-overdue'
    : item.due_state === 'due_soon' ? 'due-soon'
    : item.due_state === 'none' ? 'due-none' : '';
  return `<span class="due-tag ${cls}">${escapeHtml(item.due_label)}</span>`;
}

function render() {
  const tbody = $('#rows');
  if (!state.items.length) {
    tbody.innerHTML = '<tr><td colspan="13" class="empty">沒有符合條件的案件</td></tr>';
  } else {
    tbody.innerHTML = state.items.map((item) => `
      <tr class="${rowClass(item)}" data-id="${item.id}">
        <td class="case-no">${escapeHtml(item.case_no)}</td>
        <td class="case-no soft">${escapeHtml(item.contract_no) || '—'}</td>
        <td class="case-no">${escapeHtml(item.study_no) || '—'}</td>
        <td>${escapeHtml(item.client)}</td>
        <td>${escapeHtml(item.case_type)}</td>
        <td class="stage-cell"><span class="stage-idx">${stageIndex(item.stage)}</span>${escapeHtml(item.stage)}</td>
        <td>${escapeHtml(item.next_milestone)}</td>
        <td class="date">${escapeHtml(item.due_date || '—')}</td>
        <td>${dueCell(item)}</td>
        <td>${escapeHtml(item.owner)}</td>
        <td><span class="status ${STATUS_CLASS[item.status] || ''}">${escapeHtml(item.status)}</span></td>
        <td class="notes" title="${escapeAttr(item.notes)}">${escapeHtml(item.notes)}</td>
        <td class="act">
          <button class="link-btn" data-edit="${item.id}">編輯</button>
          <button class="link-btn danger" data-del="${item.id}">刪除</button>
        </td>
      </tr>`).join('');
  }

  const overdue = state.items.filter((i) => i.due_state === 'overdue').length;
  const soon = state.items.filter((i) => i.due_state === 'due_soon').length;
  const open = state.items.filter((i) => i.status !== '已結案').length;
  $('#summary').textContent =
    `顯示 ${state.items.length} 件　未結案 ${open}　逾期 ${overdue}　${state.meta.due_soon_days} 日內到期 ${soon}`;

  const banner = $('#banner');
  if (overdue > 0) {
    banner.hidden = false;
    banner.textContent = `注意：目前清單中有 ${overdue} 件已逾期案件需要處理。`;
  } else {
    banner.hidden = true;
  }

  $$('#ledger th.sortable').forEach((th) => {
    const mark = th.querySelector('.arrow');
    if (mark) mark.remove();
    if (th.dataset.sort === state.sort) {
      const span = document.createElement('span');
      span.className = 'arrow';
      span.textContent = state.dir === 'asc' ? '▲' : '▼';
      th.appendChild(span);
    }
  });
}

function updateExportLinks() {
  const params = filterParams().toString();
  $('#export-csv').href = `/export/cases.csv?${params}`;
  $('#export-xlsx').href = `/export/cases.xlsx?${params}`;
}

/* ------------------------------------------------------------------ */
/* 新增／編輯                                                           */
/* ------------------------------------------------------------------ */

function openModal(id) { $(id).hidden = false; }
function closeModal(id) { $(id).hidden = true; }

function openCaseForm(item) {
  const form = $('#case-form');
  form.reset();
  $('#form-error').hidden = true;
  state.editingId = item ? item.id : null;
  state.editingRev = item ? item.rev : null;

  $('#modal-title').textContent = item ? `編輯案件　${item.case_no}` : '新增案件';
  $('#meta-line').textContent = item
    ? `建立 ${item.created_at} ${item.created_by || ''}　最後更新 ${item.updated_at} ${item.updated_by || ''}　rev ${item.rev}`
    : '';

  const values = item || {
    case_type: state.meta.case_types[0],
    stage: state.meta.stages[0],
    status: state.meta.statuses[0],
  };
  FORM_FIELDS.forEach((name) => {
    const field = form.elements[name];
    if (field) field.value = values[name] || '';
  });

  openModal('#case-modal');
  setTimeout(() => form.elements.case_no.focus(), 30);
}

async function submitCase(event) {
  event.preventDefault();
  const form = event.target;
  const payload = {};
  FORM_FIELDS.forEach((name) => {
    payload[name] = form.elements[name].value;
  });

  try {
    if (state.editingId) {
      payload.rev = state.editingRev;
      await api(`/api/cases/${state.editingId}`, { method: 'PUT', body: payload });
      toast(`已更新 ${payload.case_no}`);
    } else {
      await api('/api/cases', { method: 'POST', body: payload });
      toast(`已新增 ${payload.case_no}`);
    }
    closeModal('#case-modal');
    await loadMeta();
    await loadCases();
  } catch (err) {
    const box = $('#form-error');
    const details = Object.entries(err.details || {})
      .map(([k, v]) => `${(state.meta.field_labels || {})[k] || k}：${v}`).join('；');
    box.textContent = details ? `${err.message}（${details}）` : err.message;
    box.hidden = false;
  }
}

async function deleteCase(id) {
  const item = state.items.find((i) => i.id === Number(id));
  if (!item) return;
  if (!window.confirm(`確定刪除案件 ${item.case_no}？此動作無法復原。`)) return;
  try {
    await api(`/api/cases/${id}`, { method: 'DELETE' });
    toast(`已刪除 ${item.case_no}`);
    await loadCases();
  } catch (err) {
    toast(err.message, true);
  }
}

/* ------------------------------------------------------------------ */
/* 到期摘要                                                             */
/* ------------------------------------------------------------------ */

function reportTable(cases) {
  if (!cases.length) return '<div class="empty">（無）</div>';
  return `<table>
    <thead><tr>
      <th class="col-no">案件編號</th><th class="col-no2">研究編號</th><th>客戶名稱</th><th>類型</th>
      <th>下一個里程碑</th><th class="col-date">到期日</th><th class="col-due">期限</th>
      <th>負責人</th><th>狀態</th>
    </tr></thead>
    <tbody>${cases.map((c) => `
      <tr class="${rowClass(c)}">
        <td class="case-no">${escapeHtml(c.case_no)}</td>
        <td class="case-no">${escapeHtml(c.study_no) || '—'}</td>
        <td>${escapeHtml(c.client)}</td>
        <td>${escapeHtml(c.case_type)}</td>
        <td>${escapeHtml(c.next_milestone || c.stage)}</td>
        <td class="date">${escapeHtml(c.due_date || '—')}</td>
        <td>${dueCell(c)}</td>
        <td>${escapeHtml(c.owner)}</td>
        <td><span class="status ${STATUS_CLASS[c.status] || ''}">${escapeHtml(c.status)}</span></td>
      </tr>`).join('')}</tbody></table>`;
}

async function loadReport() {
  const days = $('#report-days').value;
  const summary = await api(`/api/report/weekly?days=${days}`);
  $('#report-csv').href = `/export/weekly.csv?days=${days}`;
  $('#report-xlsx').href = `/export/weekly.xlsx?days=${days}`;

  const ownerRows = summary.by_owner.map((o) => `
    <tr><td>${escapeHtml(o.owner)}</td>
    <td class="date">${o.overdue}</td><td class="date">${o.upcoming}</td></tr>`).join('');

  $('#report-body').innerHTML = `
    <div class="report-meta">
      基準日 ${escapeHtml(summary.generated_at)}　涵蓋至 ${escapeHtml(summary.window_end)}
      （未來 ${summary.window_days} 天）
    </div>
    <div class="report-tiles">
      <div class="tile alert"><div class="n">${summary.totals.overdue}</div><div class="k">已逾期</div></div>
      <div class="tile"><div class="n">${summary.totals.upcoming}</div><div class="k">${summary.window_days} 日內到期</div></div>
      <div class="tile"><div class="n">${summary.totals.undated}</div><div class="k">未設定到期日</div></div>
      <div class="tile"><div class="n">${summary.totals.active}</div><div class="k">進行中案件</div></div>
    </div>
    <h3>已逾期（${summary.totals.overdue}）</h3>${reportTable(summary.overdue)}
    <h3>${summary.window_days} 日內到期（${summary.totals.upcoming}）</h3>${reportTable(summary.upcoming)}
    <h3>未設定到期日（${summary.totals.undated}）</h3>${reportTable(summary.undated)}
    ${ownerRows ? `<h3>依負責人</h3><table><thead><tr><th>負責人</th><th>逾期</th><th>即將到期</th></tr></thead><tbody>${ownerRows}</tbody></table>` : ''}
  `;
}

/* ------------------------------------------------------------------ */
/* 匯入 Excel／CSV                                                      */
/* ------------------------------------------------------------------ */

const ACTION_LABEL = { create: '新增', update: '更新', error: '無法匯入' };

function resetImport() {
  state.importFile = null;
  state.importSheet = '';
  $('#import-filename').textContent = '尚未選擇檔案';
  $('#import-file').value = '';
  $('#import-preview').hidden = true;
  $('#import-error').hidden = true;
  $('#import-result').hidden = true;
  $('#import-sheet-wrap').hidden = true;
}

function showImportError(message) {
  const box = $('#import-error');
  box.textContent = message;
  box.hidden = false;
  $('#import-preview').hidden = true;
}

async function sendImport(kind) {
  const file = state.importFile;
  const params = new URLSearchParams({ filename: file.name });
  if (state.importSheet) params.set('sheet', state.importSheet);
  if (kind === 'commit' && !$('#import-update').checked) {
    params.set('update_existing', '0');
  }
  const response = await fetch(`/api/import/${kind}?${params.toString()}`, {
    method: 'POST',
    headers: { 'X-Operator': encodeURIComponent(operatorName()) },
    body: await file.arrayBuffer(),
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error((payload && payload.error) || `HTTP ${response.status}`);
  }
  return payload;
}

async function previewImport() {
  if (!state.importFile) return;
  $('#import-error').hidden = true;
  $('#import-result').hidden = true;
  try {
    const data = await sendImport('preview');
    renderImportPreview(data);
  } catch (err) {
    showImportError(err.message);
  }
}

function renderImportPreview(data) {
  const info = data.info || {};

  const sheetWrap = $('#import-sheet-wrap');
  if ((info.sheet_names || []).length > 1) {
    fillSelect($('#import-sheet'), info.sheet_names);
    $('#import-sheet').value = info.sheet || info.sheet_names[0];
    state.importSheet = $('#import-sheet').value;
    sheetWrap.hidden = false;
  } else {
    sheetWrap.hidden = true;
  }

  const mapped = (info.columns || [])
    .map((c) => `${c.label}→${(state.meta.field_labels || {})[c.field] || c.field}`)
    .join('、');
  const ignored = (info.ignored_columns || []).join('、');
  $('#import-info').textContent =
    `${info.sheet ? `工作表「${info.sheet}」　` : ''}表頭在第 ${info.header_row} 列　`
    + `對應欄位：${mapped || '（無）'}`
    + (ignored ? `　未使用的欄位：${ignored}` : '');

  $('#tile-create').textContent = data.totals.create;
  $('#tile-update').textContent = data.totals.update;
  $('#tile-error').textContent = data.totals.error;

  $('#import-rows').innerHTML = data.rows.length
    ? data.rows.map((r) => `
      <tr>
        <td class="date">${r.row}</td>
        <td class="case-no">${escapeHtml(r.case_no)}</td>
        <td>${escapeHtml(r.client)}</td>
        <td>${escapeHtml(r.case_type)}</td>
        <td class="date">${escapeHtml(r.due_date) || '—'}</td>
        <td>${escapeHtml(r.owner)}</td>
        <td class="act-${r.action}">${ACTION_LABEL[r.action] || r.action}</td>
        <td>${escapeHtml(r.message)}</td>
      </tr>`).join('')
    : '<tr><td colspan="8" class="empty">這個檔案裡沒有可匯入的資料列</td></tr>';

  $('#import-commit').disabled = data.totals.create + data.totals.update === 0;
  $('#import-preview').hidden = false;
}

async function commitImport() {
  const button = $('#import-commit');
  button.disabled = true;
  button.textContent = '匯入中…';
  try {
    const data = await sendImport('commit');
    const box = $('#import-result');
    box.innerHTML =
      `匯入完成：新增 ${data.created} 筆、更新 ${data.updated} 筆`
      + (data.skipped ? `、略過 ${data.skipped} 筆` : '')
      + (data.errors.length
        ? `。有 ${data.errors.length} 列未匯入：<br>` + data.errors.map(escapeHtml).join('<br>')
        : '。');
    box.hidden = false;
    $('#import-preview').hidden = true;
    toast(`匯入完成：新增 ${data.created}、更新 ${data.updated}`);
    await loadMeta();
    await loadCases();
  } catch (err) {
    showImportError(err.message);
  } finally {
    button.disabled = false;
    button.textContent = '確認匯入';
  }
}

/* ------------------------------------------------------------------ */
/* 事件綁定                                                             */
/* ------------------------------------------------------------------ */

function debounce(fn, wait) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

function bind() {
  const reload = () => loadCases().catch((err) => toast(err.message, true));

  $('#search').addEventListener('input', debounce(reload, 250));
  ['#filter-status', '#filter-type', '#filter-stage', '#filter-owner',
    '#filter-open', '#filter-overdue'].forEach((sel) => {
    $(sel).addEventListener('change', reload);
  });

  $('#btn-reset').addEventListener('click', () => {
    $('#search').value = '';
    ['#filter-status', '#filter-type', '#filter-stage', '#filter-owner']
      .forEach((sel) => { $(sel).value = ''; });
    $('#filter-open').checked = false;
    $('#filter-overdue').checked = false;
    state.sort = '';
    state.dir = 'asc';
    reload();
  });

  $$('#ledger th.sortable').forEach((th) => {
    th.addEventListener('click', () => {
      const key = th.dataset.sort;
      if (state.sort === key) {
        state.dir = state.dir === 'asc' ? 'desc' : 'asc';
      } else {
        state.sort = key;
        state.dir = 'asc';
      }
      reload();
    });
  });

  $('#btn-new').addEventListener('click', () => openCaseForm(null));
  $('#case-form').addEventListener('submit', submitCase);

  $('#rows').addEventListener('click', (event) => {
    const editId = event.target.dataset.edit;
    const delId = event.target.dataset.del;
    if (editId) openCaseForm(state.items.find((i) => i.id === Number(editId)));
    if (delId) deleteCase(delId);
  });

  $('#rows').addEventListener('dblclick', (event) => {
    const row = event.target.closest('tr[data-id]');
    if (row) openCaseForm(state.items.find((i) => i.id === Number(row.dataset.id)));
  });

  $$('[data-close]').forEach((btn) => {
    btn.addEventListener('click', () => {
      closeModal('#case-modal');
      closeModal('#report-modal');
      closeModal('#import-modal');
    });
  });

  $$('.modal').forEach((modal) => {
    modal.addEventListener('mousedown', (event) => {
      if (event.target === modal) modal.hidden = true;
    });
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      closeModal('#case-modal');
      closeModal('#report-modal');
      closeModal('#import-modal');
    }
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)
        && !$('#case-modal').hidden) {
      $('#case-form').requestSubmit();
    }
  });

  $('#btn-import').addEventListener('click', () => {
    resetImport();
    openModal('#import-modal');
  });

  $('#import-file').addEventListener('change', (event) => {
    const file = event.target.files[0];
    if (!file) return;
    state.importFile = file;
    state.importSheet = '';
    $('#import-filename').textContent = file.name;
    previewImport();
  });

  $('#import-sheet').addEventListener('change', (event) => {
    state.importSheet = event.target.value;
    previewImport();
  });

  $('#import-commit').addEventListener('click', commitImport);

  $('#btn-report').addEventListener('click', async () => {
    openModal('#report-modal');
    try { await loadReport(); } catch (err) { toast(err.message, true); }
  });
  $('#report-days').addEventListener('change', () => loadReport());
  $('#report-print').addEventListener('click', () => window.print());

  const operator = $('#operator');
  try { operator.value = localStorage.getItem('tmt.operator') || ''; } catch (e) { /* 忽略 */ }
  operator.addEventListener('change', () => {
    try { localStorage.setItem('tmt.operator', operator.value.trim()); } catch (e) { /* 忽略 */ }
  });
}

async function boot() {
  bind();
  try {
    await loadMeta();
    await loadCases();
  } catch (err) {
    toast(`載入失敗：${err.message}`, true);
  }
  // 每 5 分鐘重新整理，讓多人同時使用時看到彼此的更新。
  setInterval(() => {
    if ($('#case-modal').hidden) loadCases().catch(() => {});
  }, 300000);
}

boot();
