#!/usr/bin/env python3
"""깨비노트 플러스 데이터 수집기.

공식 커뮤니티(forum-api) + YouTube RSS(+선택: YouTube Data API)를 읽어
data/auto.json 을 갱신한다. 표준 라이브러리만 사용 (pip 설치 불필요).

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

ROOT = Path(__file__).resolve().parent.parent
AUTO = ROOT / "data" / "auto.json"
CONFIG = ROOT / "data" / "config.json"

KST = timezone(timedelta(hours=9))
API = "https://forum-api.kakaogames.com/v1/dokkaebi"
POST_URL = "https://forum.kakaogames.com/dokkaebi/postView/?code={board}&id={id}"
UA = "Mozilla/5.0 (kkaebi-note-plus updater; +https://github.com/)"


# ---------------------------------------------------------------- HTTP
def get(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json, */*"})
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
    post = next((n for n in candidates if "쿠폰" in n["title"] and "안내" in n["title"]), None)
    if not post:
        return None
    coupons = parse_coupons(post_content(post["board"], post["id"]))
    return (coupons, post["url"]) if coupons else None


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


def fetch_videos(cfg: dict) -> list[dict]:
    kw = re.compile("|".join(map(re.escape, cfg["keywords"])))
    since = (datetime.now(KST) - timedelta(days=cfg["days"])).isoformat()
    vids: dict[str, dict] = {}
    errors = 0
    for ch in cfg["channels"]:
        try:
            xml = get(f"https://www.youtube.com/feeds/videos.xml?channel_id={ch['id']}")
        except Exception as e:  # 채널 하나 실패해도 계속
            errors += 1
            print(f"  ! RSS 실패 {ch['name']}: {e}", file=sys.stderr)
            continue
        for v in parse_rss(xml, ch["name"]):
            if v["date"] >= since and (ch.get("all") or kw.search(v["title"])):
                v["official"] = bool(ch.get("all"))
                vids[v["id"]] = v

    key = os.environ.get("YT_API_KEY")
    if key:
        try:
            q = urllib.parse.urlencode({
                "part": "snippet", "q": cfg["keywords"][0], "type": "video", "order": "date",
                "regionCode": "KR", "relevanceLanguage": "ko", "maxResults": 25,
                "publishedAfter": (datetime.now(timezone.utc) - timedelta(days=cfg["days"])).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "key": key,
            })
            for it in get_json(f"https://www.googleapis.com/youtube/v3/search?{q}").get("items", []):
                s = it["snippet"]
                vid = it["id"]["videoId"]
                vids.setdefault(vid, {
                    "id": vid, "title": _unescape(s["title"]), "channel": _unescape(s["channelTitle"]),
                    "date": datetime.fromisoformat(s["publishedAt"].replace("Z", "+00:00")).astimezone(KST).isoformat(timespec="minutes"),
                    "views": None, "official": False,
                })
        except Exception as e:
            print(f"  ! YouTube API 실패: {e}", file=sys.stderr)

    if errors == len(cfg["channels"]) and not vids:
        raise RuntimeError("모든 RSS 수집 실패")
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
        new["videos"] = fetch_videos(cfg["youtube"])

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
