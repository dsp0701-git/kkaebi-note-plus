/* 깨비노트 플러스 · 쿠폰 등록 도우미
 * 공식 쿠폰 페이지(coupon.kakaogames.com/dokkaebi)에서 북마클릿으로 실행.
 * - 미사용 쿠폰 목록을 깨비노트에서 불러와 회원번호·쿠폰 코드를 입력칸에 채워 준다.
 * - [쿠폰 사용] 버튼은 직접 누른다 (자동 제출하지 않음).
 * - 회원번호는 이 브라우저(쿠폰 페이지) localStorage 에만 저장되고 어디에도 전송하지 않는다.
 */
(function () {
  var SITE = (document.currentScript && document.currentScript.src.replace(/coupon-helper\.js.*$/, '')) ||
    'https://dsp0701-git.github.io/kkaebi-note-plus/';
  var OFFICIAL = 'https://coupon.kakaogames.com/dokkaebi/ko/';
  if (!/coupon\.kakaogames\.com\/dokkaebi/.test(location.href)) {
    if (confirm('공식 쿠폰 등록 페이지에서 실행해야 합니다.\n지금 열까요? 열린 페이지에서 북마크를 한 번 더 누르세요.')) location.href = OFFICIAL;
    return;
  }
  var old = document.getElementById('knp-helper');
  if (old) { old.remove(); return; }  // 다시 누르면 닫기

  var LS = { get: function (k, d) { try { var v = localStorage.getItem('knp-' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
             set: function (k, v) { try { localStorage.setItem('knp-' + k, JSON.stringify(v)); } catch (e) {} } };
  var done = LS.get('done', []);

  var box = document.createElement('div');
  box.id = 'knp-helper';
  box.style.cssText = 'position:fixed;right:16px;top:16px;z-index:2147483647;width:300px;max-height:calc(100vh - 32px);overflow:auto;' +
    'background:#fff;color:#16201c;border-radius:12px;box-shadow:0 10px 30px rgba(0,0,0,.35);font:14px/1.5 system-ui,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;padding:14px';
  box.innerHTML =
    '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">' +
    '<b style="color:#0f7a63">깨비노트 쿠폰 도우미</b><button id="knp-x" style="border:0;background:none;font-size:18px;cursor:pointer">×</button></div>' +
    '<label style="font-size:12px;color:#46534d">회원번호 (게임 [설정]에서 확인)</label>' +
    '<input id="knp-mem" style="width:100%;box-sizing:border-box;padding:7px 9px;border:1px solid #d5dcd8;border-radius:7px;margin:3px 0 4px;font:inherit">' +
    '<div style="font-size:11px;color:#66736d;margin-bottom:10px">이 브라우저에만 저장됩니다.</div>' +
    '<div id="knp-list">불러오는 중…</div>' +
    '<div style="font-size:11px;color:#66736d;margin-top:10px">[입력]을 누르면 칸이 채워집니다. 확인 후 페이지의 <b>쿠폰 사용</b>을 직접 누르세요. 등록이 끝난 쿠폰은 ✓로 표시해 두세요.</div>';
  document.body.appendChild(box);

  var mem = box.querySelector('#knp-mem');
  mem.value = LS.get('member', '');
  mem.oninput = function () { LS.set('member', mem.value.trim()); };
  box.querySelector('#knp-x').onclick = function () { box.remove(); };

  function setVal(el, v) {
    var setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    setter.call(el, v);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function fill(code) {
    var f1 = document.getElementById('txtField1'), f2 = document.getElementById('txtField2');
    if (!f1 || !f2) { alert('입력칸을 찾지 못했습니다. 공식 페이지 구조가 바뀌었을 수 있어요.\n코드를 복사해 직접 붙여 넣어 주세요.'); return; }
    if (!mem.value.trim()) { mem.focus(); mem.style.borderColor = '#c4472b'; return; }
    setVal(f1, mem.value.trim());
    setVal(f2, code);
    var btn = document.querySelector('.form_coupon .btn_comm');
    if (btn) { btn.focus(); btn.style.outline = '3px solid #0f7a63'; setTimeout(function () { btn.style.outline = ''; }, 2500); }
  }

  function render(list) {
    var now = Date.now();
    list = list.filter(function (c) { return !c.expires || new Date(c.expires) > now; });
    if (!list.length) { box.querySelector('#knp-list').textContent = '사용 가능한 쿠폰이 없습니다.'; return; }
    box.querySelector('#knp-list').innerHTML = list.map(function (c, i) {
      var isDone = done.indexOf(c.code) >= 0;
      var exp = c.expires ? '~' + c.expires.slice(5, 10).replace('-', '/') : '기한 없음';
      return '<div style="display:flex;align-items:center;gap:6px;padding:6px 0;border-top:1px solid #e8ece9;' + (isDone ? 'opacity:.45' : '') + '">' +
        '<input type="checkbox" data-d="' + i + '" ' + (isDone ? 'checked' : '') + ' title="등록 완료">' +
        '<div style="flex:1;min-width:0"><div style="font-weight:600;word-break:break-all">' + c.code.replace(/</g, '&lt;') + '</div>' +
        '<div style="font-size:11px;color:#66736d">' + exp + '</div></div>' +
        '<button data-f="' + i + '" style="border:0;background:#0f7a63;color:#fff;border-radius:6px;padding:5px 10px;cursor:pointer;font:inherit;font-size:12px">입력</button></div>';
    }).join('');
    box.querySelectorAll('[data-f]').forEach(function (b) { b.onclick = function () { fill(list[+b.dataset.f].code); }; });
    box.querySelectorAll('[data-d]').forEach(function (cb) {
      cb.onchange = function () {
        var code = list[+cb.dataset.d].code;
        done = done.filter(function (x) { return x !== code; });
        if (cb.checked) done.push(code);
        LS.set('done', done); render(list);
      };
    });
  }

  fetch(SITE + 'data/auto.json?t=' + Date.now())
    .then(function (r) { return r.json(); })
    .then(function (d) { render(d.coupons || []); })
    .catch(function () { box.querySelector('#knp-list').textContent = '쿠폰 목록을 불러오지 못했습니다. 깨비노트에서 코드를 복사해 주세요.'; });
})();
