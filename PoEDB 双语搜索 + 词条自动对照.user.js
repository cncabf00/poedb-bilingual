// ==UserScript==
// @name         PoEDB 双语搜索 + 词条自动对照
// @namespace    poedb-bilingual
// @version      1.0.0
// @description  poedb.tw 搜索框支持中英双语双向检索；词条页自动内联显示中文/英文对照（悬浮按钮开关，记住状态）
// @author       LazySugar
// @license      MIT
// @match        https://poedb.tw/*
// @match        https://www.poedb.tw/*
// @match        https://ptr.poedb.tw/*
// @match        https://poe2db.tw/*
// @grant        none
// @run-at       document-idle
// @downloadURL https://update.greasyfork.org/scripts/592623/PoEDB%20%E5%8F%8C%E8%AF%AD%E6%90%9C%E7%B4%A2%20%2B%20%E8%AF%8D%E6%9D%A1%E8%87%AA%E5%8A%A8%E5%AF%B9%E7%85%A7.user.js
// @updateURL https://update.greasyfork.org/scripts/592623/PoEDB%20%E5%8F%8C%E8%AF%AD%E6%90%9C%E7%B4%A2%20%2B%20%E8%AF%8D%E6%9D%A1%E8%87%AA%E5%8A%A8%E5%AF%B9%E7%85%A7.meta.js
// ==/UserScript==

(function () {
  'use strict';

  /* ==================== 语言工具 ==================== */
  const LANG_MAP = {
    'zh-Hant': 'tw', 'tw': 'tw',
    'zh-Hans': 'cn', 'cn': 'cn',
    'fr': 'fr', 'es': 'sp', 'sp': 'sp', 'de': 'de',
    'ko': 'kr', 'kr': 'kr', 'ru': 'ru', 'pt': 'pt',
    'en': 'us', 'us': 'us', 'th': 'th', 'ja': 'jp', 'jp': 'jp'
  };

  function getLang() {
    const dl = document.documentElement && document.documentElement.lang;
    if (dl && LANG_MAP[dl]) return LANG_MAP[dl];
    const m = location.pathname.match(/^\/([a-z]{2})\//);
    if (m && LANG_MAP[m[1]]) return LANG_MAP[m[1]];
    return 'us';
  }

  function isBeta() {
    const h = location.host;
    return h === 'ptr.poedb.tw' || h === 'poe2db.tw';
  }

  function jsonUrl(lang) {
    const beta = isBeta() ? 'cb' : '';
    return 'https://' + location.host + '/json/autocomplete' + beta + '_' + lang + '.json';
  }

  async function loadDict(lang) {
    try {
      const res = await fetch(jsonUrl(lang));
      if (!res.ok) return [];
      return await res.json();
    } catch (e) {
      return [];
    }
  }

  // 会话级缓存（同标签页内跨页面复用，避免反复下载 ~700KB 词库）
  async function loadDictCached(lang) {
    const key = 'poedb_dict_' + (isBeta() ? 'cb_' : '') + lang;
    try {
      const cached = sessionStorage.getItem(key);
      if (cached) {
        const obj = JSON.parse(cached);
        if (obj && obj.t && (Date.now() - obj.t) < 3600000) return obj.d;
      }
    } catch (e) {}
    const data = await loadDict(lang);
    try { sessionStorage.setItem(key, JSON.stringify({ t: Date.now(), d: data })); } catch (e) {}
    return data;
  }

  /* ==================== 功能1：双语搜索 ==================== */
  async function initSearch() {
    const $ = window.jQuery;
    if (!$ || !$.fn.autocomplete) return;
    const $input = $('#navautosearch');
    if (!$input.length) return;

    const curLang = getLang();
    const langs = [];
    ['cn', 'us', curLang].forEach(function (l) { if (langs.indexOf(l) === -1) langs.push(l); });

    const lists = await Promise.all(langs.map(loadDictCached));

    // 按 slug（跨语言统一）合并
    const merged = new Map(); // value -> {labels:{}, descs:{}, cls}
    langs.forEach(function (lang, i) {
      const list = lists[i] || [];
      list.forEach(function (item) {
        if (!item || !item.value) return;
        let e = merged.get(item.value);
        if (!e) { e = { labels: {}, descs: {}, cls: item.class || '' }; merged.set(item.value, e); }
        const oldDesc = e.descs[lang];
        if (e.labels[lang] === undefined) {
          e.labels[lang] = item.label;
          e.descs[lang] = item.desc || '';
        } else if (String(oldDesc || '').toLowerCase() === 'wiki' &&
                   String(item.desc || '').toLowerCase() !== 'wiki') {
          e.labels[lang] = item.label;
          e.descs[lang] = item.desc || '';
        }
      });
    });

    function dispLabel(e) {
      const cur = e.labels[curLang] || e.labels['us'] || '';
      if (curLang === 'cn') {
        const en = e.labels['us'];
        return (en && en !== cur) ? cur + ' (' + en + ')' : cur;
      }
      const cn = e.labels['cn'];
      return (cn && cn !== cur) ? cur + ' (' + cn + ')' : cur;
    }

    function dispDesc(e) {
      return e.descs[curLang] || e.descs['us'] || '';
    }

    const flat = [];
    merged.forEach(function (e, value) {
      let st = '';
      for (const k in e.labels) st += e.labels[k] + ' ';
      for (const k in e.descs) st += e.descs[k] + ' ';
      flat.push({ label: dispLabel(e), value: value, desc: dispDesc(e), cls: e.cls, _st: st.toLowerCase() });
    });

    function apply() {
      $input.autocomplete({
        minLength: 1,
        source: function (request, response) {
          const q = (request.term || '').toLowerCase().trim();
          if (!q) { response([]); return; }
          const starts = [], contains = [];
          for (let i = 0; i < flat.length; i++) {
            const it = flat[i];
            const idx = it._st.indexOf(q);
            if (idx === -1) continue;
            if (idx === 0) starts.push(it); else contains.push(it);
          }
          response(starts.concat(contains).slice(0, 60));
        },
        select: function (event, ui) {
          document.location = '/' + curLang + '/' + ui.item.value;
          return false;
        }
      });
      const inst = $input.autocomplete('instance');
      if (inst) {
        inst._renderItem = function (ul, item) {
          const div = $('<div>').addClass(item.cls || '');
          div.text(item.label);
          if (item.desc) div.append($('<small>').addClass('float-end normal small').text(item.desc));
          return $('<li>').append(div).appendTo(ul);
        };
      }
    }

    apply();
    setTimeout(apply, 1200);
    setTimeout(apply, 3200);
  }

  /* ==================== 功能2：词条自动对照 ==================== */
  function getSlug() {
    const m = location.pathname.match(/^\/([a-z]{2})\/([^/]+)\/?$/);
    if (!m || !LANG_MAP[m[1]]) return null;
    return m[2];
  }

  function targetLang(cur) { return cur === 'cn' ? 'us' : 'cn'; }

  function cleanBox(box) {
    const clone = box.cloneNode(true);
    clone.querySelectorAll('img,script,style,link,video,audio,iframe').forEach(n => n.remove());
    clone.querySelectorAll('a').forEach(a => {
      const span = document.createElement('span');
      span.innerHTML = a.innerHTML;
      a.replaceWith(span);
    });
    return clone.innerHTML;
  }

  async function fetchTranslation(slug, tLang) {
    const key = 'poedb_tr_' + tLang + '_' + slug;
    try {
      const cached = sessionStorage.getItem(key);
      if (cached) return { html: cached, lang: tLang };
    } catch (e) {}
    try {
      const res = await fetch('/' + tLang + '/' + slug);
      if (!res.ok) return null;
      const text = await res.text();
      const doc = new DOMParser().parseFromString(text, 'text/html');
      const box = doc.querySelector('.newItemPopup');
      if (!box) return null;
      const html = cleanBox(box);
      if (!html) return null;
      try { sessionStorage.setItem(key, html); } catch (e) {}
      return { html: html, lang: tLang };
    } catch (e) {
      return null;
    }
  }

  function initTranslation() {
    const anchor = document.querySelector('.newItemPopup');
    if (!anchor) return;
    const slug = getSlug();
    if (!slug) return;
    const curLang = getLang();
    const tLang = targetLang(curLang);

    let stored = null;
    try { stored = localStorage.getItem('poedb_tr_visible'); } catch (e) {}
    let visible = stored === null ? true : stored === '1';

    const btn = document.createElement('button');
    btn.id = 'poedb_tr_btn';
    btn.type = 'button';
    btn.title = tLang === 'cn' ? '显示 / 隐藏中文对照' : 'Show / Hide English translation';
    btn.style.cssText = 'position:fixed;right:16px;bottom:96px;z-index:2147483000;width:46px;height:46px;border-radius:50%;border:1px solid #5a6268;background:rgba(30,35,40,.92);color:#eee;font-size:15px;font-weight:600;cursor:pointer;box-shadow:0 2px 10px rgba(0,0,0,.5);line-height:1;';
    btn.addEventListener('click', function () { setVisible(!visible); });
    document.body.appendChild(btn);

    const panel = document.createElement('div');
    panel.id = 'poedb_tr_panel';
    panel.className = 'card mb-2';
    panel.style.cssText = 'border:1px solid #4a5560;margin-top:8px;';
    panel.innerHTML =
      '<div class="card-header d-flex justify-content-between align-items-center" style="cursor:pointer;">' +
        '<span id="poedb_tr_title" style="font-weight:600;"></span>' +
        '<button type="button" class="btn btn-sm btn-outline-secondary" id="poedb_tr_close" style="line-height:1;">×</button>' +
      '</div>' +
      '<div class="card-body" id="poedb_tr_body"></div>';
    anchor.parentNode.insertBefore(panel, anchor.nextSibling);

    const titleEl = panel.querySelector('#poedb_tr_title');
    const bodyEl = panel.querySelector('#poedb_tr_body');
    const closeBtn = panel.querySelector('#poedb_tr_close');
    titleEl.textContent = tLang === 'cn' ? '🌐 中文对照' : '🌐 English 对照';
    bodyEl.textContent = '加载中…';

    function setVisible(v) {
      visible = v;
      try { localStorage.setItem('poedb_tr_visible', v ? '1' : '0'); } catch (e) {}
      panel.style.display = v ? '' : 'none';
      btn.style.opacity = v ? '1' : '.55';
      btn.textContent = v ? (tLang === 'cn' ? '中' : 'EN') : '↺';
    }

    closeBtn.addEventListener('click', function () { setVisible(false); });
    setVisible(visible);

    fetchTranslation(slug, tLang).then(function (result) {
      if (!result) {
        bodyEl.textContent = '（该词条暂无' + (tLang === 'cn' ? '中文' : '英文') + '版本）';
        return;
      }
      bodyEl.innerHTML = result.html;
      setVisible(visible);
    });
  }

  /* ==================== 启动 ==================== */
  function boot() {
    const $ = window.jQuery;
    if ($ && $.fn.autocomplete) {
      initSearch();
    } else {
      setTimeout(boot, 200);
    }
    initTranslation();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();