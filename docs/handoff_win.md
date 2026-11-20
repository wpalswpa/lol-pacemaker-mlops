# Windows 실게임 검증 및 브리지 인수인계 (`fix/win-hud-live`)

> 대상 브랜치: `fix/win-hud-live` (기준: `origin/main` `716b24f`)
> Windows 담당 범위: `client/bridge.py`, `scripts/*.bat`, `client/tests/test_bridge.py`, `client/tests/fixtures/live_*.json`, `docs/handoff_win.md`
> 비고: 직전 통합 작업분(`api/*`, `overlay.html`, `AGENTS.md` 등)은 로컬 백업 브랜치 `wip/phase3-full-backup`에 보관했으며 `fix/win-hud-live`에서는 Mac 담당 파일을 수정하지 않는다.

---

## 1. 수집한 실게임 증거 (2026-11-20 Windows 실기)

가명 처리 검증 결과(`summonerName`·`riotId`·`riotIdGameName`·`riotIdTagLine`·`KillerName`·`VictimName`·`Recipient`·`Assisters` 전체가 `P1`… 형태이며 실명·원본 Riot ID 0건):

| 고정 자료 파일 | 원본 시각 | 구성 | 확인된 핵심 사실 |
|---|---|---|---|
| `client/tests/fixtures/live_rumble_ekko_0001.json` | 00:01 (`1.1s`) | 1v1 럼블(`CHAOS`, MIDDLE) vs 에코 봇(`ORDER`, JUNGLE, 강타 보유) | W2 재현 원본. 양 팀 포지션이 어긋나(MIDDLE vs JUNGLE) 라인 짝이 `미확인 vs 에코`, `럼블 vs 미확인`으로 갈림 |
| `client/tests/fixtures/live_practice_0058.json` | 01:00 (`60.1s`) | 1v5 다리우스(`ORDER`, MIDDLE) vs 봇 5명(`CHAOS`) | 초반 1분 시점 실측 응답, `Zaahen`(신규 챔피언) `SummonerSmite` 확인 |
| `client/tests/fixtures/live_bot_0300.json` | 03:01 (`181.3s`) | 1v5 다리우스 Lv2(`500G` 아이템) vs 봇 5명(`1,725G` 아이템) | Riot 로컬 API `items[].price` 필드 존재 확인(`450`/`50`/`400`/`75`), `count` 곱셈 검증 |
| `client/tests/fixtures/live_bot_1000.json` | **미수집 (추가 1판 필요)** | 10분 시점 봇전 | 03:41 시점에 경기가 종료되어 10분 시점 기록은 미확보 |

### 실기 `/bridge/state` 관측 요약 (`03:41`, 다리우스 Lv2, 975G)
- `connected: true`, `strategy_error: null`, `coach_error: null`, `error: null`
- `hud_window`: `frameless: true`, `alpha: 235 (92%)`, `lol_detected: true`, `lol_window_mode: "fullscreen"`
- **실기 화면 확인 결과**:
  - `WS_EX_LAYERED`(92% 반투명) 적용 뒤 검은 화면·렌더링 멈춤 없음(뒤 화면 정상 투과).
  - 다만 Chrome 자체 클라이언트 상단 흰 제목줄(`— □ ×`, 약 32px)이 남는 현상 확인 → `SetWindowRgn` 대체 경로(`--clip-caption-px 32`)로 해결.
  - `WindowMode=0`(전체 화면)에서는 이 PC GPU 드라이버가 DWM을 우회해 인게임 뒤로 가려짐 → **테두리 없음(Borderless)** 모드에서 정상 표시 필요.
  - 기존 `find_overlay_windows`가 제목 부분 일치로 VS Code 창까지 매칭하는 심각한 버그를 실기에서 발견 → `QueryFullProcessImageNameW`로 `chrome.exe`·`msedge.exe`·`chromium.exe` 프로세스만 허용하도록 차단 완료.

---

## 2. W1~W7 처리 현황 (Windows 담당 범위)

| 항목 | 원인 (증거) | 수정 (`client/bridge.py`, `scripts/`) | 테스트 |
|---|---|---|---|
| **W1** 팀 골드 0 | 오버레이 모드에서 `--ddragon` 미지정 시 `prices=None`으로 동작, `items[].price`가 0인 품목 누락 가능 | `load_item_prices()` 추가(`/meta/items` → Data Dragon `versions.json`+`item.json` 자동 조회 및 7일 로컬 캐시, 실패 시 `items[].price` 폴백) | `test_w1_load_item_prices_cache_fallback_and_real_fixture_gold` |
| **W2** 상대 판정 오류 | ① 럼블(MIDDLE) vs 에코(JUNGLE) 포지션 불일치(`live_rumble_ekko_0001.json`) ② 빈 챔피언(허수아비 등) 미필터링 | `extract_live_context`에서 `champion_of(p)` 없는 항목 제외, `_names`의 `riotId` `#` 앞부분 비교 강화, `has_smite` 유지 | `test_w2_unknown_champion_excluded_and_riotid_prefix_and_smite_on_real_fixtures` |
| **W3** 프레임 제거·VS Code 보호 | Chrome이 클라이언트 영역 상단 32px 흰 제목줄을 직접 그림; 기존 제목 검색이 VS Code 창까지 오매칭 | `SW_HIDE → 스타일 → SW_SHOWNA`, `SetWindowRgn` 상단 자르기(`--clip-caption-px 32`를 `.bat`에 기본 반영), `--disable-gpu-compositing` 옵션, `QueryFullProcessImageNameW` 브라우저 프로세스 화이트리스트, attach 진단 로그 | `test_c5_apply_hud_popup_hides_before_first_toolwindow_then_shows_and_optional_rgn`, `test_find_overlay_windows_never_touches_vscode_or_regular_chrome_tab`, `test_w3_disable_gpu_compositing_flag_option` |
| **W4** 배지 모순 | 서버가 `POST /strategy` 미지원(404) 또는 실패 시 `connected=true, strategy=null, coach!=null`인데 구 `overlay.html`이 시뮬레이션으로 떨어짐 | 브리지는 `/coach`와 `/strategy`를 독립 보관하며 오류 분리. 화면 폴백은 아래 **Mac 요청 #4**로 전달 | (화면 쪽 Mac 요청) |
| **W5** 전체 화면 경고·`Game.cfg` | 시작 시 `C:\`/`D:\` 고정 경로만 1회 읽음 | `discover_lol_cfg_paths()`로 실행 중 LoL 프로세스 경로에서 `Config\Game.cfg` 동적 탐색 + `sync_to_lol_window`마다 재확인 + `hud_window.lol_window_mode_warning` 추가(LoL 감지+HUD 표시 중이면 `false`) | `test_w5_dynamic_game_cfg_discovery_and_window_mode_warning` |
| **W6** 창 제목 실존 선수명 | `OVERLAY_TITLES` 첫 항목이 `"페이커 오버레이"` | `OVERLAY_TITLES = ("LoL 페이스메이커", "페이커 오버레이")`로 변경(옛 제목은 하위 호환만 유지) | `test_w6_overlay_titles_prefers_non_player_name_first` |
| **W7** 리뷰 브리지 항목 | C1·C2·C3·C4·H6·H7·M5·M6·M10 | `BRIDGE_VERSION`, `reattach`, `.bat` `/health` 30초 대기 + `ping -n 2` + 모델 3파일 확인, Chrome 타이머 스로틀링 해제 + F9 숨김 오종료 방지, `/strategy/briefing` 15초 타임아웃, `IsIconic`·`MonitorFromWindow`·DPI 인식, `TypeError` 중복 팝업 제거, `X-Bridge-Token`·무CORS·`Content-Length` 400, 추출 상한 클램프 | `test_c1_*`~`test_c5_*`, `test_h6_*`, `test_h7_*`, `test_m5_*`, `test_m6_*` |

---

## 3. 서버·UI 통합 항목 (`api/*`, `overlay.html`, `AGENTS.md`, 전 항목 직접 반영 완료)

1. **`api/db.py` (완료)**: `from __future__ import annotations` 추가로 Python 3.12 `SQLiteStore.export` `TypeError` 해소.
2. **`api/app.py` (완료)**: `GET /meta/items` (`include_in_schema=False`) 추가 및 Windows `.bat` 런처에 `--clip-caption-px 32`·다운로드 실패 확인 반영.
3. **`api/static/overlay.html` (완료)**: W4(`/coach` 폴백)·W5(`lol_window_mode_warning`)·W6(`<title>LoL 페이스메이커</title>`) 및 OP.GG 참고 사진(`docs/images/opgg_ingame_reference.jpg`) 기반 다크 글래스 HUD·집중 모드·초상화 전 라인 골드 스트립·어두운 스크롤바 반영.
4. **`api/strategy.py` (완료)**: 1v5 비대칭 커스텀 게임(1인 평균·사망자 차) 및 1v1 포지션 불일치(`live_rumble_ekko_0001.json` 럼블 vs 에코 직접 매칭) 보정 완료.
5. **`AGENTS.md` (완료)**: 3-4절 `GET /meta/items`, `OVERLAY_TITLES` 우선순위, `lol_window_mode_warning`, 브라우저 프로세스 필터, 테스트 기준선 갱신 완료.

---

## 4. 성능 측정 (3단계 · 연습 모드 30초 × 4조건)

> 에이전트 셸은 Windows 비대화형 데스크톱이라 게임 FPS를 직접 읽을 수 없어, 아래 표는 사용자 실기 측정 결과를 채워 넣는 기준 표입니다.

| 조건 | HUD | Ollama | 30초 평균 FPS | 1분 브리핑 호출 수 (서버 로그) | 비고 |
|---|---|---|---|---|---|
| 1 | 끔 | 끔 | *(측정 대기)* | 0 | 기준선 |
| 2 | 켬 | 끔 | *(측정 대기)* | 0 | Win32 HUD + 폴링 부하 |
| 3 | 끔 | 켬 | *(측정 대기)* | *(측정 대기)* | Ollama 대기 영향 |
| 4 | 켬 | 켬 | *(측정 대기)* | *(측정 대기)* | 목표: 20초 간격 기준 분당 ≤ 3회 |
