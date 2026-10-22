"""python -m unittest discover tests"""
import json
import os
import urllib.parse
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import update as U  # noqa: E402
import fixtures as F  # noqa: E402


class Parsers(unittest.TestCase):
    def test_utc_to_kst(self):
        self.assertEqual(U.utc_to_kst("2026-10-13 12:32:12"), "2026-10-13T21:32+09:00")

    def test_tags(self):
        cases = {
            "10월 14일(수) 정기 점검 안내": "점검",
            "10월 14일(수) 정기 점검 완료 및 보상 지급 안내": "보상",
            "10월 15일(목) 점검 후 변경되는 확률 정보 안내": "확률",
            "비정상적인 게임 진행 관련 계정 제재 및 보호 조치 안내 <6차>": "제재",
            "10월 14일(수) 긴급 라이브 방송 안내": "방송",
            "대구콘텐츠페어 도깨비의세계 참가 안내": "안내",
        }
        for t, want in cases.items():
            self.assertEqual(U.tag_of("notice", t), want, t)
        self.assertEqual(U.tag_of("update", "아무거나"), "업데이트")

    def test_maintenance(self):
        text, _ = U.parse_html(F.MAINT_HTML)
        self.assertEqual(U.parse_maintenance(text, 2026), ("2026-10-14T07:00+09:00", "2026-10-14T10:30+09:00"))
        self.assertEqual(U.parse_maintenance("1월 1일(목) 23:00 ~ 02:00", 2026)[1], "2026-01-02T02:00+09:00")
        self.assertIsNone(U.parse_maintenance("일정 미정", 2026))

    def test_coupons(self):
        cs = {c["code"]: c for c in U.parse_coupons(F.COUPON_HTML)}
        self.assertEqual(len(cs), 9)
        self.assertEqual(cs["도깨비1008"]["expires"], "2026-11-05T23:59+09:00")
        self.assertIsNone(cs["도깨비의마음"]["expires"])
        self.assertEqual(cs["도깨비1008"]["reward"], "꿀떡 무기 외형")
        self.assertIn("엽전 100,000개", cs["칭호나와라뚝딱"]["reward"])

    def test_event_deadline(self):
        pub = "2026-10-08T10:00+09:00"
        self.assertEqual(U.event_deadline("X (~10/28)", pub), "2026-10-28T23:59+09:00")
        self.assertEqual(U.event_deadline("X (~10/21 08:00)", pub), "2026-10-21T08:00+09:00")
        self.assertEqual(U.event_deadline("X (~27/10/6 08:00)", pub), "2027-10-06T08:00+09:00")
        self.assertEqual(U.event_deadline("X (~1/5)", "2026-12-20T10:00+09:00"), "2027-01-05T23:59+09:00")
        self.assertIsNone(U.event_deadline("도전! 문파 전투 (~ 별도 공지 시까지)", pub))

    def test_rss_filter(self):
        cfg = json.loads((U.ROOT / "data" / "config.json").read_text(encoding="utf-8"))["youtube"]
        cfg = dict(cfg, days=36500)
        with mock.patch.object(U, "get", F.fake_get):
            vids = U.fetch_videos(cfg)
        ids = [v["id"] for v in vids]
        self.assertEqual(len(ids), 12)
        self.assertNotIn("zzzOTHERzzz", ids)
        self.assertTrue(any("&" in v["title"] for v in vids))  # XML 엔티티 복원


    def test_api_fallback_when_rss_blocked(self):
        cfg = json.loads((U.ROOT / "data" / "config.json").read_text(encoding="utf-8"))["youtube"]
        cfg = dict(cfg, days=36500)

        def fake(url):
            if "feeds/videos.xml" in url:
                raise OSError("HTTP Error 404")
            if "/playlistItems" in url:
                pl = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["playlistId"][0]
                ch = "UC" + pl[2:]
                items = [{"snippet": {"title": t, "publishedAt": d.replace("+00:00", "Z"), "resourceId": {"videoId": v}}}
                         for c, v, t, d in F.VIDEOS if c == ch]
                return json.dumps({"items": items}).encode()
            if "/search" in url:
                return json.dumps({"items": []}).encode()
            if "/videos" in url:
                return json.dumps({"items": [{"id": "OrJ7py7ssqE", "statistics": {"viewCount": "1234"}}]}).encode()
            raise ValueError(url)

        blocked = mock.patch.object(U, "get_page", side_effect=OSError("HTTP Error 404"))
        with blocked, mock.patch.object(U, "get", fake), mock.patch.dict(os.environ, {"YT_API_KEY": "test"}):
            vids = U.fetch_videos(cfg)
        self.assertEqual(len(vids), 12)
        self.assertEqual(next(v for v in vids if v["id"] == "OrJ7py7ssqE")["views"], 1234)

        blocked = mock.patch.object(U, "get_page", side_effect=OSError("HTTP Error 404"))
        with blocked, mock.patch.object(U, "get", fake), mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                U.fetch_videos(cfg)


    def test_parse_relative(self):
        from datetime import datetime, timezone
        now = datetime(2026, 10, 16, 6, 30, tzinfo=timezone.utc)  # KST 15:30
        self.assertEqual(U.parse_relative("3 days ago", now), "2026-10-13T15:00+09:00")
        self.assertEqual(U.parse_relative("Streamed 2 hours ago", now), "2026-10-16T13:00+09:00")
        self.assertEqual(U.parse_relative("50분 전", now), "2026-10-16T14:00+09:00")
        self.assertEqual(U.parse_relative("2주 전", now), "2026-10-02T15:00+09:00")
        self.assertIsNone(U.parse_relative("1.7K views", now))

    def test_channel_page_fallback_without_key(self):
        cfg = json.loads((U.ROOT / "data" / "config.json").read_text(encoding="utf-8"))["youtube"]
        cfg = dict(cfg, days=36500)

        def lockup(vid, title, when):
            return {"richItemRenderer": {"content": {"lockupViewModel": {
                "contentId": vid, "contentType": "LOCKUP_CONTENT_TYPE_VIDEO",
                "metadata": {"lockupMetadataViewModel": {"title": {"content": title}, "metadata": {
                    "contentMetadataViewModel": {"metadataRows": [{"metadataParts": [
                        {"text": {"content": "1.7K views"}}, {"text": {"content": when}}]}]}}}}}}}}

        def page(url):
            ch = url.split("/channel/")[1].split("/")[0]
            items = [lockup(v, t, "2 days ago") for c, v, t, d in F.VIDEOS if c == ch]
            items.append({"richItemRenderer": {"content": {"videoRenderer": {
                "videoId": "old" + ch[-4:], "title": {"runs": [{"text": "도깨비 구형 렌더러"}]},
                "publishedTimeText": {"simpleText": "1 week ago"}}}}})
            return "<script>var ytInitialData = " + json.dumps({"contents": items}) + ";</script>"

        def rss_blocked(url):
            raise OSError("HTTP Error 404")

        some = next(v for c, v, t, d in F.VIDEOS)
        old = [{"id": some, "date": "2026-01-01T09:00+09:00"}]
        with mock.patch.object(U, "get", rss_blocked), mock.patch.object(U, "get_page", page), \
                mock.patch.dict(os.environ, {}, clear=True):
            vids = U.fetch_videos(cfg, old)
        ids = [v["id"] for v in vids]
        self.assertIn(some, ids)
        self.assertNotIn("zzzOTHERzzz", ids)
        self.assertEqual(next(v for v in vids if v["id"] == some)["date"], "2026-01-01T09:00+09:00")
        self.assertTrue(any(i.startswith("old") for i in ids))

        with self.assertRaises(ValueError):
            U.parse_channel_page("<html>consent</html>", "x")


    def test_parse_equip(self):
        html = (Path(__file__).parent / "equip_fixture.html").read_text(encoding="utf-8")
        styles = json.loads((U.ROOT / "data" / "equip.json").read_text(encoding="utf-8"))["styles"]
        r = U.parse_equip(html, styles)
        self.assertEqual(r["lines"], {"희귀": 2, "영웅": 3, "전설": 4})
        self.assertEqual(len(r["stage_prob"]), 17)
        self.assertAlmostEqual(sum(r["stage_prob"]), 100, delta=0.1)
        w = r["table"]["무기"]
        self.assertEqual(w["희귀"]["values"]["공격력"][0], 200)
        self.assertEqual(w["전설"]["values"]["공격력"][-1], 1600)       # 쉼표 제거
        self.assertEqual(w["전설"]["values"]["스타일 피해 · 강인"][-1], 6)  # 12종 공통 값 펼침
        self.assertEqual(len([o for o in w["전설"]["options"] if o.startswith("스타일")]), 12)
        acc = r["table"]["장신구"]["전설"]
        self.assertIn("PvP 공격 정확도", acc["values"])                 # 'PvP공격' 띄어쓰기 보정
        self.assertIn("일반 요귀 피해 증가", acc["values"])
        self.assertTrue(acc["options"]["PvP 피해 증가"]["pvp"])
        with self.assertRaises(ValueError):
            U.parse_equip("<p>표 없음</p>", styles)


class EndToEnd(unittest.TestCase):
    def test_main_writes_auto_json_and_keeps_old_on_failure(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "data").mkdir()
            shutil.copy(U.ROOT / "data" / "config.json", tmp / "data" / "config.json")
            auto = tmp / "data" / "auto.json"
            with mock.patch.multiple(U, ROOT=tmp, AUTO=auto, CONFIG=tmp / "data" / "config.json", get=F.fake_get, EQUIP=tmp / "data" / "equip.json"):
                self.assertEqual(U.main(), 0)
                d = json.loads(auto.read_text(encoding="utf-8"))
                self.assertEqual(d["maintenance"]["start"], "2026-10-14T07:00+09:00")
                self.assertEqual(len(d["coupons"]), 9)
                self.assertTrue(d["events"])
                self.assertTrue(d["notices"][0]["url"].endswith("id=66316"))
                stamp = d["updated_at"]

                # 두 번째 실행: 변경 없음 → 파일 그대로
                U.main()
                self.assertEqual(json.loads(auto.read_text(encoding="utf-8"))["updated_at"], stamp)

            # 네트워크 전부 실패 → 이전 데이터 유지, 종료코드 1
            def boom(url):
                raise OSError("network down")
            with mock.patch.multiple(U, ROOT=tmp, AUTO=auto, CONFIG=tmp / "data" / "config.json", get=boom, get_page=boom, EQUIP=tmp / "data" / "equip.json"):
                self.assertEqual(U.main(), 1)
                self.assertEqual(len(json.loads(auto.read_text(encoding="utf-8"))["coupons"]), 9)
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
