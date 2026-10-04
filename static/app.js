/* KB Studio 前端 —— 无框架、无外部依赖，离线可用 */

const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));
const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

let STATE = { cards: [], graph: null, tasks: [], stats: {}, filterKind: null, filterTag: null };

/* ------------------------------------------------------------ 网络 */
async function api(path, opts) {
  const r = await fetch(path, Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts));
  return r.json();
}

function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove('show'), 2600);
}

/* ------------------------------------------------------------ 载入 */
async function loadAll() {
  const [cards, graph, tasks, stats] = await Promise.all([
    api('/api/cards'), api('/api/graph'), api('/api/tasks'), api('/api/stats')
  ]);
  STATE.cards = cards.cards || [];
  STATE.graph = graph;
  STATE.tasks = tasks.tasks || [];
  STATE.stats = stats || {};
  renderSidebar();
  renderDashboard();
  renderCards();
  renderStroll();
}

/* ------------------------------------------------------------ 侧栏 */
function renderSidebar() {
  const s = STATE.stats;
  const kinds = s.byKind || {};
  $('#statbox').innerHTML = [
    ['卡片总数', s.total || 0],
    ['待复习', s.tasksDue != null ? s.tasksDue : 0],
    ['概念', kinds.concept || 0],
    ['原料', kinds.raw || 0]
  ].map(([k, v]) => `<div class="stat"><b>${v}</b><span>${k}</span></div>`).join('');

  const labels = { concept: '概念', connection: '连接', question: '问题', raw: '原料', system: '系统', page: '页面' };
  $('#kinds').innerHTML = Object.keys(labels).map(k => {
    const n = (STATE.cards.filter(c => c.kind === k)).length;
    if (!n) return '';
    return `<button class="chip ${STATE.filterKind === k ? 'on' : ''}" data-kind="${k}">${labels[k]} ${n}</button>`;
  }).join('') + (STATE.filterKind || STATE.filterTag
      ? '<button class="chip clear" data-clear="1">清除筛选</button>' : '');

  $('#tagcloud').innerHTML = (s.topTags || []).map(([t, n]) =>
    `<button class="chip ${STATE.filterTag === t ? 'on' : ''}" data-tag="${esc(t)}">${esc(t)} ${n}</button>`
  ).join('') || '<div class="empty">还没有标签</div>';

  const undone = STATE.tasks.filter(t => !t.done);
  $('#dueCount').textContent = undone.length ? undone.length : '';
  $('#tasklist').innerHTML = undone.length
    ? undone.slice(0, 8).map(t => `
        <div class="item" ${t.card ? `data-task-card="${esc(t.card)}" data-task-title="${esc(t.title)}"` : ''}>
          <div class="t">${esc(t.title)}</div>
          <div class="d">${esc(t.due || '')}${t.card ? ' ｜ 点开读完再勾' : ''}</div>
        </div>`).join('')
    : '<div class="empty">全部完成</div>';
}

/* ------------------------------------------------------------ 仪表盘 */
function renderDashboard() {
  const s = STATE.stats, k = s.byKind || {};
  $('#bigstats').innerHTML = [
    ['卡片总数', s.total || 0], ['概念', k.concept || 0], ['连接', k.connection || 0],
    ['问题', k.question || 0], ['原料', k.raw || 0], ['待复习', (s.tasksDue != null ? s.tasksDue : 0)]
  ].map(([a, b]) => `<div class="stat"><b>${b}</b><span>${a}</span></div>`).join('');

  const recent = STATE.cards.filter(c => c.kind === 'raw').sort((a, b) => b.mtime - a.mtime).slice(0, 6);
  $('#recent').innerHTML = recent.length ? recent.map(c => itemHTML(c)) .join('') : '<div class="empty">还没有原料</div>';

  // 问题卡：这套体系里「还没想清楚的」比「已知的」更值钱，所以单独占一格
  const qs = STATE.cards.filter(c => c.kind === 'question').sort((a, b) => b.mtime - a.mtime).slice(0, 6);
  $('#questions').innerHTML = qs.length ? qs.map(c => itemHTML(c)).join('')
    : '<div class="empty">还没有问题卡——读完东西记下「我还是没想清楚的那件事」</div>';

  const todo = STATE.cards.filter(c => c.status === '待实践').sort((a, b) => b.mtime - a.mtime).slice(0, 6);
  $('#practice').innerHTML = todo.length ? todo.map(c => itemHTML(c)).join('')
    : '<div class="empty">还没有标「待实践」的卡 —— 打开任意一张卡，点顶部「标为待实践」就会进这一格</div>';

  const today = new Date().toISOString().slice(0, 10);
  const due = STATE.tasks.filter(t => !t.done && (!t.due || t.due <= today));
  $('#due').innerHTML = due.length ? due.map(t =>
    `<div class="item" data-task-card="${esc(t.card || '')}" data-task-title="${esc(t.title || '')}">
       <div class="t">${esc(t.title)}</div>
       <div class="s">${esc(t.due || '')} ｜ ${esc(t.category || '')}</div>
     </div>`).join('') : '<div class="empty">今天没有到期任务</div>';
}

function itemHTML(c) {
  return `<div class="item" data-path="${esc(c.path)}">
    <div class="t">${esc(c.title)}</div>
    <div class="s">${esc(c.summary || '')}</div></div>`;
}

function renderStroll() {
  const pool = STATE.cards.filter(c => c.kind !== 'page');
  const pick = pool.sort(() => Math.random() - 0.5).slice(0, 3);
  $('#stroll').innerHTML = pick.length
    ? pick.map(c => `<div class="card ${c.kind}" data-path="${esc(c.path)}">
        <h4>${esc(c.title)}</h4><p>${esc(c.summary || '')}</p></div>`).join('')
    : '<div class="empty">还没有卡片</div>';
}

/* ------------------------------------------------------------ 卡片列表 */
function renderCards() {
  const q = ($('#filter').value || '').toLowerCase();
  let list = STATE.cards.slice();
  if (STATE.filterKind) list = list.filter(c => c.kind === STATE.filterKind);
  if (STATE.filterTag) list = list.filter(c => c.tags.includes(STATE.filterTag));
  if (q) list = list.filter(c =>
    c.title.toLowerCase().includes(q) || c.tags.join(' ').toLowerCase().includes(q));
  const sortBy = $('#sortBy').value;
  list.sort(sortBy === 'title' ? (a, b) => a.title.localeCompare(b.title, 'zh') : (a, b) => b.mtime - a.mtime);

  $('#cardlist').innerHTML = list.length ? list.map(c => `
    <div class="card ${c.kind}" data-path="${esc(c.path)}">
      <h4>${esc(c.title)}</h4>
      <p>${esc(c.summary || '（无摘要）')}</p>
      <div class="meta">
        <span class="kind ${c.kind}">${c.kindLabel}</span>
        ${c.level ? `<span class="lv">${esc(c.level)}</span>` : ''}
        ${c.status ? `<span class="tag">${esc(c.status)}</span>` : ''}
        ${c.tags.slice(0, 3).map(t => `<span class="tag">#${esc(t)}</span>`).join('')}
      </div>
    </div>`).join('') : '<div class="empty">没有匹配的卡片</div>';
  reveal($('#cardlist'));
}

/* 滚动进场 + 指针发光：都在 JS 里打状态，脚本挂了内容也不会隐身 */
let RO = null;
function reveal(root) {
  if (!root || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  if (!RO) RO = new IntersectionObserver(es => es.forEach(e => {
    if (e.isIntersecting) { e.target.classList.add('in'); RO.unobserve(e.target); }
  }), { threshold: .12 });
  [...root.children].forEach((el, i) => {
    if (el.classList.contains('reveal')) return;
    el.classList.add('reveal');
    el.style.setProperty('--i', Math.min(i, 12));
    RO.observe(el);
  });
}
document.addEventListener('pointermove', e => {
  const c = e.target.closest('.card');
  if (!c) return;
  const r = c.getBoundingClientRect();
  c.style.setProperty('--mx', (e.clientX - r.left) + 'px');
  c.style.setProperty('--my', (e.clientY - r.top) + 'px');
}, { passive: true });

/* ------------------------------------------------------------ Markdown */
// 正文宽度：他屏宽，74ch 的中文一行只有三十几个字，看着像没写完
function applyWidth() {
  const w = localStorage.getItem('kb.width') || '1';
  const body = document.getElementById('readBody');
  if (body) body.className = body.className.replace(/\bw[123]\b/, '').trim() + ' w' + w;
  document.querySelectorAll('#rdWidth button').forEach(b =>
    b.classList.toggle('on', b.dataset.w === w));
}

function renderMD(src) {
  const blocks = [];
  src = src.replace(/```(\w*)\n?([\s\S]*?)```/g, (m, lang, code) => {
    blocks.push(`<pre><code>${esc(code)}</code></pre>`);
    return `\u0000B${blocks.length - 1}\u0000`;
  });
  src = esc(src);

  src = src.replace(/^###### (.*)$/gm, '<h6>$1</h6>')
           .replace(/^##### (.*)$/gm, '<h5>$1</h5>')
           .replace(/^#### (.*)$/gm, '<h4>$1</h4>')
           .replace(/^### (.*)$/gm, '<h3>$1</h3>')
           .replace(/^## (.*)$/gm, '<h2>$1</h2>')
           .replace(/^# (.*)$/gm, '<h1>$1</h1>');

  src = src.replace(/^&gt; (.*)$/gm, '<blockquote>$1</blockquote>');
  src = src.replace(/^---+$/gm, '<hr>');
  src = src.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  src = src.replace(/(?<!\*)\*([^*\n]+)\*(?!\*)/g, '<em>$1</em>');
  src = src.replace(/`([^`\n]+)`/g, '<code>$1</code>');

  // 逐字稿时间戳 → 可点：阅读页有本地视频就跳画面，没有就告诉用户按住了什么
  // 分段格式「[00:12 – 00:47]」整块可点，点上去跳到这一段开头
  const toSec = t => t.split(':').reduce((acc, x) => acc * 60 + (+x), 0);
  src = src.replace(/\[(\d{1,2}:\d{2}(?::\d{2})?)\s*[–—~]\s*(\d{1,2}:\d{2}(?::\d{2})?)\]/g,
    (m, a, b) => `<span class="ts" data-t="${toSec(a)}" data-s="${a}" title="这一段播到 ${b}">[${a} – ${b}]</span>`);
  src = src.replace(/\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]/g, (m, a, b, c) => {
    const sec = c ? (+a * 3600 + +b * 60 + +c) : (+a * 60 + +b);
    return `<span class="ts" data-t="${sec}" data-s="${m}">${m}</span>`;
  });

  // 双链 [[X]] → 图片 → 普通链接（顺序不能换：图片必须先于链接匹配）
  src = src.replace(/\[\[([^\]\|]+?)\]\]/g, (m, t) =>
    `<a class="wiki" data-wiki="${t.trim()}">${t.trim()}</a>`);
  src = src.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (m, alt, u) => {
    const p = /^assets\//.test(u) ? '/' + u : (/^\.?\.?\/assets\//.test(u) ? u.replace(/^\.?\.?\//, '/') : u);
    return `<figure class="shot"><img src="${p}" alt="${alt || '图片'}" loading="lazy">` +
           (alt ? `<figcaption>${alt}</figcaption>` : '') + `</figure>`;
  });
  src = src.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g,
    (m, t, u) => `<a href="${u}" target="_blank" rel="noopener">${t}</a>`);

  // 块级：表格 → 嵌套列表 → 普通行
  const lines = src.split('\n'), out = [];
  let para = [];
  const cjkEnd = s => /[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]$/.test(s);
  const cjkStart = s => /^[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]/.test(s);
  // 句子已经收尾（含引号 / 破折号收尾，这些不在 CJK 区间里）就该是新的一段
  const hardEnd = s => /[。！？；…！\u201d\u2019」』）)】]$/.test(s.replace(/\s+$/, ''));
  const flushPara = () => {
    if (!para.length) return;
    // 中文稿一行就是一段（从知乎/微信/txt 粘过来都这样），不能按英文排版那样软折行拼回去；
    // 拉丁文才需要把折行接回同一段。
    const groups = [para[0]];
    for (let k = 1; k < para.length; k++) {
      const prev = groups[groups.length - 1].replace(/\u0001$/, '');
      if (hardEnd(prev) || (cjkEnd(prev) && cjkStart(para[k]))) groups.push(para[k]);
      else groups[groups.length - 1] += ' ' + para[k];
    }
    groups.forEach(g => out.push('<p>' + g + '</p>'));
    para = [];
  };
  let stack = [];            // 当前打开的 ul/ol 层级
  const closeLists = () => { while (stack.length) { out.push(stack.pop() === 'ul' ? '</ul>' : '</ol>'); } };

  const isTableLine = ln => /^\s*\|.*\|\s*$/.test(ln);
  const isSeparator = ln => /^\s*\|[\s:|-]+\|\s*$/.test(ln);
  const cells = ln => ln.trim().replace(/^\||\|$/g, '').split('|').map(s => s.trim());

  for (let i = 0; i < lines.length; i++) {
    const ln = lines[i];

    // 表格：连续的 | 行，第二行是 --- 分隔线
    if (isTableLine(ln) && lines[i + 1] && isSeparator(lines[i + 1])) {
      closeLists(); flushPara();
      const head = cells(ln);
      i += 2;
      const rows = [];
      while (i < lines.length && isTableLine(lines[i])) { rows.push(cells(lines[i])); i++; }
      i--;
      const ncol = head.length, nrow = rows.length;
      const wide = (head.join('').length + rows.flat().join('').length) > 260;
      // 又宽又长：横滚丢行标签、纵滚丢表头，所以首列和表头都钉住，滚动收进容器里
      const grid = ncol >= 3 && (wide || nrow > 10);
      const long = nrow > 12;
      const cls = ['tw', ncol <= 2 ? 'def' : '', wide ? 'wide' : ''].filter(Boolean).join(' ');
      const box = ['twx', grid ? 'grid' : '', long ? 'long' : ''].filter(Boolean).join(' ');
      const bar = grid
        ? `<div class="tbar"><span>${nrow} 行 × ${ncol} 列` +
          (long ? '，首列和表头已钉住' : '') + '</span>' +
          '<button class="tfx" type="button">放大</button></div>'
        : '';
      out.push(`<div class="${box}">${bar}<div class="${cls}"><table><thead><tr>` +
        head.map(h => `<th>${h}</th>`).join('') + '</tr></thead><tbody>' +
        rows.map(r => '<tr>' + r.map(c => `<td>${c}</td>`).join('') + '</tr>').join('') +
        '</tbody></table></div></div>');
      continue;
    }

    // 列表（支持两空格一级的嵌套 + [ ] 任务项）
    const li = /^(\s*)([-*+]|\d+\.)\s+(.*)$/.exec(ln);
    if (li) {
      flushPara();
      const depth = Math.floor(li[1].replace(/\t/g, '  ').length / 2);
      const kind = /^\d/.test(li[2]) ? 'ol' : 'ul';
      while (stack.length > depth + 1) { out.push(stack.pop() === 'ul' ? '</ul>' : '</ol>'); }
      if (stack.length < depth + 1) {
        while (stack.length < depth + 1) { out.push('<' + kind + '>'); stack.push(kind); }
      }
      let body = li[3];
      const task = /^\[( |x|X)\]\s+(.*)$/.exec(body);
      if (task) {
        body = `<label class="tk"><input type="checkbox" disabled${task[1] !== ' ' ? ' checked' : ''}>${task[2]}</label>`;
      }
      out.push(`<li>${body}</li>`);
      continue;
    }
    closeLists();

    if (/^<(h\d|hr|blockquote|pre|figure)/.test(ln)) { flushPara(); out.push(ln); continue; }
    if (/^\u0000B\d+\u0000$/.test(ln.trim())) { flushPara(); out.push(ln); continue; }
    // 连续非空行合成一个段落（Markdown 的正确行为），空行才分段；行尾两空格 = 硬换行
    const t = ln.trim();
    if (!t) { flushPara(); continue; }
    para.push(/[ \t]{2}$/.test(ln) ? t + '\u0001' : t);
  }
  closeLists();
  flushPara();
  let html = out.join('\n');
  html = html.replace(/\u0000B(\d+)\u0000/g, (m, i) => blocks[+i]);
  html = html.replace(/\u0001/g, '<br>');
  return html;
}

/* ------------------------------------------------------------ 阅读 */
// 复习任务点进来时，把正文里对应那一段找出来并闪一下
const normQ = s => String(s || "").replace(/[\s「」『』“”"'’‘、，。：:；;？！?!（）()【】\[\]—\-｜|]/g, "");
function locateInBody(q) {
  const key = normQ(String(q || "").replace(/^回看原文\s*[：:]\s*/, ""));
  if (key.length < 4) return false;
  const all = [...document.querySelectorAll('#readBody p, #readBody li, #readBody h2, #readBody h3, #readBody blockquote')];
  // 文末那份卡列表里也有同样的问题名 —— 先只在正文里找，别把高亮打在链接清单上
  const head = all.find(el => /^这张原料长成的卡/.test(el.textContent.trim()));
  const blocks = head ? all.slice(0, all.indexOf(head)) : all;
  let hit = null, best = 0;
  const scan = list => {
    for (const el of list) {
      const t = normQ(el.textContent);
      if (!t) continue;
      if (t.includes(key) || key.includes(t)) { hit = el; best = t.length; return true; }
      for (let n = Math.min(key.length, 16); n >= 8; n--) {
        for (let i = 0; i + n <= key.length; i++) {
          if (t.includes(key.slice(i, i + n))) { hit = el; best = n; return true; }
        }
      }
    }
    return false;
  };
  if (!scan(blocks)) {
    // 任务是从旧版消化稿排下来的、正文里已经换了措辞 —— 那就跳到「我的疑问」那一节。
    // 不打在文末那份链接清单上：那儿只有卡名，没有可读的段落。
    hit = all.find(el => /^我的疑问/.test(el.textContent.trim()));
    if (!hit) return false;
  }
  hit.classList.add('flash');
  hit.scrollIntoView({ block: 'center' });
  setTimeout(() => hit.classList.remove('flash'), 2600);
  return true;
}

async function openCard(path, anchor) {
  const d = await api('/api/card?path=' + encodeURIComponent(path));
  if (d.error) return toast(d.error);
  const fm = d.frontmatter || {};
  const c = cardOf(path);
  const src = fm.source && /^https?:/.test(fm.source) ? fm.source : '';
  $('#readMeta').innerHTML = `<h2>${esc(fm.title || path)}</h2>
    <span class="kind ${c.kind}">${c.kindLabel}</span>
    ${fm.level ? `<span class="lv">${esc(fm.level)}</span>` : ''}
    ${fm.status ? `<span class="tag">${esc(fm.status)}</span>` : ''}
    ${fm.platform ? `<span class="plat">来源 · ${esc(fm.platform)}</span>` : ''}
    ${fm.author ? `<span class="plat">@${esc(fm.author)}</span>` : ''}
    ${(fm.tags || []).map(t => `<span class="tag">#${esc(t)}</span>`).join('')}
    ${src ? `<a class="origin" href="${esc(src)}" target="_blank" rel="noopener">查看原文 ↗</a>` : ''}`;
  $('#readBody').innerHTML = renderMD(d.body || '');
  STATE.currentCard = path;
  STATE.currentRaw = d.raw || '';
  const isCard = /^wiki\//.test(path);
  const isRaw = /^raw\//.test(path);
  $('#rdEdit').classList.toggle('hidden', !(isCard || isRaw));
  $('#rdDel').classList.toggle('hidden', !isCard);
  $('#rdRedo').classList.toggle('hidden', !isRaw);
  $('#rdSync').classList.toggle('hidden', !isRaw);
  $('#rdPractice').classList.toggle('hidden', !isCard);
  $('#rdPractice').textContent = fm.status === '待实践' ? '取消待实践' : '标为待实践';
  $('#rdStatus').textContent = isRaw
    ? '原料可以改了；改完点「按原文重新生成」，消化稿和卡片会照新原文重出一遍'
    : '';
  $('#rdText').classList.add('hidden');
  $('#rdEdit').textContent = isRaw ? '改写原料' : '改写';
  $('#mcSource').textContent = isRaw ? ('出处：' + path) : '';
  const media = String(fm.media || '').trim();
  $('#rdPlayer').innerHTML = /\.(mp4|webm|mov|m4a|mp3)$/i.test(media)
    ? `<video id="rdVideo" src="/media/${encodeURIComponent(media)}" controls preload="metadata"></video>
         <p class="hint">本地视频已就位：点正文里的 <span class="ts">[00:00 – 00:13]</span> 就跳到那一段开头。</p>`
      : '';
  renderReadTasks();
  $('#annoText').value = '';
  $('#annoStatus').textContent = '';
  $('#backlinks').innerHTML = d.backlinks && d.backlinks.length
    ? d.backlinks.map(b => itemHTML(cardOf(b.path))).join('')
    : '<div class="empty">还没有其他卡片链接到这里</div>';
  show('read');
  if (anchor) {
    setTimeout(() => {
      locateInBody(anchor) || toast('没在这份里定位到「' + anchor.slice(0, 12) + '…」那一段，已打开全文');
    }, 60);
  }
}

function renderReadTasks() {
  const el = $('#readTasks');
  if (!el) return;
  const mine = STATE.tasks.filter(t => t.card === STATE.currentCard);
  const row = t => `<label class="task${t.done ? ' pasted' : ''}">
        <input type="checkbox" data-done-task="${esc(t.title)}"${t.done ? ' checked' : ''}>
        <span><span class="t">${esc(t.title)}</span><br><span class="d">${esc(t.due || '')} ｜ ${esc(t.category || '')}</span></span>
      </label>`;
  el.innerHTML = mine.length
    ? mine.map(row).join('')
      + '<p class="hint">读完这张卡再勾，才算真的复习过；勾错了点一下取消即可。</p>'
    : '<div class="empty">这张卡目前没有排到的复习任务</div>';
}

if (document.getElementById('readTasks')) {
  document.getElementById('readTasks').addEventListener('change', async e => {
    const box = e.target.closest('[data-done-task]');
    if (!box) return;
    const r = await api('/api/task/done', {
      method: 'POST',
      body: JSON.stringify({ title: box.dataset.doneTask, undo: !box.checked }) });
    toast(box.checked ? '已标记完成' : '已撤销');
    await loadAll();
    renderReadTasks();
  });
}

async function saveAnnotation() {
  const text = ($('#annoText').value || '').trim();
  if (!text) return toast('批注是空的');
  if (!STATE.currentCard) return toast('先打开一张卡');
  const r = await api('/api/annotate', {
    method: 'POST', body: JSON.stringify({ path: STATE.currentCard, text })
  });
  if (!r.ok) return toast('失败：' + r.error);
  $('#annoStatus').textContent = '已写进这张卡，并抄一份到 inbox/annotations.md 等归位';
  $('#annoText').value = '';
  const d = await api('/api/card?path=' + encodeURIComponent(STATE.currentCard));
  $('#readBody').innerHTML = renderMD(d.body || '');
}

function toggleCardEdit() {
  const ta = $('#rdText'), body = $('#readBody'), btn = $('#rdEdit');
  if (ta.classList.contains('hidden')) {
    ta.value = STATE.currentRaw || '';
    ta.classList.remove('hidden');
    body.classList.add('hidden');
    btn.textContent = '保存';
    $('#rdStatus').textContent = /^raw\//.test(STATE.currentCard)
      ? '这是原料本体：只替换你改的部分，消化稿要重新生成才会跟着变'
      : '改的是这张卡的 md 原文，存完 Obsidian 那边同步可见';
  } else {
    const content = ta.value;
    api('/api/save', { method: 'POST', body: JSON.stringify({ path: STATE.currentCard, content }) })
      .then(r => {
        if (!r.ok) return toast('失败：' + (r.error || '没写进去'));
        toast('已保存');
        ta.classList.add('hidden');
        body.classList.remove('hidden');
        btn.textContent = '改写';
        return openCard(STATE.currentCard);
      });
  }
}

async function markPractice() {
  if (!STATE.currentCard) return toast('先打开一张卡');
  const r = await api('/api/status', { method: 'POST',
    body: JSON.stringify({ path: STATE.currentCard }) });
  if (!r.ok) return toast(r.error || '没标上');
  $('#rdPractice').textContent = r.status === '待实践' ? '取消待实践' : '标为待实践';
  await loadAll();
  const tag = document.querySelector('#readMeta .tag');
  if (tag) tag.textContent = r.status;
  toast(r.status === '待实践' ? '已进「待实践」，仪表盘那一格就是它' : '已从「待实践」撤下');
}

async function syncCards() {
  if (!STATE.currentCard) return toast('先打开一份原料');
  $('#rdStatus').textContent = '正在照消化稿重长卡片…（不调模型，不花钱）';
  $('#rdSync').disabled = true;
  let r;
  try {
    r = await api('/api/recompile', { method: 'POST', body: JSON.stringify({ path: STATE.currentCard }) });
  } finally {
    $('#rdSync').disabled = false;
  }
  if (!r.ok) {
    $('#rdStatus').textContent = '';
    return toast('失败：' + (r.error || '没同步成') );
  }
  $('#rdStatus').textContent = r.note || '已同步';
  await loadAll();
  await openCard(STATE.currentCard);
  if (r.dropped && r.dropped.length)
    toast('拦下 ' + r.dropped.length + ' 条不该成卡的，理由在原料末尾');
}

async function redoDigest() {
  if (!STATE.currentCard) return toast('先打开一份原料');
  const ta = $('#rdText');
  const editing = !ta.classList.contains('hidden');
  const payload = { path: STATE.currentCard };
  if (editing) payload.content = ta.value;
  $('#rdStatus').textContent = '正在照当前原文重新消化…（要调模型，十几秒）';
  $('#rdRedo').disabled = true;
  let r;
  try {
    r = await api('/api/redigest', { method: 'POST', body: JSON.stringify(payload) });
  } finally {
    $('#rdRedo').disabled = false;
  }
  if (!r.ok) {
    $('#rdStatus').textContent = '';
    return toast('失败：' + (r.error || '没重新生成') + (r.hint ? '（' + r.hint + '）' : ''));
  }
  $('#rdStatus').textContent = '消化稿已重写：' + (r.note || '完成')
    + (r.backup ? '，旧稿备份在 ' + r.backup : '');
  $('#rdText').classList.add('hidden');
  $('#readBody').classList.remove('hidden');
  $('#rdEdit').textContent = '改写原料';
  await loadAll();
  openCard(STATE.currentCard);
}

async function addManualCard() {
  const name = ($('#mcName').value || '').trim();
  const text = ($('#mcText').value || '').trim();
  if (!name) return toast('卡名要写一句能独立成立的话');
  const r = await api('/api/card/add', {
    method: 'POST',
    body: JSON.stringify({ kind: $('#mcKind').value, name, text,
                           use: ($('#mcUse').value || '').trim(),
                           source: /^raw\//.test(STATE.currentCard || '') ? STATE.currentCard : '' })
  });
  if (!r.ok) {
    $('#mcStatus').textContent = '失败：' + r.error + (r.hint ? ' — ' + r.hint : '');
    return;
  }
  $('#mcStatus').textContent = (r.state === 'exists' ? '已有同名卡，没动它：' : '已建：') + r.path;
  $('#mcName').value = ''; $('#mcText').value = ''; $('#mcUse').value = '';
  await loadAll();
}

async function dropCard() {
  if (!STATE.currentCard) return toast('先打开一张卡');
  if (!confirm('把这张卡移进回收站（inbox/trash/）？文件不会真删，要恢复我随时能拿回来。')) return;
  const r = await api('/api/card/drop', {
    method: 'POST', body: JSON.stringify({ path: STATE.currentCard }) });
  if (!r.ok) return toast('失败：' + (r.error || '没删掉'));
  toast('已移进回收站' + (r.tasksRemoved ? '，顺带清掉 ' + r.tasksRemoved + ' 条复习任务' : ''));
  await loadAll();
  show('cards');
}

async function previewFetch() {  const url = ($('#digestUrl').value || '').trim();
  if (!url) return toast('先粘一条链接');
  const st = $('#digestStatus');
  const KIND_CN = { image: '图文（走图片识字）', video: '视频（走转写 + 抽帧识字）', text: '纯文字' };
  st.textContent = '探测中…（视频转写可能要几分钟）';
  $('#previewResult').classList.remove('hidden');
  try {
    const r = await api('/api/fetch', { method: 'POST', body: JSON.stringify({ url }) });
    st.textContent = '';
    $('#previewResult').innerHTML = r.ok
      ? `<div class="okline">✅ 能读｜平台 ${esc(r.platform)}｜类型：${esc(KIND_CN[r.kind] || r.kind || '未标注')}｜${r.chars} 字｜图片 ${r.images} 张｜方式：${esc(r.method)}</div>
         <div class="hint">标题：${esc(r.title || '')}${r.author ? '｜作者：' + esc(r.author) : ''}</div>
         <div class="pv">${esc(r.preview || '')}…</div>`
      : `<div class="noline">⚠️ 读不到｜${esc(r.error || '')}</div>
         <div class="hint">${esc(r.hint || '')}</div>`;
  } catch (e) { st.textContent = ''; $('#previewResult').innerHTML = '<div class="noline">探测失败：' + esc(String(e)) + '</div>'; }
}

function cardOf(path) {
  return STATE.cards.find(c => c.path === path) || { path, title: path, kind: 'page', kindLabel: '页面', summary: '' };
}

/* ------------------------------------------------------------ 图谱 */
let GP = null;
function drawGraph() {
  const cv = $('#graphCanvas');
  if (!cv || !STATE.graph) return;
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth, h = cv.clientHeight;
  cv.width = w * dpr; cv.height = h * dpr;
  const ctx = cv.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const g = STATE.graph;
  const nodes = g.nodes.map(n => Object.assign({}, n));
  const pos = {};
  nodes.forEach((n, i) => {
    const a = (i / Math.max(1, nodes.length)) * Math.PI * 2;
    pos[n.id] = { x: w / 2 + Math.cos(a) * Math.min(w, h) * 0.3, y: h / 2 + Math.sin(a) * Math.min(w, h) * 0.3, vx: 0, vy: 0 };
  });
  const degree = {};
  g.links.forEach(l => {
    degree[l.source] = (degree[l.source] || 0) + 1;
    degree[l.target] = (degree[l.target] || 0) + 1;
  });

  for (let it = 0; it < 260; it++) {
    const k = 0.55;
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = pos[nodes[i].id], b = pos[nodes[j].id];
        let dx = a.x - b.x, dy = a.y - b.y;
        let d2 = dx * dx + dy * dy || 0.01;
        const rep = 9000 / d2;
        const d = Math.sqrt(d2);
        a.vx += (dx / d) * rep; a.vy += (dy / d) * rep;
        b.vx -= (dx / d) * rep; b.vy -= (dy / d) * rep;
      }
    }
    g.links.forEach(l => {
      const a = pos[l.source], b = pos[l.target];
      if (!a || !b) return;
      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const f = (d - 110) * 0.02;
      a.vx += (dx / d) * f; a.vy += (dy / d) * f;
      b.vx -= (dx / d) * f; b.vy -= (dy / d) * f;
    });
    nodes.forEach(n => {
      const p = pos[n.id];
      p.vx += (w / 2 - p.x) * 0.006;
      p.vy += (h / 2 - p.y) * 0.006;
      p.vx *= 0.82; p.vy *= 0.82;
      p.x += p.vx * k; p.y += p.vy * k;
      p.x = Math.max(50, Math.min(w - 50, p.x));
      p.y = Math.max(40, Math.min(h - 40, p.y));
    });
  }

  const view = { ox: 0, oy: 0, k: 1 };   // 平移/缩放状态（必须在 paint() 首次调用前存在）

  // 和 .obsidian/graph.json 的 colorGroups 一致，两边同一套语言
  const color = { concept: '#22c55e', connection: '#f59e0b', question: '#a855f7',
                  raw: '#6b7280', system: '#3b82f6', page: '#adb5bd' };

  function paint() {
    ctx.clearRect(0, 0, w, h);
    ctx.save();
    ctx.translate(view.ox * view.k, view.oy * view.k);
    ctx.scale(view.k, view.k);
    ctx.lineWidth = 1;
    g.links.forEach(l => {
      const a = pos[l.source], b = pos[l.target];
      if (!a || !b) return;
      ctx.strokeStyle = 'rgba(150,158,172,.4)';
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    });
    nodes.forEach(n => {
      const p = pos[n.id];
      const r = 5 + Math.min(9, (degree[n.id] || 0) * 1.6);
      ctx.fillStyle = color[n.kind] || color.page;
      ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.stroke();
      ctx.fillStyle = '#4a5160';
      ctx.font = '12px -apple-system,"PingFang SC","Microsoft YaHei",sans-serif';
      ctx.textAlign = 'center';
      const label = n.label.length > 12 ? n.label.slice(0, 12) + '…' : n.label;
      ctx.fillText(label, p.x, p.y + r + 14);
    });
    ctx.restore();
    GP = { pos, nodes, w, h, view };
  }
  paint();

  // 交互：拖节点 / 拖空白平移 / 滚轮缩放 / 纯点击才打开卡片
  let act = null, moved = 0, hover = null;
  const hit = (x, y) => nodes.map(n => ({ n, p: pos[n.id] }))
    .find(o => Math.hypot((o.p.x + view.ox) * view.k - x, (o.p.y + view.oy) * view.k - y) < 15);
  const pt = e => { const r = cv.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
  const cursor = () => { cv.style.cursor = hover ? 'grab' : 'default'; };

  cv.onmousedown = e => {
    const [x, y] = pt(e);
    moved = 0;
    const o = hit(x, y);
    act = o ? { type: 'node', o, x, y } : { type: 'pan', x, y };
    cv.style.cursor = 'grabbing';
  };
  cv.onmousemove = e => {
    const [x, y] = pt(e);
    if (!act) { hover = hit(x, y); cursor(); return; }
    moved += Math.abs(x - act.x) + Math.abs(y - act.y);
    if (act.type === 'node') { act.o.p.x += (x - act.x) / view.k; act.o.p.y += (y - act.y) / view.k; }
    else { view.ox += (x - act.x) / view.k; view.oy += (y - act.y) / view.k; }
    act.x = x; act.y = y;
    paint();
  };
  cv.onmouseup = e => {
    const [x, y] = pt(e);
    const was = act;
    act = null;
    cursor();
    if (!was) return;
    if (moved < 4 && was.type === 'node' && was.o) {          // 没怎么动才算「点击」
      openCard(was.o.n.id);
      return;
    }
    if (moved < 4 && was.type === 'pan') { view.ox = 0; view.oy = 0; paint(); }  // 点空白复原
  };
  cv.onmouseleave = () => { act = null; };
  cv.onwheel = e => {
    e.preventDefault();
    view.k = Math.max(0.45, Math.min(2.6, view.k * (e.deltaY < 0 ? 1.12 : 0.9)));
    paint();
  };

  function tips(on) {
    const el = $('#graphTips');
    if (el) el.classList.toggle('hidden', !on);
  }
  tips(true);
}

/* ------------------------------------------------------------ 消化 */
async function doDigest() {
  const url = $('#digestUrl').value.trim();
  const text = $('#digestText').value.trim();
  if (!url && !text) return toast('请填链接或粘贴正文');
  const btn = $('#digestBtn'), st = $('#digestStatus');
  btn.disabled = true; st.textContent = '抓取中…（视频转写可能要几分钟）';
  $('#digestResult').classList.add('hidden');
  try {
    const r = await api('/api/digest', {
      method: 'POST',
      body: JSON.stringify({ url, text, compile: $('#digestCompile').checked })
    });
    if (!r.ok) {
      st.textContent = '失败：' + r.error;
      toast('消化失败');
      return;
    }
    st.textContent = `完成：${r.platform} ｜ ${r.method} ｜ 已写入 ${r.clip}`
      + (r.note ? ' ｜ ' + r.note : '');
    $('#digestOut').innerHTML = renderMD(r.digest || '');
    renderNewCards(r);
    $('#digestResult').classList.remove('hidden');
    $('#digestUrl').value = ''; $('#digestText').value = '';
    await loadAll();
    toast('已入库');
  } catch (e) {
    st.textContent = '出错：' + e.message;
  } finally {
    btn.disabled = false;
  }
}

/* ------------------------------------------------------------ 视图 */
function show(name) {
  $$('.tab').forEach(t => t.classList.toggle('active', t.dataset.view === name));
  $$('.view').forEach(v => v.classList.remove('active'));
  $('#view-' + name).classList.add('active');
  if (name === 'graph') setTimeout(drawGraph, 30);
  window.scrollTo(0, 0);
}

/* ------------------------------------------------------------ 事件 */
function bind() {
  $$('.tab').forEach(t => t.onclick = () => {
    // 筛选只属于「卡片」这一屏：切走就清掉，避免仪表盘上留着高亮的勾选态
    if (t.dataset.view !== 'cards' && (STATE.filterKind || STATE.filterTag)) {
      STATE.filterKind = null; STATE.filterTag = null; renderSidebar(); renderCards();
    }
    show(t.dataset.view);
  });

  $('#search').addEventListener('keydown', e => {
    if (e.key !== 'Enter') return;
    const q = e.target.value.trim();
    if (!q) return;
    api('/api/search?q=' + encodeURIComponent(q)).then(r => {
      const res = r.results || [];
      if (!res.length) return toast('没有找到');
      $('#cardlist').innerHTML = res.map(x =>
        `<div class="card ${x.kind}" data-path="${esc(x.path)}">
           <h4>${esc(x.title)}</h4><p>${esc(x.snippet || '')}</p>
           <div class="meta"><span class="kind ${x.kind}">${esc(x.kindLabel)}</span></div>
         </div>`).join('');
      STATE.filterKind = null; STATE.filterTag = null;
      renderSidebar();
      show('cards');
    });
  });

  $('#filter').addEventListener('input', renderCards);
  $('#sortBy').addEventListener('change', renderCards);
  $('#graphReset').onclick = () => drawGraph();
  $('#strollBtn').onclick = renderStroll;
  $('#digestBtn').onclick = doDigest;
  $('#previewBtn').onclick = previewFetch;
  $('#annoBtn').onclick = saveAnnotation;
  $('#cfgSave').onclick = saveModelConfig;
  $('#cfgTest').onclick = testModel;
  $('#ckSave').onclick = saveCookie;
  $('#ckDrop').onclick = dropCookie;
  $('#upBtn').onclick = uploadFile;
  const dz = $('#drop');
  if (dz) {
    $('#upFile').onchange = e => setPicked(e.target.files[0]);
    ['dragenter', 'dragover'].forEach(t => dz.addEventListener(t, e => {
      e.preventDefault(); dz.classList.add('over');
    }));
    ['dragleave', 'drop'].forEach(t => dz.addEventListener(t, e => {
      e.preventDefault(); dz.classList.remove('over');
    }));
    dz.addEventListener('drop', e => {
      const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) { setPicked(f); uploadFile(); }
    });
  }
  loadModelConfig();
  $('#backBtn').onclick = () => show('cards');
  $('#rdEdit').onclick = toggleCardEdit;
  $('#rdDel').onclick = dropCard;
  if ($('#rdRedo')) $('#rdRedo').onclick = redoDigest;
  if ($('#rdSync')) $('#rdSync').onclick = syncCards;
  if ($('#rdPractice')) $('#rdPractice').onclick = markPractice;
  if ($('#mcAdd')) $('#mcAdd').onclick = addManualCard;

  // 事件委托：卡片 / 列表项 / 双链 / 标签 / 类型
  document.addEventListener('click', async e => {
    const ts = e.target.closest('[data-t]');
    if (ts) {
      const v = document.getElementById('rdVideo');
      if (v) {
        v.currentTime = Number(ts.dataset.t) || 0;
        v.play().catch(() => {});
        v.scrollIntoView({ block: 'center', behavior: 'smooth' });
      } else {
        toast('这条没有本地视频，只能看到 ' + (ts.dataset.s || '') + ' 这个时间点');
      }
      return;
    }
    const fx = e.target.closest('.tfx');
    if (fx) {
      const box = fx.closest('.twx');
      const on = box.classList.toggle('spread');
      fx.textContent = on ? '退出放大' : '放大';
      return;
    }
    const wbtn = e.target.closest('#rdWidth button');
    if (wbtn) {
      localStorage.setItem('kb.width', wbtn.dataset.w);
      applyWidth();
      return;
    }
    const wiki = e.target.closest('[data-wiki]');
    if (wiki) {
      const name = wiki.dataset.wiki;
      const hit = STATE.cards.find(c => c.title === name || c.name === name);
      return hit ? openCard(hit.path) : toast('还没有这张卡片：' + name);
    }
    const card = e.target.closest('[data-path]');
    if (card) return openCard(card.dataset.path);

    const chip = e.target.closest('[data-kind]');
    if (chip) {
      STATE.filterKind = STATE.filterKind === chip.dataset.kind ? null : chip.dataset.kind;
      renderSidebar(); renderCards(); show('cards');
      return;
    }
    const tag = e.target.closest('[data-tag]');
    if (tag) {
      STATE.filterTag = STATE.filterTag === tag.dataset.tag ? null : tag.dataset.tag;
      renderSidebar(); renderCards(); show('cards');
      return;
    }
    const taskCard = e.target.closest('[data-task-card]');
    if (taskCard && taskCard.dataset.taskCard)
      return openCard(taskCard.dataset.taskCard, taskCard.dataset.taskTitle);
    if (e.target.closest('[data-clear]')) {
      STATE.filterKind = null; STATE.filterTag = null;
      renderSidebar(); renderCards();
      return;
    }
  });

  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault(); $('#search').focus();
    }
  });
  window.addEventListener('resize', () => {
    if ($('#view-graph').classList.contains('active')) drawGraph();
  });
}

async function loadModelConfig() {
  try {
    const c = await api('/api/config');
    if (!c || c.error) return;
    $('#cfgBase').value = c.base_url || '';
    $('#cfgModel').value = c.model || '';
    $('#cfgTemp').value = (c.temperature != null) ? c.temperature : '';
    $('#cfgKey').placeholder = c.hasKey ? ('已设置 ' + c.api_key + '（留空表示不改）')
                                       : '只存本机 config.json，不外传';
    $('#ckList').innerHTML = (c.cookies && c.cookies.length)
      ? c.cookies.map(k => `<div>${esc(k.site)} ｜ ${k.bytes} 字节 ｜ ${esc(k.updated)}</div>`).join('')
      : '（还没有 cookie）';
    $('#cfgStatus').textContent = c.hasKey
      ? '已配置模型 API，消化时会做 AI 提炼'
      : '未配置模型 API → 消化走规则抽取兜底（抓取和入库照常）';
  } catch (e) { /* 服务刚起来时忽略 */ }
}

async function saveModelConfig() {
  const body = { base_url: $('#cfgBase').value.trim(), model: $('#cfgModel').value.trim(),
                 temperature: $('#cfgTemp').value.trim() };
  if ($('#cfgKey').value.trim()) body.api_key = $('#cfgKey').value.trim();
  const r = await api('/api/config', { method: 'POST', body: JSON.stringify(body) });
  $('#cfgStatus').textContent = r.error ? ('保存失败：' + r.error) : '已保存到本机 config.json';
  $('#cfgKey').value = '';
  loadModelConfig();
}

async function testModel() {
  const st = $('#cfgStatus');
  st.textContent = '测试中…';
  const r = await api('/api/llm/test', { method: 'POST', body: '{}' });
  st.textContent = r.ok ? ('✅ ' + r.model + ' 通了（' + r.ms + 'ms）：' + r.echo)
                        : ('❌ ' + (r.error || '失败') + ' — ' + (r.hint || ''));
}

async function saveCookie() {
  const site = $('#ckSite').value, cookie = $('#ckVal').value.trim();
  if (!cookie) return toast('先粘贴 Cookie 内容');
  const r = await api('/api/cookie', { method: 'POST', body: JSON.stringify({ site, cookie }) });
  $('#ckStatus').textContent = r.ok ? ('已保存 ' + r.site + '（' + r.bytes + ' 字节）')
                                    : ('失败：' + r.error);
  $('#ckVal').value = '';
  loadModelConfig();
}

async function dropCookie() {
  const r = await api('/api/cookie/drop', { method: 'POST', body: JSON.stringify({ site: $('#ckSite').value }) });
  $('#ckStatus').textContent = r.ok ? '已删除本机该站点的 cookie' : ('失败：' + r.error);
  loadModelConfig();
}

let PICKED = null;

function setPicked(f) {
  PICKED = f || null;
  const nm = $('#upName'), bar = $('#upBar');
  nm.textContent = PICKED ? `${PICKED.name} · ${(PICKED.size / 1024).toFixed(0)} KB` : '';
  nm.classList.toggle('has', !!PICKED);
  $('#upBtn').disabled = !PICKED;
  $('#drop').classList.toggle('has', !!PICKED);
  bar.classList.add('hidden'); bar.value = 0;
}

function renderNewCards(r) {
  const el = $('#digestCards');
  if (!el) return;
  const cards = r.cards || [];
  if (!cards.length) {
    const why = (r.dropped || []).map(esc).join('<br>');
    el.innerHTML = `<p class="hint">这次没建卡${why ? '：<br>' + why : ''}</p>`
      + (r.note && !why ? `<p class="hint">${esc(r.note)}</p>` : '');
    return;
  }
  const order = ['概念', '连接', '问题', '原料'];
  const groups = {};
  cards.forEach(c => (groups[c.label] = groups[c.label] || []).push(c));
  const drop = (r.dropped || []).length
    ? `<p class="hint">拦下没建卡的（是互动数据 / 项目身份 / 接不上旧卡，留在原文里就够）：<br>`
      + r.dropped.map(esc).join('<br>') + '</p>'
    : '';
  el.innerHTML = `<p class="okline">本次自动长出 <b>${cards.length}</b> 张卡，点开就能看；同名卡只补不覆盖。</p>`
    + order.filter(k => groups[k]).map(k =>
        `<div class="ncrow"><span class="nclab">${k}</span>`
        + groups[k].map(c => `<button class="chip nc ${c.kind}${c.state === 'exists' ? ' dim' : ''}" `
            + `data-path="${esc(c.path)}" title="${esc(c.path)}">${esc(c.name)}`
            + (c.state === 'appended' ? '<i>补</i>' : c.state === 'exists' ? '<i>已有</i>' : '')
            + `</button>`).join('') + '</div>').join('') + drop;
}

function b64From(file) {
  return new Promise((res, rej) => {
    const fr = new FileReader();
    fr.onload = () => res(String(fr.result).split(',').pop());
    fr.onerror = rej;
    fr.readAsDataURL(file);
  });
}

async function uploadFile() {
  const f = PICKED || $('#upFile').files[0];
  if (!f) return toast('先选一个文件');
  const st = $('#upStatus'), bar = $('#upBar');
  const big = f.size > 8 * 1024 * 1024;
  st.textContent = (big ? '文件较大，读取中…' : '读取中…') + '（音视频转写可能要几分钟）';
  bar.classList.remove('hidden'); bar.value = 15;
  try {
    const b64 = await b64From(f);
    bar.value = 55;
    const r = await api('/api/import', {
      method: 'POST',
      body: JSON.stringify({ filename: f.name, base64: b64,
                             compile: $('#digestCompile').checked })
    });
    bar.value = 92;
    if (r.ok) {
      st.textContent = '完成：已写入 ' + r.clip;
      $('#digestOut').innerHTML = renderMD(r.digest || '');
      renderNewCards(r);
      $('#digestResult').classList.remove('hidden');
      setPicked(null);
      await loadAll();
      toast('已入库');
    } else {
      st.textContent = '失败：' + (r.error || '') + ' ' + (r.hint || '');
      bar.classList.add('hidden');
    }
  } catch (e) { st.textContent = '失败：' + e; bar.classList.add('hidden'); }
}

/* ------------------------------------------------------------ 启动 */
bind();
applyWidth();
loadAll().catch(e => toast('载入失败：' + e.message));
