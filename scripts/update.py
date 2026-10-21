#!/usr/bin/env python3
"""깨비노트 플러스 데이터 수집기.

공식 커뮤니티(forum-api) + YouTube RSS/채널 페이지(+선택: YouTube Data API)를 읽어
data/auto.json 을 갱신한다. 표준 라이브러리 + curl_cffi(공식 API 403 회피용, Actions 에서 설치).

  python scripts/update.py            # 수집 후 변경 시 data/auto.json 저장
  python scripts/update.py --dry-run  # 저장하지 않고 결과만 출력

환경변수
  YT_API_KEY  (선택) 있으면 "도깨비의세계" 키워드 검색 결과도 합친다.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

try:  # 공식 커뮤니티 API 는 파이썬 기본 TLS 지문을 403 으로 막음 → 크롬 TLS 를 흉내 내는 curl_cffi 사용
    from curl_cffi import requests as cffi_requests
except ImportError:  # 로컬 테스트 등 미설치 환경에서는 urllib 로 시도
    cffi_requests = None

ROOT = Path(__file__).resolve().parent.parent
AUTO = ROOT / "data" / "auto.json"
CONFIG = ROOT / "data" / "config.json"

KST = timezone(timedelta(hours=9))
API = "https://forum-api.kakaogames.com/v1/dokkaebi"
POST_URL = "https://forum.kakaogames.com/dokkaebi/postView/?code={board}&id={id}"
UA = "Mozilla/5.0 (kkaebi-note-plus updater; +https://github.com/)"


# ---------------------------------------------------------------- HTTP
def get(url: str, timeout: int = 20) -> bytes:
    headers = {"User-Agent": UA, "Accept": "application/json, */*"}
    if "kakaogames.com" in url:  # 공식 커뮤니티 API 는 봇 UA 를 403 으로 막음 → 브라우저와 같은 헤더로 요청
        headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
            "Accept-Language": "ko-KR,ko;q=0.9", "Origin": "https://forum.kakaogames.com",
            "Referer": "https://forum.kakaogames.com/dokkaebi/",
        })
    if cffi_requests is not None and "kakaogames.com" in url:
        # 헤더는 impersonate 가 크롬과 똑같이 채움 — UA 등을 직접 넣으면 TLS 지문과 어긋나 다시 403
        import time
        for attempt in range(3):
            time.sleep(0.6 + attempt * 2)  # 연속 요청 간격 (봇 판정 완화)
            r = cffi_requests.get(url, impersonate="chrome", timeout=timeout)
            if r.status_code == 200:
                return r.content
            print(f"  ! {r.status_code} {urllib.parse.urlparse(url).path} (시도 {attempt + 1}) "
                  f"server={r.headers.get('server')} cache={r.headers.get('x-cache')}", file=sys.stderr)
        r.raise_for_status()
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def get_json(url: str) -> dict:
    return json.loads(get(url).decode("utf-8"))


def api(path: str, **params) -> dict:
    params.setdefault("lang", "ko")
    data = get_json(f"{API}/{path}?{urllib.parse.urlencode(params)}")
    return data.get("payload", data)


# ---------------------------------------------------------------- 시간
def utc_to_kst(s: str) -> str:
    """forum-api 의 'YYYY-MM-DD HH:MM:SS'(UTC) -> KST ISO8601."""
    dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).isoformat(timespec="minutes")


# ---------------------------------------------------------------- HTML -> 텍스트/표
class _Html(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "table", "section"}

    def __init__(self):
        super().__init__()
        self.text: list[str] = []
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCK:
            self.text.append("\n")
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
        if tag in self.BLOCK:
            self.text.append("\n")

    def handle_data(self, data):
        self.text.append(data)
        if self._cell is not None:
            self._cell.append(data)


def parse_html(html: str) -> tuple[str, list[list[str]]]:
    p = _Html()
    p.feed(html or "")
    text = re.sub(r"[ \t\xa0]+", " ", "".join(p.text))
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return text, p.rows


# ---------------------------------------------------------------- 공지
def tag_of(board: str, title: str) -> str:
    rules = [
        ("제재", r"제재|보호 조치|강경 대응"),
        ("확률", r"확률"),
        ("점검", r"점검(?!.*완료)"),
        ("보상", r"보상|완료"),
        ("방송", r"라이브|방송"),
        ("이벤트", r"이벤트|당첨"),
    ]
    if board == "update":
        return "업데이트"
    for tag, pat in rules:
        if re.search(pat, title):
            return tag
    return "이벤트" if board == "event" else "안내"


def post_item(board: str, p: dict) -> dict:
    return {
        "id": p["post_id"],
        "board": board,
        "title": p["title"].strip(),
        "date": utc_to_kst(p["published_at"]),
        "tag": tag_of(board, p["title"]),
        "url": POST_URL.format(board=board, id=p["post_id"]),
    }


def fetch_notices(boards: list[str], size: int, limit: int) -> list[dict]:
    items: dict[int, dict] = {}
    for b in boards:
        for p in api(f"boards/{b}/list", size=size).get("posts", []):
            items[p["post_id"]] = post_item(b, p)
    return sorted(items.values(), key=lambda x: x["date"], reverse=True)[:limit]


def fetch_pinned(board: str = "notice") -> list[dict]:
    data = api(f"boards/{board}/pinned", page=1)
    posts = data.get("posts", data) if isinstance(data, dict) else data
    return [post_item(board, p) for p in posts or []]


def post_content(board: str, post_id: int) -> str:
    return api(f"boards/{board}/posts/{post_id}").get("content", "")


# ---------------------------------------------------------------- 점검 일정
MAINT_RE = re.compile(
    r"(\d{1,2})월\s*(\d{1,2})일\s*\([월화수목금토일]\)\s*(\d{1,2}):(\d{2})\s*~\s*"
    r"(?:(\d{1,2})월\s*(\d{1,2})일\s*\([월화수목금토일]\)\s*)?(\d{1,2}):(\d{2})"
)


def parse_maintenance(text: str, year: int) -> tuple[str, str] | None:
    # '점검 일정' 섹션 우선, 없으면 본문 전체에서 첫 매칭
    i = text.find("점검 일정")
    m = MAINT_RE.search(text, i if i >= 0 else 0) or MAINT_RE.search(text)
    if not m:
        return None
    mo, d, h1, m1, mo2, d2, h2, m2 = m.groups()
    start = datetime(year, int(mo), int(d), int(h1), int(m1), tzinfo=KST)
    end = datetime(year, int(mo2 or mo), int(d2 or d), int(h2), int(m2), tzinfo=KST)
    if end <= start:  # 자정 넘김
        end += timedelta(days=1)
    return start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes")


def find_maintenance(notices: list[dict], max_check: int = 4) -> dict | None:
    """최신 점검 공지 몇 개를 읽어 아직 안 끝난 것을 우선 반환, 없으면 가장 최근 것."""
    found = []
    for n in notices:  # 최신순
        t = n["title"]
        if re.search(r"점검 안내", t) and not re.search(r"완료|보상", t):
            text, _ = parse_html(post_content(n["board"], n["id"]))
            rng = parse_maintenance(text, int(n["date"][:4]))
            if rng:
                found.append({"title": t, "kind": "임시" if "임시" in t else "정기",
                              "start": rng[0], "end": rng[1], "url": n["url"]})
            if len(found) >= max_check:
                break
    now = datetime.now(KST).isoformat()
    upcoming = [f for f in found if f["end"] > now]
    return min(upcoming, key=lambda f: f["start"]) if upcoming else (found[0] if found else None)


# ---------------------------------------------------------------- 이벤트 마감일
# 제목 끝의 (~10/28) (~10/21 08:00) (~27/10/6 08:00) 형식
DEADLINE_RE = re.compile(r"\(~\s*(?:(\d{2})/)?(\d{1,2})/(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?\s*\)")


def event_deadline(title: str, published_kst: str) -> str | None:
    m = DEADLINE_RE.search(title)
    if not m:
        return None
    yy, mo, d, h, mi = m.groups()
    pub = datetime.fromisoformat(published_kst)
    year = 2000 + int(yy) if yy else pub.year
    try:
        dt = datetime(year, int(mo), int(d), int(h or 23), int(mi or 59), tzinfo=KST)
    except ValueError:
        return None
    if not yy and dt < pub - timedelta(days=1):  # 연말→연초
        dt = dt.replace(year=year + 1)
    return dt.isoformat(timespec="minutes")


def fetch_events(notices: list[dict]) -> list[dict]:
    """event 게시판 글 중 마감일이 제목에 있는 것만, 마감 임박 순."""
    out = []
    for n in notices:
        if n["board"] != "event":
            continue
        end = event_deadline(n["title"], n["date"])
        if end:
            name = DEADLINE_RE.sub("", n["title"]).strip()
            name = re.sub(r"^\[[^\]]*\]\s*", "", name)
            out.append({"name": name, "end": end, "url": n["url"]})
    return sorted(out, key=lambda e: e["end"])


# ---------------------------------------------------------------- 쿠폰
EXP_RE = re.compile(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일.*?(\d{1,2}):(\d{2})")


def parse_coupons(html: str) -> list[dict]:
    text, rows = parse_html(html)
    # 상세 안내의 '쿠폰 코드 : X' 다음 '구성품 : Y'
    rewards, code = {}, None
    for line in text.splitlines():
        line = line.strip().lstrip("-• ").strip()
        if mm := re.match(r"쿠폰 코드\s*:\s*(.+)", line):
            code = mm.group(1).strip()
        elif code and (mm := re.match(r"구성품\s*:\s*(.+)", line)):
            rewards[code] = mm.group(1).strip()
            code = None

    coupons = []
    header = None
    for r in rows:
        if any("쿠폰 코드" in c for c in r):
            header = r
            continue
        if not header or len(r) < 3:
            continue
        col = {h: i for i, h in enumerate(header)}
        ci = next((i for h, i in col.items() if "코드" in h), 1)
        ei = next((i for h, i in col.items() if "기간" in h), 2)
        gi = next((i for h, i in col.items() if "구분" in h), 0)
        c = r[ci].strip()
        if not c:
            continue
        m = EXP_RE.search(r[ei])
        exp = (
            datetime(*map(int, m.groups()[:3]), int(m.group(4)), int(m.group(5)), tzinfo=KST).isoformat(timespec="minutes")
            if m else None
        )
        coupons.append({"code": c, "name": r[gi], "expires": exp, "reward": rewards.get(c, "")})
    return coupons


def fetch_coupons(candidates: list[dict]) -> tuple[list[dict], str] | None:
    """'쿠폰 종합 안내' 글을 우선, 없으면 제목에 쿠폰·안내가 들어간 글을 차례로 시도 (표가 있는 첫 글)."""
    posts = [n for n in candidates if "쿠폰" in n["title"] and "안내" in n["title"]]
    posts.sort(key=lambda n: "종합" not in n["title"])  # 종합 안내가 맨 앞 (안정 정렬)
    seen = set()
    for post in posts:
        if post["id"] in seen:
            continue
        seen.add(post["id"])
        try:
            coupons = parse_coupons(post_content(post["board"], post["id"]))
        except Exception as e:
            print(f"  ! 쿠폰 글 파싱 실패 {post['title']}: {e}", file=sys.stderr)
            continue
        if coupons:
            return coupons, post["url"]
    return None


# ---------------------------------------------------------------- 유튜브
NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
      "media": "http://search.yahoo.com/mrss/"}


def parse_rss(xml: bytes, channel_name: str) -> list[dict]:
    root = ET.fromstring(xml)
    out = []
    for e in root.findall("a:entry", NS):
        vid = e.findtext("yt:videoId", namespaces=NS)
        stats = e.find("media:group/media:community/media:statistics", NS)
        out.append({
            "id": vid,
            "title": e.findtext("a:title", namespaces=NS) or "",
            "channel": channel_name,
            "date": datetime.fromisoformat(e.findtext("a:published", namespaces=NS)).astimezone(KST).isoformat(timespec="minutes"),
            "views": int(stats.get("views")) if stats is not None and stats.get("views") else None,
        })
    return out


def yt_api(path: str, key: str, **params) -> dict:
    params["key"] = key
    return get_json(f"https://www.googleapis.com/youtube/v3/{path}?{urllib.parse.urlencode(params)}")


def fetch_channel_api(ch: dict, key: str) -> list[dict]:
    """업로드 재생목록(UC→UU) 조회. 1 unit/호출이라 할당량 부담 거의 없음."""
    data = yt_api("playlistItems", key, part="snippet", maxResults=15, playlistId="UU" + ch["id"][2:])
    out = []
    for it in data.get("items", []):
        s = it["snippet"]
        out.append({
            "id": s["resourceId"]["videoId"], "title": _unescape(s["title"]), "channel": ch["name"],
            "date": datetime.fromisoformat(s["publishedAt"].replace("Z", "+00:00")).astimezone(KST).isoformat(timespec="minutes"),
            "views": None,
        })
    return out


# ---------------------------------------------------------------- YouTube 채널 페이지 (키 없는 대체 경로)
PAGE_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
_REL_UNITS = [  # (정규식, 초)
    (r"(\d+)\s*(?:second|초)", 1), (r"(\d+)\s*(?:minute|분)", 60), (r"(\d+)\s*(?:hour|시간)", 3600),
    (r"(\d+)\s*(?:day|일)", 86400), (r"(\d+)\s*(?:week|주)", 7 * 86400),
    (r"(\d+)\s*(?:month|개월|달)", 30 * 86400), (r"(\d+)\s*(?:year|년)", 365 * 86400),
]


def parse_relative(text: str, now: datetime) -> str | None:
    """'3 days ago' / '3일 전' / 'Streamed 2 hours ago' -> KST ISO (시 단위로 내림)."""
    t = text.lower()
    if "ago" not in t and "전" not in t:
        return None
    for pat, sec in _REL_UNITS:
        m = re.search(pat, t)
        if m:
            dt = (now - timedelta(seconds=int(m.group(1)) * sec)).astimezone(KST)
            return dt.replace(minute=0, second=0, microsecond=0).isoformat(timespec="minutes")
    return None


def _walk(o, key: str):
    if isinstance(o, dict):
        if key in o:
            yield o[key]
        for v in o.values():
            yield from _walk(v, key)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v, key)


def parse_channel_page(html: str, channel_name: str, now: datetime | None = None) -> list[dict]:
    """채널 /videos 페이지의 ytInitialData 에서 영상 목록 추출 (신형 lockupViewModel·구형 videoRenderer 모두)."""
    now = now or datetime.now(timezone.utc)
    m = re.search(r"var ytInitialData\s*=\s*(\{.*?\});\s*</script>", html, re.S)
    if not m:
        raise ValueError("ytInitialData 없음 (차단·동의 페이지 가능성)")
    data = json.loads(m.group(1))
    out, seen = [], set()

    def add(vid, title, when):
        date = parse_relative(when or "", now)
        if vid and title and date and vid not in seen:
            seen.add(vid)
            out.append({"id": vid, "title": title, "channel": channel_name, "date": date, "views": None})

    for lk in _walk(data, "lockupViewModel"):
        if lk.get("contentType") not in (None, "LOCKUP_CONTENT_TYPE_VIDEO"):
            continue
        md = lk.get("metadata", {}).get("lockupMetadataViewModel", {})
        texts = [t for row in _walk(md, "metadataRows") for r in row for p in r.get("metadataParts", [])
                 for t in (p.get("text", {}).get("content", ""), p.get("accessibilityLabel", ""))]
        add(lk.get("contentId"), md.get("title", {}).get("content"), next((x for x in texts if parse_relative(x, now)), None))
    for vr in _walk(data, "videoRenderer"):
        title = "".join(r.get("text", "") for r in vr.get("title", {}).get("runs", [])) or vr.get("title", {}).get("simpleText")
        add(vr.get("videoId"), title, vr.get("publishedTimeText", {}).get("simpleText"))
    if not out:
        title = re.search(r"<title>(.*?)</title>", html, re.S)
        tabs = [t.get("title") for t in _walk(data, "tabRenderer") if isinstance(t, dict)]
        diag = (f"len={len(html)} lockup={html.count('lockupViewModel')} videoRenderer={html.count('videoRenderer')} "
                f"title={title.group(1).strip()[:40] if title else None!r} tabs={tabs[:6]}")
        first = next(_walk(data, "lockupViewModel"), None)
        if first:
            md = first.get("metadata", {}).get("lockupMetadataViewModel", {})
            diag += f" first={json.dumps({'type': first.get('contentType'), 'id': first.get('contentId'), 'md': md.get('metadata')}, ensure_ascii=False)[:900]}"
        raise ValueError(f"영상 목록을 찾지 못함 (페이지 구조 변경 가능성) [{diag}]")
    return out


def get_page(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": PAGE_UA, "Accept-Language": "en-US,en;q=0.9",
        "Cookie": "CONSENT=YES+cb; SOCS=CAI",  # EU 동의 페이지 회피
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def fetch_channel_page(ch: dict, per_channel: int = 15) -> list[dict]:
    """동영상 탭 + 라이브 탭(다시보기)을 합쳐 최신순 15개 — RSS 가 주던 범위와 맞춘다."""
    base = f"https://www.youtube.com/channel/{ch['id']}"
    items = parse_channel_page(get_page(f"{base}/videos?hl=en&gl=US"), ch["name"])
    try:
        items += parse_channel_page(get_page(f"{base}/streams?hl=en&gl=US"), ch["name"])
    except Exception:
        pass  # 라이브 탭이 없거나 비어 있는 채널
    uniq = {v["id"]: v for v in items}
    return sorted(uniq.values(), key=lambda v: v["date"], reverse=True)[:per_channel]


def fetch_videos(cfg: dict, old: list[dict] | None = None) -> list[dict]:
    """채널별 RSS → 실패 시 채널 페이지 → 그래도 실패면 YouTube Data API(키 있을 때) → 키워드 검색 추가.

    채널 페이지는 '3일 전' 같은 상대 시각만 주므로, 이미 아는 영상은 이전 날짜를 유지해
    실행할 때마다 날짜가 흔들려 불필요한 커밋이 생기지 않게 한다.

    YouTube 는 GitHub Actions 같은 데이터센터 IP 의 RSS 요청을 404/500 으로 막는 경우가 많다.
    그래서 Actions 에서는 YT_API_KEY 를 등록해 두는 걸 권장.
    """
    kw = re.compile("|".join(map(re.escape, cfg["keywords"])))
    since = (datetime.now(KST) - timedelta(days=cfg["days"])).isoformat()
    key = os.environ.get("YT_API_KEY")
    vids: dict[str, dict] = {}
    known = {v["id"]: v["date"] for v in (old or []) if v.get("id") and v.get("date")}
    ok = 0
    for ch in cfg["channels"]:
        items = None
        try:
            items = parse_rss(get(f"https://www.youtube.com/feeds/videos.xml?channel_id={ch['id']}"), ch["name"])
        except Exception as e:
            print(f"  ! RSS 실패 {ch['name']}: {e}", file=sys.stderr)
            try:
                items = fetch_channel_page(ch)
                for v in items:
                    if v["id"] in known:
                        v["date"] = known[v["id"]]
            except Exception as e2:
                print(f"  ! 채널 페이지 실패 {ch['name']}: {e2}", file=sys.stderr)
            if items is None and key:
                try:
                    items = fetch_channel_api(ch, key)
                except Exception as e2:
                    print(f"  ! API 실패 {ch['name']}: {e2}", file=sys.stderr)
        if items is None:
            continue
        ok += 1
        for v in items:
            if v["date"] >= since and (ch.get("all") or kw.search(v["title"])):
                v["official"] = bool(ch.get("all"))
                vids[v["id"]] = v

    if key:
        try:  # 등록 안 된 채널의 영상까지 키워드 검색 (100 unit/호출)
            data = yt_api("search", key, part="snippet", q=cfg["keywords"][0], type="video", order="date",
                          regionCode="KR", relevanceLanguage="ko", maxResults=25,
                          publishedAfter=(datetime.now(timezone.utc) - timedelta(days=cfg["days"])).strftime("%Y-%m-%dT%H:%M:%SZ"))
            for it in data.get("items", []):
                s, vid = it["snippet"], it["id"]["videoId"]
                vids.setdefault(vid, {
                    "id": vid, "title": _unescape(s["title"]), "channel": _unescape(s["channelTitle"]),
                    "date": datetime.fromisoformat(s["publishedAt"].replace("Z", "+00:00")).astimezone(KST).isoformat(timespec="minutes"),
                    "views": None, "official": False,
                })
            ok += 1
        except Exception as e:
            print(f"  ! YouTube 검색 실패: {e}", file=sys.stderr)
        # 조회수 채우기 (50개당 1 unit)
        need = [v for v in vids.values() if v.get("views") is None]
        for i in range(0, len(need), 50):
            try:
                ids = ",".join(v["id"] for v in need[i:i + 50])
                for it in yt_api("videos", key, part="statistics", id=ids).get("items", []):
                    vc = it.get("statistics", {}).get("viewCount")
                    if vc and it["id"] in vids:
                        vids[it["id"]]["views"] = int(vc)
            except Exception as e:
                print(f"  ! 조회수 조회 실패: {e}", file=sys.stderr)

    if ok == 0:
        hint = "" if key else " (Actions 에서는 YT_API_KEY 시크릿 등록 필요)"
        raise RuntimeError("모든 영상 수집 실패" + hint)
    return sorted(vids.values(), key=lambda v: v["date"], reverse=True)[: cfg["limit"]]


def _unescape(s: str) -> str:
    import html
    return html.unescape(s)


# ---------------------------------------------------------------- main
def main() -> int:
    dry = "--dry-run" in sys.argv
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    old = json.loads(AUTO.read_text(encoding="utf-8")) if AUTO.exists() else {}
    new = {k: v for k, v in old.items() if k != "updated_at"}
    status = {}

    def step(name, fn):
        try:
            fn()
            status[name] = "ok"
        except Exception as e:  # 실패한 섹션은 이전 값 유지
            status[name] = f"fail: {e}"
            print(f"! {name} 실패 → 이전 데이터 유지: {e}", file=sys.stderr)

    notices: list[dict] = []
    pinned: list[dict] = []

    def s_notices():
        nonlocal notices, pinned
        notices = fetch_notices(cfg["boards"], size=20, limit=999)  # 전체(이벤트·점검 탐색용)
        try:
            pinned = fetch_pinned("notice")
        except Exception:
            pinned = []
        new["notices"] = notices[: cfg["notice_limit"]]  # 화면 표시용
        new["pinned"] = pinned

    def s_maint():
        if not notices:
            raise RuntimeError("공지 목록 없음")
        m = find_maintenance(notices)
        if m:
            new["maintenance"] = m

    def s_coupons():
        r = fetch_coupons(pinned + notices)
        if not r:
            raise RuntimeError("쿠폰 종합 안내 글을 찾지 못함")
        new["coupons"], new["coupon_source"] = r

    def s_events():
        if not notices:
            raise RuntimeError("공지 목록 없음")
        new["events"] = fetch_events(notices)

    def s_videos():
        new["videos"] = fetch_videos(cfg["youtube"], old.get("videos"))

    step("notices", s_notices)
    step("maintenance", s_maint)
    step("coupons", s_coupons)
    step("events", s_events)
    step("videos", s_videos)

    for k, v in status.items():
        print(f"{k:12} {v}  ({len(new.get(k, []) or []) if isinstance(new.get(k), list) else ''})")

    if all(v != "ok" for v in status.values()):
        print("전체 수집 실패 — 기존 파일 유지", file=sys.stderr)
        return 1  # Actions 를 빨간불로

    changed = new != {k: v for k, v in old.items() if k != "updated_at"}
    if not changed:
        print("변경 없음")
        return 0
    new["updated_at"] = datetime.now(KST).isoformat(timespec="minutes")
    out = json.dumps(new, ensure_ascii=False, indent=1)
    if dry:
        print(out[:3000])
    else:
        AUTO.write_text(out + "\n", encoding="utf-8")
        print(f"저장: {AUTO.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
