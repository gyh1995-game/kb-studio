/* ==========================================================================
   KB Atelier · 正式站前端 v2
   零构建、零外部依赖、离线可用；数据全部来自本机 server 的 /api/*。
   字段名以 server.py 为准：列表项 path/kind/kindLabel/status/platform/
   author/source/captured_at/tags/links/summary/mtime(int 秒)/size，
   阅读项 frontmatter/body/raw/backlinks/linkNames，任务项 title/category/priority/due/card/done，
   完成记录在 /api/tasks 的 log:[{title,at}]。
   原生 select / confirm / alert 一律不用：下拉走 .sel 组件，确认走 #dlg。
   ========================================================================== */
"use strict";

/* ---------------------------------------------------------------- 小工具 */

const $ = s => document.querySelector(s);
const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const cssv = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const REDUCE = matchMedia("(prefers-reduced-motion: reduce)").matches;

/* 日期按本机时区算：server 写日志用的也是本机 time.strftime，用 UTC 会错一天 */
function dstr(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
const today = () => dstr(new Date());
/* /api/cards 的 mtime 是 int 秒，不是字符串 */
const ymd = sec => {
  const n = Number(sec);
  return n ? dstr(new Date(n * 1000)) : "";
};
const dayOf = at => String(at || "").slice(0, 10);
const hmOf = at => String(at || "").slice(11, 16);
function dur(sec) {
  sec = Math.max(0, Math.round(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60);
  if (h) return h + " 小时 " + m + " 分";
  if (m) return m + " 分";
  return sec + " 秒";
}
function clock(sec) {
  sec = Math.max(0, Math.round(sec || 0));
  const m = Math.floor(sec / 60), s = sec % 60;
  return m ? m + " 分 " + s + " 秒" : s + " 秒";
}

const store = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) { } }
};

let toastTimer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("on");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("on"), 3200);
}

/* 后端所有错误都在返回体里（{"ok":false,"error":...}），不吞、不换成套话 */
async function api(path, opts) {
  let r;
  try {
    r = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, opts));
  } catch (e) {
    throw new Error("连不上本机服务：" + e.message + "（KB-Studio 的 server.py 起来了吗）");
  }
  const text = await r.text();
  let data;
  try { data = JSON.parse(text); }
  catch (e) { throw new Error(path + " 返回的不是 JSON（HTTP " + r.status + "）：" + text.slice(0, 120)); }
  if (data && data.error && r.status >= 400) throw new Error(path + "：" + data.error);
  return data;
}

/* ---------------------------------------------------------------- 站内确认框
   替代 window.confirm / alert：样式和页面同一套，正文可以放列表和说明。
   返回 "yes" / "alt" / "no"，第三个按钮按需插入（删原料那种「一并处理卡片」才出现）。 */

let dlgResolve = null;
function ask(opts) {
  const o = typeof opts === "string" ? { body: opts } : (opts || {});
  $("#dlgTitle").textContent = o.title || "请确认";
  $("#dlgBody").innerHTML = o.body || "";
  const dock = $("#dlg").querySelector(".dock");
  const old = $("#dlgAlt");
  if (old) old.remove();
  if (o.altText) {
    const b = document.createElement("button");
    b.className = "act";
    b.id = "dlgAlt";
    b.textContent = o.altText;
    b.onclick = () => closeDlg("alt");
    dock.insertBefore(b, $("#dlgYes"));
  }
  $("#dlgYes").textContent = o.okText || "确定";
  /* cancelText: null → 纯告知框，不该出现「取消」这个假选项 */
  $("#dlgNo").hidden = o.cancelText === null;
  $("#dlgNo").textContent = o.cancelText || "取消";
  $("#dlgYes").classList.toggle("act--danger", !!o.danger);
  $("#dlg").hidden = false;
  requestAnimationFrame(() => $("#dlgYes").focus());
  return new Promise(res => { dlgResolve = res; });
}
function closeDlg(v) {
  $("#dlg").hidden = true;
  const f = dlgResolve;
  dlgResolve = null;
  if (f) f(v);
}

/* ---------------------------------------------------------------- 站内下拉
   原生 select 弹出的是操作系统那套控件，和页面不是一个模板。
   .sel 是站内组件，值同时写回旁边那个隐藏的原生 select 并派发 change，
   所以下面读 $('#ckSite').value 的老代码不用改。 */

function closeSels(except) {
  $$(".sel").forEach(s => {
    if (s === except) return;
    s.classList.remove("on");
    const b = s.querySelector(".sel-btn");
    if (b) b.setAttribute("aria-expanded", "false");
  });
}
function selSet(sel, v, label, fire) {
  sel.dataset.value = v;
  sel.querySelector(".sel-cur").textContent = label;
  $$(".sel-pop button", sel).forEach(b => b.classList.toggle("on", b.dataset.v === v));
  if (fire === false) return;
  const nat = $("#" + sel.id.replace(/Sel$/, ""));
  if (nat) {
    nat.value = v;
    nat.dispatchEvent(new Event("change", { bubbles: true }));
  }
}
function initSel(sel) {
  const btn = sel.querySelector(".sel-btn");
  btn.addEventListener("click", e => {
    e.stopPropagation();
    const on = !sel.classList.contains("on");
    closeSels(sel);
    sel.classList.toggle("on", on);
    btn.setAttribute("aria-expanded", String(on));
  });
  sel.addEventListener("click", e => {
    const o = e.target.closest(".sel-pop button");
    if (!o) return;
    e.stopPropagation();
    selSet(sel, o.dataset.v, o.textContent.trim());
    sel.classList.remove("on");
    btn.setAttribute("aria-expanded", "false");
  });
}
$$(".sel").forEach(initSel);
document.addEventListener("click", () => closeSels());

/* ---------------------------------------------------------------- 衣柜 · 皮肤 */

/* ip 字段 = 这套皮肤对应哪一整套 IP 衣服（/ip/<ip>/<姿态>.png）。
   切皮肤时 IP 必须同步换装，所以这个映射是唯一真相，别在别处硬写路径。 */
const SUITS = [
  { id: "snow", name: "雪山白", en: "SNOW / 01", src: "0.0–1.0s", ip: "home", sw: ["#FCFBFD", "#2F5D8A", "#A8703F", "#2A2732"] },
  { id: "dusty", name: "雾粉", en: "DUSTY / 02", src: "4.0–5.0s", ip: "home", sw: ["#FDF8F7", "#A9635E", "#8C5B44", "#33262A"] },
  { id: "slate", name: "石板蓝", en: "SLATE / 03", src: "3.0–4.0s", ip: "home", sw: ["#F8FAFD", "#4A5D78", "#9A6B45", "#24262F"] },
  { id: "rose", name: "蔷薇红", en: "WINE ROSE / 04", src: "7.0–8.0s", ip: "home", sw: ["#FEFCFC", "#A63A4B", "#8E5A3C", "#2C2226"] },
  { id: "taupe", name: "驼米", en: "CAMEL / 05", src: "10.0–11.0s", ip: "home", sw: ["#FDFAF5", "#8A5A38", "#5E4632", "#2E2620"] },
  { id: "denim", name: "牛仔蓝", en: "DENIM / 06", src: "9.0–10.0s", ip: "home", sw: ["#FBFCFE", "#1D6899", "#A8703F", "#1E232C"] },
  { id: "night", name: "夜航", en: "NIGHT / 07", src: "11.0–12.0s", ip: "home", sw: ["#1B1D26", "#8FB4D6", "#C89A6E", "#F0F1F5"] },
  { id: "ink", name: "墨黑", en: "INK / 08", src: "5.0–6.0s", ip: "home", sw: ["#101014", "#D3AFAE", "#B0794F", "#EDECEF"] },
  { id: "shanghai", name: "上海·天蓝", en: "SHANGHAI / 09", src: "地图 4.5–6.0s", ip: "shanghai", sw: ["#F4FAFD", "#1E9BCF", "#E0A9A2", "#22333D"] },
  { id: "london", name: "伦敦·橄榄棕", en: "LONDON / 10", src: "地图 6.25–8.0s", ip: "london", sw: ["#F6F2E9", "#6E6035", "#B4783C", "#2A251C"] },
  { id: "newyork", name: "纽约·黑白蓝", en: "NEW YORK / 11", src: "地图 8.25–10.0s", ip: "newyork", sw: ["#F7FAFC", "#2F6FA8", "#8C7A6B", "#1D2027"] },
  { id: "tokyo", name: "东京·驼与酒红", en: "TOKYO / 12", src: "地图 10.25–11.75s", ip: "tokyo", sw: ["#FBF7F0", "#8E2B3F", "#B98A55", "#2B2422"] }
];

let suitId = (() => {
  const fromUrl = (location.search.match(/suit=([a-z]+)/) || [])[1];
  const s = fromUrl || store.get("kb-suit") || "snow";
  return SUITS.some(x => x.id === s) ? s : "snow";
})();

function paintHangers() {
  $("#hangers").innerHTML = SUITS.map(s =>
    `<button class="hanger" data-suit="${s.id}" role="checkbox" aria-checked="${s.id === suitId}">`
    + `<span class="strip">${s.sw.map(c => `<i style="background:${c}"></i>`).join("")}</span>`
    + `<span><b>${esc(s.name)}</b><span class="en">${esc(s.en)} · ${esc(s.src)}</span></span>`
    + `<span class="wear">${s.id === suitId ? "穿着中" : "换上"}</span></button>`).join("");
}

function applySuit(id, animate) {
  const s = SUITS.filter(x => x.id === id)[0] || SUITS[0];
  const commit = () => {
    document.documentElement.setAttribute("data-suit", s.id);
    $("#suitName").textContent = s.name;
    $("#stageTag").textContent = "IP · " + s.name;
    store.set("kb-suit", s.id);
    suitId = s.id;
    paintHangers();
    mascot.wear(s.ip); mascot.dress(s);
    companion.wear(s.ip);
    graph.recolor();
    redrawChrome();
  };
  if (!animate || REDUCE) { commit(); return; }
  const cf = $("#changefit");
  cf.classList.remove("go");
  void cf.offsetWidth;
  cf.classList.add("go");
  setTimeout(commit, 450);
  setTimeout(() => cf.classList.remove("go"), 1000);
}

function toggleWardrobe(on) {
  $("#wardrobe").classList.toggle("on", on);
  $("#scrim").classList.toggle("on", on && !paletteOpen);
}

/* 换皮肤之后重画一次画布，别让她停在旧配色上 */
function redrawChrome() {
  if (curView === "graph") graph.paint();
}

/* ---------------------------------------------------------------- IP 小人 */

const POSES = ["idle", "peace", "celebrate", "eager", "reading", "think", "sleepy"];
const SETS = {};

/* 一套衣服 7 张图，到一张通知一次，首屏不等整套下齐。
   舞台和小助手会先后要同一套，所以等待队列必须有——不然第二个来的人
   只拿到当时已经下好的那几张，剩下的永远不告诉他。 */
function loadSet(name, onFrame) {
  let set = SETS[name];
  if (!set) {
    set = SETS[name] = { frames: {}, wait: [] };
    POSES.forEach(p => {
      const url = `/ip/${name}/${p}.png`, im = new Image();
      im.onload = () => { set.frames[p] = url; set.wait.forEach(cb => cb(p, url)); };
      im.src = url;
    });
  }
  set.wait.push(onFrame);
  /* 已经下好的那几张也要等这一轮赋值结束再回调：
     wear() 里 this.frames = loadSet(...)，同步回调读到的还是上一套衣服 */
  POSES.forEach(p => {
    if (set.frames[p]) Promise.resolve().then(() => onFrame(p, set.frames[p]));
  });
  return set.frames;
}

function motion(el, name) {
  if (!el) return;
  POSES.forEach(p => el.classList.remove("a-" + p));
  if (name) el.classList.add("a-" + name);
}

const mascot = {
  root: $("#ip"), pose: "idle", pupil: { x: 0, y: 0 }, t: 0, frames: {},
  init() {
    const self = this;
    this.root.innerHTML = [
      '<g id="ipFig">',
      '<path d="M100 6a7 7 0 1 1 7 7v7" stroke="var(--wood)" stroke-width="3" fill="none" stroke-linecap="round" opacity=".8"/>',
      '<path d="M60 40q40-22 80 0" stroke="var(--wood)" stroke-width="2.6" fill="none" stroke-linecap="round" opacity=".55"/>',
      '<path d="M100 30c-24 0-39 16-39 39 0 12 3 20 6 26l6-8c-2-14 2-24 8-30h26c6 6 10 16 8 30l6 8c3-6 6-14 6-26 0-23-15-39-39-39z" fill="var(--ink)"/>',
      '<g id="ipCoat">',
      '<path d="M64 116q36-16 72 0l10 96q-46 12-92 0z" fill="var(--accent)"/>',
      '<path d="M64 116q36-16 72 0l-4 16q-32 12-64 0z" fill="var(--wood)"/>',
      '<path d="M62 120q-14 8-18 34l-4 26 16 4 8-30z" fill="var(--accent)" opacity=".85"/>',
      '<path d="M138 120q14 8 18 34l4 26-16 4-8-30z" fill="var(--accent)" opacity=".85"/>',
      '<circle cx="47" cy="188" r="8" fill="var(--skin)"/>',
      '<circle cx="153" cy="188" r="8" fill="var(--skin)"/>',
      '<path d="M100 132v66" stroke="var(--bg-page)" stroke-width="1.6" opacity=".35"/>',
      "</g>",
      '<rect x="80" y="208" width="13" height="34" rx="6" fill="var(--ink)" opacity=".82"/>',
      '<rect x="107" y="208" width="13" height="34" rx="6" fill="var(--ink)" opacity=".82"/>',
      '<rect x="74" y="238" width="22" height="10" rx="5" fill="var(--wood)"/>',
      '<rect x="104" y="238" width="22" height="10" rx="5" fill="var(--wood)"/>',
      '<ellipse cx="100" cy="74" rx="27" ry="29" fill="var(--skin)"/>',
      '<path d="M73 66q6-22 27-22t27 22q-8-10-27-10t-27 10z" fill="var(--ink)"/>',
      '<g id="ipEyes">',
      '<ellipse cx="89" cy="76" rx="5.6" ry="6.6" fill="#fff" opacity=".92"/>',
      '<ellipse cx="111" cy="76" rx="5.6" ry="6.6" fill="#fff" opacity=".92"/>',
      '<circle id="ipP1" cx="89" cy="77" r="3.1" fill="var(--ink)"/>',
      '<circle id="ipP2" cx="111" cy="77" r="3.1" fill="var(--ink)"/>',
      '<circle cx="90.6" cy="75.4" r="1" fill="#fff"/>',
      '<circle cx="112.6" cy="75.4" r="1" fill="#fff"/>',
      "</g>",
      '<circle cx="79" cy="86" r="5" fill="var(--blush)" opacity=".55"/>',
      '<circle cx="121" cy="86" r="5" fill="var(--blush)" opacity=".55"/>',
      '<path id="ipMouth" d="M94 90q6 5 12 0" stroke="var(--ink)" stroke-width="2.2" fill="none" stroke-linecap="round"/>',
      "</g>"
    ].join("");
    this.fig = $("#ipFig");
    this.coat = $("#ipCoat");
    this.eyes = $("#ipEyes");
    this.pupils = [$("#ipP1"), $("#ipP2")];
    this.mouth = $("#ipMouth");
    addEventListener("mousemove", e => {
      const r = self.root.getBoundingClientRect();
      self.pupil.x = Math.max(-1, Math.min(1, (e.clientX - (r.left + r.width / 2)) / innerWidth * 6));
      self.pupil.y = Math.max(-1, Math.min(1, (e.clientY - (r.top + r.height / 2)) / innerHeight * 6));
      if (self.layer) {
        self.layer.style.transform = `translateX(-50%) translate(${(self.pupil.x * 7).toFixed(1)}px,${(self.pupil.y * 4).toFixed(1)}px) rotate(${(self.pupil.x * 1.2).toFixed(2)}deg)`;
      }
    }, { passive: true });
    this.layer = document.createElement("img");
    this.layer.id = "ipImg";
    this.layer.alt = "知识库 IP 形象";
    $(".stage").appendChild(this.layer);
    $(".stage").onclick = () => { act("peace", "在的，我在看你读到哪了", 1600); celebrate(.25); };
    (function loop() { self.frame(); requestAnimationFrame(loop); })();
    setInterval(() => this.blink(), 3800);
  },
  /* 有一张 idle 能加载上，就把 SVG 占位整块换掉。
     回调里只能用传进来的那张 p 判断：this.frames 在回调到达前可能已经被下一套换走了。 */
  wear(name) {
    const self = this;
    this.outfit = name;
    this.layer.dataset.outfit = name;
    this.frames = loadSet(name, p => {
      if (!self.useImg && p === "idle") { self.useImg = true; self.root.style.display = "none"; }
      self.show(self.last || "idle");
    });
  },
  /* 这套还没出全的姿态回落到 home，不能让她在换装瞬间凭空消失 */
  srcOf(pose) { return this.frames[pose] || (SETS.home && SETS.home.frames[pose]); },
  show(pose) {
    this.last = pose;
    const url = this.srcOf(pose);
    if (this.useImg && url) this.layer.src = url;
  },
  frame() {
    this.t += .016;
    const base = [89, 111];
    for (let i = 0; i < this.pupils.length; i++) {
      this.pupils[i].setAttribute("cx", base[i] + this.pupil.x * 3);
      this.pupils[i].setAttribute("cy", 77 + this.pupil.y * 2.1);
    }
    if (this.fig) this.fig.setAttribute("transform", `translate(0 ${(Math.sin(this.t * 1.5) * 2.2).toFixed(2)})`);
    const s = this._blink ? .12 : 1;
    if (this.eyes) this.eyes.setAttribute("transform", `translate(0 ${(76 * (1 - s)).toFixed(2)}) scale(1 ${s})`);
  },
  blink() {
    if (REDUCE) return;
    this._blink = 1;
    setTimeout(() => { this._blink = 0; }, 120);
  },
  dress(s) {
    const m = { night: "M93 90h14", ink: "M93 90h14", rose: "M93 88q7 8 14 0", dusty: "M94 90q6 5 12 0", taupe: "M94 89q6 6 12 0" };
    if (this.mouth) this.mouth.setAttribute("d", m[s.id] || "M94 90q6 5 12 0");
    if (this.coat && !REDUCE) {
      this.coat.animate(
        [{ transform: "scaleX(.86) translateY(6px)", opacity: .35 }, { transform: "none", opacity: 1 }],
        { duration: 620, easing: "cubic-bezier(.16,1,.3,1)" });
    }
  },
  label(name) {
    const txt = {
      idle: "● 视线跟随", celebrate: "● 刚放完彩带", think: "● 在想那条链接",
      reading: "● 陪你读这张", eager: "● 她在催你复习", peace: "● 比了个耶", sleepy: "● 打瞌睡了"
    };
    $("#ipMood").textContent = txt[name] || txt.idle;
  }
};

/* 一次动作两个她都要动：悬浮小助手逐帧演（她跟着滚，一定看得见），
   首页舞台同步换姿态（大图在那儿，不能对着按钮发呆）。 */
function act(name, line, ms) {
  ms = ms || 3200;
  mascot.label(name);
  mascot.show(name);
  motion(mascot.layer, name);
  clearTimeout(mascot._mt);
  mascot._mt = setTimeout(() => {
    mascot.label("idle"); mascot.show("idle"); motion(mascot.layer, "");
  }, ms + 400);
  if (companion.visible()) companion.act(name, line, ms);
}

/* ---------------------------------------------------------------- 常驻小助手 */

const companion = {
  el: $("#pal"), img: $("#palImg"), sayEl: $("#palSay"), hint: $("#palHint"), badgeEl: $("#palBadge"),
  frames: {}, pose: "", moved: 0,
  init() {
    const self = this;
    const saved = store.get("kb-pal");
    if (saved) {
      try {
        const p = JSON.parse(saved);
        this.el.style.left = p.l + "px";
        this.el.style.top = p.t + "px";
        this.el.style.right = "auto";
        this.moved = 1;
      } catch (e) { }
    }
    let drag = null;
    this.el.addEventListener("pointerdown", e => {
      drag = { x: e.clientX, y: e.clientY, l: self.el.offsetLeft, t: self.el.offsetTop, moved: 0 };
      try { self.el.setPointerCapture(e.pointerId); } catch (err) { }
      self.el.classList.add("drag");
    });
    this.el.addEventListener("pointermove", e => {
      if (!drag) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = 1;
      if (!drag.moved) return;
      const l = Math.max(6, Math.min(innerWidth - self.el.offsetWidth - 6, drag.l + dx));
      const t = Math.max(58, Math.min(innerHeight - self.el.offsetHeight - 34, drag.t + dy));
      self.el.style.left = l + "px"; self.el.style.top = t + "px"; self.el.style.right = "auto";
    });
    this.el.addEventListener("pointerup", () => {
      self.el.classList.remove("drag");
      if (drag && !drag.moved) self.today();
      else { self.moved = 1; store.set("kb-pal", JSON.stringify({ l: self.el.offsetLeft, t: self.el.offsetTop })); }
      drag = null;
    });
    addEventListener("resize", () => this.clamp());
  },
  /* 点她一下不能没反应：当天的进度直接报出来，激励要能被问出来 */
  today() {
    const p = todayProgress();
    this.act("peace", `今天读了 ${p.n} 张、完成 ${p.t} 条复习。`, 3600);
  },
  wear(name) {
    const self = this;
    this.outfit = name;
    this.el.dataset.outfit = name;
    this.frames = loadSet(name, () => {
      self.set(self.pose || "idle");
      self.ctx(curView);
    });
  },
  /* 存过的位置可能落在窗口外（浏览器面板宽度会变），每次上屏前夹回来 */
  clamp() {
    if (!this.moved || !this.el.classList.contains("on")) return;
    const w = this.el.offsetWidth, h = this.el.offsetHeight;
    this.el.style.left = Math.max(6, Math.min(innerWidth - w - 6, this.el.offsetLeft)) + "px";
    this.el.style.top = Math.max(58, Math.min(innerHeight - h - 34, this.el.offsetTop)) + "px";
  },
  set(n) {
    const url = this.frames[n] || (SETS.home && SETS.home.frames[n]);
    if (url) { this.img.src = url; this.pose = n; }
  },
  act(name, line, ms) {
    ms = ms || 3200;
    this.set(name);
    motion(this.el, name);
    if (line) this.speak(line, ms);
    clearTimeout(this.timer);
    this.timer = setTimeout(() => { this.set("idle"); motion(this.el, ""); }, ms + 400);
  },
  speak(text, ms) {
    this.sayEl.textContent = text;
    this.sayEl.classList.add("on");
    clearTimeout(this._say);
    this._say = setTimeout(() => this.sayEl.classList.remove("on"), ms || 3200);
  },
  visible() { return this.el.classList.contains("on"); },
  /* 她全程在场：position:fixed 跟着滚。只在非首页显示的话，首页按钮一点就
     scrollIntoView，动作发生在屏幕外面，等于没做。 */
  ctx(view) {
    /* 首页舞台里已经有她的放大版 → 悬浮版退场，只把进度胶囊挪到舞台上。
       两个人同屏是重复，而且悬浮那个会压住下面的学习记录。 */
    const onStage = view === "dashboard";
    this.el.classList.toggle("on", !!this.frames.idle && !onStage);
    this.host(onStage ? $("#ipStage") : this.el);
    /* 阅读页正文右边空着一大片 → 坐到那儿，别压住右栏 */
    if (!this.moved) this.el.classList.toggle("beside", view === "read");
    this.clamp();
    this.hint.classList.toggle("on", this.visible());
    clearTimeout(this._hint);
    this._hint = setTimeout(() => $("#palHint").classList.remove("on"), 5200);
  },
  /* 胶囊跟着她走：换宿主而不是复制一份，进度只能有一个出口 */
  host(node) {
    if (node && this.badgeEl && this.badgeEl.parentNode !== node) node.appendChild(this.badgeEl);
    if (this.badgeEl) this.badgeEl.classList.toggle("on", !!this.badgeEl.textContent);
  },
  /* 当天进度挂在她身上：一眼就知道今天动了多少 */
  badge(text, title) {
    if (!this.badgeEl) return;
    this.badgeEl.textContent = text;
    this.badgeEl.classList.toggle("on", !!text);
    this.el.title = title || "";
  }
};

/* ---------------------------------------------------------------- 彩带 */

const confettiCv = $("#confetti"), confettiCx = confettiCv.getContext("2d");
let confettiParts = [];
function celebrate(power) {
  if (REDUCE) return;
  const src = companion.visible() ? companion.img : $("#ipImg");
  let ox = innerWidth / 2, oy = innerHeight * .62;
  if (src) {
    const r = src.getBoundingClientRect();
    if (r.width > 0) { ox = r.left + r.width / 2; oy = r.top + r.height * .34; }
  }
  const cols = ["--cf-1", "--cf-2", "--cf-3", "--cf-4", "--accent", "--wood"].map(cssv);
  const n = Math.round(70 * (power || 1));
  for (let i = 0; i < n; i++) {
    confettiParts.push({
      x: ox + (Math.random() - .5) * 120, y: oy,
      vx: (Math.random() - .5) * 11, vy: -Math.random() * 15 - 5,
      w: 5 + Math.random() * 9, h: 8 + Math.random() * 14,
      r: Math.random() * 6.28, vr: (Math.random() - .5) * .3,
      c: cols[i % cols.length], life: 1
    });
  }
  if (!confettiParts._run) { confettiParts._run = 1; requestAnimationFrame(confettiTick); }
}
function confettiTick() {
  confettiCx.clearRect(0, 0, confettiCv.width, confettiCv.height);
  confettiParts.forEach(p => {
    p.vy += .34; p.vx *= .992; p.x += p.vx; p.y += p.vy; p.r += p.vr; p.life -= .006;
    confettiCx.save();
    confettiCx.globalAlpha = Math.max(0, p.life);
    confettiCx.translate(p.x, p.y); confettiCx.rotate(p.r); confettiCx.scale(1, Math.cos(p.r * .8));
    confettiCx.fillStyle = p.c; confettiCx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
    confettiCx.restore();
  });
  confettiParts = confettiParts.filter(p => p.life > 0 && p.y < innerHeight + 40);
  if (confettiParts.length) requestAnimationFrame(confettiTick);
  else confettiParts._run = 0;
}
function confettiSize() { confettiCv.width = innerWidth; confettiCv.height = innerHeight; }

/* ---------------------------------------------------------------- 状态 */

/* server.py 的 STATUSES 就这五个值。库里存在历史写法（已消化 / 一段长文本），
   所以下面要能原样显示当前值，不能假装它一定是五个之一。
   原料同样要复习：raw/ 和 wiki/ 都能改状态（set_status 两边都放行）。 */
const STATUSES = ["未读", "已读", "待吃透", "待实践", "已内化"];
const STATUS_HINT = {
  "未读": "还没打开过",
  "已读": "读完了，不再排复习",
  "待吃透": "读过了，还没变成自己的话",
  "待实践": "打算真的去用",
  "已内化": "能脱稿复述、能往外扩展"
};

let menuFor = null;
function openStatusMenu(anchor, path) {
  const c = cardOf(path);
  const menu = $("#statusMenu");
  if (!/^(wiki|raw)\//.test(path)) {
    menu.innerHTML = `<div class="sm-t">这一层不标状态</div>
      <div class="sm-note">系统页与索引不参与复习流转，只有原料（raw/）和卡片（wiki/）有状态。</div>`;
  } else {
    const cur = (c.status || "").trim();
    menu.innerHTML = `<div class="sm-t">改为状态 · 当前「${esc(cur || "未填写")}」</div>`
      + STATUSES.map(s => `<button data-status-for="${esc(path)}" data-set-status="${esc(s)}"${s === cur ? ' class="on"' : ""}>${esc(s)}
          <span class="tick">${esc(STATUS_HINT[s] || "")}</span></button>`).join("")
      + `<div class="sm-note">状态记录这一份在你这里走到哪一步，保存后直接写回文件的 status 字段。</div>`;
  }
  menuFor = path;
  menu.hidden = false;
  const r = anchor.getBoundingClientRect();
  menu.style.left = Math.max(10, Math.min(innerWidth - 232, r.left - 4)) + "px";
  menu.style.top = (r.bottom + 9 + menu.offsetHeight > innerHeight ? Math.max(10, r.top - menu.offsetHeight - 9) : r.bottom + 9) + "px";
}
function closeStatusMenu() { $("#statusMenu").hidden = true; menuFor = null; }

async function setStatus(path, want) {
  const body = { path };
  if (want) body.status = want;
  let r;
  try { r = await api("/api/status", { method: "POST", body: JSON.stringify(body) }); }
  catch (e) { return toast(e.message); }
  if (!r.ok) return toast("未改成：" + (r.error || "未知原因"));
  toast("状态已改为「" + r.status + "」");
  closeStatusMenu();
  /* 后端写的是文件，本地缓存必须一起跟上：
     以前只重画了阅读页顶部那一行，链接列表里的行还显示旧值，看着就像「点了没生效」 */
  await loadAll(true);
  if (curView === "read" && STATE.currentCard) {
    paintStatusRow(r.status);
    renderLinksPanels(STATE.lastCardData || {}, cardOf(STATE.currentCard));
  }
  return true;
}

/* ---------------------------------------------------------------- 本机阅读记录
   库里没有「读过几篇、花了多久」这种字段，也不该为这个往笔记里写日志——
   阅读停留只存本机 localStorage，页面上把这一条口径写清楚。 */

const RLOG = "kb.readlog.v1";
function readLog() {
  try { return JSON.parse(store.get(RLOG) || "{}") || {}; } catch (e) { return {}; }
}
function writeLog(o) { store.set(RLOG, JSON.stringify(o)); }

let dwell = { path: "", t0: 0, chars: 0 };
function dwellStart(path, chars) {
  dwellFlush();
  dwell = { path, t0: Date.now(), chars: chars || 0 };
}
function dwellFlush() {
  if (!dwell.path) return;
  const sec = (Date.now() - dwell.t0) / 1000;
  dwell.t0 = Date.now();
  if (sec < 1.5) return;
  const log = readLog();
  const e = log[dwell.path] || { sec: 0, n: 0, chars: 0, days: {}, last: "" };
  e.sec = (e.sec || 0) + sec;
  e.n = (e.n || 0) + 1;
  e.chars = dwell.chars || e.chars || 0;
  const d = today();
  e.days = e.days || {};
  e.days[d] = (e.days[d] || 0) + sec;
  e.last = d;
  log[dwell.path] = e;
  writeLog(log);
}
addEventListener("beforeunload", dwellFlush);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) dwellFlush(); else dwell.t0 = Date.now();
});

/* 当天进度：今天读了几个文件、停留多久、勾掉几条复习 */
function todayProgress() {
  const log = readLog();
  let n = 0, sec = 0;
  Object.keys(log).forEach(k => {
    const d = (log[k].days || {})[today()];
    if (d) { n++; sec += d; }
  });
  const t = STATE.doneLog.filter(x => dayOf(x.at) === today()).length;
  return { n, t, sec };
}

/* ---------------------------------------------------------------- 学习总结 */
/* 本机行为只有浏览器知道（阅读停留记在 localStorage，不写进笔记），
   所以证据由这里攒齐再交给后端：后端只负责读卡片正文和调模型。 */
function studyEvidence(scope) {
  dwellFlush();
  const log = readLog();
  const days = [];
  const now = new Date();
  for (let i = 0; i < (scope === "week" ? 7 : 1); i++) {
    const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() - i);
    days.push(dstr(d));
  }
  const read = Object.keys(log).map(p => ({
    path: p, sec: days.reduce((a, k) => a + ((log[p].days || {})[k] || 0), 0)
  })).filter(x => x.sec >= 2);
  const done = STATE.doneLog.filter(x => days.indexOf(dayOf(x.at)) >= 0).map(x => x.title);
  return { scope, read, done };
}

async function studySummary(scope) {
  const label = scope === "week" ? "本周" : "今日";
  const ev = studyEvidence(scope);
  if (!ev.read.length && !ev.done.length) {
    return ask({ title: "还没有可总结的记录",
      body: `<p>这一${scope === "week" ? "周" : "天"}本机还没有读过任何一张卡，也没勾掉过复习条目。</p>`
        + `<p class="tip">总结只依据本机真实记录 —— 没有记录我就不让她编。</p>`,
      okText: "去读两张", cancelText: null });
  }
  companion.badge("总结中", "她正在翻你这一" + (scope === "week" ? "周" : "天") + "读过什么");
  act("think", `我在回想这一${scope === "week" ? "周" : "天"}`, 6000);
  let r;
  try { r = await api("/api/summary", { method: "POST", body: JSON.stringify(ev) }); }
  catch (e) { r = { ok: false, error: e.message }; }
  paintBadge();
  if (!r.ok) { act("sleepy", "这份总结没写成", 3600);
    return ask({ title: "总结没生成", body: `<p>${esc(r.error || "未知原因")}</p>`
      + (r.hint ? `<p class="tip">${esc(r.hint)}</p>` : ""), okText: "知道了", cancelText: null }); }
  act("celebrate", "这份总结写好了", 3600);
  const md = r.markdown || "";
  ask({ title: `${label}学习总结 · 依据读过的 ${ev.read.length} 张卡、勾掉的 ${ev.done.length} 条复习`,
    body: `<div class="md summary">${renderMD(md)}</div>`,
    okText: "复制全文", altText: "关掉", cancelText: null })
    .then(v => { if (v === "yes") copyText(md, "总结已复制"); });
}

function copyText(txt, msg) {
  const done = () => toast(msg);
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(txt).then(done, () => fallbackCopy(txt, done));
  } else fallbackCopy(txt, done);
}
function fallbackCopy(txt, done) {
  const ta = document.createElement("textarea");
  ta.value = txt; ta.style.position = "fixed"; ta.style.opacity = "0";
  document.body.appendChild(ta); ta.select();
  try { document.execCommand("copy"); done(); } catch (e) { toast("这个浏览器不让网页写剪贴板，请手动选中复制"); }
  ta.remove();
}

/* ---------------------------------------------------------------- 数据 */

const KIND_CN = { concept: "概念", connection: "连接", question: "问题", raw: "原料", system: "系统", page: "页面" };
const KIND_ORDER = ["raw", "concept", "connection", "question"];
/* 等级（L0-L3）不作为筛选维也不作为徽标展示：它由处理深度决定，绝大多数卡停在 L1，
   摆在页面上只会让人以为它可控制、可比较。分级只在 system.md 的口径里生效。 */

const STATE = {
  cards: [], byPath: {}, byName: {}, graph: null, tasks: [], doneLog: [], stats: {},
  currentCard: "", currentRaw: "", rawPrefix: "",
  /* tag 永远是数组：单选模式下最多一个元素，切换多选只是放开这个上限 —— 一份状态两种形状迟早在各处判空判错 */
  f: { kind: "", q: "", sort: "mtime", status: "", plat: "", tag: [] },
  tagMulti: false,
};

const cardOf = p => STATE.byPath[p] ||
  { path: p, title: p, kind: "page", kindLabel: "页面", summary: "", tags: [], links: [], status: "", mtime: 0, size: 0 };

async function loadAll(silent) {
  /* 局部变量千万别叫 graph：会把下面那个全局图谱对象盖掉 */
  let cards, gdata, tasks, stats;
  try {
    [cards, gdata, tasks, stats] = await Promise.all([
      api("/api/cards"), api("/api/graph"), api("/api/tasks"), api("/api/stats")
    ]);
  } catch (e) {
    $("#heroSub").textContent = "读取失败：" + e.message;
    toast(e.message);
    return;
  }
  STATE.cards = cards.cards || [];
  STATE.byPath = {};
  STATE.byName = {};
  STATE.cards.forEach(c => {
    STATE.byPath[c.path] = c;
    if (c.title) STATE.byName[c.title] = c;
    if (c.name) STATE.byName[c.name] = STATE.byName[c.name] || c;
  });
  STATE.graph = gdata;
  STATE.tasks = tasks.tasks || [];
  STATE.doneLog = tasks.log || [];
  STATE.stats = stats || {};
  $("#liveTag").textContent = "obsidian · " + String(stats.vault || "").split(/[\\/]/).pop();
  if (!silent) graph.build();
  renderDashboard();
  renderBrowse();
  renderStroll();
  paintBadge();
  graph.recolor();
}

function paintBadge() {
  const p = todayProgress();
  const min = Math.round(p.sec / 60);
  companion.badge(`今日 ${min} 分 · ${p.n} 篇`,
    `今天在这套库里停留 ${dur(p.sec)}，打开过 ${p.n} 份内容，勾掉 ${p.t} 条复习`);
}

/* ---------------------------------------------------------------- 路由
   hash 里带上卡片路径（#/read/raw%2Fclips%2Fx.md），后退就能一层层退：
   仪表盘 → 卡片列表 → 某张卡，每一跳都是一条真实的历史记录。 */

const VIEWS = ["dashboard", "cards", "graph", "digest", "read"];
let curView = "dashboard", prevView = "dashboard";

const hashFor = (v, path) => "#/" + v + (v === "read" && path ? "/" + encodeURIComponent(path) : "");

function setView(v) {
  curView = v;
  if (v !== "read") prevView = v;
  $$(".view").forEach(el => el.classList.toggle("on", el.id === "view-" + v));
  $$("#nav button").forEach(b => b.setAttribute("aria-current", String(b.dataset.view === v)));
  graph.setActive(v === "graph");
  companion.ctx(v);
  closeBlocks();
  reveal();
}

/* 只在用户主动跳转时推历史记录；hashchange 回来的时候不再写 hash，否则后退键会原地打转 */
function go(v, path, replace) {
  if (VIEWS.indexOf(v) < 0) v = "dashboard";
  if (v === "read" && !path && !STATE.currentCard) {
    const first = STATE.cards.find(c => c.kind === "concept") || STATE.cards[0];
    if (first) { openCard(first.path); return; }
    v = "cards";
  }
  const h = hashFor(v, v === "read" ? (path || STATE.currentCard) : "");
  if (replace) history.replaceState(null, "", h);
  else if (location.hash !== h) location.hash = h;
  setView(v);
  if (v === "digest") act("think", "把链接丢进来，我先探测一下能不能读", 3000);
  scrollTo({ top: 0, behavior: REDUCE ? "auto" : "smooth" });
}

function applyRoute() {
  /* hash 里的路径是 encodeURIComponent 过的（/ 变成 %2F），所以先按 / 切段再解码那一段 */
  const seg = location.hash.replace(/^#\/?/, "").split("/");
  let v = seg[0] || "dashboard";
  if (VIEWS.indexOf(v) < 0) v = "dashboard";
  if (v === "read") {
    const p = seg.length > 1 ? decodeURIComponent(seg.slice(1).join("/")) : "";
    if (p && p !== STATE.currentCard) return openCard(p, "", true);
    if (!p && !STATE.currentCard) {
      const first = STATE.cards.find(c => c.kind === "concept") || STATE.cards[0];
      if (first) { history.replaceState(null, "", hashFor("read", first.path)); return openCard(first.path, "", true); }
    }
    return setView("read");
  }
  setView(v);
}
addEventListener("hashchange", applyRoute);

/* ---------------------------------------------------------------- 仪表盘 */

function tierKindColor(k) {
  return k === "raw" ? "var(--ink-3)" : k === "concept" ? "var(--accent)"
    : k === "connection" ? "var(--wood)" : k === "question" ? "var(--blush)" : "var(--ink-2)";
}

/* tiers 那一排给每层的定义和数量（全站唯一一处），这里只写准入判据 */
const LADDER_COPY = "准入判据：卡片收的是离开这条内容仍然成立的主张，一条主张一张卡；"
  + "概念之间接不出连接并不构成一个问题——「读完还不知道自己该怎么动」的那种才进问题层。";

function renderDashboard() {
  const C = STATE.cards, s = STATE.stats;
  const n = t => C.filter(c => c.kind === t).length;
  const counts = { raw: n("raw"), concept: n("concept"), connection: n("connection"), question: n("question") };
  const total = C.length || 1;
  const undone = STATE.tasks.filter(t => !t.done);
  const dueToday = undone.filter(t => !t.due || t.due <= today());

  $("#heroEyebrow").textContent = "本机工作台 · " + today();
  const hour = new Date().getHours();
  const greet = hour < 6 ? "凌晨" : hour < 11 ? "上午" : hour < 14 ? "午间" : hour < 18 ? "下午" : "晚间";
  /* 首屏只说今天有没有事：条数归「今天该复习」那一栏的 #dueCnt，分层计数归下面的 STACK。
     这里再写一个数字就是同一事实第二个出口。 */
  $("#heroTitle").innerHTML = dueToday.length
    ? `${greet}好，先去把到期的那几条复习勾掉。`
    : `${greet}好，今日没有到期条目。`;
  /* 准入判据只在 #ladderCopy 一处，这里不重述 */
  $("#heroSub").textContent =
    "工作循环：提交链接 → 生成原料 → 自动长概念 / 连接 / 问题三层卡 → 到期复习 → 状态改到「已内化」。";
  $("#ipLine").innerHTML = "点她一下，会报今天的进度。";
  $("#ladderCopy").textContent = LADDER_COPY;

  const TIERS = [
    { cls: "tier--src", kind: "raw", en: "SOURCE · 原料", cn: "原料", v: counts.raw,
      sub: "抓回的完整内容，只增不改" },
    { kind: "concept", en: "CONCEPT · 概念", cn: "概念", v: counts.concept, sub: "一条能独立成立的主张" },
    { kind: "connection", en: "LINK · 连接", cn: "连接", v: counts.connection, sub: "两个概念在什么情况下相互影响" },
    { kind: "question", en: "ISSUE · 问题", cn: "问题", v: counts.question, sub: "读完仍没有答案的疑问" },
    { cls: "tier--due", kind: "", en: "DUE · 账期", cn: "今日到期", v: dueToday.length, hi: 1,
      sub: dueToday.length ? "和下面「今天该复习」同一批条目"
        : `还没到期的排在后面（疑问 +3 天、原料 +7 天）` },
    /* 新库还没有内容时把 6 个系统页算成「卡片总数 6」，第一眼像 bug —— 只数上面那四层 */
    { kind: "", en: "TOTAL · 全库", cn: "卡片总数",
      v: counts.raw + counts.concept + counts.connection + counts.question,
      sub: "原料 + 概念 + 连接 + 问题" }
  ];
  $("#tiers").innerHTML = TIERS.map((t, i) =>
    `<div class="tier glass${t.cls ? " " + t.cls : ""}" data-i="${i}" data-kind="${t.kind}" data-goto="${t.cls === "tier--due" ? "review" : "cards"}"
        data-hi="${t.hi || 0}" style="--kind:${tierKindColor(t.kind)}">
      <span class="en">${esc(t.en)}</span>
      <div class="num" data-to="${t.v}">${t.v}</div>
      <div class="cn">${esc(t.cn)}</div>
      <div class="sub">${esc(t.sub)}</div>
      <div class="bar"><i style="--w:${Math.max(6, Math.min(100, t.v / total * 100)).toFixed(1)}%"></i></div></div>`).join("");

  /* 标签：数字不给（多值维度，数了会误导），点一下进卡片页按这个标签筛 */
  $("#tags").innerHTML = (s.topTags || []).slice(0, 10).map(([t]) =>
    `<button class="chip${STATE.f.tag.indexOf(t) >= 0 ? " on" : ""}" data-tag="${esc(t)}">${esc(t)}</button>`).join("")
    || '<span class="lbl">库里还没有可用标签</span>';

  renderRecent();
  renderDue();
  renderRecord();
  renderDone();

  /* 三块列表都钉死行数：不足就用空槽补齐。
     以前条数一多整块就往下长，首页每刷一次版式都在动 —— 他要的是「没有就空着」。 */
  /* 「长实」的门槛只写这一次，界面上那句判据由它生成 —— 数字改了说明还停在昨天，
     是他这轮明确要下调的：500 字会命中全部概念卡，先按 300。 */
  const thin = C.filter(c => c.kind === "concept"
    && (!(c.links || []).length || (c.chars || 0) < THIN_CHARS))
    .sort((a, b) => (a.chars || 0) - (b.chars || 0));
  $("#thinCnt").textContent = thin.length + " 张";
  $("#thinRule").textContent = "判据：正文净字数不足 " + THIN_CHARS + "，或一条双链都没接";
  $("#thin").innerHTML = slots(thin.slice(0, FIX.thin).map(rowHTML), FIX.thin,
    "概念卡都已长实") || '<div class="lbl">库里还没有概念卡</div>';

  const qcards = C.filter(c => c.kind === "question").sort((a, b) => b.mtime - a.mtime);
  $("#qCnt").textContent = qcards.length + " 张";
  $("#qlist").innerHTML = slots(qcards.slice(0, FIX.q).map(rowHTML), FIX.q,
    "还没有问题卡：消化时那条「我的疑问」就是它们的来源") || '<div class="lbl">库里还没有问题卡</div>';

  buildTicker();
  countUp();
}

/* 学习记录只放本机行为数据；库内分层计数归 STACK 那一排，两边不重复 */
function renderRecord() {
  let log = readLog();
  /* 库被清空或卡片被删之后，本机记录会指着不存在的路径继续计数 —— 先丢掉孤儿条目 */
  const alive = {};
  Object.keys(log).forEach(p => { if (STATE.byPath[p]) alive[p] = log[p]; });
  if (Object.keys(alive).length !== Object.keys(log).length) writeLog(alive);
  log = alive;
  const paths = Object.keys(log);
  const sec = paths.reduce((a, p) => a + (log[p].sec || 0), 0);
  const readChars = paths.reduce((a, p) => a + (log[p].chars || 0), 0);
  const p = todayProgress();
  const TILES = [
    /* 抬头不写「· 本机」：#recordNote 一句话把这一排的口径讲完，四个标签重复四遍没必要。
       今日时长和份数归她头上的胶囊，这里只放胶囊没有的那个数（今天勾掉几条）。 */
    { en: "TODAY", cn: "今天勾掉的复习", v: p.t, u: "条",
      sub: "在「今天该复习」里点完成的" },
    { en: "READ", cn: "打开过", v: paths.length, u: "份",
      sub: "在阅读页停留超过 1.5 秒才算一次" },
    { en: "TIME", cn: "累计停留", v: dur(sec), txt: 1, sub: "切走标签页就停表" },
    { en: "WORDS", cn: "读过的字数", v: readChars, u: "字", sub: "只计打开过的那几份" }
  ];
  $("#recGrid").innerHTML = TILES.map(t =>
    `<div class="rec"><span class="en">${esc(t.en)}</span>`
    + `<b>${esc(t.txt ? t.v : fmt(t.v))}</b>${t.u ? `<i>${t.u}</i>` : ""}`
    + `<span class="cn">${esc(t.cn)}</span><span class="sub">${esc(t.sub)}</span></div>`).join("");
  $("#recordNote").textContent =
    "这一排全部由本机浏览器记录：只在当前设备累积，不写进笔记文件，换设备会从零开始。"
    + "库里有多少页、每层多少张，看上面 STACK 那一排。";
}
const fmt = v => String(v).replace(/\B(?=(\d{3})+(?!\d))/g, ",");

/* 近期已完成：成就 + 手滑撤回，勾掉的条目从「今天该复习」移到这里 */
function renderDone() {
  const L = STATE.doneLog;
  $("#doneCnt").textContent = L.length + " 条";
  $("#donelist").innerHTML = L.length ? L.slice(0, 8).map(t => {
    const c = cardOf(t.title);
    const tk = STATE.tasks.find(x => x.title === t.title) || {};
    const card = tk.raw || tk.card || c.path || "";
    return `<div class="drow done"><div class="txt" ${card ? `data-path="${esc(card)}"` : ""}>
      <div class="dt">${esc(t.title)}</div>
      <div class="meta">${esc(t.at ? (dayOf(t.at) === today() ? "今天 " + hmOf(t.at) : t.at) : "更早")}</div></div>
      <button class="mini" data-done-task="${esc(t.title)}" data-undo="1" title="撤回这条完成标记，重新排回队列">撤回</button>
      </div>`;
  }).join("") : '<div class="lbl">还没有完成记录</div>';
}

function renderRecent() {
  const raws = STATE.cards.filter(c => c.kind === "raw").sort((a, b) => b.mtime - a.mtime);
  $("#recent").innerHTML = slots(raws.slice(0, FIX.recent).map(rowHTML), FIX.recent,
    "还没有原料：去「消化」提交一条链接");
  /* 原料共多少份只在 STACK 那一排给；这一列的标题只说排到哪天 */
  $("#recentCnt").textContent = raws.length ? "最新 " + ymd(raws[0].mtime) : "空";
}

/* 每块钉死的行数。空槽也渲染出来，块高就只跟行数有关、跟库里有多少东西无关。 */
const FIX = { recent: 5, due: 6, thin: 5, q: 5 };
/* 概念卡「长实」的字数门槛：低于它就算薄。 */
const THIN_CHARS = 300;
function slots(rows, n, emptyMsg) {
  const filled = rows.filter(Boolean);
  if (!filled.length) {
    return '<div class="lbl slot-empty">' + esc(emptyMsg || "这一栏还空着") + "</div>"
      + repeat(n - 1, '<div class="lbl slot-empty" aria-hidden="true">·</div>');
  }
  return filled.join("") + repeat(n - filled.length, '<div class="slot" aria-hidden="true"></div>');
}
const repeat = (n, s) => new Array(Math.max(0, n)).fill(s).join("");

/* 行里那颗状态是按钮，所以整行必须用 div：button 套 button 是非法 HTML，内层那颗点不动。
   形态 / 预估 / 来源三个字段只在抓取或生成时写入，界面一律读字段不重新估算 ——
   否则页面和 Obsidian 里看到的会开始各说各话。 */
function rowHTML(c) {
  return `<div class="row" data-path="${esc(c.path)}" data-kind="${esc(c.kind)}">
    <div class="rt">${esc(c.title)}</div>
    ${c.summary ? `<div class="rx">${esc(c.summary)}</div>` : ""}
    <div class="rf">
      <span class="kind-dot"></span><span>${KIND_CN[c.kind] || c.kindLabel || c.kind}</span>
      <button class="st" data-status-for="${esc(c.path)}" title="点击修改状态">${esc(c.status || "未填写")}</button>
      <span class="sp"></span>
      ${typeBits(c)}
      <span title="这张卡正文里指向别的双链条数">${(c.links || []).length} 出链</span>
      <span>${esc(ymd(c.mtime))}</span>
    </div></div>`;
}

/* 类型 · 预估时长 · 来源：三个字段在抓取或生成就写进 frontmatter，界面只读不猜 */
function typeBits(c) {
  const out = [];
  if (c.media_type) out.push(`<span title="内容形态（media_type）">${esc(c.media_type)}</span>`);
  if (c.est_min) out.push(`<span title="预估时间：按字数或视频实际时长折算（est_min）">约 ${c.est_min} 分钟</span>`);
  if (c.platform) out.push(`<span title="来源平台">${esc(c.platform)}</span>`);
  return out.join("");
}

function dueRow(t) {
  /* 「查看原文」开的永远是那份原料：条目本身只是一个问题，答案要回到原文那一段去找 */
  const card = t.raw || t.card || "";
  const bits = [t.media_type || "文本", "约 " + (t.est_min || 1) + " 分钟",
    t.platform || "本地"].join(" · ");
  return `<div class="drow" data-task-anchor="${esc(t.title)}">
      <div class="txt"><div class="dt">${esc(t.title)}</div>
        <div class="meta" title="到期 ${esc(t.due || "今天")}；类型 / 预估 / 来源在生成这条复习任务时就写进队列了">
          ${esc(bits)}${t.origin ? " · " + esc(String(t.origin).slice(0, 24)) : ""}</div></div>
      <div class="btns">
        <button class="mini" ${card ? `data-path="${esc(card)}" data-anchor="${esc(t.title)}"` : "disabled"}>查看原文</button>
        <button class="mini ok" data-done-task="${esc(t.title)}" title="标记完成并从本列移出">已完成</button>
      </div></div>`;
}

let duePage = 0;
function renderDue() {
  /* 勾完就从这一列消失，进右侧「近期已完成」——留在这儿划掉只会让列表越读越长。
     判定只看 due <= 今天，和首页 DUE 那一格同一个口径。 */
  const rows = STATE.tasks.filter(t => !t.done && (!t.due || t.due <= today()));
  const later = STATE.tasks.filter(t => !t.done && t.due && t.due > today()).length;
  const pages = Math.max(1, Math.ceil(rows.length / FIX.due));
  if (duePage >= pages) duePage = 0;
  $("#dueCnt").textContent = rows.length + " 条待做";
  $("#due").innerHTML = slots(rows.slice(duePage * FIX.due, duePage * FIX.due + FIX.due)
    .map(dueRow), FIX.due,
    later ? "今天没有到期条目，后面还有 " + later + " 条排在账期上"
      : "复习队列是空的：消化一条链接会自动长出「我的疑问」和一条「重读原料」并排进这里");
  const nav = $("#dueNav");
  if (nav) {
    nav.hidden = pages <= 1;
    nav.innerHTML = `<button class="mini" data-due-page="-1" ${duePage ? "" : "disabled"}>‹ 上一页</button>`
      + `<span class="lbl">${duePage + 1} / ${pages}</span>`
      + `<button class="mini" data-due-page="1" ${duePage + 1 < pages ? "" : "disabled"}>下一页 ›</button>`
      + `<span class="sp"></span><span class="lbl">一条 = 一个疑问（+3 天）或一份原料（+7 天）</span>`;
  }
}

function countUp() {
  $$(".tier .num").forEach(el => {
    const to = +el.dataset.to, t0 = performance.now(), dur2 = REDUCE ? 0 : 1000;
    (function step(t) {
      const p = dur2 ? Math.max(0, Math.min(1, (t - t0) / dur2)) : 1;
      el.textContent = Math.round(to * (1 - Math.pow(1 - p, 3)));
      if (p < 1) requestAnimationFrame(step);
    })(t0);
  });
}

let strollPick = [];
function renderStroll(reshuffle) {
  const pool = STATE.cards.filter(c => c.kind !== "system" && c.kind !== "page");
  /* 默认留住已经抽到的三张：只有点「随机漫步」才换一批，否则每次刷新数据这一栏都在跳 */
  if (reshuffle || strollPick.length !== 3 || !strollPick.every(c => STATE.byPath[c.path])) {
    strollPick = [];
    while (strollPick.length < 3 && pool.length) {
      const c = pool[Math.floor(Math.random() * pool.length)];
      if (strollPick.indexOf(c) < 0) strollPick.push(c);
    }
  }
  $("#stroll").innerHTML = strollPick.map(cardHTML).join("") || '<div class="lbl">还没有卡片</div>';
  tilt();
}

function cardHTML(c) {
  return `<div class="kcard" data-path="${esc(c.path)}" data-kind="${esc(c.kind)}">
    <div class="head"><span class="kind-dot"></span><span class="en">${KIND_CN[c.kind] || c.kind}</span>
      <span style="flex:1"></span>
      <button class="st" data-status-for="${esc(c.path)}">${esc(c.status || "未填写")}</button></div>
    <h4>${esc(c.title)}</h4>
    <p>${esc(c.summary || "（没有摘要）")}</p>
    <div class="foot"><span>${esc(ymd(c.mtime))}</span><span>·</span><span>${fmt(c.chars || 0)} 字</span>
      ${typeBits(c)}
      <span style="flex:1"></span><span title="这张卡正文里指向别的双链条数">${(c.links || []).length} 出链</span></div></div>`;
}

/* ---------------------------------------------------------------- 底部走马灯：可点 + 悬停浮出 */

function buildTicker() {
  /* 只走这台设备还没打开过的卡：发现入口，不承担再列一遍库里有什么 */
  const log = readLog();
  const picks = STATE.cards
    .filter(c => (c.kind === "concept" || c.kind === "question") && !log[c.path])
    .sort((a, b) => (a.summary || "").length - (b.summary || "").length)
    .slice(0, 16);
  const one = picks.map(c => `<span data-path="${esc(c.path)}"><b>${KIND_CN[c.kind]}</b> · ${esc(c.title.slice(0, 24))}</span>`).join("");
  $("#ticker").innerHTML = one + one;
  $("#ticker").parentElement.hidden = !picks.length;
}

let peekTimer;
function showPeek(el) {
  const c = STATE.byPath[el.dataset.path];
  if (!c) return;
  const p = $("#peek");
  p.innerHTML = `<div class="pk-h"><span class="kind-dot" style="--kind:${c.kind === "question" ? "var(--blush)" : "var(--accent)"}"></span>
      <b>${esc(c.title)}</b></div>
    <div class="pk-x">${esc(c.summary || "（还没有摘要）")}</div>
    <div class="pk-f"><span>${KIND_CN[c.kind] || c.kind}</span>
      <span>${esc(c.status || "未填写状态")}</span><span>${(c.links || []).length} 出链</span>
      <span>${esc(ymd(c.mtime))}</span></div>`;
  p.hidden = false;
  const r = el.getBoundingClientRect(), w = p.offsetWidth, h = p.offsetHeight;
  p.style.left = Math.max(10, Math.min(innerWidth - w - 10, r.left)) + "px";
  p.style.top = Math.max(10, r.top - h - 12) + "px";
  requestAnimationFrame(() => p.classList.add("on"));
  clearTimeout(peekTimer);
}
function hidePeek() {
  const p = $("#peek");
  p.classList.remove("on");
  clearTimeout(peekTimer);
  peekTimer = setTimeout(() => { p.hidden = true; }, 240);
}

/* ---------------------------------------------------------------- 卡片视图
   每一维的数字都按「其余筛选都生效」之后再数一遍（faceted counting）：
   选了「原料」再看 L 那一排才有意义，否则显示的是全库总数，点了像没反应。 */

function activeFilters() {
  const f = STATE.f, out = [];
  if (f.kind) out.push(["kind", f.kind, (KIND_CN[f.kind] || f.kind)]);
  if (f.q) out.push(["q", "", "关键词 · " + f.q]);
  if (f.status) out.push(["status", f.status, "状态 · " + f.status]);
  if (f.plat) out.push(["plat", f.plat, "平台 · " + f.plat]);
  /* 每个选中的标签一颗芯片：一颗只摘掉一个，不做「一键清空这一维」那种粗暴事 */
  f.tag.forEach(t => out.push(["tag", t, "标签 · " + t]));
  return out;
}

function renderFbar() {
  const bar = $("#fbar"), acts = activeFilters();
  bar.hidden = !acts.length;
  if (!acts.length) { bar.innerHTML = ""; return; }
  /* 命中数只写在 #browseCnt 一处（那是它唯一的写入方），这里只放能撤销的芯片 */
  bar.innerHTML = '<span class="lbl">当前筛选</span>'
    + acts.map(([type, val, label]) =>
      `<button class="fx" data-unfilter="${type}" data-val="${esc(val)}">${esc(label)}
        <svg viewBox="0 0 24 24"><use href="#i-close"/></svg></button>`).join("")
    + '<button class="clear" data-clearall="1">清除全部</button>';
}

/* 命中判定：match(c, skip) 里 skip 那一维不参与，剩下照常 */
const NO_VALUE = "未填写";
const statusOf = c => c.status || NO_VALUE;
function match(c, skip) {
  const f = STATE.f, q = f.q.toLowerCase();
  if (skip !== "kind" && f.kind && c.kind !== f.kind) return false;
  if (skip !== "status" && f.status && statusOf(c) !== f.status) return false;
  if (skip !== "plat" && f.plat && (c.platform || NO_VALUE) !== f.plat) return false;
  if (skip !== "tag" && f.tag.length) {
    const mine = (c.tags || []).map(t => String(t).toLowerCase());
    const want = f.tag.map(t => String(t).toLowerCase());
    // 多选默认「任一」：标签是多值维度，要交集的话点进去再筛比在这里 AND 更符合直觉
    const hit = STATE.tagMulti ? want.some(t => mine.includes(t)) : mine.includes(want[0]);
    if (!hit) return false;
  }
  if (skip !== "q" && q && !(c.title + " " + (c.summary || "") + " " + (c.tags || []).join(" ")
    + " " + (c.author || "") + " " + (c.platform || "")).toLowerCase().includes(q)) return false;
  return true;
}
const filtered = () => STATE.cards.filter(c => match(c, ""));

/* 筛选每一维单选；数字 = 这一维在全库的总数，不随其他筛选联动。
   标签是多值的，只给名字不给数字；命中多少由 #fbar 那行「N / 总数 页」说。 */
const countBy = (key, get) => {
  const m = {};
  STATE.cards.forEach(c => { (get ? get(c) : [c[key] || NO_VALUE]).forEach(v => { if (v) m[v] = (m[v] || 0) + 1; }); });
  return m;
};

function renderBrowse() {
  const f = STATE.f, C = STATE.cards;

  const kindCount = countBy("kind");
  $("#kindFilter").innerHTML = `<button class="chip${!f.kind ? " on" : ""}" data-k="">全部<i>${C.length}</i></button>`
    + KIND_ORDER.concat(["system", "page"]).map(k =>
      kindCount[k] ? `<button class="chip${f.kind === k ? " on" : ""}" data-k="${k}">${KIND_CN[k]}<i>${kindCount[k]}</i></button>` : "").join("");

  const stCount = countBy("status", c => [statusOf(c)]);
  $("#statusFilter").innerHTML = [`<button class="chip${!f.status ? " on" : ""}" data-s="">全部</button>`]
    .concat(STATUSES.map(k =>
      `<button class="chip${f.status === k ? " on" : ""}" data-s="${esc(k)}">${esc(k)}<i>${stCount[k] || 0}</i></button>`))
    .join("");

  const pl = countBy("platform");
  $("#platforms").innerHTML = `<button class="chip${!f.plat ? " on" : ""}" data-p="">全部</button>`
    + Object.keys(pl).sort((a, b) => pl[b] - pl[a])
      .map(p => `<button class="chip${f.plat === p ? " on" : ""}" data-p="${esc(p)}">${esc(p)}<i>${pl[p]}</i></button>`).join("");

  /* Obsidian 的标签不分大小写：老库里「AI工具」和「ai工具」会各占一格。
     按小写归并成一枚芯片，拼写取出现次数最多的那种。 */
  const tg = {};
  STATE.cards.forEach(c => (c.tags || []).forEach(t => {
    const k = String(t).toLowerCase();
    (tg[k] = tg[k] || { show: {}, n: 0 });
    tg[k].show[t] = (tg[k].show[t] || 0) + 1;
    tg[k].n++;
  }));
  const tagNames = Object.keys(tg).map(k =>
    Object.keys(tg[k].show).sort((a, b) => tg[k].show[b] - tg[k].show[a] || a.localeCompare(b))[0]);
  const kw = ($("#tagSearch").value || "").trim().toLowerCase();
  const keys = tagNames.sort((a, b) => a.localeCompare(b, "zh")).filter(k => !kw || k.toLowerCase().includes(kw));
  $("#allTags").innerHTML = `<button class="chip${!f.tag.length ? " on" : ""}" data-tag="">全部</button>`
    + (keys.length ? keys.map(k =>
      `<button class="chip${f.tag.indexOf(k) >= 0 ? " on" : ""}" data-tag="${esc(k)}">${esc(k)}</button>`).join("")
      : '<span class="lbl">库里还没有可用标签</span>');

  const list = filtered().sort((a, b) => {
    if (f.sort === "chars") return (b.chars || 0) - (a.chars || 0);
    if (f.sort === "links") return (b.links || []).length - (a.links || []).length;
    if (f.sort === "title") return a.title.localeCompare(b.title, "zh");
    if (f.sort === "status") return String(a.status || "zz").localeCompare(String(b.status || "zz"), "zh");
    return b.mtime - a.mtime;
  });
  $("#cardGrid").innerHTML = list.slice(0, 240).map(cardHTML).join("")
    || '<div class="lbl">没有符合条件的卡片，先清掉一个筛选</div>';
  $("#browseCnt").textContent = list.length + " / " + C.length + " 页";
  tilt();

  $$("#tags .chip").forEach(b => b.classList.toggle("on", f.tag.indexOf(b.dataset.tag) >= 0));
  const tm = $("#tagMode");
  if (tm) {
    /* 一颗状态芯片而不是一个链接：字面只留「多选」，亮着就是开着 */
    tm.classList.toggle("on", STATE.tagMulti);
    tm.textContent = "多选";
    tm.setAttribute("aria-pressed", String(STATE.tagMulti));
    tm.title = STATE.tagMulti ? "多选已开：命中任意一个选中标签的卡都算，点我改回单选"
      : "当前单选：一次只看一个标签，点我改成多选";
  }
  renderFbar();
}

/* ---------------------------------------------------------------- Markdown
   表格 / 嵌套列表 / 任务项 / 逐字稿时间戳 / 双链 / 正文图片都能渲染。
   每个块都记住它在源文件里的行号（data-ln / data-le）：高亮和批注要靠行号回源文件改字，
   而不是靠 DOM 位置 —— reflow 会重排换行，字符偏移会飘，行号区间不会。 */

const CAL_TYPE = { note: "理解", tip: "要实践", question: "存疑", warning: "警告" };

function renderMD(src) {
  const blocks = [];
  src = String(src || "").replace(/```(\w*)\n?([\s\S]*?)```/g, (m, lang, code) => {
    blocks.push(`<pre><code>${esc(code)}</code></pre>`);
    /* 占位符必须把吃掉行数补回来，不然下面所有 data-ln 会整体错位 */
    return `\u0000B${blocks.length - 1}\u0000` + "\n".repeat((m.match(/\n/g) || []).length);
  });
  src = esc(src);
  src = src.replace(/^###### (.*)$/gm, "<h6>$1</h6>").replace(/^##### (.*)$/gm, "<h5>$1</h5>")
    .replace(/^#### (.*)$/gm, "<h4>$1</h4>").replace(/^### (.*)$/gm, "<h3>$1</h3>")
    .replace(/^## (.*)$/gm, "<h2>$1</h2>").replace(/^# (.*)$/gm, "<h1>$1</h1>");
  src = src.replace(/^---+$/gm, "<hr>");
  src = src.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  src = src.replace(/(?<!\*)\*([^*\n]+)\*(?!\*)/g, "<em>$1</em>");
  src = src.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  /* Obsidian 的高亮语法：==文字== → 原生 mark。内容里常有 URL 的 =，所以用非贪婪而不是排除 = */
  src = src.replace(/==([^=\n][^\n]*?)==/g, '<mark>$1</mark>');

  const toSec = t => t.split(":").reduce((a, x) => a * 60 + (+x), 0);
  src = src.replace(/\[(\d{1,2}:\d{2}(?::\d{2})?)\s*[–—~]\s*(\d{1,2}:\d{2}(?::\d{2})?)\]/g,
    (m, a, b) => `<span class="ts" data-t="${toSec(a)}" data-s="${a}" title="这一段播到 ${b}">[${a} – ${b}]</span>`);
  src = src.replace(/\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]/g, (m, a, b, c) => {
    const sec = c ? (+a * 3600 + +b * 60 + +c) : (+a * 60 + +b);
    return `<span class="ts" data-t="${sec}" data-s="${m}">${m}</span>`;
  });
  /* 顺序不能换：图片必须先于链接匹配 */
  src = src.replace(/\[\[([^\]\|]+?)\]\]/g, (m, t) =>
    `<span class="wiki" data-wiki="${esc(t.trim())}">${esc(t.trim())}</span>`);
  src = src.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (m, alt, u) => {
    const p = /^assets\//.test(u) ? "/assets/" + u.slice(7)
      : (/^\.?\.?\//.test(u) ? u.replace(/^\.?\.?\//, "/") : u);
    return `<figure class="shot"><img src="${p}" alt="${alt || "图片"}" loading="lazy">`
      + (alt ? `<figcaption>${alt}</figcaption>` : "") + "</figure>";
  });
  /* 链接允许带括号（B站/抖音的 URL 里常有 (1) 这种尾巴），也不允许在空格处被截断 */
  src = src.replace(/\[([^\]]+)\]\(([^)\s]*(?:\([^)]*\)[^)\s]*)*)\)/g, (m, t, u) =>
    `<a href="${u}" target="_blank" rel="noopener">${t}</a>`);
  /* 裸 URL 自动成链：中文正文里 URL 前面常常是「：」「，」这类全角符号，
     只认空白的写法会让它留在文本里，而一长串 URL 没有断行点，手机上就会顶破容器 */
  src = src.replace(/(^|[\s(（【「『：:，,、])(https?:\/\/[^\s<>"'）)】」』,，、]+)/g, (m, pre, u) =>
    pre + `<a href="${u}" target="_blank" rel="noopener" class="bare">${u}</a>`);

  const lines = src.split("\n"), out = [];
  let para = [], stack = [];
  const cjkEnd = s => /[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]$/.test(s);
  const cjkStart = s => /^[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]/.test(s);
  const hardEnd = s => /[。！？；…”’」』）)】]$/.test(s.replace(/\s+$/, ""));
  const flushPara = () => {
    if (!para.length) return;
    /* 中文稿一行就是一段（从知乎 / 微信 / txt 粘过来都这样），拉丁文才把折行接回同一段 */
    const groups = [{ t: para[0].t, ln: para[0].ln, le: para[0].ln }];
    for (let k = 1; k < para.length; k++) {
      const prev = groups[groups.length - 1].t.replace(/\u0001$/, "");
      if (hardEnd(prev) || (cjkEnd(prev) && cjkStart(para[k].t))) groups.push({ ...para[k], le: para[k].ln });
      else { groups[groups.length - 1].t += " " + para[k].t; groups[groups.length - 1].le = para[k].ln; }
    }
    groups.forEach(g => out.push(`<p data-ln="${g.ln}" data-le="${g.le}">${g.t}</p>`));
    para = [];
  };
  const closeLists = () => { while (stack.length) out.push(stack.pop() === "ul" ? "</ul>" : "</ol>"); };
  const isTableLine = ln => /^\s*\|.*\|\s*$/.test(ln);
  const isSep = ln => /^\s*\|[\s:|-]+\|\s*$/.test(ln);
  const cells = ln => ln.trim().replace(/^\||\|$/g, "").split("|").map(s => s.trim());

  for (let i = 0; i < lines.length; i++) {
    const ln = lines[i];

    if (isTableLine(ln) && lines[i + 1] && isSep(lines[i + 1])) {
      closeLists(); flushPara();
      const start = i, head = cells(ln);
      i += 2;
      const rows = [];
      while (i < lines.length && isTableLine(lines[i])) { rows.push(cells(lines[i])); i++; }
      const ncol = head.length, nrow = rows.length;
      const wide = (head.join("").length + rows.flat().join("").length) > 260;
      /* 又宽又长：横滚丢行标签、纵滚丢表头，所以首列和表头都钉住，还能放大 */
      const grid = ncol >= 3 && (wide || nrow > 10);
      const long = nrow > 12;
      const cls = ["tw", ncol <= 2 ? "def" : "", wide ? "wide" : ""].filter(Boolean).join(" ");
      const box = ["twx", grid ? "grid" : "", long ? "long" : ""].filter(Boolean).join(" ");
      const bar = grid
        ? `<div class="tbar"><span>${nrow} 行 × ${ncol} 列${long ? "，首列和表头已钉住" : ""}</span>`
          + '<button class="tfx" type="button">放大</button></div>'
        : "";
      out.push(`<div class="${box}" data-ln="${start}" data-le="${i - 1}">${bar}<div class="${cls}"><table><thead><tr>`
        + head.map(h => `<th>${h}</th>`).join("") + "</tr></thead><tbody>"
        + rows.map(r => "<tr>" + r.map(c => `<td>${c}</td>`).join("") + "</tr>").join("")
        + "</tbody></table></div></div>");
      continue;
    }

    const li = /^(\s*)([-*+]|\d+\.)\s+(.*)$/.exec(ln);
    if (li) {
      flushPara();
      const depth = Math.floor(li[1].replace(/\t/g, "  ").length / 2);
      const kind = /^\d/.test(li[2]) ? "ol" : "ul";
      while (stack.length > depth + 1) out.push(stack.pop() === "ul" ? "</ul>" : "</ol>");
      while (stack.length < depth + 1) { out.push("<" + kind + ">"); stack.push(kind); }
      let body = li[3];
      const task = /^\[( |x|X)\]\s+(.*)$/.exec(body);
      if (task) body = `<label class="tk"><input type="checkbox" disabled${task[1] !== " " ? " checked" : ""}>${task[2]}</label>`;
      out.push(`<li data-ln="${i}" data-le="${i}">${body}</li>`);
      continue;
    }
    closeLists();

    /* 引用块 / Obsidian callout：连续的 > 行归成一个块，这样多行批注才是一个框 */
    if (/^\s*&gt;/.test(ln)) {
      flushPara();
      const start = i, raw = [];
      while (i < lines.length && /^\s*&gt;/.test(lines[i])) { raw.push(lines[i]); i++; }
      i--;
      const strip = s => s.replace(/^\s*&gt;\s?/, "");
      const first = strip(raw[0]);
      const cal = /^\[!(\w+)\]([+-]?)\s*(.*)$/.exec(first);
      const txt = raw.slice(cal ? 1 : 0).map(strip).join("<br>");
      const attrs = `data-ln="${start}" data-le="${i}"`;
      if (cal) {
        const type = cal[1].toLowerCase();
        const rawTitle = cal[3] || CAL_TYPE[type] || type;
        /* 标题里的 `kb-xxxx` 已经被行内 code 规则换成 <code> 了，两种写法都要认 */
        const idm = /(?:`|<code>)(kb-[0-9a-f]{4,8})(?:`|<\/code>)/.exec(rawTitle);
        const title = rawTitle.replace(/(?:`|<code>)kb-[0-9a-f]{4,8}(?:`|<\/code>)/, "").replace(/[·\s–—-]+$/, "").trim();
        out.push(`<div class="cal cal-${type} anno${idm ? " on" : ""}" data-anno="${idm ? idm[1] : ""}" ${attrs}>`
          + `<div class="cal-h"><b>${esc(title)}</b>`
          + (idm ? `<span class="cal-id">${esc(idm[1])}</span><span class="sp"></span>`
            + `<button class="mini" data-anno-edit="${esc(idm[1])}">编辑</button>`
            + `<button class="mini danger" data-anno-del="${esc(idm[1])}">删除</button>` : '<span class="sp"></span>')
          + `</div><div class="cal-x">${txt}</div></div>`);
      } else {
        out.push(`<blockquote ${attrs}>${raw.map(strip).join("<br>")}</blockquote>`);
      }
      continue;
    }

    if (/^<(h\d|hr)/.test(ln)) { flushPara(); out.push(ln.replace(/^<(h\d|hr)/, `<$1 data-ln="${i}" data-le="${i}"`)); continue; }
    if (/^\u0000B\d+\u0000$/.test(ln.trim())) { flushPara(); out.push(`<div class="codeblk" data-ln="${i}" data-le="${i}">${ln}</div>`); continue; }
    if (/^<figure/.test(ln)) { flushPara(); out.push(ln.replace(/^<figure/, `<figure data-ln="${i}" data-le="${i}"`)); continue; }
    const t = ln.trim();
    if (!t) { flushPara(); continue; }
    para.push({ t: /[ \t]{2}$/.test(ln) ? t + "\u0001" : t, ln: i });
  }
  closeLists();
  flushPara();
  let html = out.join("\n");
  html = html.replace(/\u0000B(\d+)\u0000/g, (m, i) => blocks[+i]);
  return html.replace(/\u0001/g, "<br>");
}

/* ---------------------------------------------------------------- 就地高亮与批注
   写法全部落在 .md 里，Obsidian 打开同一份文件能看到同样的效果：
   高亮 = ==整段包住==；批注 = 紧跟这段的 callout 框（> [!note] 批注 · 日期 · `kb-xxxx`）。
   定位只靠「块在源文件里的行号区间」，批注贴着自己的块走，正文改写也不会飘。 */

const ANNO_RE = /^> \[!(\w+)\][+-]? 批注 · /;
let annoTarget = null;   /* {mode:'add'|'edit', ln?, le?, id?, type} */
const rid = () => "kb-" + Math.random().toString(16).slice(2, 6);

function bodyArr() { return (STATE.currentBody || "").split("\n"); }
function bodySet(arr) { STATE.currentBody = arr.join("\n"); }

/* 缩进：列表项的批注要缩进 4 空格，才会成为那一条的子块 */
function indentOf(lines, ln) {
  const m = /^(\s*)(?:[-*+]|\d+\.)\s+/.exec(lines[ln] || "");
  if (m) return m[1] + "    ";
  return /^(\s*)/.exec(lines[ln] || "")[1] || "";
}

/* Obsidian 的 == 是行内标记：包在列表符号外面会让那一行不再是列表项，
   所以高亮永远包住「标记之后」的内容，缩进 / # / - / 1. / > 留在外面。 */
function splitLine(line) {
  const m = /^(\s*(?:#{1,6}\s+|>|[-*+]\s+|\d+\.\s+)?)([\s\S]*?)(\s*)$/.exec(line) || [null, "", line, ""];
  return { pre: m[1], body: m[2], post: m[3] };
}
const isWrap = b => b.length > 3 && b.startsWith("==") && b.endsWith("==");

function wrapRange(lines, ln, le) {
  for (let i = ln; i <= le; i++) {
    const s = splitLine(lines[i]);
    if (!s.body) continue;
    lines[i] = s.pre + "==" + s.body + "==" + s.post;
  }
  return lines;
}
function unwrapRange(lines, ln, le) {
  for (let i = ln; i <= le; i++) {
    const s = splitLine(lines[i]);
    if (isWrap(s.body)) lines[i] = s.pre + s.body.slice(2, -2) + s.post;
  }
  return lines;
}
function isMarked(lines, ln, le) {
  let any = false;
  for (let i = ln; i <= le; i++) {
    const s = splitLine(lines[i]);
    if (!s.body) continue;
    if (!isWrap(s.body)) return false;
    any = true;
  }
  return any;
}
function toggleMark(ln, le) {
  const lines = bodyArr();
  const off = isMarked(lines, ln, le);
  bodySet(off ? unwrapRange(lines, ln, le) : wrapRange(lines, ln, le));
  return saveBody(off ? "已取消高亮" : "已高亮这一段");
}

function annoBlock(text, type, id) {
  const head = `> [!${type}] 批注 · ${today()} · \`${id}\``;
  const body = String(text).split("\n").map(s => s.trim()).filter(Boolean).map(s => "> " + s);
  return [head].concat(body.length ? body : ["> （还没写内容）"]);
}

function insertAnno(ln, le, text, type) {
  const lines = bodyArr();
  const ind = indentOf(lines, le);
  lines.splice(le + 1, 0, "", ...annoBlock(text, type || "note", rid()).map(l => ind + l));
  bodySet(lines);
  return saveBody("批注已写在这段下面");
}
function replaceAnno(id, text, type) {
  const lines = bodyArr();
  const at = lines.findIndex(l => l.includes("`" + id + "`") && /^\s*>/.test(l));
  if (at < 0) return Promise.resolve("这条批注在 Obsidian 里已经被删掉了");
  const indR = /^(\s*)/.exec(lines[at])[1];
  let e = at;
  while (e + 1 < lines.length && (lines[e + 1].trim() === ">" || /^\s*>\s?\S/.test(lines[e + 1]))) e++;
  lines.splice(at, e - at + 1, ...annoBlock(text, type || "note", id).map(l => indR + l));
  bodySet(lines);
  return saveBody("批注已更新");
}
function deleteAnno(id) {
  const lines = bodyArr();
  const at = lines.findIndex(l => l.includes("`" + id + "`") && /^\s*>/.test(l));
  if (at < 0) return Promise.resolve("这条批注已经不在了");
  let s = at, e = at;
  while (s > 0 && /^\s*>/.test(lines[s - 1]) && !/`kb-/.test(lines[s - 1])) s--;
  while (e + 1 < lines.length && (lines[e + 1].trim() === ">" || /^\s*>\s?\S/.test(lines[e + 1]))) e++;
  /* 顺手把批注前面留出的那个空行也吃掉，不然删一次多一个空行 */
  if (s > 0 && !lines[s - 1].trim()) s--;
  lines.splice(s, e - s + 1);
  bodySet(lines);
  return saveBody("批注已删除");
}

/* 写盘前比一次原文：他在 Obsidian 里改过的话不能直接盖掉 */
async function saveBody(msg) {
  const path = STATE.currentCard;
  if (!path) return toast("先打开一份文件");
  let fresh;
  try { fresh = await api("/api/card?path=" + encodeURIComponent(path)); }
  catch (e) { return toast(e.message); }
  if (fresh.raw && STATE.currentRaw && fresh.raw !== STATE.currentRaw) {
    const v = await ask({
      title: "这份文件在本机被改过",
      body: "<p>服务器上的内容和页面里加载的那一份已经不一致（可能是 Obsidian 或别的窗口刚改过）。"
        + "继续会覆盖那次的改动。</p>",
      okText: "仍然覆盖", cancelText: "放弃并重新加载", altText: ""
    });
    if (v !== "yes") { openCard(path, "", true); return "abort"; }
  }
  const content = STATE.rawPrefix + STATE.currentBody;
  let r;
  try { r = await api("/api/save", { method: "POST", body: JSON.stringify({ path, content }) }); }
  catch (e) { return toast(e.message); }
  if (!r.ok) return toast("未写入：" + (r.error || "未知原因"));
  STATE.currentRaw = content;
  const keep = scrollY;
  $("#readBody").innerHTML = renderMD(STATE.currentBody);
  paintTaskMarks();
  renderMemos();
  scrollTo({ top: keep, behavior: "auto" });
  closeBlocks();
  loadAll(true);
  /* 消化稿那一节会被「按原文重新生成」整节重写：落在里面的批注会被带走，得提前说 */
  if (/^raw\//.test(path) && /^\s*#\s*消化稿/m.test(STATE.currentBody)) {
    toast(msg + " · 提醒：这段在「消化稿」节内，按原文重新生成会重写这一节");
  } else toast(msg);
  return "ok";
}

/* 鼠标移到块上 → 右侧浮出「高亮 / 批注」，就地作用在整块（他要的就是段落级） */
let blockFor = null;
/* 批注显示在文章右侧：文件里仍然是紧跟那一段的 callout（Obsidian 打开看就是原生的），
   页面上把它从正文抽出来，摆到右栏对应的高度上。定位只按 DOM 位置算，不写进文件。 */
function memoAnchorOf(callEl) {
  /* 批注在源文件里紧贴着它要说的这一段：往回找第一个不是批注的块 */
  let el = callEl;
  while (el && el.previousElementSibling) {
    el = el.previousElementSibling;
    if (!el.classList.contains("anno")) return el;
  }
  return callEl;
}

function renderMemos() {
  const col = $("#memoCol"), inner = $("#memoInner");
  if (!col || !inner) return;
  const calls = $$("#readBody .cal.anno");
  if (!calls.length) {
    col.hidden = true;
    inner.innerHTML = "";
    $$("#readBody [data-memo]").forEach(el => { delete el.dataset.memo; el.classList.remove("has-memo"); });
    return;
  }
  col.hidden = false;
  $("#memoCnt").textContent = calls.length + " 条";
  inner.innerHTML = calls.map(el => {
    const id = el.dataset.anno || "";
    const type = (el.className.match(/cal-(\w+)/) || [, "note"])[1];
    const title = (el.querySelector(".cal-h b") || {}).textContent || "批注";
    const text = (el.querySelector(".cal-x") || {}).innerHTML || "";
    return `<div class="amemo m-${type}" data-memo-for="${esc(id)}">
      <div class="am-h"><b>${esc(title.trim() || CAL_TYPE[type] || "批注")}</b></div>
      <div class="am-x">${text}</div>
      <div class="am-f">
        <button class="mini" data-anno-goto="${esc(id)}">跳到那一段</button>
        <button class="mini" data-anno-edit="${esc(id)}">编辑</button>
        <button class="mini danger" data-anno-del="${esc(id)}">删除</button></div></div>`;
  }).join("");
  $$("#readBody [data-memo]").forEach(el => { delete el.dataset.memo; el.classList.remove("has-memo"); });
  layoutMemos();
}

function layoutMemos() {
  const col = $("#memoCol"), inner = $("#memoInner");
  if (!col || col.hidden) return;
  const narrow = innerWidth <= 900;
  const base = inner.getBoundingClientRect().top;
  let bottom = 0;
  $$("#memoInner .memo-card").forEach(card => {
    const id = card.dataset.memoFor;
    const call = $('#readBody .cal.anno[data-anno="' + id + '"]');
    if (!call) return;
    const anchor = memoAnchorOf(call);
    anchor.classList.add("has-memo");
    anchor.dataset.memo = id;
    if (narrow) { card.style.top = ""; return; }
    const want = anchor.getBoundingClientRect().top - base;
    const top = Math.max(want, bottom + 10);
    card.style.top = top + "px";
    bottom = top + card.offsetHeight;
  });
  /* 右栏的高度要跟正文一样高，批注多了才不会顶出这一屏 */
  const body = $("#readBody");
  $("#memoCol").style.minHeight = (body.offsetHeight + 8) + "px";
}

function showBlockTools(el) {
  const ln = +el.dataset.ln, le = +(el.dataset.le || el.dataset.ln);
  if (!isFinite(ln) || el.classList.contains("anno")) return;
  blockFor = { ln, le, el };
  const t = $("#blkTools");
  t.hidden = false;
  const r = el.getBoundingClientRect(), tr = t.getBoundingClientRect();
  const left = r.right + 10 + tr.width > innerWidth ? r.right - tr.width - 10 : r.right + 10;
  t.style.left = Math.max(8, left) + "px";
  t.style.top = Math.max(60, Math.min(innerHeight - tr.height - 8, r.top + 2)) + "px";
  const lines = bodyArr();
  t.querySelector('[data-blk="mark"]').classList.toggle("on", isMarked(lines, ln, le));
}
function closeBlocks() {
  const t = $("#blkTools");
  if (t) t.hidden = true;
  const a = $("#annoEdit");
  if (a) a.hidden = true;
  blockFor = null;
  blkPinned = false;
  annoHide = false;
}

function openAnnoEditor(el) {
  const ln = +el.dataset.ln, le = +(el.dataset.le || el.dataset.ln);
  annoTarget = { mode: "add", ln, le, type: "note" };
  const box = $("#annoEdit");
  box.hidden = false;
  box.dataset.le = le;
  $("#annoText").value = "";
  $("#annoStatus").textContent = "";
  $$("#annoType button").forEach(b => b.classList.toggle("on", b.dataset.t === "note"));
  const r = el.getBoundingClientRect();
  box.style.left = Math.max(8, Math.min(innerWidth - box.offsetWidth - 8, r.left)) + "px";
  box.style.top = Math.max(60, Math.min(innerHeight - box.offsetHeight - 12, r.bottom + 8)) + "px";
  setTimeout(() => $("#annoText").focus(), 30);
}

/* ---------------------------------------------------------------- 复习任务 → 段落着色 */

const normQ = s => String(s || "").replace(/[\s「」『』“”"'’‘、，。：:；;？！?!（）()【】\[\]—\-｜|]/g, "");

/* 复习条目一条疑问 = 一个字不改的一条。card 指问题卡，raw 指原料：
   这两页都要能看到它，不然原料页的「本篇待复习」永远是空的 */
function myTasks() {
  const p = STATE.currentCard;
  return STATE.tasks.filter(t => (t.card === p || t.raw === p) && !t.done);
}
function paintTaskMarks() {
  const blocks = $$("#readBody [data-ln]");
  blocks.forEach(b => { b.classList.remove("tied"); delete b.dataset.tied; });
  myTasks().forEach(t => {
    const key = normQ(String(t.title || "").replace(/^回看原文\s*[：:]\s*/, ""));
    if (key.length < 4) return;
    const hit = blocks.find(el => {
      const txt = normQ(el.textContent);
      if (!txt) return false;
      if (txt.includes(key) || key.includes(txt)) return true;
      for (let n = Math.min(key.length, 16); n >= 8; n--)
        for (let i = 0; i + n <= key.length; i++)
          if (txt.includes(key.slice(i, i + n))) return true;
      return false;
    });
    if (hit) { hit.classList.add("tied"); hit.dataset.tied = t.title; }
  });
}

function locateInBody(q) {
  const key = normQ(String(q || "").replace(/^回看原文\s*[：:]\s*/, ""));
  if (key.length < 4) return false;
  const all = $$("#readBody [data-ln]");
  const head = all.find(el => /^这张原料长成的卡/.test(el.textContent.trim()));
  const blocks = head ? all.slice(0, all.indexOf(head)) : all;
  const hit = blocks.find(el => {
    const t = normQ(el.textContent);
    if (!t) return false;
    if (t.includes(key) || key.includes(t)) return true;
    for (let n = Math.min(key.length, 16); n >= 8; n--)
      for (let i = 0; i + n <= key.length; i++)
        if (t.includes(key.slice(i, i + n))) return true;
    return false;
  }) || all.find(el => /^我的疑问/.test(el.textContent.trim()));
  if (!hit) return false;
  /* 上一次定位的那一段先暗下去，屏幕上同时只留一个「就是这儿」 */
  $$("#readBody .located").forEach(el => el.classList.remove("located"));
  hit.classList.add("located", "flash");
  hit.scrollIntoView({ block: "center", behavior: REDUCE ? "auto" : "smooth" });
  setTimeout(() => hit.classList.remove("flash"), 2600);
  return true;
}

/* ---------------------------------------------------------------- 阅读页 */

async function openCard(path, anchor, quiet) {
  if (!path) return;
  let d;
  try { d = await api("/api/card?path=" + encodeURIComponent(path)); }
  catch (e) { return toast(e.message); }
  if (d.error) return toast(d.error);

  const fm = d.frontmatter || {};
  const c = cardOf(path);
  const src = fm.source && /^https?:/.test(fm.source) ? fm.source : "";
  STATE.currentCard = path;
  STATE.lastCardData = d;
  STATE.currentRaw = d.raw || "";
  STATE.currentBody = d.body || "";
  /* body 是 raw 的子串（parse_frontmatter 从 text[m.end():] 切），前缀按长度回推最稳 */
  STATE.rawPrefix = String(d.raw || "").endsWith(String(d.body || ""))
    ? String(d.raw).slice(0, String(d.raw).length - String(d.body || "").length) : "";

  const h = hashFor("read", path);
  if (location.hash !== h) {
    if (quiet) history.replaceState(null, "", h);
    else location.hash = h;
  }
  setView("read");

  $("#readTitle").textContent = fm.title || c.title || path;
  const chip = (txt, title) => `<span class="chip"${title ? ` title="${esc(title)}"` : ""}>${esc(txt)}</span>`;
  /* 这一排只放「这一份是什么、从哪来、多长」；状态在右栏那一块改，别在这里再摆一遍 */
  $("#readMeta").innerHTML = [
    chip(KIND_CN[c.kind] || c.kindLabel || c.kind, "这一页在库里的层级"),
    c.media_type ? chip("类型 · " + c.media_type, "内容形态（media_type），抓取或生成时写入") : "",
    c.est_min ? chip("预估 · 约 " + c.est_min + " 分钟",
      "按正文净字数折算：文本 200 字/分（中文按字、英文按词），卡片 150 字/分；"
        + "视频和音频直接取原片时长，不按字数重复计。常数出处见 skill/PROMPTS.md §6") : "",
    fm.platform ? chip("来源 · " + fm.platform, "这条主张从哪个平台进来") : "",
    fm.author ? chip("作者 · " + fm.author) : "",
    fm.captured_at ? chip("入库 " + fm.captured_at, "captured_at：进库日期") : "",
    chip(fmt(c.chars || 0) + " 字", "正文净字数：中文字按字、英文按词，不含 frontmatter / Markdown 语法 / 裸链接"),
    src ? `<a class="chip origin" href="${esc(src)}" target="_blank" rel="noopener">查看原始内容 ↗</a>` : "",
    `<span class="chip file" title="${esc(path)}">${esc(path)}</span>`
  ].filter(Boolean).join("");

  renderTagBox(fm.tags || [], c.tags || []);
  $("#readBody").innerHTML = renderMD(STATE.currentBody);
  /* 文件第一行就是 `# 标题`，页面顶部已经排过一遍。这里只把它藏起来、不删行：
     删行会让后面每个块的 data-ln 整体错位，高亮和批注就会写错地方。 */
  const h1 = $("#readBody h1");
  if (h1 && h1.textContent.trim() === ($("#readTitle").textContent || "").trim()) h1.classList.add("dup");
  applyWidth();
  paintTaskMarks();
  renderMemos();
  renderReadTasks();

  const isCard = /^wiki\//.test(path), isRaw = /^raw\//.test(path);
  $("#rdEdit").hidden = !(isCard || isRaw);
  $("#rdDel").hidden = !(isCard || isRaw);
  $("#rdRedo").hidden = !isRaw;
  $("#rdSync").hidden = !isRaw;
  $("#rdSave").hidden = true;
  $("#rdText").classList.add("hidden");
  $("#readBody").classList.remove("hidden");
  rdEditLabel("改写");
  $("#annoTip").hidden = !(isCard || isRaw);
  $("#rdStatus").textContent = isRaw
    ? "原料文稿来自视频 / 音频转写或图片识别，可能存在错漏，可在此人工修正。保存只改这一份文件，卡片要另行同步。"
    : isCard ? "" : "这一页既不是卡片也不是原料（系统页 / 索引），只读。";
  paintMcSrc(STATE.currentCard);
  $("#mcSource").textContent = "入库后我会拿这张卡和库里已有的卡比一遍，真接得上的才写进「接上的线」——接不上就留空，不造边。";

  const media = String(fm.media || "").trim();
  $("#rdPlayer").innerHTML = /\.(mp4|webm|mov|m4a|mp3)$/i.test(media)
    ? `<video id="rdVideo" src="/media/${encodeURIComponent(media)}" controls preload="metadata"></video>
       <p class="tip">本地视频已就位：点正文里的 <span class="ts">[00:00 – 00:13]</span> 跳到那一段开头。</p>` : "";

  paintStatusRow(fm.status || c.status || "");
  $("#annoText").value = "";
  $("#annoStatus").textContent = "";

  renderLinksPanels(d, c);

  dwellStart(path, c.chars || 0);
  act("reading", isRaw ? (grownCards(path).length ? "这份原料已经长成卡了" : "这份原料还没长出卡")
    : "我陪你读这一张");
  if (anchor) setTimeout(() => {
    locateInBody(anchor) || toast(`未在这份里定位到「${String(anchor).slice(0, 12)}…」那一段，已打开全文`);
  }, 90);
}

/* 右栏的链接只有一套口径：
   原料页 → 「这张原料长成的卡」（按卡片的 from_raw 算），不给它反向链接那一栏 ——
   原料被谁引用基本等于它自己长出来的那批卡，两栏并排就是同一件事说两遍。
   卡片页 → 「链出去的」列这张卡指向的其它卡，原料走 from_raw 单独一行；
   「反向链接」是别的卡指向这里，和出链是两个方向，所以两个都留。 */
function grownCards(path) {
  const c = cardOf(path), names = [c.title, c.name].concat(c.aliases || []);
  return STATE.cards.filter(x => x.kind !== "raw" && x.path !== path && (
    x.from_raw === path
    || (!c.from_raw && names.some(t => t && (x.links || []).indexOf(t) >= 0))));
}

function renderLinksPanels(d, c) {
  const path = STATE.currentCard, isRaw = /^raw\//.test(path);
  $("#blPanel").hidden = isRaw;                 // 原料不显示反向链接栏

  if (isRaw) {
    const grew = grownCards(path);
    $("#outTitle").textContent = "这张原料长成的卡";
    $("#outCnt").textContent = grew.length + " 张";
    $("#outlinks").innerHTML = grew.length ? grew.map(rowHTML).join("")
      : '<div class="lbl">这份原料还没有长出卡片，去「消化」提交它，或在下面补一张</div>';
    $("#fromRaw").textContent = "";
    return;
  }

  const rawTargets = [];
  const outs = [];
  (c.links || []).forEach(t => {
    const x = STATE.byName[t] || STATE.byName[String(t).trim()];
    if (!x) return;
    (x.kind === "raw" ? rawTargets : outs).push(x);
  });
  $("#outTitle").textContent = "链出去的";
  $("#outCnt").textContent = outs.length + " 张";
  $("#outlinks").innerHTML = outs.length ? outs.map(rowHTML).join("")
    : '<div class="lbl">这张卡没有指向其它卡</div>';
  const ghosts = (c.links || []).filter(t => !STATE.byName[t] && !STATE.byName[String(t).trim()]);
  if (ghosts.length) {
    $("#outlinks").insertAdjacentHTML("beforeend",
      `<p class="tip">库里还没有这几张卡（Obsidian 里显示为幽灵链接）：` + ghosts.map(esc).join("、") + `</p>`);
  }
  /* 原料在这张卡上只出现一次：正文那行「出处：[[原料]]」是它的主出口（Obsidian 的图谱边也靠它），
     侧栏只在正文里确实没有这条链时才补位，反向链接里则一律滤掉原料。 */
  const inBody = new Set(rawTargets.map(x => x.path));
  const srcs = (c.from_raw && !inBody.has(c.from_raw)) ? [cardOf(c.from_raw)].filter(Boolean) : [];
  $("#fromRaw").innerHTML = srcs.length
    ? "来自原料 · " + srcs.map(s => `<a data-path="${esc(s.path)}">${esc(s.title)}</a>`).join("、") : "";

  const rawSeen = new Set(rawTargets.map(x => x.path));
  if (c.from_raw) rawSeen.add(c.from_raw);
  const bl = ((d && d.backlinks) || []).filter(b => !rawSeen.has(b.path));
  $("#blCnt").textContent = bl.length + " 张引用这里";
  $("#backlinks").innerHTML = bl.length ? bl.map(b => rowHTML(cardOf(b.path))).join("")
    : '<div class="lbl">没有其他卡片引用这一张</div>';
}

/* 状态改完之后，页面上所有显示这条的地方都要跟着动 —— 侧栏那几行是可点状态按钮，
   以前只重画了阅读页顶部，链接列表里的行还停在旧值上，看起来像「改了没生效」。 */
function refreshReadPanels() {
  if (curView !== "read" || !STATE.currentCard) return;
  const c = cardOf(STATE.currentCard);
  paintStatusRow(c.status || "");
  renderLinksPanels(STATE.lastCardData || {}, c);
  renderReadTasks();
  paintTaskMarks();
}

/* 动作反馈写在 #rdNote：#rdStatus 是这一层的固定说明，不能被一次点击覆盖掉 */
function rdNote(msg, bad) {
  const el = $("#rdNote");
  el.hidden = !msg;
  el.textContent = msg || "";
  el.classList.toggle("bad", !!bad);
}

function rdEditLabel(txt) { const b = $("#rdEdit"); if (b) b.lastChild.textContent = txt; }

function paintStatusRow(status) {
  const c = cardOf(STATE.currentCard);
  const canDo = /^(wiki|raw)\//.test(STATE.currentCard);
  /* 不再单列「当前 · X」：下面那颗高亮的状态芯片说的就是同一件事 */
  $("#stFlow").innerHTML = canDo
    ? STATUSES.map(s => `<button class="chip${s === status ? " on" : ""}" data-set-status="${esc(s)}"
        data-status-for="${esc(STATE.currentCard)}">${esc(s)}<i>${esc(STATUS_HINT[s] || "")}</i></button>`).join("")
    : '<span class="lbl">系统页与索引不参与复习流转</span>';
  /* 文件里 status 的原值可能是历史写法（「已消化」、甚至一整句），页面按五值显示并把原值摊出来 */
  const dirty = $("#stDirty");
  const raw = (c.status_raw || "").trim();
  if (canDo && raw && raw !== (c.status || "")) {
    dirty.hidden = false;
    dirty.innerHTML = `文件里的原值是 <code>${esc(raw.slice(0, 40))}</code>，不在五个状态之内，上面按「${esc(c.status || "未填写")}」显示。点一下任意状态就把它改写规范。`;
  } else dirty.hidden = true;
  $("#stTip").textContent = canDo
    ? "状态记录这一份在你这里走到哪一步，点一下直接写回 frontmatter 的 status 字段。这一栏是全站唯一的状态写入处。"
    : "";
}

function renderTagBox(fmTags, allTags) {
  /* data-tag 上存原值：芯片文字里带了显示用的 # 前缀，删标签不能拿 textContent 去比 */
  const fm = fmTags || [], extra = (allTags || []).filter(t => fm.indexOf(t) < 0);
  $("#cardTags").innerHTML = fm.map(t =>
    `<span class="chip tag" data-tag="${esc(t)}">#${esc(t)}<button class="tag-x" data-tag-del="${esc(t)}" title="移除这个标签">×</button></span>`).join("")
    + extra.map(t => `<span class="chip tag dim" title="这个 # 写在正文里，不在 frontmatter 的 tags 里，删不掉">${esc(t)}</span>`).join("")
    || '<span class="lbl">还没有标签</span>';
}
const chipTags = () => $$("#cardTags .chip.tag:not(.dim)").map(x => x.dataset.tag);

async function commitTags(next) {
  let r;
  try { r = await api("/api/card/tags", { method: "POST", body: JSON.stringify({ path: STATE.currentCard, tags: next }) }); }
  catch (e) { return toast(e.message); }
  if (!r.ok) return toast("标签未保存：" + (r.error || "未知原因"));
  renderTagBox(r.tags, (cardOf(STATE.currentCard).tags) || []);
  await loadAll(true);
  toast("标签已保存");
}

function renderReadTasks() {
  const p = STATE.currentCard;
  const mine = STATE.tasks.filter(t => t.card === p || t.raw === p);
  const undone = mine.filter(t => !t.done);
  const panel = $("#tasksPanel");
  if (panel) panel.hidden = !mine.length;
  if ($("#tasksCnt")) $("#tasksCnt").textContent = undone.length ? undone.length + " 条" : "都勾完了";
  $("#readTasks").innerHTML = undone.length ? undone.map(t =>
    `<div class="drow"><div class="txt"><div class="dt">${esc(t.title)}</div>
      <div class="meta">这条约 ${esc(t.est_min || 1)} 分钟</div></div>
      <div class="btns">
        <button class="mini" data-jump="${esc(t.title)}">定位段落</button>
        <button class="mini ok" data-done-task="${esc(t.title)}">已完成</button>
      </div></div>`).join("")
    + '<p class="tip">这一栏里的条目都出自当前这一份，类型和来源就是上面那排芯片；只有预估时间按每条疑问单独算。</p>'
    : (mine.length ? '<div class="lbl">这一份的复习条目都勾完了</div>'
      : '<div class="lbl">这一份没排到复习条目</div>');
}

async function doneTask(title, undo) {
  let r;
  try { r = await api("/api/task/done", { method: "POST", body: JSON.stringify({ title, undo: !!undo }) }); }
  catch (e) { return toast(e.message); }
  if (!r.ok) return toast("未改成：" + (r.error || "未知原因"));
  if (!undo) { act("celebrate", "这条过了，间隔往后推一级"); celebrate(.4); }
  await loadAll(true);
  renderDue();
  renderDone();
  if (curView === "read") { paintTaskMarks(); renderReadTasks(); }
  paintBadge();
  toast(r.done ? "已完成，已从今日列表移出" : "已撤回，重新排回队列");
}

/* 改写：切到源文件编辑态。#rdSave 只保存当前这一份文件，不动别的卡片 */
function toggleCardEdit() {
  const ta = $("#rdText"), body = $("#readBody");
  if (ta.classList.contains("hidden")) {
    ta.value = STATE.currentRaw || "";
    ta.classList.remove("hidden");
    body.classList.add("hidden");
    $("#rdSave").hidden = false;
    rdEditLabel("取消编辑");
    $("#rdStatus").textContent = /^raw\//.test(STATE.currentCard)
      ? "这是原料本体。保存只替换这一份文件；消化稿和卡片要照新内容重出一遍，再点「同步卡片（不调模型）」或「按原文重新生成」。"
      : "改的是这张卡的 md 原文，保存后 Obsidian 那边同步可见。";
    ta.focus();
  } else {
    $("#rdSave").hidden = true;
    rdEditLabel("改写");
    ta.classList.add("hidden");
    body.classList.remove("hidden");
    $("#rdStatus").textContent = /^raw\//.test(STATE.currentCard)
      ? "原料文稿来自转写或识别，可能有错漏，可在此人工修正。" : "";
  }
}

async function saveCurrentFile() {
  const ta = $("#rdText");
  let r;
  try { r = await api("/api/save", { method: "POST", body: JSON.stringify({ path: STATE.currentCard, content: ta.value }) }); }
  catch (e) { return toast(e.message); }
  if (!r.ok) return toast("保存失败：" + (r.error || "未知原因"));
  toggleCardEdit();
  await loadAll(true);
  openCard(STATE.currentCard, "", true);
  toast("已保存这一份文件");
}

async function dropCard() {
  const path = STATE.currentCard;
  if (!path) return toast("先打开一份文件");
  const isRaw = /^raw\//.test(path);
  const grew = isRaw ? grownCards(path) : [];
  const body = isRaw
    ? `<p>将 <code>${esc(path)}</code> 移入回收站 <code>inbox/trash/</code>，文件不硬删，随时可以拿回来。</p>`
      + (grew.length
        ? `<p class="warn">这份原料已经长成 <b>${grew.length}</b> 张卡：`
          + `<span class="who">${grew.slice(0, 6).map(c => esc(c.title)).join("、")}${grew.length > 6 ? " 等" : ""}</span>。`
          + "卡片是独立主张，删原料默认不连带删卡；先只移原料，确认之后卡片自己留着。</p>"
          + `<p class="tip">要一起处理，选「原料和卡片一并移入」。</p>`
        : '<p class="tip">这份还没有长出卡片，移走它不会影响库里其他内容。</p>')
    : `<p>将 <code>${esc(path)}</code> 移入回收站 <code>inbox/trash/</code>，文件不硬删。</p>`
      + '<p class="tip">这张卡排到的复习条目会一起清掉。</p>';
  const v = await ask({
    title: isRaw ? "移入回收站 · 原料" : "移入回收站 · 卡片",
    body, okText: isRaw && grew.length ? "仅移入原料" : "移入回收站",
    altText: isRaw && grew.length ? "原料和卡片一并移入" : "", danger: true
  });
  if (v === "no" || !v) return;
  const gone = [];
  try {
    const r = await api("/api/card/drop", { method: "POST", body: JSON.stringify({ path }) });
    if (!r.ok) return toast("未删除：" + (r.error || "未知原因"));
    gone.push(path);
    toast(`已移入 ${r.trash}` + (r.tasksRemoved ? `，并清掉 ${r.tasksRemoved} 条复习条目` : ""));
    if (v === "alt") {
      const list = r.grew && r.grew.length ? r.grew : grew;
      for (const g of list) {
        const r2 = await api("/api/card/drop", { method: "POST", body: JSON.stringify({ path: g.path }) });
        if (r2.ok) gone.push(g.path);
      }
      toast(`原料与 ${gone.length - 1} 张卡片已一并移入回收站`);
    }
  } catch (e) { return toast(e.message); }
  await loadAll();
  go(prevView === "read" ? "cards" : prevView);
}

async function syncCards() {
  if (!/^raw\//.test(STATE.currentCard)) return toast("只有原料需要按现有消化稿同步卡片");
  const btn = $("#rdSync");
  btn.disabled = true;
  rdNote("正在按现有消化稿重长卡片…（不调用模型，不产生费用）");
  let r;
  try { r = await api("/api/recompile", { method: "POST", body: JSON.stringify({ path: STATE.currentCard }) }); }
  catch (e) { r = { ok: false, error: e.message }; }
  btn.disabled = false;
  if (!r.ok) { rdNote("同步失败：" + (r.error || "未知原因"), 1); return; }
  rdNote("已同步：" + (r.note || "完成"));
  await loadAll();
  openCard(STATE.currentCard, "", true);
  if (r.dropped && r.dropped.length) toast(`拦下 ${r.dropped.length} 条不该成卡的内容，理由写在原料末尾`);
}

async function redoDigest() {
  if (!/^raw\//.test(STATE.currentCard)) return toast("只有原料能按原文重新生成");
  const ta = $("#rdText");
  const payload = { path: STATE.currentCard };
  if (!ta.classList.contains("hidden")) payload.content = ta.value;
  const v = await ask({
    title: "按原文重新生成消化稿",
    body: '<p>这会调用模型，按当前原文重写「消化稿」整节并重长卡片；旧稿会留备份。</p>'
      + '<p class="tip">落在消化稿节内的就地批注会被带走，先确认没有没归位的批注。</p>',
    okText: "开始生成", cancelText: "取消"
  });
  if (v !== "yes") return;
  const btn = $("#rdRedo");
  btn.disabled = true;
  startProg("模型重新消化中");
  let r;
  try { r = await api("/api/redigest", { method: "POST", body: JSON.stringify(payload) }); }
  catch (e) { r = { ok: false, error: e.message }; }
  stopProg(r.ok);
  btn.disabled = false;
  if (!r.ok) { rdNote("生成失败：" + (r.error || "") + (r.hint ? "（" + r.hint + "）" : ""), 1); return; }
  rdNote("消化稿已重写：" + (r.note || "完成") + (r.backup ? ` · 旧稿备份 ${r.backup}` : ""));
  toggleCardEdit();
  await loadAll();
  openCard(STATE.currentCard, "", true);
}

/* 补卡只有阅读页这一个入口（消化页那张是同一个模块的第二份，已撤）：
   ai 模式只填一句提示词，走 /api/card/generate；自己写走 /api/card/add。 */
let genProgTimer = null;
function startGenProg(ai) {
  const box = $("#genProg"), fill = $("#genFill"), tip = $("#genTip");
  box.hidden = false;
  const t0 = Date.now(), est = ai ? 40 : 6;
  fill.style.width = "4%";
  tip.textContent = ai
    ? "模型在写这张卡，通常 10–40 秒。可以先去别的页面看，不用守着这一栏，写好会来问你。"
    : "写入中…";
  clearInterval(genProgTimer);
  genProgTimer = setInterval(() => {
    const sec = (Date.now() - t0) / 1000;
    fill.style.width = (Math.min(.9, sec / est) * 100).toFixed(1) + "%";
    if (ai && sec > est) {
      tip.textContent = `已用 ${clock(sec)}，比通常慢 —— 素材长或模型在想要接哪张卡。别刷新这一页，跑完会自己停。`;
    }
  }, 400);
}
function stopGenProg() {
  clearInterval(genProgTimer);
  genProgTimer = null;
  $("#genProg").hidden = true;
  $("#genFill").style.width = "0%";
}

/* 原料下拉：按最近入库排，第一项是「不挂原料」。
   新建卡片不该被钉死在某一份材料下面 —— 有时我就是想记一条自己的主张。 */
function paintMcSrc(prefer) {
  const raws = STATE.cards.filter(c => c.kind === "raw").sort((a, b) => b.mtime - a.mtime).slice(0, 30);
  const sel = $("#mcSrcSel");
  if (!sel) return;
  const opts = [{ v: "", t: raws.length ? "不挂原料" : "库里还没有原料" }]
    .concat(raws.map(c => ({ v: c.path, t: c.title.slice(0, 26) + " · " + ymd(c.mtime) })));
  $("#mcSrcPop").innerHTML = opts.map(o =>
    `<button role="option" data-v="${esc(o.v)}">${esc(o.t)}</button>`).join("");
  $("#mcSrc").innerHTML = opts.map(o => `<option value="${esc(o.v)}">${esc(o.t)}</option>`).join("");
  const want = opts.some(o => o.v === prefer) ? prefer : (raws.length ? raws[0].path : "");
  const hit = opts.find(o => o.v === want) || opts[0];
  selSet(sel, hit.v, hit.t, false);
}
const mcSource = () => ($("#mcSrcSel") || { dataset: {} }).dataset.value || "";

async function makeCard() {
  const sourcePath = mcSource();
  const g = id => $("#mc" + id);
  const st = g("Status");
  const kindSel = $("#mcKindSel");
  const kind = (kindSel ? kindSel.dataset.value : g("Kind").value) || "concept";
  const ai = $("#mcMode").dataset.mode === "ai";
  if (ai && !(g("Ask").value || "").trim()) { st.textContent = "先写一句想让模型补什么"; return; }
  if (!ai && !(g("Name").value || "").trim()) { st.textContent = "卡名要写成一句能独立成立的话"; return; }
  st.textContent = ai ? "生成中…" : "写入中…";
  const btn = g("Add");
  btn.disabled = true;
  startGenProg(ai);
  companion.badge(ai ? "生卡中" : "写入中", ai ? "模型正在写这张卡" : "正在写入文件");
  let r;
  try {
    r = ai
      ? await api("/api/card/generate", {
        method: "POST",
        body: JSON.stringify({ kind, prompt: (g("Ask").value || "").trim(), source: sourcePath || "" })
      })
      : await api("/api/card/add", {
        method: "POST",
        body: JSON.stringify({
          kind, name: (g("Name").value || "").trim(), text: (g("Text").value || "").trim(),
          use: (g("Use") ? g("Use").value || "" : "").trim(), source: sourcePath || ""
        })
      });
  } catch (e) { r = { ok: false, error: e.message }; }
  stopGenProg();
  btn.disabled = false;
  paintBadge();
  if (!r.ok) { st.textContent = "失败：" + (r.error || "") + (r.hint ? " — " + r.hint : ""); return; }
  const gen = r.generated || {};
  st.textContent = (r.state === "exists" ? "已有同名卡，只做了补充：" : "已入库：") + r.path
    + (gen.tags && gen.tags.length ? ` · 标签：${gen.tags.join("、")}` : "");
  g("Ask") && (g("Ask").value = "");
  if (g("Name")) g("Name").value = "";
  if (g("Text")) g("Text").value = "";
  if (g("Use")) g("Use").value = "";
  /* 新卡要立刻出现在侧栏和右栏里：重读一次当前这份，反链 / 长成的卡都会跟着更新 */
  await loadAll();
  if (curView === "read" && STATE.currentCard) openCard(STATE.currentCard, "", true);
  act("celebrate", "多了一张卡，已经挂进侧栏");
  const linked = r.linked || [];
  const v = await ask({
    title: "处理完成",
    body: `<p>${esc(r.label || KIND_CN[kind])}卡已写入库里：</p><p><code>${esc(r.path)}</code></p>`
      + (gen.use ? `<p class="tip">用在哪：${esc(gen.use)}</p>` : "")
      + (linked.length
        ? `<p>自动接上了 ${linked.length} 条线：</p>`
          + linked.map(t => `<p><code>[[${esc(t)}]]</code></p>`).join("")
          + `<p class="tip">关系写在这张卡正文的「接上的线」一节，不对就删掉那一行。</p>`
        : `<p class="tip">这次没接上任何一条线：库里没有和它真有关系的主张。空着比造一条假边好。</p>`)
      + `<p class="tip">它已经挂在当前这份材料的卡片列表里了。</p>`,
    okText: "现在查看", cancelText: "先留着", altText: "去卡片列表"
  });
  if (v === "yes") await openCard(r.path);
  else if (v === "alt") go("cards");
}

/* ---------------------------------------------------------------- 消化
   接口是同步的：一次 POST 从抓取跑到长卡，中间没有 SSE 也没有轮询。
   所以进度条只能「按时间估」——那就估给他看，别让他对着一根无限循环的条猜。 */

/* 处理流程只写这一份：右栏那张说明和消化时的进度条都从它取，
   两处各写一套名字就会开始互相对不上 */
const PIPELINE = [
  { t: "取回原文", d: "先判形态（视频 / 图文 / 纯文字），再取正文；读不到就把平台返回的原话贴出来，附怎么给 cookie。" },
  { t: "转写 / 抽帧识字", d: "视频走本地转写和抽帧识字两条通道，画面里的字幕能纠正语音识别错的专有名词。" },
  { t: "模型提炼", d: "按 skill/PROMPTS.md 的规范出消化稿：核心观点、可执行清单、概念 / 连接候选、正好 3 条疑问、主题标签。" },
  { t: "长卡入库", d: "新概念与已有卡重叠时回填旧卡，不另起近义名；产品名、许可证、互动数据一律拦下并给出理由。" }
];
const STAGES = PIPELINE.map(x => x.t);
/* 视频要本地转写，量级完全不同；网页和纯文字几十秒内会回来 */
const EST = { video: 210, image: 90, text: 45, web: 35 };

function renderPipeSteps() {
  const el = $("#pipeSteps");
  if (!el) return;
  el.innerHTML = PIPELINE.map((s, i) =>
    `<div class="step"><span class="n">${i + 1}</span><div><b>${esc(s.t)}</b><p>${esc(s.d)}</p></div></div>`).join("");
}
let prog = null;

function estFor(o) {
  if (o && o.kind === "video") return EST.video;
  if (o && o.kind === "image") return EST.image;
  if (o && !String($("#digestUrl").value || "").trim()) return EST.text;
  if (o && o.kind === "text") return EST.web;
  return o ? EST.web : 60;   /* 没探测过：给个中位期望 */
}
function startProg(label, est) {
  stopProgTimer();
  est = est || 60;
  const box = $("#digestProg"), fill = $("#progFill"), stages = $("#progStages");
  box.hidden = false;
  stages.innerHTML = STAGES.map(s => `<span>${esc(s)}</span>`).join("");
  const t0 = Date.now();
  const tick = () => {
    const sec = (Date.now() - t0) / 1000;
    const p = Math.min(.92, sec / est);
    fill.style.width = (p * 100).toFixed(1) + "%";
    const n = Math.min(4, Math.floor(p * 4) + 1);
    $$("#progStages span").forEach((s, i) => {
      s.classList.toggle("on", i < n);
      s.classList.toggle("now", i === n - 1);
    });
    $("#progTip").textContent = sec < est
      ? `${label} · 已用 ${clock(sec)}。${kindWord()}通常 ${clock(est)} 内完成，这段时间可以切到别的页面看，处理不会中断。`
      : `已用 ${clock(sec)}，超过常见耗时——多半是大视频在本地转写，属正常。别关掉这个标签页，也不用刷新，它跑完会自己停。`;
  };
  tick();
  prog = { t: setInterval(tick, 500), est };
}
function kindWord() {
  const url = String($("#digestUrl").value || "").trim();
  if (!url) return "粘贴正文；";
  if (/douyin|bilibili|\.bilibili|xiaohongshu|youtu|视频/.test(url)) return "视频类素材；";
  return "网页类素材；";
}
function stopProgTimer() { if (prog) { clearInterval(prog.t); prog = null; } }
function stopProg(ok) {
  stopProgTimer();
  const box = $("#digestProg");
  $("#progFill").style.width = "100%";
  $$("#progStages span").forEach(s => { s.classList.add("on"); s.classList.remove("now"); });
  $("#progTip").textContent = ok ? "完成。原料与卡片已经写进库，结果在下方。" : "已结束，这次没有写入内容，原因见上方提示。";
  setTimeout(() => { box.hidden = true; }, 4200);
}

const KIND_FORM = { image: "图文（走图片识字）", video: "视频（走转写 + 抽帧识字）", text: "纯文字" };

async function previewFetch() {
  const url = ($("#digestUrl").value || "").trim();
  if (!url) return toast("先填一条链接");
  const st = $("#digestStatus"), box = $("#previewResult");
  st.textContent = "探测中…";   /* 慢不慢由 #progTip 报，这里不抢着说第二遍 */
  $("#previewBtn").disabled = true;
  box.hidden = false;
  try {
    const r = await api("/api/fetch", { method: "POST", body: JSON.stringify({ url }) });
    STATE.lastKind = r.kind;
    box.innerHTML = r.ok
      ? `<div class="okline">可读取｜平台 ${esc(r.platform)}｜形态 ${esc(KIND_FORM[r.kind] || r.kind || "未标注")}｜`
        + `${fmt(r.chars)} 字｜图片 ${r.images} 张｜方式 ${esc(r.method)}｜预计消化耗时 ${clock(estFor(r))}</div>`
        + `<div class="kv">标题：${esc(r.title || "")}${r.author ? "｜作者：" + esc(r.author) : ""}</div>`
        + `<div class="quote">${esc(r.preview || "")}…</div>`
      : `<div class="noline">不可读｜${esc(r.error || "")}</div><div class="quote">${esc(r.hint || "")}</div>`;
    st.textContent = "";
    act(r.ok ? "eager" : "think", r.ok ? "这条能读，要不要直接消化" : "这条读不到，把正文粘进来最快", 3600);
  } catch (e) { st.textContent = ""; box.innerHTML = `<div class="noline">探测失败：${esc(String(e))}</div>`; }
  finally { $("#previewBtn").disabled = false; }
}

async function doDigest() {
  const url = $("#digestUrl").value.trim(), text = $("#digestText").value.trim();
  if (!url && !text) return toast("填一条链接，或者把正文粘进来");
  const btn = $("#digestBtn"), st = $("#digestStatus");
  btn.disabled = true;
  st.textContent = "";
  act("think", "我先把它取回来，再决定长几张卡", 4200);
  $("#digestResult").classList.add("hidden");
  const est = estFor({ kind: STATE.lastKind || (/douyin|bilibili|xiaohongshu|youtu/.test(url) ? "video" : url ? "web" : "text") });
  startProg("消化开始", est);
  try {
    const r = await api("/api/digest", {
      method: "POST",
      body: JSON.stringify({ url, text, compile: $("#digestCompile").checked })
    });
    stopProg(r.ok);
    if (!r.ok) {
      $("#previewResult").hidden = false;
      $("#previewResult").innerHTML = `<div class="noline">消化失败｜${esc(r.error || "")}</div>`
        + `<div class="quote">${esc(r.hint || "")}</div>`;
      toast("消化失败：" + (r.error || "未知原因"));
      act("idle");
      return;
    }
    st.textContent = `完成：${esc(r.platform)}｜${esc(r.method)}｜已写入 ${esc(r.clip)}` + (r.note ? "｜" + esc(r.note) : "");
    $("#digestOut").innerHTML = renderMD(r.digest || "");
    renderNewCards(r);
    $("#digestResult").classList.remove("hidden");
    $("#digestUrl").value = ""; $("#digestText").value = "";
    STATE.lastKind = "";
    await loadAll();
    act("celebrate", "卡长好了，在下面这一栏", 4200);
    celebrate(.5);
    toast("已入库");
    $("#digestResult").scrollIntoView({ behavior: REDUCE ? "auto" : "smooth", block: "start" });
  } catch (e) { stopProg(false); st.textContent = "出错：" + e.message; }
  finally { btn.disabled = false; }
}

function renderNewCards(r) {
  const el = $("#digestCards"), cards = r.cards || [], dropped = r.dropped || [];
  /* 张数只写在面板抬头这一处；「拦下几条」由下面那份清单自己数，别报两遍 */
  $("#digestResultCnt").textContent = cards.length + " 张卡";
  const rawRow = r.clip
    ? `<div class="ncrow"><span class="nclab">原料</span>`
      + `<button class="chip nc raw" data-path="${esc(r.clip)}" title="点开就能读原文，右栏「在这份材料下补一张卡」是唯一的补卡入口">`
      + `${esc(r.title || r.clip.split("/").pop())}<i>${esc(r.media_type || "")} · 约 ${esc(r.est_min || 1)} 分钟</i></button></div>`
    : "";
  if (!cards.length) {
    el.innerHTML = rawRow + `<p class="tip">这次没有建卡${dropped.length ? `：<br>${dropped.map(esc).join("<br>")}` : ""}</p>`
      + (r.note && !dropped.length ? `<p class="tip">${esc(r.note)}</p>` : "");
    return;
  }
  const groups = {};
  cards.forEach(c => { (groups[c.label] = groups[c.label] || []).push(c); });
  el.innerHTML = `<p class="okline">这次长成的卡如下，同名卡只补不覆盖；原料写在原处没动。</p>`
    + rawRow
    + ["概念", "连接", "问题"].filter(k => groups[k]).map(k =>
      `<div class="ncrow"><span class="nclab">${k}</span>`
      + groups[k].map(c => `<button class="chip nc${c.state === "exists" ? " dim" : ""}" data-path="${esc(c.path)}"
          title="${esc(c.path)} · ${esc(c.state)}">${esc(c.name)}<i>${c.state === "appended" ? "补" : c.state === "exists" ? "已有" : "新"}</i></button>`).join("")
      + "</div>").join("")
    + (dropped.length
      ? `<p class="tip">拦下没建卡的（互动数据 / 项目身份 / 接不上旧卡）：<br>${dropped.map(esc).join("<br>")}</p>` : "")
    + (r.questions
      ? `<p class="tip">同时排了 ${r.questions} 条复习条目。</p>`
      : '<p class="noline">这份消化稿没写「我的疑问」，复习队列里不会有条目 —— 打开原料点「按原文重新生成」补一次。</p>');
}

/* ---------------------------------------------------------------- 模型配置 */

/* cookies/ 下是文件名（zhihu.txt），页面上要说人能看懂的平台名 */
const SITE_CN = {
  zhihu: "知乎", douyin: "抖音", xiaohongshu: "小红书", bilibili: "B站",
  instagram: "Instagram", facebook: "Facebook", weixin: "公众号"
};
const siteCN = s => SITE_CN[String(s || "").replace(/\.txt$/, "").toLowerCase()] || String(s || "").replace(/\.txt$/, "");

async function loadModelConfig() {
  let c;
  try { c = await api("/api/config"); } catch (e) { return; }
  if (!c || c.error) return;
  $("#cfgBase").value = c.base_url || "";
  $("#cfgModel").value = c.model || "";
  $("#cfgTemp").value = c.temperature != null ? c.temperature : "";
  $("#cfgMax").value = c.maxTokens != null ? c.maxTokens : "";
  $("#cfgFrames").value = c.maxFrames != null ? c.maxFrames : "";
  $("#cfgKey").value = "";
  $("#cfgKey").placeholder = c.hasKey ? `已设置 ${c.api_key}（留空表示不改）` : "只存本机 config.json，不外传";
  /* 主库路径顶栏 #liveTag 已经在报，这里不再重复一遍 */
  $("#cfgKeySource").textContent = `当前 key 来源：${c.keySource || "无"}｜提示词规范 skill/PROMPTS.md：${c.skill ? "在" : "缺"}`;
  /* #cfgStatus 只留给「保存 / 测试」这类动作反馈：静态的有 key / 没 key 状态上面那行已经说了 */
  $("#cfgStatus").textContent = "";

  /* cookie 的账只在 Cookie 面板这一处报：新装机器这里是空的，数量随本机文件而定，不是固定值 */
  const ck = c.cookies || [];
  $("#ckList").innerHTML = `<div class="ck-sum">本机已登记 <b>${ck.length}</b> 个平台`
    + (ck.length ? "：" + ck.map(k => esc(siteCN(k.site))).join("、") : "（新装的环境这里就是空的）") + "</div>"
    + (ck.length ? ck.map(k =>
      `<div><b>${esc(siteCN(k.site))}</b> ｜ ${k.pairs || "?"} 对 ｜ ${k.bytes} 字节 ｜ ${k.updated || ""}`
      + `<br>${k.logged_in_guess ? "键名里看到登录标记" : '<span class="miss">没看到登录标记</span>'}`
      + (k.missing_keys && k.missing_keys.length ? `（缺 ${esc(k.missing_keys.join("、"))}）` : "")
      + ` ｜ UA：${esc(k.ua || "内置指纹")}</div>`).join("") : "");

  /* 「不经过第三方」页顶那条管道已经说了；这里只补它没说的：模型地址在哪配 */
  $("#pipeNote").innerHTML = "模型地址取自下面的「模型配置」；不配也能跑，提炼退化成规则抽取。";
}

async function saveModelConfig() {
  const body = {
    base_url: $("#cfgBase").value.trim(),
    model: $("#cfgModel").value.trim(),
    temperature: $("#cfgTemp").value.trim(),
    maxTokens: $("#cfgMax").value.trim()
  };
  const frames = $("#cfgFrames").value.trim();
  if (frames) body.maxFrames = frames;
  if ($("#cfgKey").value.trim()) body.api_key = $("#cfgKey").value.trim();
  let r;
  try { r = await api("/api/config", { method: "POST", body: JSON.stringify(body) }); }
  catch (e) { r = { error: e.message }; }
  $("#cfgStatus").textContent = r.error ? "保存失败：" + r.error : "已写入本机 config.json";
  await loadModelConfig();
  if (!r.error) toast("配置已保存到本机");
}

async function testModel() {
  const st = $("#cfgStatus");
  st.textContent = "测试中…";
  let r;
  try { r = await api("/api/llm/test", { method: "POST", body: "{}" }); }
  catch (e) { st.textContent = "测试失败：" + e.message; return; }
  st.textContent = r.ok ? `连通：${esc(r.model)}（${r.ms}ms）回显 ${esc(r.echo || "")}`
    : `不通：${esc(r.error || "失败")} — ${esc(r.hint || "")}`;
  act(r.ok ? "peace" : "think", r.ok ? "模型这边通了，可以放心消化" : "模型没通，先检查接口地址和 key", 3600);
}

/* ---------------------------------------------------------------- cookie 通行证 */

async function saveCookie() {
  const site = $("#ckSiteSel").dataset.value, cookie = $("#ckVal").value.trim();
  if (!cookie) return toast("先粘贴 Cookie 内容");
  let r;
  try { r = await api("/api/cookie", { method: "POST", body: JSON.stringify({ site, cookie, ua: $("#ckUa").value.trim() }) }); }
  catch (e) { r = { ok: false, error: e.message }; }
  if (!r.ok) { $("#ckStatus").textContent = "失败：" + (r.error || "未写入"); return; }
  $("#ckStatus").textContent = `已存 ${esc(r.site)}：识别为「${esc(r.format)}」· ${r.pairs} 对 · ${r.bytes} 字节 · ${esc(r.saved_at)}`;
  const out = $("#ckCheckOut");
  out.hidden = false;
  out.innerHTML = `<div class="${r.logged_in_guess ? "okline" : "noline"}">`
    + (r.logged_in_guess ? "键名里看到登录标记" : "这份里没看到登录标记键：" + esc(r.warning || "")) + "</div>"
    + `<div class="kv">${esc(r.note || "")}</div>`
    + (r.missing_keys && r.missing_keys.length ? `<div class="kv">还缺：${esc(r.missing_keys.join("、"))}</div>` : "");
  $("#ckVal").value = "";
  await loadModelConfig();
}

async function checkCookie() {
  const site = $("#ckSiteSel").dataset.value, st = $("#ckStatus"), box = $("#ckCheckOut");
  st.textContent = "体检中…（会真的请求一次站点）";
  $("#ckCheck").disabled = true;
  let r;
  try { r = await api("/api/cookie/check", { method: "POST", body: JSON.stringify({ site }) }); }
  catch (e) { r = { ok: false, site, error: e.message }; }
  $("#ckCheck").disabled = false;
  st.textContent = "";
  box.hidden = false;
  box.innerHTML = `<div class="${r.ok ? "okline" : "noline"}">${r.ok ? "可用" : "不可用"}｜${esc(r.verdict || r.error || "无结论")}</div>`
    + (r.error && !r.verdict ? "" : `<div class="kv">站点：${esc(r.site || site)}｜${r.pairs || "?"} 对｜UA：${esc(r.ua || "内置指纹")}</div>`)
    + (r.http ? `<div class="kv">HTTP ${esc(r.http)}｜${esc(r.bytes || 0)} 字节｜命中标记：${esc((r.markers || []).join(" / ") || "无")}</div>` : "")
    + (r.key_note ? `<div class="quote">${esc(r.key_note)}</div>` : "");
  act(r.ok ? "peace" : "think", r.ok ? "这份 cookie 能用" : "这份不太行，换一条真实链接「先探测」更准", 3600);
}

async function dropCookie() {
  const site = $("#ckSiteSel").dataset.value;
  const v = await ask({
    title: "清除本机 cookie",
    body: `<p>将删除本机 <code>cookies/${esc(site)}</code>（连同一起存的 UA），对应平台「${esc(siteCN(site))}」。只影响这台机器上的读取凭证，不动库里的内容。</p>`,
    okText: "清除", danger: true
  });
  if (v !== "yes") return;
  let r;
  try { r = await api("/api/cookie/drop", { method: "POST", body: JSON.stringify({ site }) }); }
  catch (e) { r = { ok: false, error: e.message }; }
  $("#ckStatus").textContent = r.ok ? "已删除本机该站点的 cookie（连同 UA）" : "失败：" + (r.error || "未删除");
  $("#ckCheckOut").hidden = true;
  await loadModelConfig();
}

/* ---------------------------------------------------------------- 上传入库 */

let PICKED = null;

function setPicked(f) {
  PICKED = f || null;
  const nm = $("#upName");
  nm.textContent = PICKED ? `${PICKED.name} · ${(PICKED.size / 1024).toFixed(0)} KB` : "";
  $("#upBtn").disabled = !PICKED;
  $("#drop").classList.toggle("has", !!PICKED);
  const bar = $("#upBar");
  bar.classList.add("hidden");
  bar.value = 0;
}

const b64From = file => new Promise((res, rej) => {
  const fr = new FileReader();
  fr.onload = () => res(String(fr.result).split(",").pop());
  fr.onerror = rej;
  fr.readAsDataURL(file);
});

async function uploadFile() {
  const f = PICKED || $("#upFile").files[0];
  if (!f) return toast("先选一个文件");
  const st = $("#upStatus"), bar = $("#upBar");
  const av = /\.(mp4|mov|m4a|mp3|wav|webm)$/i.test(f.name);
  st.textContent = (f.size > 8 * 1024 * 1024 ? "文件较大，读取中…" : "读取中…");
  bar.classList.remove("hidden");
  bar.value = 12;
  startProg(av ? "本地转写 + 消化" : "读取 + 消化", av ? 240 : 60);
  try {
    const b64 = await b64From(f);
    bar.value = 55;
    const r = await api("/api/import", {
      method: "POST",
      body: JSON.stringify({ filename: f.name, base64: b64, compile: $("#digestCompile").checked })
    });
    bar.value = 92;
    stopProg(r.ok);
    if (!r.ok) { st.textContent = "失败：" + (r.error || "") + " " + (r.hint || ""); bar.classList.add("hidden"); return; }
    st.textContent = "完成：已写入 " + r.clip;
    $("#digestOut").innerHTML = renderMD(r.digest || "");
    renderNewCards(r);
    $("#digestResult").classList.remove("hidden");
    setPicked(null);
    bar.classList.add("hidden");
    await loadAll();
    act("celebrate", "这份读完了，卡也长好了", 4000);
    toast("已入库");
  } catch (e) { stopProg(false); st.textContent = "失败：" + e; bar.classList.add("hidden"); }
}

/* ---------------------------------------------------------------- 图谱
   v2 的三个改动：① 不许平移——图定死在框里，只留缩放和旋转，空白处拖动 = 转整张图，
   背面和后面被挡的点才转得出来；② 鼠标靠近某个点，整张图先停住（阻尼逼近，不硬闸），
   手要点的目标不能还在飘；③ 聚焦态把邻居的名字全打出来。 */

const graph = {
  cv: $("#graph"), ctx: null, nodes: [], edges: [], adj: new Map(), deg: new Map(),
  on: false, spin: false, focus: null, hover: null, drag: null, moved: 0, alpha: 1,
  cam: { k: 1, tk: 1, rot: { x: -0.16, y: 0.4 }, prx: 0, pry: 0 },
  fz: 1, mouse: { x: 0, y: 0 }, t: 0, w: 0, h: 0,

  init() {
    const self = this;
    this.ctx = this.cv.getContext("2d");
    this.cv.addEventListener("mousedown", e => self.down(e));
    this.cv.addEventListener("touchstart", e => {
      if (e.touches.length !== 1) return;
      self.down({ clientX: e.touches[0].clientX, clientY: e.touches[0].clientY, preventDefault() { } });
    }, { passive: true });
    addEventListener("mousemove", e => self.move(e), { passive: true });
    addEventListener("touchmove", e => {
      if (self.drag && e.touches.length === 1) self.move({ clientX: e.touches[0].clientX, clientY: e.touches[0].clientY });
    }, { passive: true });
    addEventListener("mouseup", () => self.up());
    addEventListener("touchend", () => self.up());
    this.cv.addEventListener("wheel", e => {
      e.preventDefault();
      /* 不允许平移，所以缩放只朝画布中心拧：朝指针拧会把图推出框外且拉不回来 */
      this.cam.tk = Math.max(.18, Math.min(3.2, this.cam.tk * (e.deltaY < 0 ? 1.14 : .88)));
      this.zoomed = true;
    }, { passive: false });
    this.cv.addEventListener("dblclick", e => {
      const o = this.pick(this.pt(e));
      if (o) openCard(o.id);
    });
    this.cv.addEventListener("mouseleave", () => { this.hover = null; });
    addEventListener("resize", () => { if (this.on) this.size(); });
  },

  build() {
    const g = STATE.graph;
    if (!g || !this.ctx) return;
    this.nodes = g.nodes.map((n, i) => {
      const a = i / Math.max(1, g.nodes.length) * 6.283;
      const r = 200 + (i % 6) * 26;
      return {
        id: n.id, label: n.label, kind: n.kind, level: n.level,
        x: Math.cos(a) * r, y: Math.sin(a) * r * .72, z: ((i * 53) % 100) / 100 * 300 - 150,
        vx: 0, vy: 0, vz: 0, fx: 0, fy: 0, fz: 0
      };
    });
    const idx = {};
    this.nodes.forEach((n, i) => { idx[n.id] = i; });
    this.edges = (g.links || []).map(l => [idx[l.source], idx[l.target]])
      .filter(e => e[0] != null && e[1] != null);
    this.adj = new Map();
    this.deg = new Map();
    this.nodes.forEach(n => this.adj.set(n.id, new Set()));
    this.edges.forEach(([a, b]) => {
      const na = this.nodes[a], nb = this.nodes[b];
      this.adj.get(na.id).add(nb.id);
      this.adj.get(nb.id).add(na.id);
      this.deg.set(na.id, (this.deg.get(na.id) || 0) + 1);
      this.deg.set(nb.id, (this.deg.get(nb.id) || 0) + 1);
    });
    this.alpha = 1;
    for (let i = 0; i < 140; i++) this.step(true);
    this.focus = null;
    this.hideHud();
    this.size();
    this.fitToView();
    this.loop();
  },

  fitToView() {
    if (!this.nodes.length || !this.w) return;
    let maxR = 1;
    this.nodes.forEach(n => { maxR = Math.max(maxR, Math.abs(n.x), Math.abs(n.y), Math.abs(n.z) * .55); });
    /* 名字画在点的右侧，最宽能伸出去 130px —— 留出来，不然最右一列的点会被裁掉 */
    const room = Math.min(this.w, this.h) * .42, side = this.w * .5 - 132;
    this.cam.tk = Math.max(.2, Math.min(1.5, Math.min(room, side * .95) / maxR));
  },

  setActive(on) { this.on = on; if (on) { this.size(); this.loop(); } },
  recolor() { if (this.on) this.paint(); },

  size() {
    if (!this.ctx) return;
    const r = this.cv.getBoundingClientRect(), dpr = devicePixelRatio || 1;
    this.cv.width = Math.max(1, r.width * dpr);
    this.cv.height = Math.max(1, r.height * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.w = r.width; this.h = r.height;
    if (!this.zoomed) this.fitToView();
  },

  step(silent) {
    const N = this.nodes, E = this.edges;
    if (!N.length) return;
    /* 冻结系数：靠近点的时候物理力也一起降下来，目标才不会一边受力一边抖 */
    const a = this.alpha * (0.1 + 0.9 * this.fz);
    const REP = 30000, SPRING = .018, REST = 175, GRAV = .0105;
    for (let i = 0; i < N.length; i++) { N[i].fx = 0; N[i].fy = 0; N[i].fz = 0; }
    for (let i = 0; i < N.length; i++) {
      const p = N[i];
      for (let j = i + 1; j < N.length; j++) {
        const q = N[j];
        const dx = q.x - p.x, dy = q.y - p.y, dz = q.z - p.z;
        const d2 = dx * dx + dy * dy + dz * dz || 1;
        const d = Math.sqrt(d2), f = REP / d2 * a;
        const ux = dx / d, uy = dy / d, uz = dz / d;
        p.fx -= ux * f; p.fy -= uy * f; p.fz -= uz * f;
        q.fx += ux * f; q.fy += uy * f; q.fz += uz * f;
      }
    }
    E.forEach(([ia, ib]) => {
      const p = N[ia], q = N[ib];
      const dx = q.x - p.x, dy = q.y - p.y, dz = q.z - p.z;
      const d = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1;
      const f = (d - REST) * SPRING * a;
      const ux = dx / d, uy = dy / d, uz = dz / d;
      p.fx += ux * f; p.fy += uy * f; p.fz += uz * f;
      q.fx -= ux * f; q.fy -= uy * f; q.fz -= uz * f;
    });
    N.forEach(p => {
      if (this.drag && this.drag.n === p) return;
      p.fx -= p.x * GRAV * a; p.fy -= p.y * GRAV * a; p.fz -= p.z * GRAV * a * .6;
      p.vx = (p.vx + p.fx) * .86; p.vy = (p.vy + p.fy) * .86; p.vz = (p.vz + p.fz) * .86;
      p.x += Math.max(-14, Math.min(14, p.vx));
      p.y += Math.max(-14, Math.min(14, p.vy));
      p.z += Math.max(-8, Math.min(8, p.vz));
    });
    if (!silent) this.alpha = Math.max(.055, this.alpha * .9965);
  },

  project(n) {
    const c = this.cam;
    const rx = c.rot.x + c.prx, ry = c.rot.y + c.pry;
    const cx = this.w / 2, cy = this.h / 2;
    const cr = Math.cos(ry), sr = Math.sin(ry);
    const x1 = n.x * cr - n.z * sr, z1 = n.x * sr + n.z * cr;
    const cp = Math.cos(rx), sp = Math.sin(rx);
    const y1 = n.y * cp - z1 * sp, z2 = n.y * sp + z1 * cp;
    /* 透视分母必须夹住：z2 一旦小于 -620，半径会变负，arc 直接抛异常整张图卡死 */
    const persp = 620 / Math.max(150, 620 + z2);
    return {
      x: cx + x1 * persp * c.k, y: cy + y1 * persp * c.k,
      r: Math.max(.8, (3 + Math.min(10, (this.deg.get(n.id) || 0) * 1.25)) * persp * c.k),
      z: z2, p: persp
    };
  },

  paint() {
    if (!this.ctx || !this.w) return;
    const g = this, cx = this.ctx, c = this.cam;
    /* 冻结是逼近不是硬停：靠近点的时候速度自己滑到 0，看起来像她屏住呼吸 */
    const want = (g.hover || g.drag) ? 0 : 1;
    g.fz += (want - g.fz) * .07;
    c.rot.y += (g.spin ? .0042 : .0011) * g.fz;
    c.prx += ((g.mouse.y * .05 - .16) * g.fz - c.prx) * .05;
    c.pry += ((g.mouse.x * .07) * g.fz - c.pry) * .05;
    c.k += (c.tk - c.k) * .12;

    g.step();
    cx.clearRect(0, 0, g.w, g.h);
    const col = {
      concept: cssv("--accent"), connection: cssv("--wood"), question: cssv("--blush"),
      raw: cssv("--ink-3"), system: cssv("--ink-2"), page: cssv("--line")
    };
    const focusNode = g.focus || (g.drag && g.drag.n) || g.hover;
    const nb = focusNode ? g.adj.get(focusNode.id) : null;
    const near = n => !focusNode || n === focusNode || (nb && nb.has(n.id));
    const labels = !focusNode ? "auto" : "all";

    g.edges.forEach(([ia, ib]) => {
      const na = g.nodes[ia], nbb = g.nodes[ib];
      const a = g.project(na), b = g.project(nbb);
      const tied = focusNode && (na === focusNode || nbb === focusNode);
      cx.globalAlpha = focusNode ? (tied ? .85 : .05) : Math.max(.17, Math.min(.52, (a.p + b.p) / 2 - .45));
      cx.strokeStyle = tied ? (col[na === focusNode ? nbb.kind : na.kind] || col.concept) : cssv("--ink-3");
      cx.lineWidth = tied ? 1.7 : 1;
      cx.beginPath(); cx.moveTo(a.x, a.y); cx.lineTo(b.x, b.y); cx.stroke();
    });

    g.nodes.map(n => ({ n, s: g.project(n) })).sort((x, y) => y.s.z - x.s.z).forEach(({ n, s }) => {
      const on = near(n), hot = n === focusNode || n === g.hover;
      const c2 = col[n.kind] || col.page;
      cx.globalAlpha = on ? Math.max(.35, Math.min(1, s.p * 1.1)) : .09;
      if (hot) {
        cx.beginPath(); cx.arc(s.x, s.y, s.r + 13, 0, 6.283);
        cx.fillStyle = c2; cx.globalAlpha = .14; cx.fill();
        cx.globalAlpha = on ? Math.max(.35, Math.min(1, s.p * 1.1)) : .09;
      }
      cx.beginPath(); cx.arc(s.x, s.y, s.r, 0, 6.283);
      cx.fillStyle = c2; cx.fill();
      cx.strokeStyle = cssv("--bg-page"); cx.globalAlpha = on ? .85 : .2;
      cx.lineWidth = 1.2; cx.stroke();
      /* 聚焦时把这一片的名字全打出来（他要的就是这个）；全景只给大点和放大后的点 */
      const wantLabel = on && (labels === "all" || hot || s.r > 6.4 || c.k > 1.7);
      if (wantLabel) {
        cx.globalAlpha = Math.min(1, s.p * 1.3);
        const cap = innerWidth < 700 ? 9 : (focusNode ? 18 : 13);
        const label = n.label.length > cap ? n.label.slice(0, cap - 1) + "…" : n.label;
        cx.font = (hot ? "600 " : "") + '10.5px "Microsoft YaHei UI", sans-serif';
        const tw = cx.measureText(label).width;
        if (hot || focusNode || s.r > 6.2) {
          cx.fillStyle = cssv("--glass");
          cx.beginPath();
          if (cx.roundRect) cx.roundRect(s.x + s.r + 4, s.y - 8.5, tw + 10, 17, 8);
          else cx.rect(s.x + s.r + 4, s.y - 8.5, tw + 10, 17);
          cx.fill();
          cx.strokeStyle = cssv("--line"); cx.lineWidth = 1; cx.stroke();
        }
        cx.fillStyle = hot ? c2 : cssv("--ink");
        cx.fillText(label, s.x + s.r + 9, s.y + 3.5);
      }
    });
    cx.globalAlpha = 1;
    /* 邻居数归聚焦卡 #gfMeta，这里只报全图规模 —— 同一数字不写两遍 */
    $("#graphStat").textContent = `${g.nodes.length} 个节点 · ${g.edges.length} 条连接`
      + (g.fz < .4 ? " · 已停住" : "");
  },

  loop() {
    const self = this;
    if (this._raf) return;
    (function t(now) {
      if (!self.on) { self._raf = null; return; }
      self._raf = requestAnimationFrame(t);
      self.t = now || performance.now();
      self.paint();
    })(performance.now());
  },

  pt(e) { const r = this.cv.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; },
  pick(p) {
    let best = null, bd = 1e9;
    this.nodes.forEach(n => {
      const s = this.project(n);
      const d = Math.hypot(p.x - s.x, p.y - s.y);
      if (d < Math.max(15, s.r * 1.8) && d < bd) { bd = d; best = n; }
    });
    return best;
  },

  down(e) {
    const p = this.pt(e);
    this.moved = 0;
    const o = this.pick(p);
    /* 空白处不再是平移：记下起始角度，交给 rotate */
    this.drag = o ? { n: o, px: p.x, py: p.y }
      : { rot: 1, px: p.x, py: p.y, ax: this.cam.rot.x, ay: this.cam.rot.y };
    this.alpha = Math.max(this.alpha, .55);
  },
  move(e) {
    if (!this.on) return;
    const p = this.pt(e);
    this.mouse = { x: (p.x / this.w - .5) * 2, y: (p.y / this.h - .5) * 2 };
    const d = this.drag;
    if (!d) {
      const h = this.pick(p);
      if (h !== this.hover) { this.hover = h; this.cv.style.cursor = h ? "grab" : "default"; }
      return;
    }
    const dx = p.x - d.px, dy = p.y - d.py;
    this.moved += Math.abs(dx) + Math.abs(dy);
    if (d.rot) {
      /* 拖动方向和转的方向一致：往右拖 = 整张图往右转，能看见背面那些点 */
      this.cam.rot.y = d.ay + dx * .0075;
      this.cam.rot.x = Math.max(-1.15, Math.min(1.15, d.ax + dy * .006));
    } else {
      const s = this.project(d.n);
      const k = (s.p * this.cam.k) || 1;
      d.n.x += dx / k; d.n.y += dy / k;
      d.n.vx = d.n.vy = 0;
    }
    d.px = p.x; d.py = p.y;
  },
  up() {
    const was = this.drag;
    this.drag = null;
    if (!was) return;
    if (was.rot) {
      if (this.moved < 6) this.clearFocus();
      this.cv.style.cursor = this.hover ? "grab" : "default";
      return;
    }
    const n = was.n;
    if (this.moved >= 6) {
      this.focus = n; this.showHud(n); this.alpha = Math.max(this.alpha, .45);
      this.cv.style.cursor = "grab";
      return;
    }
    /* 聚焦态里再点同一颗 = 打开那张卡；点邻居 = 换聚焦；空态第一下只是聚焦 */
    if (this.focus && n === this.focus) { openCard(n.id); return; }
    if (this.focus && this.adj.get(this.focus.id).has(n.id)) { openCard(n.id); return; }
    this.focus = n; this.showHud(n); this.alpha = Math.max(this.alpha, .5);
    this.cv.style.cursor = "grab";
  },
  clearFocus() { this.focus = null; this.hideHud(); },
  showHud(n) {
    const c = STATE.byPath[n.id] || {}, nb = this.adj.get(n.id);
    const hud = $("#gfocus");
    hud.hidden = false;
    /* 这里绝对不能再往 hud 上写 data-path：整个文档有一条 [data-path] 的委托点击，
       写了它 = 点「取消聚焦」也会先命中打开卡片。路径只存在 graph.focus 里。 */
    $("#gfTitle").textContent = n.label;
    $("#gfMeta").innerHTML = [
      KIND_CN[n.kind] || n.kind,
      c.status || "未填写状态",
      (nb ? nb.size : 0) + " 个邻居",
      c.platform ? "来源 " + c.platform : "",
      (c.links ? `${c.links.length} 出链` : "")
    ].filter(Boolean).map(esc).join(" ｜ ");
    this.cv.classList.add("focus");
  },
  hideHud() { $("#gfocus").hidden = true; this.cv.classList.remove("focus"); },
  fit() {
    this.zoomed = false;
    this.cam.rot = { x: -0.16, y: 0.4 };
    this.cam.prx = this.cam.pry = 0;
    this.fitToView();
    this.alpha = .9;
    this.clearFocus();
  }
};

/* ---------------------------------------------------------------- 命令面板 */

let paletteOpen = false, palSel = 0, palHits = [];

function pal(on) {
  paletteOpen = on;
  $("#palette").classList.toggle("on", on);
  $("#scrim").classList.toggle("on", on);
  if (on) { $("#palInput").focus(); $("#palInput").select(); palSearch(""); }
}

async function palSearch(term) {
  let hits = [];
  if (term) {
    try { hits = (await api("/api/search?q=" + encodeURIComponent(term))).results || []; }
    catch (e) { toast(e.message); return; }
  } else {
    hits = STATE.cards.slice().sort((a, b) => b.mtime - a.mtime).slice(0, 12)
      .map(c => ({ path: c.path, title: c.title, kind: c.kind, kindLabel: c.kindLabel, snippet: c.summary }));
  }
  palHits = hits.slice(0, 14);
  palSel = 0;
  $("#palResults").innerHTML = palHits.map((c, i) =>
    `<button data-i="${i}" class="${i === 0 ? "sel" : ""}">
      <span class="kind-dot" style="--kind:${tierKindColor(c.kind)}"></span>
      <span class="t">${esc(c.title)}</span>
      <span class="k">${KIND_CN[c.kind] || c.kindLabel || ""}</span></button>`).join("")
    || `<div class="empty">没有匹配「${esc(term)}」的卡</div>`;
}

/* ---------------------------------------------------------------- 正文宽度 / 进场 / 卡片倾斜 */

function applyWidth() {
  const w = store.get("kb.width") || "1";
  const body = $("#readBody");
  if (body) body.className = "prose md w" + w;
  $$("#rdWidth button").forEach(b => b.classList.toggle("on", b.dataset.w === w));
  $("#rdWidth").dataset.w = w;
  requestAnimationFrame(layoutMemos);      /* 正文宽度一变，批注对齐的段就挪位置了 */
}
addEventListener("resize", () => requestAnimationFrame(layoutMemos));

let RO = null;
function reveal() {
  const els = $$(".seen:not(.in)");
  if (!els.length) return;
  if (!("IntersectionObserver" in window) || REDUCE) { els.forEach(e => e.classList.add("in")); return; }
  if (!RO) RO = new IntersectionObserver(en => en.forEach(x => {
    if (x.isIntersecting) { x.target.classList.add("in"); RO.unobserve(x.target); }
  }), { rootMargin: "0px 0px -8% 0px" });
  els.forEach(e => RO.observe(e));
  setTimeout(() => $$(".seen:not(.in)").forEach(e => {
    if (e.getBoundingClientRect().top < innerHeight) e.classList.add("in");
  }), 1400);
}

function tilt() {
  if (matchMedia("(pointer: coarse)").matches) return;
  $$(".kcard").forEach(el => {
    if (el._tilt) return;
    el._tilt = 1;
    el.addEventListener("mousemove", e => {
      const r = el.getBoundingClientRect();
      const px = (e.clientX - r.left) / r.width, py = (e.clientY - r.top) / r.height;
      el.style.setProperty("--cx", px * 100 + "%");
      el.style.setProperty("--cy", py * 100 + "%");
      if (REDUCE) return;
      el.style.transform = `perspective(760px) rotateY(${((px - .5) * 5).toFixed(2)}deg) rotateX(${((.5 - py) * 5).toFixed(2)}deg) translateY(-3px)`;
    });
    el.addEventListener("mouseleave", () => { el.style.transform = ""; });
  });
}

/* ---------------------------------------------------------------- 接线 */

/* 就地高亮 / 批注 */
const COARSE = matchMedia("(hover: none)").matches;
let blkHide = null, annoHide = false, blkPinned = false;
function hideBlockTools() { if (!annoHide && !blkPinned) $("#blkTools").hidden = true; }
function scheduleHide() { clearTimeout(blkHide); blkHide = setTimeout(hideBlockTools, 260); }

function bind() {
  $$("#nav button").forEach(b => { b.onclick = () => go(b.dataset.view); });
  $$("[data-goto]").forEach(el => {
    if (el.closest("#nav")) return;
    el.addEventListener("click", () => {
      if (el.dataset.goto === "review") return focusReview();
      go(el.dataset.goto);
    });
  });

  $("#omniBtn").onclick = () => pal(true);
  $("#palInput").addEventListener("input", e => palSearch(e.target.value.trim()));
  $("#palResults").addEventListener("click", e => {
    const b = e.target.closest("button[data-i]");
    if (!b) return;
    pal(false);
    openCard(palHits[+b.dataset.i].path);
  });

  $("#wardrobeBtn").onclick = () => toggleWardrobe(true);
  $("#wardrobeClose").onclick = () => toggleWardrobe(false);
  $("#scrim").onclick = () => { toggleWardrobe(false); pal(false); };
  $("#hangers").addEventListener("click", e => {
    const b = e.target.closest(".hanger");
    if (!b) return;
    applySuit(b.dataset.suit, true);
    toast("换装：" + b.querySelector("b").textContent);
    setTimeout(() => toggleWardrobe(false), 620);
  });

  /* 顶栏那颗「新建卡片」不另开一套表单：它把人送到当前材料（或最新一份原料）的阅读页，
     焦点落在右栏那唯一的补卡位上 */
  $("#newCardBtn").onclick = async () => {
    /* 新建卡片不再要求先「站在某一份材料上」：打开哪一张只是给下拉一个默认值，
       下拉里可以换成任意一份近期原料，也可以选「不挂原料」凭空记一条自己的主张。 */
    const raws = STATE.cards.filter(c => c.kind === "raw").sort((a, b) => b.mtime - a.mtime);
    const target = /^(raw|wiki)\//.test(STATE.currentCard || "") ? STATE.currentCard
      : (raws[0] ? raws[0].path : "");
    if (!target) { go("digest"); act("peace", "库里还没有材料，先丢一条链接进来", 3200); return; }
    await openCard(target);
    setTimeout(() => {
      $("#mcAsk").scrollIntoView({ behavior: REDUCE ? "auto" : "smooth", block: "center" });
      $("#mcAsk").focus();
    }, 200);
    act("peace", "选类型，写一句话，模型按本库格式补一张卡", 3000);
  };
  $("#focusReview").onclick = focusReview;
  $("#clearReadLog").onclick = async () => {
    const n = Object.keys(readLog()).length;
    if (!n) return toast("本机记录本来就是空的");
    const v = await ask({ title: "清空本机学习记录",
      body: `<p>要删掉这台浏览器记的 <b>${n}</b> 份阅读记录（读了哪几张、各停留多久）。</p>`
        + `<p class="tip">只动本机 localStorage：笔记文件、复习队列、卡片内容一个字都不改。`
        + `换设备 / 换浏览器本来就是从零开始，这一条口径在下面的说明里。</p>`,
      okText: "清空", cancelText: "留着", danger: true });
    if (v !== "yes") return;
    writeLog({});
    renderRecord(); paintBadge();
    toast("本机记录已清空");
  };
  $("#sumDay").onclick = () => studySummary("day");
  $("#sumWeek").onclick = () => studySummary("week");
  $("#strollBtn").onclick = () => { renderStroll(true); act("think", "抽三张，看到关联就补双链"); };
  $("#tagMore").onclick = () => {
    go("cards");
    setTimeout(() => {
      const p = $("#tagPanel");
      p.scrollIntoView({ behavior: REDUCE ? "auto" : "smooth", block: "center" });
      p.animate([{ boxShadow: "0 0 0 0 " + cssv("--accent-soft") }, { boxShadow: "0 0 0 14px transparent" }],
        { duration: 1000, easing: "ease-out" });
    }, 120);
  };

  $("#filter").addEventListener("input", e => { STATE.f.q = e.target.value.trim(); renderBrowse(); });
  $("#tagSearch").addEventListener("input", () => renderBrowse());
  $("#sortBy").onchange = e => { STATE.f.sort = e.target.value; renderBrowse(); };

  $("#graphSpin").onclick = function () { graph.spin = !graph.spin; this.classList.toggle("act--primary", graph.spin); };
  $("#graphFit").onclick = () => graph.fit();
  $("#gfOpen").onclick = () => { if (graph.focus) openCard(graph.focus.id); };
  $("#gfClear").onclick = () => graph.clearFocus();
  $("#gfocus").addEventListener("click", e => e.stopPropagation());

  $("#previewBtn").onclick = previewFetch;
  $("#digestBtn").onclick = doDigest;
  $("#cfgSave").onclick = saveModelConfig;
  $("#cfgTest").onclick = testModel;
  $("#ckSave").onclick = saveCookie;
  $("#ckCheck").onclick = checkCookie;
  $("#ckDrop").onclick = dropCookie;
  $("#upBtn").onclick = uploadFile;
  $("#upFile").onchange = e => setPicked(e.target.files[0]);
  const dz = $("#drop");
  ["dragenter", "dragover"].forEach(t => dz.addEventListener(t, e => { e.preventDefault(); dz.classList.add("over"); }));
  ["dragleave", "drop"].forEach(t => dz.addEventListener(t, e => { e.preventDefault(); dz.classList.remove("over"); }));
  dz.addEventListener("drop", e => {
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) { setPicked(f); uploadFile(); }
  });
  /* 补卡只有阅读页这一个入口：消化页那张是同一个模块的第二份，撤了 */
  $("#mcAdd").onclick = () => makeCard();
  $$("button", $("#mcMode")).forEach(b => b.onclick = () => setMode("mc", b.dataset.m));
  setMode("mc", "ai");

  /* 后退：优先用浏览器历史（hash 里带着卡片路径，一层层退得回去），没历史才回上一视图 */
  $("#backBtn").onclick = () => {
    if (history.length > 1) history.back();
    else go(prevView === "read" ? "dashboard" : prevView);
  };
  $("#rdEdit").onclick = toggleCardEdit;
  $("#rdSave").onclick = saveCurrentFile;
  $("#rdDel").onclick = dropCard;
  $("#rdRedo").onclick = redoDigest;
  $("#rdSync").onclick = syncCards;

  /* 就地高亮 / 批注 */
  const body = $("#readBody");
  body.addEventListener("mousemove", e => {
    if (!/^(wiki|raw)\//.test(STATE.currentCard)) return;
    if (!$("#rdText").classList.contains("hidden")) return;
    const el = e.target.closest("[data-ln]");
    clearTimeout(blkHide);
    if (el) showBlockTools(el); else scheduleHide();
  });
  body.addEventListener("mouseleave", scheduleHide);
  /* 触屏没有 hover：第一下 tap 只唤出段工具，第二下才落在按钮上 */
  body.addEventListener("click", e => {
    if (!COARSE) return;
    const el = e.target.closest("[data-ln]");
    if (!el || blkPinned || !/^(wiki|raw)\//.test(STATE.currentCard)) return;
    if (!$("#rdText").classList.contains("hidden")) return;
    e.preventDefault(); e.stopPropagation();
    clearTimeout(blkHide);
    showBlockTools(el);
    blkPinned = true;
  });
  document.addEventListener("pointerdown", e => {
    if (blkPinned && !e.target.closest("#blkTools")) { blkPinned = false; $("#blkTools").hidden = true; }
  });
  $("#blkTools").addEventListener("mouseenter", () => clearTimeout(blkHide));
  $("#blkTools").addEventListener("mouseleave", scheduleHide);
  $("#blkTools").addEventListener("click", e => {
    const b = e.target.closest("[data-blk]");
    if (!b || !blockFor) return;
    e.stopPropagation();
    if (b.dataset.blk === "mark") toggleMark(blockFor.ln, blockFor.le);
    else { annoHide = true; $("#blkTools").hidden = true; openAnnoEditor(blockFor.el); }
  });
  $("#annoText").addEventListener("keydown", e => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") $("#annoSave").click();
    if (e.key === "Escape") { annoHide = false; closeBlocks(); }
  });
  $$("#annoType button").forEach(b => b.onclick = () => {
    $$("#annoType button").forEach(x => x.classList.toggle("on", x === b));
    if (annoTarget) annoTarget.type = b.dataset.t;
  });
  $("#annoSave").onclick = async () => {
    const text = ($("#annoText").value || "").trim();
    if (!text) { $("#annoStatus").textContent = "批注内容为空"; return; }
    const type = ($("#annoType button.on") || { dataset: {} }).dataset.t || "note";
    $("#annoStatus").textContent = "写入中…";
    const v = annoTarget && annoTarget.mode === "edit"
      ? await replaceAnno(annoTarget.id, text, type)
      : await insertAnno(annoTarget.ln, annoTarget.le, text, type);
    annoHide = false;
    if (v && v !== "ok") $("#annoStatus").textContent = v;
    else $("#annoEdit").hidden = true;
  };
  $("#annoCancel").onclick = () => { annoHide = false; $("#annoEdit").hidden = true; };

  $("#tagAdd").addEventListener("keydown", async e => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const v = e.target.value.trim().replace(/^#/, "");
    if (!v) return;
    const now = chipTags();
    if (now.indexOf(v) >= 0) { e.target.value = ""; return toast("这个标签已经有了"); }
    await commitTags(now.concat(v));
    e.target.value = "";
  });

  $("#dlgYes").onclick = () => closeDlg("yes");
  $("#dlgNo").onclick = () => closeDlg("no");
  $("#dlg").addEventListener("click", e => { if (e.target === $("#dlg")) closeDlg("no"); });

  document.addEventListener("click", e => {
    /* 顺序要紧：小控件先于整行，不然点状态会连带打开卡片 */
    const st = e.target.closest("[data-status-for]");
    if (st) {
      e.preventDefault(); e.stopPropagation();
      if (st.dataset.setStatus) return setStatus(st.dataset.statusFor, st.dataset.setStatus);
      return openStatusMenu(st, st.dataset.statusFor);
    }
    const tick = e.target.closest("[data-done-task]");
    if (tick) {
      e.preventDefault(); e.stopPropagation();
      return doneTask(tick.dataset.doneTask, tick.dataset.undo === "1");
    }
    const del = e.target.closest("[data-anno-del]");
    if (del) {
      e.preventDefault(); e.stopPropagation();
      return deleteAnno(del.dataset.annoDel);
    }
    const ed = e.target.closest("[data-anno-edit]");
    if (ed) {
      e.preventDefault(); e.stopPropagation();
      const id = ed.dataset.annoEdit;
      const box = $("#annoEdit");
      const lines = bodyArr();
      const at = lines.findIndex(l => l.includes("`" + id + "`") && /^\s*>/.test(l));
      if (at < 0) return toast("这条批注已经不在了");
      let end = at;
      while (end + 1 < lines.length && (lines[end + 1].trim() === ">" || /^\s*>\s?\S/.test(lines[end + 1]))) end++;
      annoTarget = { mode: "edit", id, type: (/^>\s*\[!(\w+)\]/.exec(lines[at].trim()) || [, "note"])[1] };
      $("#annoText").value = lines.slice(at + 1, end + 1).map(l => l.replace(/^\s*>\s?/, "")).join("\n").trim();
      $("#annoStatus").textContent = "";
      $$("#annoType button").forEach(b => b.classList.toggle("on", b.dataset.t === annoTarget.type));
      annoHide = true;
      box.hidden = false;
      box.style.left = Math.max(8, Math.min(innerWidth - box.offsetWidth - 8, e.clientX - 150)) + "px";
      box.style.top = Math.max(60, Math.min(innerHeight - box.offsetHeight - 12, e.clientY + 12)) + "px";
      setTimeout(() => $("#annoText").focus(), 30);
      return;
    }
    const goto2 = e.target.closest("[data-anno-goto]");
    if (goto2) {
      e.preventDefault(); e.stopPropagation();
      const call = $('#readBody .cal.anno[data-anno="' + goto2.dataset.annoGoto + '"]');
      if (!call) return toast("这条批注已经不在这份文件里了");
      const anchor = memoAnchorOf(call);
      anchor.classList.add("flash");
      anchor.scrollIntoView({ block: "center", behavior: REDUCE ? "auto" : "smooth" });
      setTimeout(() => anchor.classList.remove("flash"), 2400);
      return;
    }
    const jump = e.target.closest("[data-jump]");
    if (jump) { e.preventDefault(); e.stopPropagation(); return locateInBody(jump.dataset.jump) || toast("没在正文里找到那一段"); }
    const dp = e.target.closest("[data-due-page]");
    if (dp) { e.preventDefault(); e.stopPropagation();
      duePage = Math.max(0, duePage + Number(dp.dataset.duePage)); return renderDue(); }
    const gl = e.target.closest("[data-glossary]");
    if (gl) { e.preventDefault(); e.stopPropagation(); return ask({
      title: "什么是双链",
      body: '<p>「双链」= <b>双向链接</b>（bidirectional link），Wikipedia 里叫 internal link，'
        + 'Obsidian / Roam / Notion 里写成 <code>[[另一张卡的名字]]</code>。</p>'
        + '<p>它做两件事：① 在这张卡上留一条指过去的线；② 系统同时反查——'
        + '被指那张卡的「反向链接」里自动出现这一张，<b>不用两边各写一次</b>。'
        + '这就是「双向」：写一条线，得到两个方向。</p>'
        + '<p class="tip">为什么它决定一张卡长实没长实：一条主张只有被别的主张指过一次，'
        + '才说明它接进了你的网络；零双链的卡等于又变成了一个孤立的收藏。</p>',
      okText: "知道了", cancelText: null }); }
    const tagDel = e.target.closest("[data-tag-del]");
    if (tagDel) {
      e.preventDefault(); e.stopPropagation();
      const now = chipTags().filter(t => t !== tagDel.dataset.tagDel);
      return commitTags(now);
    }
    const ts = e.target.closest("[data-t]");
    if (ts) {
      const v = document.getElementById("rdVideo");
      if (v) {
        v.currentTime = Number(ts.dataset.t) || 0;
        v.play().catch(() => { });
        v.scrollIntoView({ block: "center", behavior: REDUCE ? "auto" : "smooth" });
      } else toast("这一份没有本地视频，只能看到 " + (ts.dataset.s || "") + " 这个时间点");
      return;
    }
    if (e.target.closest(".tfx")) {
      const box = e.target.closest(".twx"), fx = e.target.closest(".tfx");
      const on = box.classList.toggle("spread");
      fx.textContent = on ? "退出放大" : "放大";
      return;
    }
    const wb = e.target.closest("#rdWidth button");
    if (wb) { store.set("kb.width", wb.dataset.w); applyWidth(); return; }
    const wiki = e.target.closest("[data-wiki]");
    if (wiki) {
      const name = wiki.dataset.wiki;
      const hit = STATE.byName[name];
      return hit ? openCard(hit.path)
        : toast(`「${name}」库里还没有这张卡，可以在右栏「补一张卡」里生成`);
    }
    const chipKind = e.target.closest("[data-k]");
    if (chipKind) { toggleFilter("kind", chipKind.dataset.k); return; }
    const fold = e.target.closest("footer a[data-kind]");
    if (fold) { toggleFilter("kind", fold.dataset.kind); go("cards"); return; }
    const tier = e.target.closest(".tier[data-kind]");
    if (tier) {
      if (tier.dataset.kind) { toggleFilter("kind", tier.dataset.kind); go("cards"); }
      else if (tier.dataset.goto === "review") focusReview();
      return;
    }
    const sv = e.target.closest("[data-s]");
    if (sv) { toggleFilter("status", sv.dataset.s); return; }
    const pl = e.target.closest("[data-p]");
    if (pl) { toggleFilter("plat", pl.dataset.p); return; }
    const tg = e.target.closest("[data-tag]");
    if (tg) { toggleFilter("tag", tg.dataset.tag); if (curView !== "cards") go("cards"); else renderBrowse(); return; }
    if (e.target.closest("#tagMode")) {
      STATE.tagMulti = !STATE.tagMulti;
      store.set("kb.tagMulti", STATE.tagMulti ? "1" : "0");
      if (!STATE.tagMulti && STATE.f.tag.length > 1) STATE.f.tag = STATE.f.tag.slice(0, 1);
      renderBrowse();
      return;
    }
    const unf = e.target.closest("[data-unfilter]");
    if (unf) { toggleFilter(unf.dataset.unfilter, unf.dataset.val); return; }
    if (e.target.closest("[data-clearall]")) {
      const f = STATE.f;
      f.kind = ""; f.q = ""; f.status = ""; f.plat = ""; f.tag = [];
      $("#filter").value = "";
      renderBrowse(); renderDashboard();
      return;
    }
    const ghost = e.target.closest("[data-ghost]");
    if (ghost) return toast(`「${ghost.dataset.ghost}」是幽灵链接：Obsidian 里有引用，库里还没有这张卡`);
    const path = e.target.closest("[data-path]");
    if (path) return openCard(path.dataset.path, path.dataset.anchor);
  });

  document.addEventListener("keydown", e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); pal(!paletteOpen); return; }
    if (e.key === "Escape") {
      if (!$("#dlg").hidden) return closeDlg("no");
      if (paletteOpen) return pal(false);
      if (!$("#annoEdit").hidden) { annoHide = false; return closeBlocks(); }
      if ($("#wardrobe").classList.contains("on")) return toggleWardrobe(false);
      if (!$("#statusMenu").hidden) return closeStatusMenu();
      if (graph.focus) return graph.clearFocus();
      return;
    }
    if (!paletteOpen) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      palSel = Math.max(0, Math.min(palHits.length - 1, palSel + (e.key === "ArrowDown" ? 1 : -1)));
      $$("#palResults button").forEach((b, i) => b.classList.toggle("sel", i === palSel));
    }
    if (e.key === "Enter" && palHits[palSel]) { pal(false); openCard(palHits[palSel].path); }
  });

  document.addEventListener("pointerdown", e => {
    if (!$("#statusMenu").hidden && !e.target.closest("#statusMenu") && !e.target.closest("[data-status-for]")) closeStatusMenu();
  });
  addEventListener("mousemove", e => {
    document.documentElement.style.setProperty("--mx", (e.clientX / innerWidth * 100).toFixed(1) + "%");
    document.documentElement.style.setProperty("--my", (e.clientY / innerHeight * 100).toFixed(1) + "%");
  }, { passive: true });
  addEventListener("scroll", () => $("#topbar").classList.toggle("stuck", scrollY > 12), { passive: true });
}

/* 补卡面板两种模式：ai 只要一句话；own 才要卡名和正文。两处面板同一套逻辑 */
function setMode(prefix, m) {
  const seg = $("#" + prefix + "Mode");
  if (!seg) return;
  seg.dataset.mode = m;
  $$("button", seg).forEach(b => b.classList.toggle("on", b.dataset.m === m));
  $("#" + prefix + "AskRow").hidden = m !== "ai";
  ["Row", "Text", "Use"].forEach(s => {
    const el = $("#" + prefix + "Own" + s);
    if (el) el.hidden = m !== "own";
  });
}

function toggleFilter(type, val) {
  const f = STATE.f;
  if (type === "q") f.q = "";
  else if (type === "kind") f.kind = f.kind === val ? "" : val;
  else if (type === "status") f.status = f.status === val ? "" : val;
  else if (type === "plat") f.plat = f.plat === val ? "" : val;
  else if (type === "tag") {
    const at = f.tag.indexOf(val);
    if (at < 0) f.tag = STATE.tagMulti ? f.tag.concat([val]) : [val];
    else f.tag = f.tag.filter((x, i) => i !== at);
  }
  renderBrowse();
}

function focusReview() {
  const undone = STATE.tasks.filter(t => !t.done && (!t.due || t.due <= today()));
  act("eager", undone.length ? `今天有 ${undone.length} 条到期` : "今天没有到期条目", 3600);
  const sec = $("#reviewSection");
  sec.scrollIntoView({ behavior: REDUCE ? "auto" : "smooth", block: "center" });
  sec.animate(
    [{ boxShadow: "0 0 0 0 " + cssv("--accent-soft") }, { boxShadow: "0 0 0 12px transparent" }],
    { duration: 900, easing: "ease-out" });
}

/* 什么都不做 45 秒，她自己打瞌睡——常驻角色不能是静止的 */
let dozeAt;
function wake() {
  clearTimeout(dozeAt);
  dozeAt = setTimeout(() => act("sleepy", "没有操作，她先打了个盹", 4200), 45000);
}

/* ---------------------------------------------------------------- 启动 */

function boot() {
  confettiSize();
  addEventListener("resize", confettiSize);
  STATE.tagMulti = store.get("kb.tagMulti") === "1";
  paintHangers();
  /* 先让两个她穿上默认那套，再应用记住的皮肤——反过来的话刷新后会一直
     停在 home 那套衣服上不换装。 */
  mascot.init();
  companion.init();
  graph.init();
  applySuit(suitId, false);
  bind();
  applyWidth();
  renderPipeSteps();
  ["pointerdown", "keydown", "mousemove", "scroll", "wheel"].forEach(ev => addEventListener(ev, wake, { passive: true }));
  wake();
  loadModelConfig();
  loadAll().then(() => {
    if (!location.hash) go("dashboard"); else applyRoute();
    reveal();
    /* 截图脚本要拍衣柜，又不能靠手点：?wardrobe=1 直接拉开 */
    if (/wardrobe=1/.test(location.search)) toggleWardrobe(true);
    /* 停留要一直在长：先结算当前这一段，再重画她头上的数 */
    setInterval(() => { dwellFlush(); paintBadge(); }, 30000);
  });
}

boot();




