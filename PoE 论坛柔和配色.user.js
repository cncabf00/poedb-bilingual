// ==UserScript==
// @name         PoE 论坛柔和配色
// @namespace    poe-forum-comfort
// @version      2.0.0
// @description  pathofexile.com 官方论坛黑底黄字修复器:20+ 套配色×字号预设 + 面板实时微调(字号/行距/明度/链接/黄字中和),点选即换、自动记忆
// @author       LazySugar
// @license      MIT
// @match        https://www.pathofexile.com/forum*
// @grant        none
// @run-at       document-start
// ==/UserScript==

(function () {
  'use strict';

  /* ==================== 原理 ==================== *
   * 原版关键色（chunk.Dav7X54nSYpi.css）：
   *   正文 body/.defaultText #a38d6d   行底 #1a1b18/#0f0f0f   面板 #000000e8
   *   正文实际字号被 .container{font-size:.8125rem} 钉死 13px（body 上改无效，
   *   所以字号声明在 .forumTheme 自身——元素自己的声明赢过一切继承）
   *
   * 本版全部走 CSS 变量：颜色规则只引用 var(--poe-fc-*)，
   * JS 按「预设 + 滑杆微调」推导变量值写到 <html> 的 style 上。
   * 开关仍是 html[data-poefc="soft"]；所有规则用 :where() 做前缀
   * （特异性归零：同特异性我们后来者胜，站点更高特异性的身份色规则
   *  —— blockquote.staff 边框、道具稀有度 .textXxx —— 自动保留）。
   */
  const CSS = `
html[data-poefc="soft"]{
  --poe-fc-text:#d3d0ca; --poe-fc-text-hi:#dedad3; --poe-fc-panel:#181a1d;
  --poe-fc-row1:#24262a; --poe-fc-row0:#1d1f23; --poe-fc-info:#16171a;
  --poe-fc-quote:#2b2d32; --poe-fc-quote-line:#3a3d43; --poe-fc-line:#101113;
  --poe-fc-meta:#8d8a84; --poe-fc-hr:#54565c; --poe-fc-accent:#cfb381;
  --poe-fc-fs:15px; --poe-fc-lh:1.55;
}
/* 文字色放在 .container（论坛内容祖先）而不是 body：亮色预设时页眉导航不该变；
   同时给 .container 铺同色底，亮色预设下整列成纸面，避免深色脚注文字落在黑底上 */
:where(:root[data-poefc="soft"]) .container { color:var(--poe-fc-text); background:var(--poe-fc-panel); }
:where(:root[data-poefc="soft"]) .container .defaultText { color:var(--poe-fc-text); }
:where(:root[data-poefc="soft"]) h1,
:where(:root[data-poefc="soft"]) h2,
:where(:root[data-poefc="soft"]) h3,
:where(:root[data-poefc="soft"]) h4 { color:var(--poe-fc-text-hi); }

:where(:root[data-poefc="soft"]) .container>.content>.backdrop { background:var(--poe-fc-panel); }
:where(:root[data-poefc="soft"]) .forumTable tr { background-color:var(--poe-fc-row1); }
:where(:root[data-poefc="soft"]) .forumTable tr.even,
:where(:root[data-poefc="soft"]) .forumTable tr:nth-child(2n) { background-color:var(--poe-fc-row0); }
:where(:root[data-poefc="soft"]) .forumTable th { background:linear-gradient(var(--poe-fc-quote),var(--poe-fc-row1)); }
:where(:root[data-poefc="soft"]) .forumTable .newsPost { background:var(--poe-fc-row0); }
:where(:root[data-poefc="soft"]) .forumTable .newsPostInfo { background:var(--poe-fc-info); }
:where(:root[data-poefc="soft"]) .forumTable td { border-top-color:var(--poe-fc-line); }
:where(:root[data-poefc="soft"]) .forumTable hr { border-top-color:var(--poe-fc-hr); }

:where(:root[data-poefc="soft"]) .forumTable tr blockquote {
  background-color:var(--poe-fc-quote); border-color:var(--poe-fc-quote-line); box-shadow:0 0 2px 1px var(--poe-fc-quote-line);
}
:where(:root[data-poefc="soft"]) .forumTable tr.even blockquote,
:where(:root[data-poefc="soft"]) .forumTable tr:nth-child(2n) blockquote {
  background-color:var(--poe-fc-quote); border-color:var(--poe-fc-quote-line); box-shadow:0 0 2px 1px var(--poe-fc-quote-line);
}
:where(:root[data-poefc="soft"]) .forumPostListTable .content-container .bug-report {
  background:var(--poe-fc-info); border-color:var(--poe-fc-quote-line);
}

:where(:root[data-poefc="soft"]) .forumTable tr .signature,
:where(:root[data-poefc="soft"]) .forumTable tr .signature a,
:where(:root[data-poefc="soft"]) .forumTable tr .last_edited_by,
:where(:root[data-poefc="soft"]) .forumTable tr .last_bumped { color:var(--poe-fc-meta); }

:where(:root[data-poefc="soft"]) .thread_title a { color:var(--poe-fc-accent); }
:where(:root[data-poefc="soft"]) .thread_title a:hover { color:var(--poe-fc-text-hi); }

/* 链接模式：只染无 class 的裸链接（道具/身份链接都带 class，自动豁免） */
:where(:root[data-poefc="soft"][data-poefc-link="accent"]) .forumTheme a:not([class]) { color:var(--poe-fc-accent); }
:where(:root[data-poefc="soft"][data-poefc-link="text"]) .forumTheme a:not([class]) { color:var(--poe-fc-text); }
/* 黄字中和：GGG staff 公告帖整篇 #cec59f，可选压成正文色 */
:where(:root[data-poefc="soft"][data-poefc-staff="muted"]) .forum-table .staff .content-container,
:where(:root[data-poefc="soft"][data-poefc-staff="muted"]) .forumTable .staff .content-container,
:where(:root[data-poefc="soft"][data-poefc-staff="muted"]) .support .content-container strong,
:where(:root[data-poefc="soft"][data-poefc-staff="muted"]) .forumTable .valued-poster .content-container strong,
:where(:root[data-poefc="soft"][data-poefc-staff="muted"]) .forumTable .valued-thread .content-container strong { color:var(--poe-fc-text); }

:where(:root[data-poefc="soft"]) .forumTheme { font-size:var(--poe-fc-fs); line-height:var(--poe-fc-lh); }

/* ---------- 悬浮按钮与面板（固定中性深灰，不随主题变） ---------- */
#poefc_btn { position:fixed; right:16px; bottom:64px; z-index:2147483000;
  width:46px; height:46px; border-radius:50%; border:1px solid #5a6268;
  background:rgba(30,35,40,.92); color:#eee; font-size:15px; font-weight:600;
  cursor:pointer; box-shadow:0 2px 10px rgba(0,0,0,.5); line-height:1; }
#poefc_btn:hover { opacity:.85; }
#poefc_panel { position:fixed; right:16px; bottom:120px; z-index:2147483001;
  width:340px; max-height:70vh; overflow:auto; padding:12px 14px 14px;
  background:#20232a; color:#d5d3cf; border:1px solid #3c4048; border-radius:10px;
  box-shadow:0 6px 24px rgba(0,0,0,.55); font:13px/1.5 Verdana,Arial,sans-serif; }
#poefc_panel h4 { margin:10px 0 6px; font-size:12px; color:#8f939c; font-weight:600; }
#poefc_panel h4:first-child { margin-top:0; }
#poefc_chips { display:flex; flex-wrap:wrap; gap:5px; }
#poefc_chips .chip { display:flex; align-items:center; gap:5px; padding:3px 8px 3px 6px;
  border:1px solid #3c4048; border-radius:6px; cursor:pointer; background:#181a1f; color:#c8c5c0; }
#poefc_chips .chip:hover { border-color:#5a626e; }
#poefc_chips .chip.on { border-color:#d8b45a; color:#f0e6cc; background:#26241a; }
#poefc_chips .chip .sw { width:26px; height:14px; border-radius:3px; border:1px solid #00000060; flex:none; }
#poefc_panel .row { display:flex; align-items:center; gap:8px; margin:4px 0; }
#poefc_panel .row label { width:44px; color:#9a9ea6; flex:none; }
#poefc_panel .row output { width:40px; text-align:right; color:#d5d3cf; font-variant-numeric:tabular-nums; }
#poefc_panel input[type=range] { flex:1; accent-color:#c9a35c; }
#poefc_panel .btns { display:flex; gap:6px; margin-top:10px; }
#poefc_panel .btn { flex:1; padding:5px 0; text-align:center; border:1px solid #3c4048;
  border-radius:6px; background:#181a1f; color:#c8c5c0; cursor:pointer; }
#poefc_panel .btn:hover { border-color:#5a626e; }
#poefc_panel .togs { display:flex; gap:6px; flex-wrap:wrap; }
#poefc_panel .tog { padding:4px 8px; border:1px solid #3c4048; border-radius:6px;
  background:#181a1f; color:#c8c5c0; cursor:pointer; }
#poefc_panel .tog b { color:#e3c37a; font-weight:600; }
`;

  /* ==================== 预设 ==================== *
   * [id, 名称, 色相, 饱和度, 文字亮度, 底色亮度]
   * 底色亮度 <50 为暗色（层级往上提亮），≥50 为亮色（层级往下加深）。
   */
  const PRESETS = [
    ['warm-std',  '暖灰·标准',   36, 12, 82, 7.5],
    ['warm-hi',   '暖灰·亮字',   36, 12, 87, 9],
    ['warm-soft', '暖灰·柔光',   36, 9,  73, 8],
    ['neu-std',   '中性灰',      0,  0,  82, 8],
    ['neu-soft',  '中性·低刺激', 0,  0,  72, 10],
    ['neu-hi',    '中性·高对比', 0,  0,  89, 6],
    ['ash',       '烟灰',        220,6,  70, 11],
    ['contrast',  '石灰·极暗',   0,  0,  90, 4],
    ['blue-std',  '蓝夜',        215,18, 81, 8],
    ['blue-deep', '蓝夜·沉静',   215,14, 71, 6],
    ['blue-bri',  '蓝夜·鲜亮',   215,24, 86, 12],
    ['midnight',  '午夜靛',      224,28, 80, 9],
    ['ink',       '墨玉',        160,12, 79, 7],
    ['moss',      '苔绿·护眼',   120,9,  70, 10],
    ['dusk',      '暮紫',        268,12, 80, 8],
    ['coffee',    '咖啡',        28, 20, 78, 8],
    ['sepia',     '棕褐·护眼',   33, 15, 69, 11],
    ['paper-yel', '米黄纸',      38, 28, 26, 95],
    ['paper-wht', '白纸黑字',    38, 6,  17, 98],
    ['paper-blu', '浅蓝灰',      210,16, 20, 95]
  ];

  const DEFAULTS = { preset: 'warm-std', fs: 15, lh: 1.55, tl: 0, link: 'orig', staff: 'keep' };
  const KEY = 'poefc_state_v2';

  function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }
  function hsl(h, s, l) { return 'hsl(' + h + ' ' + clamp(s, 0, 100).toFixed(1) + '% ' + clamp(l, 0, 100).toFixed(1) + '%)'; }

  function derive(p, st) {
    const hue = p[2], sat = p[3], tl = st.tl;
    const textL = clamp(p[4] + tl, 0, 100);
    const panelL = p[5];
    const dark = panelL < 50;
    // 层级偏移：暗色主题往亮走，亮色主题往暗走
    const d = function (delta) { return panelL + (dark ? delta : -delta); };
    return {
      '--poe-fc-text':        hsl(hue, sat, textL),
      '--poe-fc-text-hi':     hsl(hue, sat * 1.15, textL + 4),
      '--poe-fc-panel':       hsl(hue, sat * 0.55, panelL),
      '--poe-fc-row1':        hsl(hue, sat * 0.55, d(6)),
      '--poe-fc-row0':        hsl(hue, sat * 0.55, d(3)),
      '--poe-fc-info':        hsl(hue, sat * 0.55, d(dark ? -1 : -2)),
      '--poe-fc-quote':       hsl(hue, sat * 0.45, d(10)),
      '--poe-fc-quote-line':  hsl(hue, sat * 0.45, d(14)),
      '--poe-fc-line':        hsl(hue, sat * 0.45, d(dark ? -2 : 3)),
      '--poe-fc-meta':        hsl(hue, sat * 0.4, textL - (dark ? 26 : -22)),
      '--poe-fc-hr':          hsl(hue, sat * 0.4, textL - (dark ? 30 : -26)),
      '--poe-fc-accent':      st.link === 'orig' && dark ? hsl(36, 32, 66) : hsl(dark ? 208 : 212, 48, dark ? 68 : 40),
      '--poe-fc-fs':          st.fs + 'px',
      '--poe-fc-lh':          String(st.lh)
    };
  }

  /* ==================== 状态 ==================== */
  function loadState() {
    try {
      const raw = localStorage.getItem(KEY);
      if (raw) {
        const obj = JSON.parse(raw);
        return Object.assign({}, DEFAULTS, obj);
      }
    } catch (e) {}
    return Object.assign({}, DEFAULTS);
  }
  let st = loadState(); // st.on: 是否启用柔和主题（默认 true，同旧版）
  if (typeof st.on !== 'boolean') st.on = true;

  function save() { try { localStorage.setItem(KEY, JSON.stringify(st)); } catch (e) {} }

  function applyTheme() {
    const root = document.documentElement;
    if (!st.on) {
      root.removeAttribute('data-poefc');
    } else {
      const preset = PRESETS.find(function (x) { return x[0] === st.preset; }) || PRESETS[0];
      const vars = derive(preset, st);
      root.setAttribute('data-poefc', 'soft');
      root.setAttribute('data-poefc-link', st.link);
      root.setAttribute('data-poefc-staff', st.staff);
      for (const k in vars) root.style.setProperty(k, vars[k]);
    }
    if (btn) {
      btn.textContent = st.on ? '调色' : '原色';
      btn.style.opacity = st.on ? '1' : '.55';
      btn.title = st.on ? '论坛配色：柔和主题已启用，点击打开面板/关闭' : '论坛配色：官方原色，点击打开面板';
    }
  }

  /* ==================== 面板 ==================== */
  const LINK_MODES = [['orig', '原金'], ['accent', '跟随主题'], ['text', '同文字']];
  const STAFF_MODES = [['keep', '保留'], ['muted', '中和']];

  function swatch(p) {
    const v = derive(p, Object.assign({}, st, { tl: 0 }));
    return '<span class="sw" style="background:linear-gradient(90deg,' + v['--poe-fc-panel'] + ' 0 33%,' + v['--poe-fc-row1'] + ' 33% 66%,' + v['--poe-fc-text'] + ' 66%)"></span>';
  }

  function renderPanel(panel) {
    const linkName = (LINK_MODES.find(function (m) { return m[0] === st.link; }) || LINK_MODES[0])[1];
    const staffName = (STAFF_MODES.find(function (m) { return m[0] === st.staff; }) || STAFF_MODES[0])[1];
    panel.innerHTML =
      '<h4>配色预设</h4><div id="poefc_chips">' +
        PRESETS.map(function (p) {
          return '<button class="chip' + (p[0] === st.preset ? ' on' : '') + '" data-p="' + p[0] + '">' +
                 swatch(p) + p[1] + '</button>';
        }).join('') +
      '</div>' +
      '<h4>微调</h4>' +
      '<div class="row"><label>字号</label><input type="range" id="poefc_fs" min="13" max="19" step="0.5" value="' + st.fs + '"><output>' + st.fs + 'px</output></div>' +
      '<div class="row"><label>行距</label><input type="range" id="poefc_lh" min="1.2" max="1.9" step="0.05" value="' + st.lh + '"><output>' + Number(st.lh).toFixed(2) + '</output></div>' +
      '<div class="row"><label>文字亮</label><input type="range" id="poefc_tl" min="-12" max="10" step="1" value="' + st.tl + '"><output>' + (st.tl > 0 ? '+' : '') + st.tl + '</output></div>' +
      '<h4>开关</h4><div class="togs">' +
        '<button class="tog" id="poefc_link">链接 <b>' + linkName + '</b></button>' +
        '<button class="tog" id="poefc_staff">公告帖黄字 <b>' + staffName + '</b></button>' +
      '</div>' +
      '<div class="btns">' +
        '<button class="btn" id="poefc_rand">🎲 随机</button>' +
        '<button class="btn" id="poefc_def">恢复默认</button>' +
        '<button class="btn" id="poefc_off">官方原色</button>' +
      '</div>';

    panel.querySelectorAll('.chip').forEach(function (c) {
      c.addEventListener('click', function () { st.preset = c.dataset.p; st.on = true; save(); applyTheme(); renderPanel(panel); });
    });
    const bind = function (id, key, fmt) {
      panel.querySelector('#' + id).addEventListener('input', function (e) {
        st[key] = Number(e.target.value); save(); applyTheme();
        e.target.parentNode.querySelector('output').textContent = fmt(st[key]);
      });
    };
    bind('poefc_fs', 'fs', function (v) { return v + 'px'; });
    bind('poefc_lh', 'lh', function (v) { return Number(v).toFixed(2); });
    bind('poefc_tl', 'tl', function (v) { return (v > 0 ? '+' : '') + v; });
    panel.querySelector('#poefc_link').addEventListener('click', function () {
      const i = LINK_MODES.findIndex(function (m) { return m[0] === st.link; });
      st.link = LINK_MODES[(i + 1) % LINK_MODES.length][0]; save(); applyTheme(); renderPanel(panel);
    });
    panel.querySelector('#poefc_staff').addEventListener('click', function () {
      const i = STAFF_MODES.findIndex(function (m) { return m[0] === st.staff; }) || 0;
      st.staff = STAFF_MODES[(i + 1) % STAFF_MODES.length][0]; save(); applyTheme(); renderPanel(panel);
    });
    panel.querySelector('#poefc_rand').addEventListener('click', function () {
      st.preset = PRESETS[Math.floor(Math.random() * PRESETS.length)][0];
      st.fs = [13.5, 14, 14.5, 15, 15.5, 16, 17][Math.floor(Math.random() * 7)];
      st.lh = [1.35, 1.45, 1.55, 1.65, 1.75][Math.floor(Math.random() * 5)];
      st.tl = [-4, -2, 0, 2][Math.floor(Math.random() * 4)];
      st.link = 'orig'; st.on = true; save(); applyTheme(); renderPanel(panel);
    });
    panel.querySelector('#poefc_def').addEventListener('click', function () {
      st = Object.assign({ on: true }, DEFAULTS); save(); applyTheme(); renderPanel(panel);
    });
    panel.querySelector('#poefc_off').addEventListener('click', function () {
      st.on = false; save(); applyTheme(); panel.hidden = true;
    });
  }

  /* ==================== 启动 ==================== */
  let btn = null;

  const style = document.createElement('style');
  style.id = 'poefc-style';
  style.textContent = CSS;
  (document.head || document.documentElement).appendChild(style);
  applyTheme(); // document-start 即生效，不闪官方黑底黄字

  let panel = null;
  function initUI() {
    btn = document.createElement('button');
    btn.id = 'poefc_btn';
    btn.type = 'button';
    btn.addEventListener('click', function () {
      if (!st.on) { st.on = true; save(); applyTheme(); } // 原色状态下一击先回到主题
      panel.hidden = !panel.hidden;
      if (panel.hidden) return;
      renderPanel(panel);
    });
    panel = document.createElement('div');
    panel.id = 'poefc_panel';
    panel.hidden = true;
    document.body.appendChild(btn);
    document.body.appendChild(panel);
    applyTheme();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initUI);
  } else {
    initUI();
  }
})();
