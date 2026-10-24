// ==UserScript==
// @name         깨비노트 플러스 공지 동기화
// @namespace    https://dsp0701-git.github.io/kkaebi-note-plus/
// @version      1.0.1
// @description  도깨비의세계 공식 커뮤니티에 들어가면 공지·점검·쿠폰·장비확률 원본을 깨비노트 플러스 저장소(data/forum_cache.json)로 올린다.
// @match        https://forum.kakaogames.com/dokkaebi/*
// @match        https://m-forum.kakaogames.com/dokkaebi/*
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_registerMenuCommand
// @grant        GM_xmlhttpRequest
// @grant        unsafeWindow
// @connect      api.github.com
// @run-at       document-idle
// @downloadURL  https://raw.githubusercontent.com/dsp0701-git/kkaebi-note-plus/main/tools/kkaebi-sync.user.js
// @updateURL    https://raw.githubusercontent.com/dsp0701-git/kkaebi-note-plus/main/tools/kkaebi-sync.user.js
// ==/UserScript==

(async () => {
  'use strict';
  const REPO = 'dsp0701-git/kkaebi-note-plus';
  const FILE = 'data/forum_cache.json';
  const API = 'https://forum-api.kakaogames.com/v1/dokkaebi';
  const MIN_INTERVAL = 30 * 60 * 1000;          // 30분에 한 번만 동기화
  const EQUIP_TITLE = '장비 등급별 추가 효과';   // scripts/update.py 와 동일

  GM_registerMenuCommand('지금 동기화', () => run(true));
  GM_registerMenuCommand('GitHub 토큰 다시 입력', () => { GM_setValue('token', ''); askToken(); });
  run(false);

  function askToken() {
    const t = prompt('깨비노트 동기화: GitHub fine-grained 토큰 입력\n(저장소 kkaebi-note-plus 전용, Contents: Read and write)');
    if (t) GM_setValue('token', t.trim());
    return t ? t.trim() : '';
  }

  function toast(msg, err) {
    const d = document.createElement('div');
    d.textContent = '깨비노트 · ' + msg;
    d.style.cssText = `position:fixed;right:16px;bottom:16px;z-index:99999;padding:10px 14px;border-radius:8px;
      font:13px/1.4 sans-serif;color:#fff;background:${err ? '#c0392b' : '#2d7d46'};box-shadow:0 2px 8px #0004`;
    document.body.appendChild(d);
    setTimeout(() => d.remove(), err ? 8000 : 4000);
  }

  const sleep = ms => new Promise(r => setTimeout(r, ms));
  async function api(path, params = {}) {
    await sleep(400); // 연속 요청 간격
    const q = new URLSearchParams({ ...params, lang: 'ko' });
    // 페이지 자신의 fetch 로 호출 (사이트가 공지를 불러오는 것과 같은 요청)
    const pageFetch = (typeof unsafeWindow !== 'undefined' && unsafeWindow.fetch) ? unsafeWindow.fetch.bind(unsafeWindow) : fetch;
    let r;
    try { r = await pageFetch(`${API}/${path}?${q}`, { headers: { Accept: 'application/json' } }); }
    catch (e) { throw new Error(`[공식 API] ${e.message} (${path})`); }
    if (!r.ok) throw new Error(`[공식 API] ${r.status} ${path}`);
    const j = await r.json();
    return j.payload ?? j;
  }
  // update.py cache_key() 와 같은 규칙: lang 제외, 파라미터 이름순
  const key = (path, params = {}) => {
    const q = Object.keys(params).sort().map(k => `${k}=${encodeURIComponent(params[k])}`).join('&');
    return q ? `${path}?${q}` : path;
  };

  async function collect() {
    const res = {};
    const lists = [['notice', 'list', { size: 20 }], ['update', 'list', { size: 20 }], ['event', 'list', { size: 20 }],
                   ['notice', 'pinned', { page: 1 }], ['prob', 'list', { size: 20 }]];
    const items = [];
    for (const [b, kind, p] of lists) {
      const path = `boards/${b}/${kind}`;
      const data = await api(path, p);
      res[key(path, p)] = data;
      const posts = Array.isArray(data) ? data : (data.posts || []);
      for (const post of posts) items.push([b, post]);
    }
    // 본문이 필요한 글: 점검 안내(최신 6개), 쿠폰 안내, 장비 확률표
    const want = new Map();
    let maint = 0;
    for (const [b, p] of items) {
      const t = p.title || '';
      let need = (t.includes('쿠폰') && t.includes('안내')) || t.includes(EQUIP_TITLE);
      if (t.includes('점검 안내') && !/완료|보상/.test(t) && maint < 6) { maint++; need = true; }
      if (need) want.set(`boards/${b}/posts/${p.post_id}`, true);
    }
    for (const path of want.keys()) {
      const d = await api(path);
      res[path] = { content: d.content || '' };
    }
    return res;
  }

  function b64(str) {
    const bytes = new TextEncoder().encode(str);
    let bin = '';
    for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    return btoa(bin);
  }

  // GitHub 은 GM_xmlhttpRequest 로 (페이지 CORS/CSP 영향 없음)
  function gh(method, url, token, body) {
    return new Promise((resolve, reject) => GM_xmlhttpRequest({
      method,
      url: `https://api.github.com/repos/${REPO}/contents/${FILE}${url}`,
      headers: { Authorization: `Bearer ${token}`, Accept: 'application/vnd.github+json', 'Content-Type': 'application/json' },
      data: body && JSON.stringify(body),
      onload: r => {
        if (method === 'GET' && r.status === 404) return resolve(null);
        if (r.status < 200 || r.status >= 300) return reject(new Error(`[GitHub] ${r.status}: ${(r.responseText || '').slice(0, 120)}`));
        resolve(JSON.parse(r.responseText));
      },
      onerror: r => reject(new Error(`[GitHub] 연결 실패 ${r.error || ''}`)),
    }));
  }

  async function run(force) {
    try {
      const last = GM_getValue('last', 0);
      if (!force && Date.now() - last < MIN_INTERVAL) return;
      const token = GM_getValue('token', '') || askToken();
      if (!token) return toast('토큰이 없어 동기화를 건너뜀', true);

      const responses = await collect();
      const now = new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 16) + '+09:00';
      const cur = await gh('GET', '?ref=main', token);
      if (cur) {
        const old = JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(cur.content.replace(/\n/g, '')), c => c.charCodeAt(0))));
        if (JSON.stringify(old.responses) === JSON.stringify(responses)) {
          GM_setValue('last', Date.now());
          return force && toast('변경 없음');
        }
      }
      const text = JSON.stringify({ saved_at: now, source: location.host, responses }, null, 1) + '\n';
      await gh('PUT', '', token, {
        message: `data: forum cache (브라우저 동기화) ${now.slice(5, 16).replace('T', ' ')}`,
        content: b64(text), sha: cur?.sha, branch: 'main',
      });
      GM_setValue('last', Date.now());
      toast('공지 동기화 완료 — 1~2분 뒤 사이트에 반영');
    } catch (e) {
      console.error('[kkaebi-sync]', e.message, e);
      toast('동기화 실패: ' + e.message, true);
    }
  }
})();
