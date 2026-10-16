# 깨비노트 플러스

도깨비의세계 공지·쿠폰·이벤트 마감·점검 카운트다운·공략 영상을 **자동으로 갱신**하는 정적 사이트.
GitHub Pages + GitHub Actions 만으로 동작한다 (서버·DB·비용 없음).

## 구조

```
index.html                     화면 (data/*.json 을 읽어서 그림)
data/auto.json                 ← Actions 가 2시간마다 자동 갱신 (손대지 말 것)
data/manual.json               ← 사람이 고치는 것: 운영자 메모, 수동 일정, 숙제 기본값
data/config.json               수집 설정: 게시판, 유튜브 채널 목록, 키워드
scripts/update.py              수집기 (Python 표준 라이브러리만 사용)
tests/                         파서 단위 테스트 + 가짜 응답으로 돌리는 E2E 테스트
.github/workflows/update.yml   스케줄 실행 → 변경 있으면 auto.json 커밋
```

```
[cron 2h] → update.py ─┬─ forum-api.kakaogames.com  공지/업데이트/이벤트 목록, 점검·쿠폰 공지 본문
                       └─ YouTube RSS (채널별)       최근 14일, 키워드 필터
          → data/auto.json 변경 시 commit → Pages 재배포 → index.html 이 fetch
```

## 자동으로 바뀌는 것

| 항목 | 출처 | 방식 |
|---|---|---|
| 공지 목록 | 공식 커뮤니티 공지·업데이트·이벤트 게시판 | 최신 40개, 제목 키워드로 태그 분류 |
| 점검 카운트다운 | 최신 "점검 안내" 공지 본문 | `10월 14일(수) 07:00 ~ 10:30` 형식 파싱. 공지 없으면 매주 수요일 추정 |
| 쿠폰 | 고정 공지 "쿠폰 종합 안내"의 표 | 코드·만료일·구성품. 만료되면 자동으로 숨김 |
| 이벤트 마감 D-day | 이벤트 게시판 제목의 `(~10/21 08:00)` | 마감일 추출, 같은 시각 마감은 묶어서 표시 |
| 공략 영상 | config.json 의 채널 RSS | 공식 채널은 전부, 나머지는 제목에 "도깨비" 포함 시 |

수집 단계별로 실패를 격리한다. 예를 들어 유튜브만 실패하면 영상은 이전 값을 유지하고 나머지는 갱신된다. 전부 실패하면 Actions 가 빨간불이 된다.

## 처음 설정

1. 이 폴더를 GitHub 저장소로 push
2. **Settings → Pages** → Source: `Deploy from a branch`, Branch: `main` / `/ (root)`
3. **Settings → Actions → General → Workflow permissions** → `Read and write permissions`
4. **Actions** 탭 → `update-data` → `Run workflow` 로 첫 실행, 로그에서 `notices ok` 등 확인

**영상 수집용 YouTube API 키 (사실상 필수)** — YouTube 는 GitHub Actions 서버의 RSS 요청을 404/500 으로 막는다.
1. https://console.cloud.google.com → 프로젝트 생성 → "YouTube Data API v3" 사용 설정
2. 사용자 인증 정보 → API 키 만들기 → (권장) API 제한사항에서 YouTube Data API v3 만 허용
3. 저장소 **Settings → Secrets and variables → Actions → New repository secret** → 이름 `YT_API_KEY`
무료 할당량(10,000 unit/일) 중 하루 약 1,300 unit 사용. 키가 없으면 영상만 이전 목록을 유지하고 나머지는 정상 갱신된다.

## 평소 운영

- 운영자 메모·수동 일정 수정: `data/manual.json` 편집 → 커밋 (GitHub 웹에서 바로 가능)
- 유튜버 추가: `data/config.json` 의 `channels` 에 `{"name": "...", "id": "UC..."}` 추가
  (채널 ID 는 채널 페이지 → 공유 → 채널 ID 복사)
- 즉시 갱신: Actions → `update-data` → Run workflow

## 로컬 실행

```bash
python -m unittest discover tests     # 테스트
python scripts/update.py --dry-run    # 실제 수집, 저장 안 함
python -m http.server 8000            # http://localhost:8000 (file:// 로 열면 fetch 가 막힘)
```

## 주의

- 공식 커뮤니티 API 는 공개 문서가 없는 비공식 엔드포인트다. 구조가 바뀌면 파서가 깨질 수 있다.
  Actions 실패 알림 메일이 오면 `tests/` 부터 돌려볼 것.
- 요청은 2시간에 십여 건 수준으로 제한했다. 주기를 과하게 줄이지 말 것.
- GitHub 는 60일간 저장소 활동이 없으면 스케줄 워크플로를 멈춘다. 멈추면 Actions 탭에서 다시 켜면 된다.
- 체크 상태(숙제·쿠폰 사용 여부)는 각자 브라우저 localStorage 에만 저장된다.
