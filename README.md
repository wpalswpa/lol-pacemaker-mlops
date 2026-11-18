# LoL 페이스메이커 (페이커)

> **연습모드·리플레이·관전 중 10분 기준 모델의 페이스로 실시간 승률과 3분 행동 목표를 진단하고, 상점 구매 아이템·스킬 추천을 강조 테두리로 알려 주는 AI LoL 코치 서비스 & MLOps 자동 운영 파이프라인**

---

## 바로 접속 · 원클릭 실행 (어느 컴퓨터에서든 10초 시작)

| 목적 | 접속 주소 | 비고 |
|---|---|---|
| **데스크톱 원클릭 다운로드** | **`http://<서버 IP>/download`** | Windows(`.bat`) / macOS(`.command`) 다운로드 후 더블클릭 |
| **웹 시뮬레이터 & 전적 DB** | **`http://<서버 IP>/`** | 설치 없이 브라우저에서 코칭 체험 · PostgreSQL 전적 저장 |
| **인게임 팝업 HUD 미리보기** | **`http://<서버 IP>/overlay`** | `380x560` 컴팩트 HUD (지금 살 아이템 금색 · 찍을 스킬 청록 강조) |
| **5분 발표 웹 슬라이드** | **`http://<서버 IP>/slides`** | 7장 웹 프레젠테이션 (`→`·스페이스로 이동) |
| **서버·모델 상태 확인** | **`http://<서버 IP>/health`** | 코드 버전(`app_version`) · 모델 버전(`model_version`) 실시간 조회 |

### 내 PC에서 인게임 오버레이 바로 띄우기 (Windows / macOS)
1. 브라우저에서 **`http://<서버 IP>/download`** 접속 후 OS에 맞는 버튼 클릭
2. 내려받은 파일(`lol-pacemaker-overlay.bat` 또는 `.command`) 더블클릭
   - Python 3 표준 라이브러리만 사용하므로 추가 패키지 설치가 필요 없습니다.
3. 롤 클라이언트 **연습모드·리플레이**(화면 설정: **테두리 없음**)를 켜면 우측 상단에 항상 위 팝업 HUD가 뜨고, 경기 종료 시 전처리 피처가 서버 PostgreSQL에 자동 기록됩니다.

---

## 프로젝트 한눈에 보기

마라톤 선수 옆에서 속도와 목표를 잡아 주는 **페이스메이커**처럼, 기존 사후 복기 서비스(`lol-win-prediction`)를 **연습·리플레이·관전 중에 실시간 페이스·목표·아이템/스킬을 안내하는 코치 서비스**로 전환하고, 패치마다 코드 재배포 없이 모델만 검증·교체·롤백하는 **MLOps 파이프라인**과 **PostgreSQL 전적·재학습 데이터 루프**를 결합했습니다.

API 계약, 검증 기준 수치, 승격·되돌리기 절차의 정본은 [AGENTS.md](AGENTS.md)다. 이 문서는 기획, 설계 이유, 흐름, 실행 방법을 다룬다.

## 바로 쓰기 (어떤 컴퓨터에서든)

| 하고 싶은 것 | 필요한 것 | 바로 실행 |
|---|---|---|
| ① 설치 없이 둘러보기 | 브라우저 | `https://<내 도메인>/` (시뮬레이터 + 전적 표), `/overlay` (게임 중 HUD, 데모 3개), `/slides` (발표 슬라이드) |
| ② Windows 게임 중 팝업 | Python 3.9 이상(추가 설치 없음), 이 저장소 폴더 | `python -m client.bridge --server http://<고정 IP> --insecure-local --overlay --launch-popup --save-match` |
| ③ macOS 게임 중 팝업 | 기본 `python3`(3.9 이상), 이 저장소 폴더 | `python3 -m client.bridge --server http://<고정 IP> --insecure-local --overlay --launch-popup --save-match` |
| ④ 개발·학습 환경 전체 | [uv](https://docs.astral.sh/uv/) | `uv sync --all-groups && uv run pytest -q` |

**① 브라우저로 접속.** 서버 주소 뒤에 경로만 붙인다. 서버에 도메인(`SITE_ADDRESS`)을 넣어 HTTPS 로 띄웠다면 `https://<내 도메인>/…`, 도메인 없이 HTTP 로 띄웠다면 `http://<고정 IP>/…`이다(도메인을 넣은 서버는 IP 주소로 들어온 요청에 답하지 않는다). 시뮬레이터 맨 아래에서 승·패를 골라 `전적 저장`을 누르면 전적 DB 에 쌓인다. `/overlay`는 브리지가 없으면 시뮬레이션 배지로 데모만 돈다.

**② Windows 인게임 팝업.** 저장소를 내려받아(`git clone` 또는 GitHub 의 Download ZIP) 그 폴더에서 PowerShell 로 실행한다. `client/bridge.py`는 표준 라이브러리만 쓰므로 `pip install`이 필요 없다(`python` 대신 `py`도 된다).

```powershell
cd lol-pace-mlops
python -m client.bridge --server http://<고정 IP> --insecure-local --overlay --launch-popup --save-match
# 도메인을 쓰는 서버면 --server https://<내 도메인>
# 게임 없이 시험: --file client/tests/fixtures/allgamedata_10min.json 을 붙인다
```

- LoL 을 **테두리 없는 창 모드**로 둔다(전체 화면이면 어떤 창도 게임 위에 못 뜬다). 팝업은 Edge 또는 Chrome 의 `--app` 창(380×560, 우측 상단)이고 항상 위에 고정한다. 둘 다 없으면 기본 브라우저 탭으로 연다.
- 팝업 주소는 `http://127.0.0.1:8765/overlay`다. 브리지가 서버의 HUD 를 받아 이 PC 에서 내주고 `/coach`만 서버로 넘긴다. 공개 주소에서 연 페이지는 브라우저가 이 PC(`127.0.0.1`)의 브리지를 읽지 못하게 막기 때문이다(AGENTS.md 3-4절). 팝업을 닫았으면 이 주소를 브라우저에 다시 열면 된다.
- 게임 API(`127.0.0.1:2999`)는 게임이 진행 중일 때만 열린다. 관전·리플레이·연습 모드에서 쓰고 랭크 게임 중에는 쓰지 않는다(AGENTS.md 12절).
- `--insecure-local`은 게임 클라이언트의 자체 인증서를 확인하지 않고 이 PC(`127.0.0.1`)만 읽는다는 뜻이다. Riot 문서의 `riotgames.pem`이 있으면 대신 `--cafile riotgames.pem`을 쓴다.
- `--save-match`는 경기가 끝날 때(GameEnd) 점수판 값과 승패만 서버 전적 DB 에 한 번 올린다. 소환사명 같은 개인 정보는 보내지 않는다. 끄려면 빼고 실행한다. 멈출 때는 Ctrl+C.

**③ macOS 인게임 팝업.** 같은 폴더에서 `python3`로 실행한다. macOS 기본 `python3`(3.9)에서도 돈다.

```bash
cd lol-pace-mlops
python3 -m client.bridge --server http://<고정 IP> --insecure-local --overlay --launch-popup --save-match
```

팝업은 Swift 로 만든 항상 위 패널(`client/overlay_popup.swift`, 처음 한 번 컴파일)이다. 컴파일러가 없으면(`xcode-select --install`로 설치) Chrome `--app` 창, 그것도 없으면 기본 브라우저로 연다. 테두리 없는 창 모드와 나머지 옵션은 ②와 같다.

**④ 개발·학습 환경 복제.** 테스트는 원본 CSV·Docker·외부 DB 없이 돈다(전적 DB 는 메모리 SQLite).

```bash
git clone <저장소 주소> && cd lol-pace-mlops
uv sync --all-groups && uv run pytest -q         # 303 passed 기대 (학습 그룹 없이 돌리면 2건 건너뜀)
MODEL_IMPL=rule MODEL_DIR=api/tests/fixtures uv run uvicorn api.app:app --port 8084   # http://127.0.0.1:8084/ (학습 산출물 없이 규칙 모델 + 테스트용 기준선)
```

학습·ONNX 변환·전 과정 재현은 아래 9절. 학습에는 Kaggle 원본 CSV 를 `data/`에 두어야 한다(6절).

## 현재 상태

<!-- STATUS:START -->
- **완료(로컬·실서버 검증)**: API(`/health`, `/predict`, `/coach`, `/matches`, `/download`), 4종 예측 구현체(`rule`·`sklearn`·`dl`·`onnx`), 성능 게이트, ONNX 변환·일치 검사, 빌드 가이드(`build_advice`), Always-On-Top 인게임 오버레이 HUD(`/overlay`), 원클릭 데스크톱 다운로드(`/download`), PostgreSQL 전적 DB 및 재학습 CSV 내보내기, 단위·계약 테스트 전체 통과.
- **클라우드 배포 및 부하 실측(2026-11-18)**: AWS Lightsail에 `v1.0` → `v1.1` → `v1.2` → **`v1.3` (`lol-api` + `postgres:16-alpine` + `caddy:2.10`)** 배포 완료. 실서버 부하 실측([docs/benchmark_cloud_summary.json](docs/benchmark_cloud_summary.json)) 160건 동시 10건 **성공률 100%(오류 0건)**, 로컬 ONNX 추론 p95 **0.73ms**.
- **모델 판 승격·되돌리기 확인(2026-11-18)**: `model.yml` 2회(`m20261118-1` → `m20261118-2`)와 `rollback.yml` 2회(**16초 롤백** · 15초 재승격) 모두 성공.
- **남은 작업**: Windows 데스크탑 실제 LoL 클라이언트(`127.0.0.1:2999`) 인게임 팝업 시연 녹화 및 도메인(`jjm.eta.pyhub.kr`) DNS 전파 후 HTTPS 전환.
<!-- STATUS:END -->

## 1. 문제와 목적

**일반 유저는 끝난 게임의 리플레이를 돌려 보지 않는다.** 기존 프로젝트는 끝난 경기의 10분 시점을 되감아 "언제 갈렸나"를 판정했지만, 판정을 보려면 경기가 끝난 뒤 웹사이트에 다시 들어와야 했다. 고칠 수 있는 시점(게임 중)에는 아무것도 주지 못했다.

게임 중에 도우려면 정책의 선을 먼저 정해야 한다.

- 상대의 가려진 정보(전장의 안개 속 위치, 상대 골드, 와드)를 몰래 읽는 것은 부정행위다.
- 게임 클라이언트가 내 PC에 여는 Live Client Data API(`127.0.0.1:2999`)는 점수판(Tab)에 이미 보이는 값(양 팀의 킬·어시스트·CS·레벨)과 오브젝트 이벤트(드래곤·전령·포탑·첫 킬)를 준다. 이 값은 플레이어가 Tab만 눌러도 보는 정보다.

그래서 **입력은 점수판에 공개된 값만 받는다**(AGENTS.md 3절). 원본 프로젝트의 13개 피처 중 상대 골드·경험치·와드처럼 게임 중에 보이지 않는 값은 뺐고, 대신 점수판 CS(미니언 + 정글 몬스터)를 한 피처로 묶어 8개로 다시 학습한다.

**티어 평균 기준선**: 학습 데이터(다이아몬드 솔로랭크 10분 스냅샷)의 평균 경기가 기준이다. 모델 안의 정규화 평균·표준편차가 곧 그 티어의 10분 평균이고, 요인 분해는 "이 지표만 티어 평균이었다면 승률이 얼마였나"로 계산한다. 기준선은 학습할 때마다 새 데이터에서 다시 계산해 모델 판에 함께 담는다(`baseline.json`).

## 2. 핵심 기능 네 가지 (올인원 인게임 코칭)

사용자가 다른 전적 앱(OP.GG 등)을 따로 켜지 않고 이 서비스 하나만 쓰도록, 페이스 진단과 운영 목표에 더해 **우세·열세 및 상대 급성장 챔피언에 맞춘 아이템·스킬 추천**까지 한 번에 돌려준다.

| 기능 | 무엇을 돌려주나 | 예(응답을 사람 말로 옮긴 것) |
|---|---|---|
| ① 페이스 진단 `/predict` | 내 팀 승률, 페이스 구간, 같은 티어 경기 중 내 위치(상위 몇 %) | "승률 26% · 열세 페이스 · 티어 상위 76%" |
| ② 격차 요인 `top_factors`, `tier_percentile` | 승률에 가장 크게 작용하는 지표 3개와 그 몫(%p), 지표별 티어 내 위치 | "승률을 깎는 1순위는 CS 격차 -18개(-6.4%p), CS 격차는 상위 70%" |
| ③ 목표 수치 `/coach` | 다음 3분 동안 채우면 승률이 오르는 목표, 오르는 폭, 목표를 채웠을 때 옮겨 갈 위치 | "킬 2개 더 → 상위 70%에서 50%로 (승률 26.5% → 37.2%, +10.7%p)" |
| ④ 상황별 아이템·스킬 가이드 `build_advice` | 현재 우세·열세 상태와 내 챔피언·상대 위협 유형에 맞춘 빌드 방향(`posture`), 추천 아이템 3단계, 스킬 마스터 원칙 | "열세 + 상대 마법 위협 → 헤르메스의 발걸음(1,250G) → 마법무효화의 망토(400G) → 맬모셔스의 아귀, 라인 클리어·생존기 우선" |

예의 수치는 형식을 보여 주는 예시다. 실제 값은 검증 결과 절(아래 12절)에 적는다.

**"티어 평균과의 차이"보다 "티어 안의 위치"를 앞에 둔다.** "CS −18"은 그게 큰지 작은지 모르지만 "상위 78%"는 바로 읽힌다. 분위수 표(5%·10%·…·95%)를 학습할 때마다 새 데이터로 계산해 모델 판에 함께 담고, 서빙은 원본 데이터 없이 그 표만 보간한다(정의는 AGENTS.md 4절).

코칭은 전술 지시가 아니라 **정량 목표**다. "바론 가라"가 아니라 "CS 20개를 채우면 이만큼 오른다"를 준다. 이 값은 과거 경기에서 그 수치를 더 가졌던 경우의 승률이고, 그렇게 하면 이긴다는 개입 효과가 아니다. 응답의 `how_to_read`가 이 읽는 법을 함께 싣는다.

## 3. 누구를 위한 서비스인가: 사람과 AI

목표는 "플레이어에게는 1초 안에 읽히는 숫자 목표를, AI 코칭 에이전트에게는 지어내지 않는 계산 도구를 주는 것"이다. 둘은 **같은 JSON 계약**을 쓴다.

1. **게임 중인 플레이어**: 긴 글을 읽을 시간이 없다. 브리지(`client/bridge.py`)가 10분이 되면 한 번만 계산을 받아, 화면 우측 상단에 3줄짜리 작은 카드를 5초 띄웠다가 지우고(테두리가 우세 초록·접전 노랑·열세 빨강으로 빛남), 원하면 음성으로 한 번 읽어 준다. 둘은 `--effect on|off`, `--tts on|off`(또는 환경 변수 `LOL_EFFECT`, `LOL_TTS`)로 따로 켜고 끈다. 내 챔피언과 상대 최다 킬 챔피언은 게임 응답에서 자동으로 찾아 빌드 가이드에 넣는다. "승률 43% · 상위 68% / CS +20 → 53% (상위 45%)". 표나 긴 글은 띄우지 않는다. 카드 문구는 응답의 `toast`를 그대로 쓴다.
2. **AI 코칭 에이전트(음성 비서, 디스코드 봇)**: 언어 모델이 승률을 어림하면 그럴듯한 숫자를 지어낸다. 원본 프로젝트의 정성 시험(SC-7)에서도 언어 모델이 킬 수만 보고 틀린 조언을 냈다. 에이전트가 `/openapi.json`의 도구(`predict_pace`, `coach_targets`)를 부르면 승률·요인·위치·목표가 담긴 JSON을 받아 그 숫자로만 말한다. 응답의 `model_version`으로 어느 판의 계산인지도 남긴다. 이 과정을 `client/agent_demo.py`가 그대로 보여 준다(도구 정의 변환 → `coach_targets` 호출 → 응답 숫자만으로 브리핑, 언어 모델 키 없이 재생).
3. **게임 중 오버레이(`GET /overlay`, `client/bridge.py --overlay --launch-popup`)**: OP.GG 데스크톱 앱처럼 게임 창 우측 상단에 항상 떠 있는 380×560 HUD다. 브리지가 2초마다 점수판·레벨·인벤토리를 읽어 지금 시각의 누적 값을 10분 페이스로 환산해 `/coach`를 부르고(AGENTS.md 3-4절), HUD 는 `실시간 게임 중 승률`, 전술 배지, 한 줄 목표, 시작·상황별 아이템, 구매 순서(이미 산 것은 `✓ 보유 완료`)와 **지금 살 아이템(금색)·이번 레벨에 찍을 스킬 키(청록)에 빛나는 강조 테두리**를 보여 준다. 상단 버튼으로 이펙트를 끈다. 브리지가 없으면 `초반 우세 06:30`·`중반 박빙 14:20`·`후반 열세 23:10` 데모로 단독 시연한다. 관전·리플레이·연습 모드용이다.
4. **발표·연습용 웹 시뮬레이터(`GET /`)**: 게임 클라이언트가 없어도 점수판 값과 챔피언을 프리셋·슬라이더·목록으로 바꿔 보는 어두운 카드 화면이다. 초보자가 글을 읽지 않고 따라 하도록 그림으로 보여 준다. 상태에 따라 화면 가장자리와 결과 카드 테두리가 초록(우세 · 적극 교전 가능)·노랑(접전 · 파밍 집중)·빨강(열세 · 1대1 싸움 금지)으로 빛나고 상단 버튼으로 끈다. 티어 분포 막대에는 지금 위치 ●와 첫 목표를 채웠을 때 위치 ★, 그 아래 3단계 행동 카드(예: 🛡️ 1대1 싸움 멈추기 → 🌾 포탑 앞에서 미니언 챙기기 + 첫 목표 → 🏃 아군 강세 라인 합류), Data Dragon 실제 아이콘으로 그린 내 챔피언 대 상대 위협과 아이템 카드 3장(신발 → 하위템 → 코어, 가격과 이유), 스킬 키캡 `[Q] ▶ [E] ▶ [W]`가 나온다. 행동 카드는 상태별 고정 문구이고 아이템·스킬은 규칙표다(AGENTS.md 3-3절).

5. **경기 뒤 전적 기록과 재학습 데이터 루프(`POST·GET /matches`, `GET /matches/export`)**: 오버레이가 게임 중 코칭을 맡는다면, 웹(`GET /`)은 OP.GG 처럼 경기 뒤 기록을 남긴다. 웹 맨 아래 "최근 코칭 전적 기록 (DB)" 표에서 승·패를 고르고 `전적 저장`을 누르거나, 브리지 `--overlay --save-match`가 경기가 끝날 때(GameEnd 이벤트) 한 번 올린다. 서버는 받은 점수판으로 8개 피처·승률·코칭 요약을 **직접 다시 계산해** 저장하고(클라이언트 값을 믿지 않는다), 소환사명·Riot ID 같은 칸은 422로 거절한다. 운영 서버는 PostgreSQL 16(`compose.yml`의 `db`, `shared_buffers=64MB`, `max_connections=30`), 로컬·테스트는 같은 함수의 SQLite 폴백이다. 승패가 정해진 기록은 원본 Kaggle CSV 와 같은 열 이름으로 내보내 `training/data.py`가 그대로 읽는다(학습에 섞을지는 사람이 정한다). AI 에이전트 도구에는 이 쓰기 경로를 내놓지 않는다.

**입력을 어디서 얻나(합법적 경계)**: 사람이 웹 화면에 넣거나, 브리지가 Riot 공식 로컬 API(`127.0.0.1:2999/liveclientdata/allgamedata`)를 관전·리플레이·연습 모드에서 읽는다. 점수판과 오브젝트 이벤트만 쓰고, 랭크 게임 중에는 쓰지 않는다. Data Dragon 아이템 가격표로 계산한 양 팀 아이템 골드는 참고 값으로 로그에만 남기고 모델에 넣지 않는다. 학습 데이터의 골드(쓰지 않은 골드 포함 총골드)와 정의가 달라 섞으면 학습과 서빙이 어긋나기 때문이다.

## 4. 왜 이 구조인가 (설계 이유 다섯 가지)

### ① 규칙(`rule`)부터 출시하고 학습 모델과 경쟁시킨다

> 인게임 서비스는 모델이 완성될 때까지 기다릴 수 없습니다. 먼저 "골드로 환산한 격차 1,000당 승률 약 +10%p"라는 규칙으로 API와 배포 길을 뚫고, 학습한 `sklearn`·`dl` 모델은 이 규칙보다 확률을 더 정확하게 낼 때만(성능 게이트 통과) 배포되게 했습니다. 데이터로 재 보니 맞힌 비율은 규칙과 모델이 거의 같았습니다. 차이는 확률이 실제와 얼마나 맞는지(Brier)에서 났습니다. 우리 서비스는 "43%"라는 숫자를 보여 주므로 게이트도 그 기준으로 세웠습니다.

### ② 정규화를 모델 안에 넣고 ONNX Runtime으로 서빙한다

> 10분 알림은 게임을 방해하지 않게 바로 답해야 하고, 서버는 월 12 USD짜리 2 GB 인스턴스입니다. 교안 실측에서 CPU용 PyTorch만 넣어도 서빙 이미지가 1.47 GB였습니다. 그래서 학습 라이브러리 대신 정규화(티어 평균·표준편차)까지 모델 안에 넣은 ONNX 파일만 서버에 올리고, 원본 모델과 ONNX 서버가 같은 답을 내는지 매번 확인합니다(오차 1e-4 미만).

### ③ 코드 판(`v1.x`)과 모델 판(`mYYYYMMDD-N`)을 나눈다

> LoL은 2주마다 패치로 미니언 골드, 드래곤 효과, 티어별 10분 평균이 바뀝니다. 그때 API 코드는 그대로이고 모델만 다시 학습하면 됩니다. 한 번호로 묶으면 바뀌지도 않은 이미지를 2주마다 다시 굽게 됩니다. 그래서 코드 판은 GHCR 이미지, 모델 판은 Lightsail 버킷에 두고, 패치 날에는 버킷의 새 판만 받아 바꿉니다.

### ④ 서빙은 정액 Lightsail, 무거운 일은 필요할 때만 EC2. 둘 다 키 없이

> 추론 모델은 가벼워서 월 정액 Lightsail 한 대가 Caddy로 HTTPS를 받고 24시간 답합니다. 대량 수집과 재학습은 크레딧으로 쓰는 EC2를 그때만 켰다가 끕니다. 키는 어디에도 두지 않습니다. Lightsail 서버는 자기 자신을 GitHub 러너로 등록해 SSH 키 없이 배포를 받고, 버킷은 리소스 액세스로 키 없이 읽습니다. 그런데 Lightsail 버킷의 리소스 액세스는 Lightsail 인스턴스만 받아서, EC2가 거기에 쓰려면 장기 액세스 키가 필요합니다(AWS 문서 확인). 그래서 EC2는 학습 결과를 GitHub에 넘기기만 하고, 버킷에 올리는 일은 Lightsail 러너가 합니다. EC2를 켜고 끄는 권한은 GitHub OIDC 단기 토큰으로 받습니다.

### ⑤ 되돌리기를 따로 둔다

> 새 패치 직후 데이터가 적어 새 판이 엉뚱한 승률을 내면 게임 중 사용자 경험이 바로 망가집니다. 버킷에 옛 판을 지우지 않고 두었으므로, 되돌리기 워크플로에 옛 판 이름만 넣으면 재학습도 이미지 빌드도 없이 서버 모델과 포인터(`current.json`)를 옛 판으로 돌립니다. 서버는 새 판의 `/health`가 그 판 번호로 응답하지 않으면 스스로 이전 판으로 돌아갑니다.

## 5. 전체 흐름

```
[1. 요청이 지나가는 길]
브리지(관전·리플레이·연습) / 웹 시뮬레이터 / AI 코칭 봇
   │  HTTPS <내 도메인>  POST /predict, POST /coach, GET /health, GET /openapi.json
   ▼
Lightsail 고정 IP → 인스턴스 lol-mini (Docker Compose 프로젝트 lol)
                     ├ caddy (80·443, 인증서 자동) ──내부망──► api:8080
                     ├ api 컨테이너 (lol-api:v1.x, ONNX Runtime만, 호스트에는 127.0.0.1:8080만)
                     └ 모델 판 폴더 (/srv/lol/models/<판>/, 읽기 전용)
                                        ▲
                                        │ 리소스 액세스 (AWS 키 없음)
                                 Lightsail 버킷 (models/onnx/<판>/, current.json)

[2. 배포와 운영이 지나가는 길]
① 코드 배포 ci.yml (git 태그 v1.x)
   GitHub 러너 : test → build(학습·게이트·ONNX → parity → GHCR 이미지, 한 잡)
   서버 러너   : deploy (server.sh image v1.x → /health, 실패면 이전 태그)
② 모델 배포 model.yml (수동 실행, 새 패치 데이터)
   (trainer=ec2 이면) GitHub 러너가 OIDC 로 EC2 워커를 켠다
   학습 러너   : train(DATA_N·DATA_SEED → 게이트 → ONNX → 모델 카드 → parity, 한 잡)
                 → 서버용 세 파일을 잡 출력(base64 + sha256)으로 넘긴다 (Actions 아티팩트 안 씀)
                 (GitHub 러너 또는 EC2 워커. EC2 는 IAM Role 로 원천 데이터 S3 만 읽는다)
   서버 러너   : promote (낡은 실행 차단 → 버킷에 새 판 → 서버로 받기
                 → server.sh model <판> → /health 확인 뒤 current.json)
   (trainer=ec2 이면) 끝나면 성공·실패와 관계없이 EC2 를 끈다
③ 되돌리기 rollback.yml (장애 때 수동 실행)
   서버 러너   : 버킷의 옛 판 → server.sh model <판> → current.json (이미지 그대로)
```

자원 분리와 HTTPS 구성, 배포 전 확인 목록(DNS 확인 포함)은 [AGENTS.md](AGENTS.md) 11절에 있다. 실습용 `eta-mini-aws` 인스턴스·버킷과는 이름(`lol-` 접두어), 서버 경로(`/srv/lol`), 동시 실행 그룹을 나눠 섞이지 않게 한다.

## 6. 데이터와 모델

| 항목 | 내용 |
|---|---|
| 원천 | Kaggle "League of Legends Diamond Ranked Games (10 min)" 9,879경기. 원본 CSV는 저장소에 넣지 않는다(재배포 조건 미확인) |
| 피처 | 첫 킬, 킬·어시스트·CS·평균 레벨·드래곤·전령·포탑의 블루 − 레드 차이(8개) |
| 평가셋 | 전체의 20%를 시드 42로 한 번 고정. 모델 판이 바뀌어도 같은 시험지 |
| 학습셋 | 나머지 80%에서 `DATA_N`건을 `DATA_SEED`로 뽑는다. 패치가 바뀐 상황을 흉내 내는 장치이고, 실제 새 패치 데이터는 아니다 |
| 구현체 | `rule`(골드 환산 규칙) · `sklearn`(`StandardScaler` + `LogisticRegression`) · `dl`(정규화 버퍼를 품은 PyTorch 신경망) · `onnx`(서빙용) |
| 서빙 모델 | `model_sklearn.onnx`. 로지스틱 회귀라 요인과 목표를 사람이 검산할 수 있다. `dl`은 같은 게이트를 통과해야 하는 비교 후보다 |

**공개 지표만 쓰니 생긴 차이**: 원본 13개 피처 모델은 골드와 킬·CS가 겹쳐 킬·CS 계수가 음수로 나왔고, "CS를 먹지 마라"가 나오지 않게 골드를 함께 올리는 보정이 필요했다. 골드를 빼고 다시 학습하니 계수가 모두 양수가 됐다(사전 분석). 그래도 킬을 올리면 어시스트·레벨도 따라 오르므로, 코칭은 학습 데이터에서 잰 기울기로 동반 지표를 함께 움직인다(AGENTS.md 4절).

**한계**: 데이터는 다이아몬드 한 티어, 한 시기다. 다른 티어의 기준선과 실제 패치 변화는 데이터를 새로 모아야 확인할 수 있다. Live Client Data API의 실제 응답과 이 입력의 대응(특히 점수판 CS에 정글 몬스터가 포함되는지)은 실제 게임 클라이언트로 확인하지 않았다.

## 7. 기존 프로젝트와 무엇이 달라졌나

| 항목 | 기존 `lol-win-prediction` | 이번 프로젝트 |
|---|---|---|
| 쓰는 시점 | 경기가 끝난 뒤 복기 | 게임 중 10분 시점 |
| 입력 | 끝난 경기의 13개 차이값(상대 골드·와드 포함) | 점수판 공개 값 8개, 양 팀 원값으로 받아 서버가 차이를 계산 |
| 사용자 | 웹 화면 방문자 | 게임 중 플레이어 + AI 코칭 에이전트(같은 JSON) |
| 요인 계산 | 로지스틱 계수 × 표준화값(모델 전용) | "티어 평균이었다면"과의 차이(모든 구현체 공통) |
| 모델 | sklearn 하나 | 규칙 → sklearn·PyTorch 경쟁 → ONNX 변환 |
| 품질 확인 | 정답지 회귀 테스트, 서빙 경로 일치 | 계약 테스트 + 뮤테이션 확인, 성능 게이트, ONNX 오차, 서버 간 Parity |
| 배포·운영 | 학원 서버에 수동 배포, 모델을 git에 커밋 | Lightsail 인스턴스·버킷, 코드 판·모델 판 분리, 키 없는 러너 배포, 되돌리기 |

## 8. AI를 어떻게 썼나

| 맡긴 것 (AI 코딩 도구) | 사람이 정한 것 |
|---|---|
| API·학습·변환·배포 스크립트 작성, 테스트 작성, 실행과 출력 수집 | 입력 범위와 422 규칙, 요인·코칭 계산 정의, 게이트 기준(Brier·LogLoss·정확도 0.68), Parity 기준(1e-4·23건), 판 이름 형식, 하지 않을 것 |

- 계약과 기준은 구현 전에 [AGENTS.md](AGENTS.md)에 먼저 적고, 테스트의 기대값은 그 계약에서 손으로 계산하게 했다. 현재 코드의 출력을 정답으로 옮겨 적으면 버그도 정답이 되기 때문이다.
- 테스트가 실제로 버그를 잡는지 일부러 버그를 넣어 확인했다(뮤테이션 확인, 결과는 12절).
- 서비스 쪽에서 AI는 사용자다. 코칭 에이전트가 숫자를 지어내지 않게 계산은 API가 하고, 에이전트는 받은 숫자로 말만 한다.

## 9. 실행 방법

<!-- RUN:START -->
```bash
# 1. 의존성 설치 및 계약·단위 테스트 (원본 CSV·Docker 없이 실행 가능)
uv sync --all-groups
uv run pytest -q

# 2. 학습 · 성능 게이트 · ONNX 변환 · 모델 카드 생성 (data/high_diamond_ranked_10min.csv 필요)
DATA_N=500 DATA_SEED=0 uv run --group train python -m training.train_sklearn
uv run python -m training.validate_model sklearn
DATA_N=500 DATA_SEED=0 uv run --group train python -m training.train_dl
uv run python -m training.validate_model dl
uv run --group train python -m training.export_onnx
MODEL_VERSION=m20261118-1 uv run python -m training.model_card

# 3. ONNX 서빙 서버 실행 (웹 시뮬레이터: http://127.0.0.1:8083/)
MODEL_IMPL=onnx uv run uvicorn api.app:app --port 8083

# 4. 브리지 (게임 없이 저장한 응답으로 시연. 실제 게임은 --file 대신 --cafile riotgames.pem)
uv run python -m client.bridge --api http://127.0.0.1:8083 --file client/tests/fixtures/allgamedata_10min.json --effect on --tts off

# 5. AI 에이전트 도구 호출 시연 (/openapi.json → coach_targets → 근거 수치만으로 브리핑)
uv run python -m client.agent_demo --server http://127.0.0.1:8083

# 발표 슬라이드: http://127.0.0.1:8083/slides (←·→·스페이스·클릭, 주소 #n 으로 바로 가기)

# 게임 중 오버레이 팝업 (관전·리플레이·연습 모드. 브리지는 표준 라이브러리만 써서 python3 로 바로 돈다)
python3 -m client.bridge --server http://<고정 IP> --insecure-local --overlay --launch-popup
# 게임 없이: 저장한 응답 + 로컬 서버 (HUD: http://127.0.0.1:8083/overlay, 상태: http://127.0.0.1:8765/bridge/state)
uv run python -m client.bridge --server http://127.0.0.1:8083 --file client/tests/fixtures/allgamedata_10min.json --overlay --launch-popup

# 전적 DB (AGENTS.md 3-5절). DATABASE_URL 이 없으면 data/matches.db (SQLite), postgresql:// 이면 PostgreSQL
DATABASE_URL=sqlite:///data/matches.db MODEL_IMPL=onnx uv run uvicorn api.app:app --port 8083   # 웹 맨 아래 "최근 코칭 전적 기록" 표
uv run python -m client.bridge --server http://127.0.0.1:8083 --file client/tests/fixtures/allgamedata_10min.json --overlay --save-match
curl -s "http://127.0.0.1:8083/matches?limit=5"                       # 최근 전적
curl -s "http://127.0.0.1:8083/matches/export?format=csv" -o data/matches_export.csv   # 승패가 정해진 기록 → 원본 CSV 와 같은 열

# 서빙 지연시간·부하 실측 (순차 100건 + 동시 20, /predict·/coach). 원격 주소는 JSON 에 <원격 서버>로 가려 적는다
uv run python scripts/benchmark_serving.py --url http://127.0.0.1:8083 --requests 100 --concurrency 20 --out docs/benchmark_summary.json
# 모델 판 비교 m1(500경기) vs m2(5,000경기): LogLoss·AUC·규칙 대비 개선율·게이트·피처 가중치 (artifacts/ 는 안 건드림)
uv run --group train python -m training.compare_models --out docs/model_comparison.json --md docs/model_comparison.md

# 6. 로컬 전 과정 재현 (승격 m20261118-1 → -2, 깨진 판 자동 복구, 되돌리기)
E2E_DIR="$HOME/Library/Caches/lol-mlops-e2e" bash deploy/local_e2e.sh
```
<!-- RUN:END -->

## 10. 파일 구조

```
lol-pace-mlops/
├── README.md                 기획·설계 이유·흐름·실행 방법 (이 문서)
├── AGENTS.md                 API 계약·검증 기준·승격 절차의 정본
├── 인수인계.md               다음 세션 이어받기용 상태·수치·남은 일 기록
├── pyproject.toml, uv.lock   기본(서빙): fastapi·uvicorn·numpy·onnxruntime·psycopg[binary] / train 그룹: pandas·scikit-learn·torch·onnx
├── api/
│   ├── app.py                GET /health, POST /predict, POST /coach, GET / (웹 시뮬레이터), GET /overlay (게임 중 오버레이 HUD), GET /slides (발표 슬라이드), POST·GET /matches, GET /matches/export (전적 DB), GET /meta/champions (숨은 경로)
│   ├── contract.py           입력·출력 모델과 422 규칙, 피처 변환 (MatchIn: 전적 저장 본문)
│   ├── db.py                 전적 DB: PostgreSQL(psycopg 3) 또는 SQLite 폴백, init_db·save_match·list_matches·export_matches
│   ├── service.py            요인 분해·백분위·코칭·티어 비교 (모든 구현체 공통)
│   ├── build.py              우세·열세 상태 및 상대 위협 맞춤 아이템·스킬 가이드 계산
│   ├── build_rules.json      사람이 정한 상태·계열·위협별 아이템·스킬 규칙표
│   ├── game_data.json        Data Dragon 16.23.1 챔피언·아이템 검증 데이터
│   ├── coach_steps.json      코칭 단계(올릴 지표와 양, 연쇄 지표)
│   ├── predictors/           rule · sklearn · dl · onnx (load → predict_proba)
│   ├── static/index.html     웹 시뮬레이터 (테두리 이펙트 ON/OFF, ●★ 분포 막대, 행동 카드, 아이템·챔피언 아이콘, 스킬 키캡)
│   ├── static/slides.html    5분 발표 슬라이드 7장 (외부 자원 없는 한 파일, 키보드·클릭·진행 막대)
│   ├── static/overlay.html   게임 중 오버레이 HUD 380×560 (브리지 폴링, 구매·스킬 강조 테두리, 데모 3개)
│   ├── tests/                계약 테스트와 고정 자료
│   └── Dockerfile            ONNX Runtime만 담은 서빙 이미지
├── client/
│   ├── bridge.py             로컬 게임 API → 입력 변환(챔피언 자동 추출), 10분 한 번 호출, 테두리 카드·음성 ON/OFF,
│   │                         --overlay(10분 페이스 환산·보유 아이템·다음 스킬·/bridge/state), --launch-popup
│   ├── overlay_popup.swift   macOS 항상 위 팝업(NSPanel .floating + WKWebView), 브리지가 컴파일해 실행
│   ├── agent_demo.py         AI 에이전트 시연: /openapi.json → 도구 정의, coach_targets 호출, 응답 숫자만으로 브리핑
│   └── tests/                변환 테스트와 고정 자료
├── training/
│   ├── data.py               CSV 읽기, 고정 평가셋, DATA_N·DATA_SEED 표본, 기준선·분위수 계산
│   ├── ddragon.py            Data Dragon 최신 판에서 game_data.json 갱신 및 규칙표 대조
│   ├── evaluate.py           정확도·Brier·LogLoss 계산 (게이트와 학습이 같이 쓴다)
│   ├── train_sklearn.py      학습 + 지표 + baseline.json
│   ├── train_dl.py           WithNormalize 신경망 학습 + 지표
│   ├── validate_model.py     성능 게이트 (미달이면 exit 1)
│   ├── export_onnx.py        ONNX 변환 + 원본 대비 오차 확인
│   ├── model_card.py         model.json (판 이름, 커밋, 데이터, 성능)
│   ├── compare_models.py     모델 판 비교(학습 표본 수별 LogLoss·AUC·개선율·게이트·가중치) → docs/model_comparison.*
│   └── check_parity.py       원본 서버 vs ONNX 서버 응답 비교
├── scripts/
│   └── benchmark_serving.py  /predict·/coach 순차·동시 부하 실측 (성공률, p50/p95/p99, RPS, 표준 라이브러리만)
├── docs/                     benchmark_summary.json · model_comparison.json · model_comparison.md (실측 결과), images/
├── tests/                    부하 실측 백분위·모델 판 비교·전적 DB 저장 계층 테스트 (학습 라이브러리 없이)
├── deploy/
│   ├── lightsail/            launch-script-lightsail.sh · compose.yml · Caddyfile · server.sh
│   ├── tests/                server.sh 테스트 (가짜 docker·curl)
│   └── local_e2e.sh          로컬에서 승격·실패 복구·되돌리기 전 과정을 재현
└── .github/workflows/        ci.yml(test → build 한 잡 → deploy) · model.yml(train 한 잡 → promote) · rollback.yml
```

이 폴더를 별도 GitHub 저장소의 루트로 올려야 워크플로가 돈다(GitHub는 저장소 루트의 `.github/workflows/`만 읽는다).

## 11. 남은 일

<!-- TODO:START -->
1. 판을 바꾸는 동안 요청이 끊기는지 실측: `rollback.yml`을 돌리는 동안 다른 창에서 `scripts/benchmark_serving.py --url https://<내 도메인> --requests 2000 --concurrency 4`를 돌려 `failed`를 센다. `server.sh`는 `docker compose up -d`로 컨테이너를 새로 띄우므로 몇 초 끊길 수 있고, 아직 재지 않았다(그래서 "무중단"이라고 쓰지 않는다).
2. Docker 가 되는 PC 에서 `deploy/local_e2e.sh` 전체 재실행(이번 PC 에서는 학습·게이트·ONNX·Parity 단계까지만 확인).
3. 발표 리허설 (5분, `/slides` 기반).
4. 화면에 로그인한 Mac 에서 오버레이 팝업이 실제로 게임 위에 뜨는지 확인하고 시연 영상 녹화(이번 PC 계정은 화면 세션이 없어 창을 띄우지 못했다).
5. 전적 DB 를 실제 PostgreSQL 16 에서 확인: 서버에서 `server.sh status`로 `db` 가 healthy 인지, 웹에서 `전적 저장` 후 `/matches`에 보이는지, `docker compose restart api` 뒤에도 남는지. 이 PC 에서는 Docker·PostgreSQL 을 쓸 수 없어 SQLite 와 가짜 연결로만 확인했다. 다음 `model.yml`·`rollback.yml`·태그 배포 때 새 `compose.yml`이 서버에 처음 올라가고 `server.sh`가 `POSTGRES_PASSWORD`를 만든다.
<!-- TODO:END -->

## 12. 검증 결과

<!-- RESULT:START -->
2026-11-18 로컬 실측 결과(고정 평가셋 1,976경기, 시드 42):

| 검증 항목 | 실행 명령 / 조건 | 실측 결과 | 판정 |
|---|---|---|---|
| 단위·계약 테스트 | `uv run pytest -q` (api·client·deploy) | **181 passed, skipped 0** | 통과 |
| 뮤테이션 ① | `rule.py` 확률식을 `(gold + 1) / SCALE`로 | 6건 실패(`test_predict_win_prob_follows_rule`, `test_predict_red_side_flips`, `test_top_factors_are_three_sorted_by_impact`, `test_even_game_is_close_pace`, `test_coach_targets_follow_rule`, `test_coach_red_side`) | 잡음 |
| 뮤테이션 ② | `service.py` `impact_pp`의 `sign *` 제거 | 1건 실패(`test_predict_red_side_flips`) | 잡음 |
| 뮤테이션 ③ | `server.sh` 실패 복구를 `/models/$VER`로 | 1건 실패(`deploy/tests/test_server_sh.py::test_model_failure_restores_previous_dir`) | 잡음 |
| CI/CD `v1.0` (GitHub Actions) | 실행 44255436869, 아티팩트 없이 한 잡 `build` | `test` 성공 → `build` 성공(데이터 받기·게이트·ONNX·Parity·GHCR 푸시) → `deploy` 성공. 서버 `/health`: `status ok`, `onnx`, `v1.0`, `model_version baked` | 통과 |
| 로컬 E2E 재실행 (2차 PC) | `E2E_DIR=$HOME/Library/Caches/lol-mlops-e2e` | 0단계 테스트 181 passed, 학습 2회(500/0 → m20261118-1, 5000/1 → m20261118-2) 게이트·ONNX(1.19e-07)·Parity(23건 1.19e-07) 통과 후 버킷 폴더에 올림. 2단계 `docker build`에서 이 PC 계정에 Docker 가 없어 exit 127로 멈춤(1차 PC 실행은 exit 0, 위 행) | 일부 |
| 웹 시뮬레이터 화면 | 헤드리스 Chromium, 1280px·400px | 가로 넘침 0(`scrollWidth` = 화면 폭), Data Dragon 아이콘 5장 로드, 우세·접전·열세 테두리 색, ON/OFF 토글과 새로고침 뒤 유지, 모르는 챔피언 422 때 칸 이름이 든 오류 문구 | 통과 |
| 발표 슬라이드 `/slides` | 헤드리스 Chromium, 1366px·1024px·400px | 7장 모두 가로 넘침 0, →·←·Home·클릭·`#5` 이동과 진행 막대, 마지막 장 시연 버튼이 `/`로 이동, 외부 요청 0건, 콘솔 오류 0 | 통과 |
| AI 에이전트 시연 | `client/tests/test_agent_demo.py` 9건, ONNX 서버에 CLI 실행 | 도구 2개(`$ref` 없는 독립 스키마) 추출, 브리핑의 숫자가 모두 응답 값(응답에 없는 숫자를 넣으면 검사가 잡음), `model_version`·`basis` 포함 | 통과 |
| 게임 중 오버레이 `/overlay` | 헤드리스 Chromium 380×560·380×532, 브리지 `--file --overlay` + ONNX 서버 | 가로 넘침 0, 532px 안에 들어감, `🟢 게임 중 LIVE (10:00)`, 구매 강조가 카드 클릭마다 다음 카드로 이동, 이펙트 OFF 즉시 해제·새로고침 뒤 유지, 데모 우세 92%·접전 48%·열세 2%, 브리지 없으면 시뮬레이션, 콘솔 오류 0 | 통과 |
| 브리지 오버레이 모드 | `client/tests/test_bridge.py` 새 21건 | 10분 페이스 환산(3·15·20분, 30초, 10분), 보유 아이템, 스킬 계획, 호출 간격 5초, 상태 서버 CORS·404·게임 API 실패 때 `connected: false`, 팝업 방식 선택. 뮤테이션 3건(반올림·랭크 상한·호출 간격) 모두 검출 | 통과 |
| 규칙 기준선 (`rule`) | 고정 평가셋 1,976경기 | 정확도 **0.7151** · Brier **0.1917** · LogLoss **0.5676** | 기준선 |
| 성능 게이트 (`DATA_N=500, SEED=0`) | `sklearn` / `dl` | `sklearn`: 정확도 **0.7090**, Brier **0.1891**, LogLoss **0.5555**<br>`dl`: 정확도 **0.7100**, Brier **0.1887**, LogLoss **0.5566** | 통과 |
| 성능 게이트 (`DATA_N=5000, SEED=1`) | `sklearn` / `dl` | `sklearn`: 정확도 **0.7156**, Brier **0.1824**, LogLoss **0.5385**<br>`dl`: 정확도 **0.7136**, Brier **0.1837**, LogLoss **0.5424** | 통과 |
| 게이트 차단 확인 | 조기 종료 없이 `dl` 400회 과적합 | Brier **0.2197** (> 0.1917), 정확도 **0.6721** (< 0.68) → `exit 1` 차단 확인 | 통과 |
| ONNX 변환 오차 | `training.export_onnx` (평가셋 앞 200건) | `sklearn`·`dl` 모두 원본 대비 최대 오차 **1.19e-07** (< `1e-4`) | 통과 |
| 서버 간 Parity | `training.check_parity` (23건) | `sklearn` 원본 서버 vs `onnx` 서버 최대 오차 **1.19e-07** 통과 (대조군 `dl` 서버 투입 시 오차 0.241로 실패 검출 확인) | 통과 |
| 클라우드 모델 판 승격·롤백 | GitHub Actions `model.yml`·`rollback.yml` (Lightsail 러너) | `model.yml` 2회(실행 44256625358: `DATA_N` 500·seed 0 → `m20261118-1`, 44256928140: 5,000·seed 1 → `m20261118-2`)와 `rollback.yml` 2회(44257135715: → `m20261118-1` 잡 **16초**, 44257191599: → `m20261118-2` 15초). 승격 잡 19초(그중 서버에서 판 받아 띄우기 5~6초), 롤백 잡 16초·15초(띄우기 5초). 승격 실행마다 게이트 3조건 PASS, Parity 23건 최대 오차 `1.79e-07`·`1.19e-07` (< `1e-4`) | 통과 |
| 모델 판 비교 m1 vs m2 | `training/compare_models.py`, 고정 평가셋 1,976경기 | 규칙 Brier 0.1917·LogLoss 0.5676·AUC 0.7987 / m1(500): 0.1891·0.5555·0.7853 / m2(5,000): **0.1824·0.5385·0.8005**. 규칙 대비 개선율 Brier 1.34% → 4.84%, LogLoss 2.12% → 5.11%. 가중치 1위 평균 레벨 → 킬, CS 7위 → 3위. 위 `model.yml` 기록과 같은 값. m1 은 게이트를 통과했지만 AUC 는 규칙보다 낮다(`docs/model_comparison.md`) | 통과 |
| 서빙 지연시간·부하 | `scripts/benchmark_serving.py`, 로컬 ONNX 서버(uvicorn 1개, `m20261118-1`), 순차 100 + 동시 20 × 100 | `/predict` 순차 p50 0.55ms·p95 0.73ms·p99 0.76ms, 동시 p95 7.1ms·2,998 RPS / `/coach` 순차 p95 0.79ms, 동시 p95 10.6ms·2,056 RPS. 400건 성공률 100%, 실패 0 (`docs/benchmark_summary.json`). 같은 PC 안이라 네트워크 시간은 빠져 있다 | 통과 |
| 실측 스크립트 테스트 | `tests/` 29건 (학습 라이브러리를 막고도 통과) | 백분위 보간(5개·100개·빈 값·1개), 성공률·RPS, 원격 주소 가리기, 단계별 호출 수, 개선율·게이트 경계(`<`·`≥`)·가중치 순위. 뮤테이션 4건(백분위 반올림, 실패 지연 빼기, Brier `<=`, 부호 있는 정렬) 모두 검출 | 통과 |
| 전적 DB `/matches` (Issue #13) | `tests/test_db.py` 21건, `api/tests/test_app.py` 새 17건, 브리지 새 11건, `deploy/tests` 새 2건, 헤드리스 Chromium 1280·400·360px | SQLite 저장·최신순·챔피언 필터(한국어 이름도 영문 ID 로)·`limit` 1~100, 승패 확정분만 내보내기와 블루 기준 `blueWins` 4가지, 내보낸 CSV 를 `training/data.py`가 읽어 같은 8개 피처, 개인 식별자 칸·범위 밖 값 422 후 아무것도 안 적힘, DB 끊김 503 이어도 `/health`·`/predict` 200, PostgreSQL 이 안 떠도 앱 기동. PostgreSQL SQL 은 가짜 연결로 확인(실제 PostgreSQL 서버 대조는 남은 일). 화면: 저장 2건 → 표 2줄(열세 색, 승·패), 모르는 챔피언 422 문구, 가로 넘침 0, 콘솔 오류 0 | 통과 |
| 로컬 승격·복구·롤백 | `deploy/local_e2e.sh` (서빙 이미지 430MB) | `baked` → `m20261118-1` → `m20261118-2` 승격, 깨진 판(`m20261118-3`) 투입 시 `m20261118-2`로 자동 복구, `m20261118-1` 롤백 **2초** 완료 (`app_version` `v1.0` 유지) | 통과 |
<!-- RESULT:END -->
