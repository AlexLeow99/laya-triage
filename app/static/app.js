// 主逻辑：分析 + 网页端分类管理 + 三语界面切换
// 依赖 i18n.js（在其之后加载）

// 支持 ?lang=zh|en|ms 深链接，方便分享和测试；否则用上次选择，默认中文
// 支持 ?text=<urlencoded> 预填并自动分析，方便别的工具直接链过来
const _q = new URLSearchParams(location.search);
const urlLang = _q.get('lang');
const urlText = _q.get('text');
let _autoRan = false;

const state = {
  uiLang: (urlLang && I18N[urlLang]) ? urlLang : (localStorage.getItem('laya.uiLang') || 'zh'),
  theme: (localStorage.getItem('laya.theme') || 'system'),
  editLang: null,          // 分类管理里正在编辑的语言
  tab: 'analyze',
  categories: [],          // 服务端当前生效的分类（已启用+未启用全量）
  questions: null,         // {category_question, questions}
  qDirty: false,
  configOk: true,
  dirty: false,
  modelReady: false,
};

const DEMOS = {
  zh: '你们系统这个月已经扣了我两次钱了，我打了三次客服电话都没人理，今天之内不退款我就直接投诉到消协。',
  en: 'You charged me twice this month and nobody answered my three calls. Refund the duplicate today or I report you.',
  ms: 'Saya telah dicaj dua kali bulan ini dan tiada siapa menjawab panggilan saya. Pulangkan wang itu hari ini atau saya akan membuat aduan.',
};

const $ = id => document.getElementById(id);
const esc = s => String(s == null ? '' : s)
  .replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

// 锁定分类：不能删除、不能停用、标识不能改。服务端也会强制修复，
// 前端这层是为了让用户根本点不到，而不是保存时才报错。
const LOCKED_KEY = 'other';

// ------------------------------------------------------------------ 界面语言
function setUiLang(lang) {
  state.uiLang = lang;
  if (!state.editLang) state.editLang = lang;
  localStorage.setItem('laya.uiLang', lang);
  applyI18n();
  renderQuestions();
  if (state.lastResult) renderResult(state.lastResult);
  refreshConfigStatus();
}

function applyI18n() {
  document.documentElement.lang = state.uiLang === 'zh' ? 'zh-CN' : state.uiLang;
  $('h-title').textContent = t('title');
  $('h-sub').textContent = t('subtitle');
  $('h-sub2').textContent = t('subtitle2');
  $('lbl-lang').textContent = t('uiLangLabel');
  $('lbl-theme').textContent = t('themeLabel');
  (function () {
    const sel = $('uiTheme');
    if (!sel) return;
    const labels = { system: t('themeSystem'), light: t('themeLight'), dark: t('themeDark') };
    Array.from(sel.options).forEach(o => { o.textContent = labels[o.value] || o.value; });
    sel.value = state.theme;
  })();
  $('banner-title').textContent = t('bannerTitle');
  $('banner-body').innerHTML = t('bannerBody');
  $('tab-analyze').textContent = t('tabAnalyze');
  $('tab-questions').textContent = t('tabQuestions');
  $('q-title').textContent = t('qTitle');
  $('q-intro').textContent = t('qIntro');
  $('q-add').textContent = t('qAdd');
  $('q-save').textContent = t('saveBtn');
  $('q-discard').textContent = t('discardBtn');
  $('q-preview').textContent = t('qPreview');
  $('q-export').textContent = t('qExport');
  $('q-note-wording').textContent = t('qNoteWording');
  $('q-import').textContent = t('importBtn');
  $('q-export-xlsx').textContent = t('exportXlsx');
  $('q-export-csv').textContent = t('exportCsv');
  $('text').placeholder = t('placeholder');
  $('go').textContent = t('analyzeBtn');
  document.querySelectorAll('[data-demo]').forEach(b => {
    b.textContent = { zh: t('demoZh'), en: t('demoEn'), ms: t('demoMs') }[b.dataset.demo];
  });
  $('hint-ctrl').textContent = t('hintCtrlEnter');
  const tt = $('to-top');
  if (tt) { tt.title = t('toTop'); tt.setAttribute('aria-label', t('toTop')); }
  $('foot-local').textContent = t('footerLocal');
  $('foot-disc').textContent = t('footerDisclaimer');
  if (!state.modelReady) $('st').textContent = t('loadingModel');
  else if (state.modelLoadedIn != null) $('st').textContent = t('modelReady', { s: state.modelLoadedIn });
}

// ------------------------------------------------------------------ 模型状态
async function pollStatus() {
  try {
    const s = await (await fetch('/api/status')).json();
    const dot = $('dot');
    if (s.status === 'ready') {
      state.modelReady = true;
      state.modelLoadedIn = s.loaded_in;
      dot.className = 'dot ok';
      $('st').textContent = t('modelReady', { s: s.loaded_in });
      $('go').disabled = false;
      if (urlText && !_autoRan) { _autoRan = true; $('text').value = urlText; analyze(); }
    } else if (s.status === 'error') {
      dot.className = 'dot err';
      $('st').textContent = t('modelFailed') + ': ' + s.error;
    } else {
      setTimeout(pollStatus, 1000);
    }
  } catch (e) { setTimeout(pollStatus, 1500); }
}

// ------------------------------------------------------------------ 分析
async function analyze() {
  const text = $('text').value.trim();
  if (!text) return;
  $('go').disabled = true;
  $('out').innerHTML = '<div class="card"><div style="color:var(--dim)">' + esc(t('analyzing')) + '</div></div>';
  try {
    const d = await (await fetch('/api/analyze', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text })
    })).json();
    $('go').disabled = false;
    if (!d.ok) { $('out').innerHTML = errBox(d); return; }
    state.lastResult = d;
    renderResult(d);
  } catch (e) {
    $('go').disabled = false;
    $('out').innerHTML = '<div class="card"><div class="err">' + esc(t('requestFailed')) + ': ' + esc(e.message) + '</div></div>';
  }
}

function errBox(d) {
  const map = { model_not_ready: 'errModelNotReady', empty_input: 'errEmptyInput', config_invalid: 'errConfigInvalid' };
  let msg = t(map[d.error] || 'errInternal');
  if (d.detail && d.detail.length) msg += '<br><span style="color:var(--dim);font-size:13px">' + d.detail.map(esc).join('<br>') + '</span>';
  return '<div class="card"><div class="err">' + msg + '</div></div>';
}

function tierLabel(tier) {
  return { agree: t('tierAgree'), disagree: t('tierDisagree'), other: t('tierOther') }[tier] || tier;
}

// 附加问题是使用者配置的，种类和数量都不固定，所以通用渲染。
// 全部文字（名称 / 档位 / 选项）都跟【界面语言】走，和界面其余部分保持一致。
// 服务端会把三语都返回，这里只负责挑。
function _pickText(obj, lg) {
  if (!obj) return '';
  return obj[lg] || obj.zh || obj.en || obj.ms || '';
}

function renderExtraItem(row) {
  const lg = state.uiLang || 'zh';
  const label = _pickText(row.names, lg) || row.key;
  let value = '', sub = '';
  if (row.type === 'score') {
    const levels = (row.levels || []).map(l => _pickText(l, lg));
    const max = Math.max(levels.length - 1, 1);
    value = row.score.toFixed(2) + ' <span class="unit">/ ' + max + '</span>';
    if (levels.length) {
      sub = '<div class="lvls">' + levels.map((lv, i) =>
        '<span class="lvl' + (Math.round(row.score) === i ? ' on' : '') + '">' + esc(lv) + '</span>').join('') + '</div>';
    }
  } else if (row.type === 'noul') {
    value = (row.value * 100).toFixed(1) + '%';
  } else {
    const opts = (row.options || []).map(o => ({
      key: o.key, p: o.p, label: _pickText(o.labels, lg) || o.key
    }));
    const picked = opts.find(o => o.key === row.choice);
    value = '<span class="choiceval">' + esc(picked ? picked.label : row.choice) + '</span>';
    sub = '<div class="lvls">' + opts.map(o =>
      '<span class="lvl' + (o.key === row.choice ? ' on' : '') + '">' + esc(o.label) + ' ' +
      (o.p * 100).toFixed(0) + '%</span>').join('') + '</div>';
  }
  return '<div class="item"><div class="k">' + esc(label) + '</div>' +
         '<div class="v">' + value + '</div>' + sub + '</div>';
}

function renderResult(d) {
  const v = d.verdict;
  const name = pickName(v.names);
  const max = Math.max(...d.distribution.map(x => x.p));
  const bars = d.distribution.map(x =>
    '<div class="bar' + (x.key === v.category ? ' top' : '') + '">' +
    '<div class="bname">' + esc(pickName(x.names)) + '</div>' +
    '<div class="track"><div class="fill" style="width:' + (Math.max(x.p, 0.004) / max * 100).toFixed(1) + '%"></div></div>' +
    '<div class="bval">' + (x.p * 100).toFixed(1) + '%</div></div>').join('');

  const agree = d.method_check.agree;
  const agreeHtml = agree
    ? '<span style="color:var(--green)">' + esc(t('agreeYes')) + '</span>　<span style="color:var(--dim2)">' +
      t('agreeExplain', { cat: esc(name) }) + '</span>'
    : '<span style="color:var(--red)">' + esc(t('agreeNo')) + '</span>　<span style="color:var(--dim2)">' +
      t('disagreeExplain', { a: esc(pickName(d.method_check.choice.names)), b: esc(pickName(d.method_check.noul.names)) }) + '</span>';

  let note = '';
  if (v.tier === 'disagree') note = '<div class="note red">' + esc(t('noteDisagree')) + '</div>';
  else if (v.tier === 'other') note = '<div class="note">' + esc(t('noteOther')) + '</div>';

  const meta = Object.entries(d.method_check.noul_raw).sort((a, b) => b[1] - a[1]);
  const nameOf = k => { const f = d.distribution.find(x => x.key === k); return f ? pickName(f.names) : k; };
  const rawRows = meta.map(([k, p]) => '<tr><td>' + esc(nameOf(k)) + '</td><td class="num">' + p.toFixed(3) + '</td></tr>').join('');

  $('out').innerHTML =
    '<div class="card">' +
    '<h3>' + esc(t('verdictTitle')) + '</h3>' +
    '<div class="vrow">' +
    '<div><div class="vcat">' + esc(name) + '</div>' +
    '<div class="vsub">' + esc(t('normalizedLikelihood')) + ' <b style="color:var(--fg)">' + (v.confidence * 100).toFixed(1) + '%</b>　' +
    esc(t('leadsBy', { n: (v.margin * 100).toFixed(1) })) +
    ' <span style="color:var(--dim2)">' + esc(t('unrelatedNote')) + '</span></div></div>' +
    '<span class="badge t-' + v.tier + '">' + esc(tierLabel(v.tier)) + '</span>' +
    '</div>' + note +
    '<div class="meta">' + esc(t('consistency')) + '：' + agreeHtml + '</div>' +
    '</div>' +

    '<div class="card">' +
    '<h3>' + esc(t('distTitle')) + '</h3><div class="barlist">' + bars + '</div>' +
    '</div>' +

    '<div class="card">' +
    '<h3>' + esc(t('auxTitle')) + '</h3>' +
    '<div class="aux">' + (d.extra || []).map(renderExtraItem).join('') + '</div>' +
    '<div class="meta">' + esc(t('auxNote')) + '</div></div>' +

    '<div class="card"><details><summary>' + esc(t('techSummary')) + '</summary>' +
    '<div class="meta">' + esc(t('inputScript')) + '：<code>' + esc(d.input_script) + '</code> · ' +
    esc(t('questionLang')) + '：<code>' + esc(d.question_lang) + '</code> · ' +
    esc(t('elapsedLabel')) + '：<b>' + d.elapsed_ms + ' ms</b> · ' + esc(t('singlePass')) + '</div>' +
    '<div class="meta" style="margin-top:10px">' + esc(t('noulNote')) + '</div>' +
    '<table><tr><th>' + esc(t('colCategory')) + '</th><th style="text-align:right">' + esc(t('colPyes')) + '</th></tr>' + rawRows + '</table>' +
    '</details></div>';
}

// ------------------------------------------------------------------ 分类管理
async function loadCategories() {
  const c = await (await fetch('/api/categories')).json();
  state.categories = c.categories || [];
  state.questions = c.questions || { category_question: {}, questions: [] };
  state.configOk = c.ok;
  state.configErrors = c.errors || [];
  state.configWarnings = c.warnings || [];
  state.configMeta = { path: c.path, loaded_at: c.loaded_at, count: c.count };
  if (!state.editLang) state.editLang = state.uiLang;
  renderQuestions();
  refreshConfigStatus();
}

function refreshConfigStatus() {
  const el = $('q-status');
  if (!el) return;
  const ok = state.configOk;
  let html = '<div class="' + (ok ? 'okline' : 'errline') + '">' +
    esc(t(ok ? 'cfgOk' : 'cfgError', { n: state.configMeta ? state.configMeta.count : 0 })) + '</div>';
  if (!ok) html += state.configErrors.map(e => '<div class="errsub">· ' + esc(e) + '</div>').join('');
  state.configWarnings.forEach(w => { html += '<div class="warnsub">⚠ ' + esc(w) + '</div>'; });
  if (state.configMeta) {
    html += '<div class="meta">' + esc(t('configFile')) + '：<code>' + esc(state.configMeta.path) + '</code>　' +
      esc(t('loadedAt')) + '：' + esc(state.configMeta.loaded_at || '—') + '</div>';
  }
  el.innerHTML = html;
}

// ---------------------------------------------------------------- 选项（Choices）
// 参考 Jev playground 的 Choices 呈现：一个 Choice 问题的选项列成 A / B / C…，
// 每项可增可删，右上角显示已用数量。
//
// 关键：分类问题的选项（= 分类）和 choice 问题的选项本来是两套代码，
// 现在合并成这一个模块。用 (root, listPath) 定位列表：
//   root = 'cats'      -> state.categories        （分类问题的选项）
//   root = 'questions' -> state.questions         （附加问题及其选项）
//   listPath 是该列表在 root 里的路径，分类是 ''，choice 问题是 'questions.0.criteria'

const CHOICE_LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
const CAT_RECOMMEND_MAX = 12;      // 与服务端 MAX_ENABLED_WARN 一致，只是建议值不是硬上限

function _optRoot(name) { return name === 'cats' ? state.categories : state.questions; }

function _pathParts(path) { return String(path || '').split('.').filter(s => s !== ''); }

function _getByPath(rootName, path) {
  return _pathParts(path).reduce((o, k) => (o == null ? undefined : o[k]), _optRoot(rootName));
}

function _setByPath(rootName, path, v) {
  const parts = _pathParts(path);
  if (!parts.length) return;
  let o = _optRoot(rootName);
  for (let i = 0; i < parts.length - 1; i++) o = o[parts[i]];
  o[parts[parts.length - 1]] = v;
  markDirty();
}

// 某个选项自身的路径
function _optSelfPath(listPath, idx) {
  return (listPath ? listPath + '.' : '') + idx;
}

// 拼出某个字段的完整路径
function _optFieldPath(listPath, idx, field, lg) {
  const head = listPath ? listPath + '.' : '';
  if (field === 'key') return head + idx + '.key';
  if (field === 'enabled') return head + idx + '.enabled';
  return head + idx + '.' + lg + '.' + field;      // name / desc
}

function optionRows(rootName, listPath, opts) {
  const lg = state.editLang || state.uiLang;
  const listAttr = rootName + '|' + listPath;
  return opts.map((o, i) => {
    // 「other」这一项锁定：不能删、不能停用、标识不能改（名称和描述仍可改）
    const locked = rootName === 'cats' && o.key === LOCKED_KEY;
    const letter = CHOICE_LETTERS[i] || String(i + 1);
    const cell = o[lg] || { name: '', desc: '' };

    const head =
      '<span class="ltr' + (o.enabled ? '' : ' off') + '">' + esc(letter) + '</span>' +
      (locked
        ? '<input class="kin" value="' + esc(o.key) + '" readonly>' +
          '<span class="lockbadge" title="' + esc(t('lockedHintShort')) + '">🔒 ' + esc(t('lockedBadge')) + '</span>'
        : '<input class="okin" data-opt-list="' + esc(listAttr) + '" data-opt-idx="' + i + '" data-opt-field="key" value="' + esc(o.key) + '" spellcheck="false" placeholder="' + esc(t('qOptKey')) + '">' +
          '<label class="chk"><input type="checkbox" data-opt-list="' + esc(listAttr) + '" data-opt-idx="' + i + '" data-opt-field="enabled"' + (o.enabled ? ' checked' : '') + '> ' + esc(t('colEnabled')) + '</label>' +
          '<button class="del" data-opt-del="' + esc(listAttr) + '|' + i + '">' + esc(t('deleteBtn')) + '</button>');

    return '<div class="catcard' + (locked ? ' locked' : '') + '">' +
      '<div class="cathead">' + head + '</div>' +
      '<input class="nin" data-opt-list="' + esc(listAttr) + '" data-opt-idx="' + i + '" data-opt-field="name" value="' + esc(cell.name || '') + '" placeholder="' + esc(t('colName')) + ' — ' + esc(t('nameHint')) + '">' +
      '<textarea class="din" data-opt-list="' + esc(listAttr) + '" data-opt-idx="' + i + '" data-opt-field="desc" rows="2" placeholder="' + esc(t('colDesc')) + ' — ' + esc(t('descHint')) + '">' + esc(cell.desc || '') + '</textarea>' +
      (locked ? '<div class="lockhint">' + esc(t('lockedHint')) + '</div>' : '') +
      '</div>';
  }).join('');
}

function bindOptions(rootEl) {
  const lg = state.editLang || state.uiLang;

  rootEl.querySelectorAll('[data-opt-list]').forEach(el => {
    el.oninput = el.onchange = () => {
      const [rootName, listPath] = el.dataset.optList.split('|');
      const idx = +el.dataset.optIdx, field = el.dataset.optField;
      const opt = _getByPath(rootName, _optSelfPath(listPath, idx));
      if (!opt) return;
      if (rootName === 'cats' && opt.key === LOCKED_KEY && (field === 'key' || field === 'enabled')) return;  // 双保险
      _setByPath(rootName, _optFieldPath(listPath, idx, field, lg),
                 field === 'enabled' ? el.checked : el.value.trim());
    };
  });

  rootEl.querySelectorAll('[data-opt-del]').forEach(b => b.onclick = () => {
    const [rootName, listPath, idxStr] = b.dataset.optDel.split('|');
    const idx = +idxStr;
    const list = _getByPath(rootName, listPath);
    if (!Array.isArray(list) || !list[idx]) return;
    if (rootName === 'cats' && list[idx].key === LOCKED_KEY) return;            // 双保险
    list.splice(idx, 1);
    markDirty();
    renderQuestions();
  });

  rootEl.querySelectorAll('[data-opt-add]').forEach(b => b.onclick = () => {
    const [rootName, listPath] = b.dataset.optAdd.split('|');
    let list = _getByPath(rootName, listPath);
    if (!Array.isArray(list)) { _setByPath(rootName, listPath, []); list = _getByPath(rootName, listPath); }
    list.push({
      key: '', enabled: true,
      zh: { name: '', desc: '' }, en: { name: '', desc: '' }, ms: { name: '', desc: '' },
    });
    markDirty();
    renderQuestions();
    // 聚焦到刚加的那一行（同一个列表里的最后一个可编辑标识框）
    const box = $('q-list');
    if (box) {
      const same = Array.from(box.querySelectorAll('input[data-opt-field="key"]:not([readonly])'))
        .filter(el => el.dataset.optList === b.dataset.optAdd);
      if (same.length) same[same.length - 1].focus();
    }
  });
}

// 导入表格：.xlsx / .xlsm / .csv / .tsv 都行。
// xlsx 是二进制，统一走 base64；具体格式由服务端按扩展名 + 文件头判断，
// 前端不需要关心（也就不需要维护一份"支持哪些格式"的清单）。
async function importTable(file) {
  try {
    const bytes = new Uint8Array(await file.arrayBuffer());
    // 大文件用分块避免 String.fromCharCode 的参数长度上限
    let bin = '';
    const CHUNK = 0x8000;
    for (let i = 0; i < bytes.length; i += CHUNK) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
    }
    const r = await fetch('/api/categories/import', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: file.name, content_b64: btoa(bin) })
    });
    const d = await r.json();
    if (!r.ok || !d.ok) {
      $('q-msg').innerHTML = '<div class="errline">' + esc(t('importFailed')) + '</div>' +
        (d.errors || []).map(e => '<div class="errsub">· ' + esc(e) + '</div>').join('');
      return;
    }
    state.categories = d.categories;
    markDirty();
    renderQuestions();
    $('q-msg').innerHTML = '<div class="okline">' + esc(t('importPreview')) + '</div>' +
      (d.warnings || []).map(w => '<div class="warnsub">⚠ ' + esc(w) + '</div>').join('');
  } catch (e) {
    $('q-msg').innerHTML = '<div class="errline">' + esc(t('importFailed')) + ': ' + esc(e.message) + '</div>';
  }
}

// ============================================================ 问题设置
// 问题由使用者配置，所以这里全部按 data-path 通用寻址，不为每个字段写绑定。
// 类型名一律用英文（choice / score / noul）——这是 Laya 的原生术语，
// 中文界面上也跟着写英文，和官方文档、报错信息对得上，减少来回翻译的困惑。
const QTYPE_LABEL = { choice: 'choice', score: 'score', noul: 'noul' };

function setPath(path, value) {
  const parts = path.split('.');
  let o = state.questions;
  for (let i = 0; i < parts.length - 1; i++) o = o[parts[i]];
  o[parts[parts.length - 1]] = value;
  markDirty();
}

function getPath(path) {
  return path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), state.questions);
}

function langField(path, label, kind) {
  const lg = state.editLang || state.uiLang;
  const v = getPath(path + '.' + lg) || '';
  const hint = kind === 'textarea' ? '' : '';
  const inner = kind === 'textarea'
    ? '<textarea class="din" rows="2" data-path="' + esc(path + '.' + lg) + '">' + esc(v) + '</textarea>'
    : '<input class="nin" data-path="' + esc(path + '.' + lg) + '" value="' + esc(v) + '">';
  return '<label class="qfield"><span>' + esc(label) + '</span>' + inner + hint + '</label>';
}

// ---------------------------------------------------------------- 未保存状态
// 两个 dirty 标志（dirty / qDirty）合并成一个，并同步吸顶条上的提示，
// 这样滚动到任何位置都能看到"有改动没保存"。
function markDirty() {
  state.dirty = true;
  const el = $('q-dirty');
  if (el) { el.textContent = t('unsaved'); el.classList.add('on'); }
}

function clearDirty() {
  state.dirty = false;
  const el = $('q-dirty');
  if (el) { el.textContent = ''; el.classList.remove('on'); }
}

function renderQuestions() {
  const lg = state.editLang || state.uiLang;
  const q = state.questions || {};
  const cq = q.category_question || {};
  const list = q.questions || [];

  const langTabs = ['zh', 'en', 'ms'].map(x =>
    '<button class="lbtn' + (x === lg ? ' on' : '') + '" data-qeditlang="' + x + '">' +
    LANG_NAMES[x] + '</button>').join('');

  // ---- Q1：分类问题
  // 它的「选项」就是分类本身，所以分类管理并入了这里（不再单独开标签页）。
  const nOn = state.categories.filter(c => c.enabled).length;
  const catBlock =
    '<div class="qcard locked">' +
      '<div class="qhead">' +
        '<span class="qno">Q1</span>' +
        '<span class="typebadge">choice</span>' +
        '<input class="kin" value="category" readonly>' +
        '<span class="lockbadge" title="' + esc(t('qCatLockHint')) + '">🔒 ' + esc(t('lockedBadge')) + '</span>' +
      '</div>' +
      langField('category_question.name', t('qName')) +
      langField('category_question.instructions', t('qAsk'), 'textarea') +
      '<div class="qhint warn">' + esc(t('qAskChoicesHint')) + '</div>' +
      langField('category_question.noul_template', t('qTemplate'), 'textarea') +
      '<div class="qhint">' + esc(t('qTemplateHint')) + '</div>' +
      // ---- Choices（= 分类）—— 与 choice 问题共用 optionRows()
      '<div class="choices" id="cat-choices">' +
        '<div class="choiceshead">' +
          '<span class="choicestitle">' + esc(t('choicesTitle')) + '</span>' +
          '<span class="choicecount' + (nOn > CAT_RECOMMEND_MAX ? ' over' : '') + '">' +
            esc(t('choicesCount', { n: nOn, max: CAT_RECOMMEND_MAX })) + '</span>' +
        '</div>' +
        '<div class="qhint">' + esc(t('choicesHint')) + '</div>' +
        optionRows('cats', '', state.categories) +
        '<button class="ghost mini" data-opt-add="cats|">' + esc(t('addChoice')) + '</button>' +
      '</div>' +
      '<div class="qhint">' + esc(t('qCatLockHint')) + '</div>' +
      '<div class="qhint">' + esc(t('descLangNote')) + '</div>' +
    '</div>';

  // ---- 附加问题
  const cards = list.map((item, i) => {
    const base = 'questions.' + i;
    const crit = item.criteria || [];
    let critHtml = '';

    if (item.type === 'choice') {
      // 与分类问题共用同一条 optionRows() —— 同一个模块，不是两套实现
      const optListPath = base + '.criteria';
      critHtml =
        '<div class="qcrit">' +
          '<div class="qhint">' + esc(t('qOptionsHint')) + '</div>' +
          optionRows('questions', optListPath, crit) +
          '<button class="ghost mini" data-opt-add="questions|' + esc(optListPath) + '">' +
            esc(t('qAddOption')) + '</button>' +
        '</div>';
    } else if (item.type === 'score') {
      critHtml =
        '<div class="qcrit"><div class="qhint">' + esc(t('qLevelsHint')) + '</div>' +
        crit.map((c, j) =>
          '<div class="critrow">' +
            '<span class="lvlno">' + j + '</span>' +
            '<input class="nin" data-path="' + esc(base + '.criteria.' + j + '.' + lg) + '" value="' + esc(c[lg] || '') + '" placeholder="' + esc(t('qLevelLabel')) + '">' +
            '<button class="mini" data-qdelcrit="' + i + '.' + j + '">✕</button>' +
          '</div>').join('') +
        '<button class="ghost mini" data-qaddcrit="' + i + '">' + esc(t('qAddLevel')) + '</button></div>';
    } else {
      critHtml = '<div class="qhint">' + esc(t('qNoulNoCriteria')) + '</div>';
    }

    return '<div class="qcard">' +
      '<div class="qhead">' +
        '<span class="qno">Q' + (i + 2) + '</span>' +
        '<label class="chk"><input type="checkbox" data-qtoggle="' + i + '"' + (item.enabled ? ' checked' : '') + '> ' + esc(t('colEnabled')) + '</label>' +
        '<input class="kin" data-path="' + esc(base + '.key') + '" value="' + esc(item.key) + '" spellcheck="false">' +
        '<select class="qtype" data-qtype="' + i + '">' +
          ['choice', 'score', 'noul'].map(ty =>
            '<option value="' + ty + '"' + (item.type === ty ? ' selected' : '') + '>' + QTYPE_LABEL[ty] + '</option>').join('') +
        '</select>' +
        '<button class="del" data-qdel="' + i + '">' + esc(t('deleteBtn')) + '</button>' +
      '</div>' +
      langField(base + '.name', t('qName')) +
      langField(base + '.instructions', t('qAsk'), 'textarea') +
      critHtml +
      '</div>';
  }).join('');

  $('q-list').innerHTML =
    catBlock + cards +
    '<div class="meta" style="margin-top:8px">' + esc(t('qKeyHint')) + '</div>';

  // 语言切换和数量放进吸顶条——滚到哪儿都能点
  $('q-langtabs').innerHTML = langTabs;
  $('q-count').textContent = t('qCount', { n: list.filter(x => x.enabled).length });

  // --- 绑定
  $('q-langtabs').querySelectorAll('[data-qeditlang]').forEach(b => b.onclick = () => {
    state.editLang = b.dataset.qeditlang; renderQuestions();
  });
  // 选项（分类问题的 Choices + choice 问题的选项）——同一个模块，同一次绑定
  bindOptions($('q-list'));
  $('q-list').querySelectorAll('[data-path]').forEach(el => el.oninput = el.onchange = () => {
    setPath(el.dataset.path, el.value.trim());
  });
  $('q-list').querySelectorAll('[data-qtoggle]').forEach(el => el.onchange = () => {
    state.questions.questions[+el.dataset.qtoggle].enabled = el.checked;
    markDirty();
  });
  $('q-list').querySelectorAll('[data-qtype]').forEach(el => el.onchange = () => {
    const i = +el.dataset.qtype;
    state.questions.questions[i].type = el.value;
    // 换类型后选项结构可能不兼容，给个合理的初值，再重绘
    const cur = state.questions.questions[i].criteria || [];
    if (el.value === 'noul') state.questions.questions[i].criteria = [];
    else if (el.value === 'score' && (!cur.length || cur[0].key !== undefined))
      state.questions.questions[i].criteria = [{ zh: '', en: '', ms: '' }, { zh: '', en: '', ms: '' }];
    else if (el.value === 'choice' && (!cur.length || cur[0].key === undefined))
      state.questions.questions[i].criteria = [
        { key: 'a', zh: '', en: '', ms: '' }, { key: 'b', zh: '', en: '', ms: '' }];
    markDirty(); renderQuestions();
  });
  $('q-list').querySelectorAll('[data-qdel]').forEach(b => b.onclick = () => {
    state.questions.questions.splice(+b.dataset.qdel, 1);
    markDirty(); renderQuestions();
  });
  $('q-list').querySelectorAll('[data-qaddcrit]').forEach(b => b.onclick = () => {
    const item = state.questions.questions[+b.dataset.qaddcrit];
    item.criteria = item.criteria || [];
    item.criteria.push(item.type === 'choice'
      ? { key: 'opt' + (item.criteria.length + 1), zh: '', en: '', ms: '' }
      : { zh: '', en: '', ms: '' });
    markDirty(); renderQuestions();
  });
  $('q-list').querySelectorAll('[data-qdelcrit]').forEach(b => b.onclick = () => {
    const [i, j] = b.dataset.qdelcrit.split('.').map(Number);
    state.questions.questions[i].criteria.splice(j, 1);
    markDirty(); renderQuestions();
  });
}

function addQuestion() {
  state.questions.questions = state.questions.questions || [];
  state.questions.questions.push({
    key: 'q' + (state.questions.questions.length + 1),
    enabled: true, type: 'noul',
    name: { zh: '', en: '', ms: '' },
    instructions: { zh: '', en: '', ms: '' },
    criteria: [],
  });
  markDirty();
  renderQuestions();
}

async function saveQuestions() {
  $('q-msg').innerHTML = '';
  try {
    const r = await fetch('/api/categories', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ categories: state.categories, questions: state.questions })
    });
    const d = await r.json();
    if (!r.ok || !d.ok) {
      $('q-msg').innerHTML = '<div class="errline">' + esc(t('saveFailed')) + '</div>' +
        (d.errors || []).map(e => '<div class="errsub">· ' + esc(e) + '</div>').join('');
      return;
    }
    clearDirty();
    await loadCategories();
    $('q-msg').innerHTML = '<div class="okline">' + esc(t('saved')) + '</div>' +
      (d.warnings || []).map(w => '<div class="warnsub">⚠ ' + esc(w) + '</div>').join('');
  } catch (e) {
    $('q-msg').innerHTML = '<div class="errline">' + esc(t('saveFailed')) + ': ' + esc(e.message) + '</div>';
  }
}

async function discardQuestions() {
  await loadCategories();
  clearDirty();
  $('q-msg').innerHTML = '<div class="meta">' + esc(t('discardDone')) + '</div>';
}

// 预览：把「真正送给模型的问题」摊开给你看，便于确认改动是否如你所愿。
// 再点一次按钮（或点右上角 ✕）收起。
async function previewQuestions() {
  const box = $('q-preview-box');

  if (!box.classList.contains('hidden')) {          // 已经开着 -> 收起
    box.className = 'hidden';
    box.innerHTML = '';
    return;
  }

  const qlang = (state.uiLang === 'zh') ? 'zh' : 'en';   // 界面中文看中文问法，其余看英文
  let d;
  try {
    d = await (await fetch('/api/questions/preview?qlang=' + qlang)).json();
  } catch (e) {
    box.className = 'note red';
    box.innerHTML = esc(t('errInternal')) + ': ' + esc(e.message);
    return;
  }
  if (!d.ok) { box.className = 'note red'; box.innerHTML = esc(t('errInternal')); return; }

  const rows = Object.entries(d.questions).map(([k, v]) => {
    const crit = v.criteria
      ? (Array.isArray(v.criteria) ? v.criteria.join(' ｜ ') : Object.values(v.criteria).join(' ｜ '))
      : '—';
    return '<tr><td><code>' + esc(k) + '</code></td><td><span class="typebadge">' + esc(v.type) +
           '</span></td><td>' + esc(v.instructions) + '</td>' +
           '<td style="color:var(--dim);font-size:12px">' + esc(crit) + '</td></tr>';
  }).join('');

  const help = [['choice', t('qTypeChoice')], ['score', t('qTypeScore')], ['noul', t('qTypeNoul')]]
    .map(([k, v]) => '<div class="typehelp"><span class="typebadge">' + k + '</span>' + esc(v) + '</div>')
    .join('');

  box.className = 'note';
  box.innerHTML =
    '<div class="previewhead">' +
      '<b>' + esc(t('qPreviewTitle')) + '</b>' +
      '<button class="mini" id="q-preview-close" title="' + esc(t('qPreviewClose')) + '">✕ ' + esc(t('qPreviewClose')) + '</button>' +
    '</div>' +
    '<div style="font-size:13px;margin-top:4px">' +
      esc(t('qPreviewHint', { lang: qlang, n: Object.keys(d.questions).length })) + '</div>' +
    '<div class="typehelps">' + help + '</div>' +
    '<table style="margin-top:10px"><tr><th>' + esc(t('qKeyCol')) + '</th><th>' + esc(t('qTypeCol')) +
    '</th><th>' + esc(t('qAsk')) + '</th><th>' + esc(t('qOptLabel')) + '</th></tr>' + rows + '</table>';

  $('q-preview-close').onclick = () => {
    box.className = 'hidden';
    box.innerHTML = '';
  };
}

// ---------------------------------------------------------------- 主题
// 三档：跟随系统 / 浅色 / 深色，默认跟随系统。
// data-theme 属性在 <head> 的内联脚本里已经设过一遍（防首屏闪色），
// 这里负责后续切换、持久化，以及"跟随系统"时响应系统主题变化。
function resolveTheme(mode) {
  if (mode === 'light' || mode === 'dark') return mode;
  const mq = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)');
  return (mq && mq.matches) ? 'dark' : 'light';
}

function applyTheme(mode, persist) {
  state.theme = ['light', 'dark'].includes(mode) ? mode : 'system';
  document.documentElement.setAttribute('data-theme', resolveTheme(state.theme));
  if (persist !== false) {
    try { localStorage.setItem('laya.theme', state.theme); } catch (e) { /* 隐私模式 */ }
  }
  const sel = $('uiTheme');
  if (sel) sel.value = state.theme;
}

function initTheme() {
  const mq = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)');
  if (mq && mq.addEventListener) {
    mq.addEventListener('change', () => {
      if (state.theme === 'system') applyTheme('system', false);
    });
  }
  applyTheme(state.theme, false);          // 不重复写 localStorage
  const sel = $('uiTheme');
  if (sel) sel.onchange = e => applyTheme(e.target.value);
}

// ---------------------------------------------------------------- 回到最上面
// 滚下去才出现；点击平滑回顶。阈值用 320px，短页面（比如只有一个结果卡片）
// 不会一闪一闪地冒出来。
const TO_TOP_AT = 320;

function updateToTop() {
  const el = $('to-top');
  if (el) el.classList.toggle('on', window.scrollY > TO_TOP_AT);
}

function initToTop() {
  const el = $('to-top');
  if (!el) return;
  el.onclick = () => {
    const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    window.scrollTo({ top: 0, behavior: reduce ? 'auto' : 'smooth' });
  };
  window.addEventListener('scroll', updateToTop, { passive: true });
  updateToTop();
}

function switchTab(name) {
  state.tab = name;
  ['analyze', 'questions'].forEach(p => {
    const el = $('pane-' + p);
    if (el) el.classList.toggle('hidden', p !== name);
  });
  document.querySelectorAll('.tab').forEach(b => b.classList.toggle('on', b.dataset.tab === name));
  if (name === 'questions') { renderQuestions(); refreshConfigStatus(); }
}

document.querySelectorAll('.tab').forEach(b => b.onclick = () => switchTab(b.dataset.tab));
$('uiLang').value = state.uiLang;
$('uiLang').onchange = e => setUiLang(e.target.value);
$('go').onclick = analyze;
// 问题与选项（分类已经并进来，所以导出/保存只有一套）
$('q-add').onclick = addQuestion;
$('q-save').onclick = saveQuestions;
$('q-discard').onclick = discardQuestions;
$('q-preview').onclick = previewQuestions;
$('q-export').onclick = () => { window.location.href = '/api/config/export.json'; };
$('q-import').onclick = () => $('file-csv').click();
$('q-export-xlsx').onclick = () => { window.location.href = '/api/categories/export.xlsx'; };
$('q-export-csv').onclick = () => { window.location.href = '/api/categories/export.csv'; };
$('file-csv').onchange = e => { if (e.target.files[0]) importTable(e.target.files[0]); e.target.value = ''; };
document.querySelectorAll('[data-demo]').forEach(b => b.onclick = () => {
  $('text').value = DEMOS[b.dataset.demo]; $('text').focus(); switchTab('analyze');
});
$('text').addEventListener('keydown', e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) analyze(); });

// 深链接：#analyze / #manage / #questions
const _hashTab = (location.hash || '').replace('#', '');
const _tabOf = () => (['analyze', 'manage', 'questions'].includes(_hashTab) ? _hashTab : 'analyze');
state.editLang = state.uiLang;
$('go').disabled = true;
applyI18n();
initTheme();
pollStatus();
loadCategories();
initToTop();
switchTab(_tabOf());
window.addEventListener('hashchange', () => switchTab(_tabOf()));
