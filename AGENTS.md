# AGENTS.md (AI 작업 계약)

이 파일은 이 프로젝트의 **API 계약·검증 기준·배포 절차의 정본**이다. Claude Code, Codex, Gemini, Kiro 어느 도구든 코드나 테스트를 고치기 전에 이 파일을 읽는다. 기획과 설계 이유는 [README.md](README.md)에 있고, 수치 기준은 이 파일만 정본으로 둔다(README는 이 파일을 가리키기만 한다).

## 1. 작업 규칙

| 규칙 | 이유 | 예외 |
|---|---|---|
| 계약·기준을 바꾸려면 이 파일을 먼저 고치고 구현한다. 구현 결과가 달라지면 이 파일을 다시 맞춘다 | 테스트와 다른 서버가 이 파일을 정답으로 쓴다. 코드만 바뀌면 정답이 조용히 움직인다 | 없음 |
| AWS 키, 서버 IP, 도메인, 계정명, SSH 키, 버킷·인스턴스 이름을 코드·워크플로·문서에 적지 않는다. 워크플로는 `${{ github.token }}`, `${{ vars.* }}`, `${{ secrets.* }}`만 쓰고, 문서는 `<고정 IP>`, `<내 도메인>`처럼 자리 표시로 쓴다 | 저장소가 공개되거나 서버가 바뀌어도 코드를 고치지 않게 하고, 유출을 막는다 | 없음 |
| 토큰·비밀키·실명·연락처·개인 신상을 어디에도 쓰지 않는다 | 공개 저장소·발표 자료로 그대로 나간다 | 없음 |
| git push, AWS 자원(Lightsail·EC2·S3·IAM) 생성·변경, GitHub 변수 등록은 로컬 검증을 끝내고 사용자 승인을 받은 뒤에 한다 | 비용과 공개 범위가 바뀌는 외부 쓰기다 | 없음 |
| 원본 CSV(`data/*.csv`)와 학습 산출물(`artifacts/`)은 커밋하지 않는다 | 원본 데이터의 재배포 조건을 확인하지 않았다. 모델 판은 버킷이 보관한다 | 테스트 고정 자료 `api/tests/fixtures/`, `client/tests/fixtures/` |
| 판단 내용(규칙 상수, 코칭 단계, 기준선)은 데이터나 한 곳의 상수로 두고, 코드는 그것을 적용만 한다 | 패치마다 바뀌는 값을 코드 여러 곳에서 고치면 서로 어긋난다 | 규칙 기준선의 상수는 `api/predictors/rule.py` 한 곳 |
| 모델 파일(`*.joblib`, `*.pt`)은 우리 학습이 만든 `MODEL_DIR`에서만 읽는다. 요청 본문이나 외부 경로에서 받은 파일을 열지 않는다 | joblib·torch 파일은 열 때 코드가 실행될 수 있다. 서빙 이미지는 ONNX만 연다 | 없음 |

## 2. 실행 설정 (환경 변수)

| 변수 | 기본값 | 뜻 |
|---|---|---|
| `MODEL_IMPL` | `rule` | 예측 구현체. `rule`·`sklearn`·`dl`·`onnx` 중 하나. 배포 이미지는 `onnx` |
| `MODEL_DIR` | `artifacts` | 모델 판 폴더. `model.json`·`baseline.json`과 구현체별 모델 파일이 있다 |
| `ONNX_FILE` | `model_sklearn.onnx` | `MODEL_IMPL=onnx`일 때 읽을 파일 이름 |
| `APP_VERSION` | `dev` | 코드 판. 이미지를 만들 때 git 태그(`v1.0` 등)가 들어간다 |

- 모든 구현체(`api/predictors/<이름>.py`)는 `load(model_dir)` 하나만 제공하고, 이 함수가 `predict_proba(X) -> P(블루 승)`를 돌려준다. `X`는 `float32 [N, 8]`, 열 순서는 3절의 `FEATURES`, 단위는 원래 값(정규화 전)이다. 정규화는 모델 안에 있다.
- 모델 파일은 앱이 시작할 때 `load`로 한 번 읽는다. 학습 스크립트가 `WithNormalize`를 가져오기만 해도 모델 파일을 읽는 일이 없게 하기 위해서다.
- 요인 분해·코칭·티어 비교는 `api/service.py` 한 곳에서 `predict_proba`만 불러 계산한다. 그래서 구현체가 달라도 계산 방식이 같고, 확률이 같으면 나머지도 같다.

## 3. 입력 계약 (`POST /predict`, `POST /coach` 공통)

```json
{
  "blue": {"kills": 5, "assists": 6, "cs": 250, "avg_level": 6.8, "dragons": 0, "heralds": 0, "towers": 0},
  "red":  {"kills": 7, "assists": 9, "cs": 268, "avg_level": 7.0, "dragons": 1, "heralds": 0, "towers": 0},
  "first_blood": "red",
  "my_side": "blue"
}
```

10분 시점에 게임 점수판(Tab)과 이벤트에 공개되는 값만 받는다. 상대 골드·경험치·와드처럼 가려진 값은 받지 않는다.

| 필드 | 뜻 | 형식 | 허용 범위(밖이면 422) |
|---|---|---|---|
| `kills` | 팀 킬 합 | 정수 | 0~100 |
| `assists` | 팀 어시스트 합 | 정수 | 0~300 |
| `cs` | 팀 CS 합(미니언 + 정글 몬스터) | 정수 | 0~2000 |
| `avg_level` | 팀 평균 레벨 | 실수(정수도 받음) | 1~18 |
| `dragons` | 처치한 드래곤 수 | 정수 | 0~7 |
| `heralds` | 처치한 전령 수 | 정수 | 0~2 |
| `towers` | 그 팀이 부순 포탑 수 | 정수 | 0~11 |
| `first_blood` | 첫 킬을 낸 팀 | `"blue"` 또는 `"red"` | 그 밖의 값 |
| `my_side` | 요청한 사람의 팀 | `"blue"` 또는 `"red"` | 그 밖의 값 |
| `my_champion` (선택) | 내 챔피언 또는 역할군 | 챔피언 영문 ID(`"Ahri"`)·한국어 이름(`"아리"`), 또는 역할군 `"AD"` · `"AP"` · `"TANK"` · `"SUPPORT"`. 생략하면 `"AD"` | 목록에 없는 이름, 40자 초과 |
| `enemy_top_threat` (선택) | 상대 팀에서 가장 크게 성장한 위협 | 챔피언 이름(위와 같은 방식), 또는 피해 유형 `"AD"` · `"AP"` · `"HEAL"` · `"TANK"`. 생략하면 `"AP"` | 목록에 없는 이름, 40자 초과 |

두 선택 칸은 승률 계산(모델 입력)에 쓰지 않는다. `/coach`의 `build_advice`에만 쓴다. 챔피언 목록은 `api/game_data.json`(Data Dragon에서 만든 표)이 정본이고, 대소문자와 앞뒤 공백은 가리지 않는다.

**422로 거절하는 입력** (이유: 계산할 수 없는 값을 200으로 돌려주면 쓰는 쪽이 틀린 숫자를 믿는다)

- 필드 누락, 정의하지 않은 필드 추가
- 따옴표 친 숫자(`"3"`), `true`/`false`를 숫자 칸에 넣은 값, 정수 칸의 소수(`3.5`, `3.0`)
- `NaN`, `Infinity`, `-Infinity`
- 음수, 위 표의 허용 범위 밖(게임 규칙상 한 팀이 10분에 가질 수 없는 값)
- JSON 형식 오류, 본문이 객체가 아닌 경우

**거절하지 않고 `warnings`로 알리는 입력**: 허용 범위 안이지만 학습 데이터의 최소~최대 밖인 값. 이례적인 판을 아예 못 보게 막지 않고, 믿을지 말지는 쓰는 쪽이 정한다(원본 프로젝트의 정책을 이어받음).

**모델 입력 변환**: `FEATURES = [first_blood, kills, assists, cs, avg_level, dragons, heralds, towers]`. `first_blood`는 블루면 1, 레드면 0이고, 나머지 일곱 개는 `블루 값 − 레드 값`이다.

### 3-1. 입력을 어디서 얻는가 (합법적 데이터 경계)

| 출처 | 쓰는 방식 | 쓰지 않는 방식 |
|---|---|---|
| 웹 시뮬레이터(`GET /`) | 사람이 점수판 값을 프리셋·슬라이더로 넣어 보는 화면. 게임 클라이언트 없이 시연할 때 쓴다 | |
| Riot 공식 로컬 API `https://127.0.0.1:2999/liveclientdata/allgamedata` | `client/bridge.py`가 관전·리플레이·연습 모드에서 응답을 받아 3절 입력으로 바꾼다. 점수판(양 팀 킬·어시스트·CS·레벨)과 이벤트(첫 킬·드래곤·전령·포탑)만 쓴다 | 랭크 게임 중에 쓰지 않는다. 상대 위치·쿨다운 등 점수판에 없는 값을 읽지 않는다 |
| Data Dragon 아이템 가격표 | 브리지가 양 팀 아이템 가격 합을 **참고 값**(`item_gold`)으로 로그에 남긴다 | 모델 입력으로 쓰지 않는다. 학습 데이터의 골드는 "벌어들인 총골드"(쓰지 않은 골드 포함)라 아이템 가격 합과 정의가 달라, 섞으면 학습과 서빙의 값이 어긋난다 |

브리지는 `allPlayers`에서 내 챔피언(`activePlayer`와 같은 선수)과 상대 팀 최다 킬 챔피언(동률이면 최다 CS, 그래도 같으면 목록 순서)을 찾아 `my_champion`·`enemy_top_threat`로 넘긴다. 챔피언은 `rawChampionName`의 `game_character_displayname_` 뒤 영문 ID를 먼저 쓰고, 없으면 `championName`을 쓴다. 둘 다 없으면 그 칸을 넣지 않는다(서버 기본값 AD·AP). 관전처럼 `activePlayer`가 없어 `--side`로 팀을 정하면 `my_champion`은 넣지 않고 `enemy_top_threat`만 넣는다. 새 챔피언처럼 `api/game_data.json`에 아직 없는 이름이라 `/coach`가 422를 돌려주면, 두 칸을 빼고 한 번만 다시 부른다(승률·목표는 받고 빌드만 기본값). 브리지가 쓰는 응답 필드와 변환 규칙은 `client/bridge.py` 머리 주석이 정본이다. 실제 게임 클라이언트의 응답으로는 아직 확인하지 않았고, 테스트는 공개 문서의 필드 구조로 만든 고정 자료로 한다.

### 3-2. 게임을 방해하지 않는 알림 (브리지)

- (기본 모드) `gameData.gameTime >= 600`(10분)이 된 뒤 **한 번만** `/coach`를 부른다. 그 전에는 2초마다 시간만 확인한다. 게임 내내 계속 판단하는 오버레이 모드는 3-4절.
- 알림은 두 가지이고 각각 켜고 끈다. ① 이펙트: 화면 우측 상단의 작은 카드(토스트), 최대 3줄(승률 · 상위 % · 핵심 목표 1개), 5초 뒤 자동으로 사라짐(3~8초 사이로 조정). 카드 테두리는 `build_advice.state`에 따라 초록(`ahead`)·노랑(`even`)·빨강(`behind`)으로 빛난다. ② 음성 한 번(PC 내장 음성 합성). 표·긴 글·그래프는 띄우지 않는다.
- 켜고 끄는 법: 실행 옵션이 환경 변수보다 앞선다.

| 옵션 | 환경 변수 | 기본값 | 뜻 |
|---|---|---|---|
| `--effect on\|off` | `LOL_EFFECT` | `on` | 테두리 빛남이 있는 토스트 카드 |
| `--tts on\|off` | `LOL_TTS` | `off` | 음성 안내 |

- 환경 변수 값은 `on`·`off`(대소문자 무시, `1`·`0`·`true`·`false`도 받음)만 받고, 그 밖의 값이면 시작할 때 오류로 멈춘다. 둘 다 `off`면 터미널에 같은 문구를 찍는다. 이전 옵션 `--card`·`--voice`는 각각 `--effect on`·`--tts on`과 같다.
- 카드·음성 문구는 응답의 `toast` 그대로다(아래 4절). 브리지가 따로 문장을 만들지 않는다.

### 3-3. 웹 시뮬레이터 표시 규칙 (`GET /`, `api/static/index.html`)

초보자가 글을 읽지 않고도 따라 하도록 응답 값을 그림으로 바꿔 보여 준다. 화면이 계산을 새로 하지 않고 `/coach` 응답만 옮긴다.

| 칸 | 무엇을 보고 | 어떻게 보여 주나 |
|---|---|---|
| 결과 카드 제목 | 고정 | `실시간 게임 중 승률 예측`(입력은 10분 페이스 기준 점수판). 위쪽에 오버레이(`/overlay`)·발표 슬라이드(`/slides`) 링크 |
| 테두리 이펙트 | `build_advice.state` | 뷰포트 가장자리와 결과 카드 테두리를 초록(`ahead`, 문구 `우세 · 적극 교전 가능`)·노랑(`even`, `접전 · 파밍 집중`)·빨강(`behind`, `열세 · 1대1 싸움 금지`)으로 빛나게 한다. 상단의 **[테두리 이펙트 ON/OFF]** 버튼으로 바로 끄고 켠다(`aria-pressed`, 브라우저에 기억, 저장이 안 되는 환경에서도 동작). 동작 줄이기 설정(`prefers-reduced-motion`)이면 깜빡임 없이 색만 |
| 분포 막대 | `tier_percentile.overall_top_pct`, `coaching[0].overall_top_pct_after` | 현재 위치 ●, 첫 목표를 채웠을 때 위치 ★ |
| 챔피언 초상화 | 선택한 내 챔피언·상대 위협 | `https://ddragon.leagueoflegends.com/cdn/{version}/img/champion/{id}.png`. `version`·`id`는 `GET /meta/champions`. 역할군·유형으로 고르면 글자 배지 |
| 아이템 카드 | `build_advice.recommended_items` | `[1단계 신발] → [2단계 하위템] → [3단계 코어]` 카드 3장. 각 카드에 `.../cdn/{version}/img/item/{item_id}.png` 아이콘, `name`, `{gold}G`, `why` |
| 스킬 키 | `skill_order.order` | `"Q > E > W"`를 키캡 `[Q] ▶ [E] ▶ [W]`로 차례로 점등. `null`이면 키캡 없이 `focus` 문장과 `원칙만` |
| 3단계 행동 카드 | `build_advice.state` + `coaching[0]` | 상태별 고정 문구 3장. 2번째 카드에 첫 목표(`{name} +{add}`)를 넣는다. 아래 표 |
| 오류 | 422 응답의 `detail[].loc` | 챔피언 칸이면 `내 챔피언(또는 상대 위협) 이름을 찾지 못했어요`, 그 밖이면 일반 입력 오류. 챔피언 칸 오류는 결과 카드와 그 입력 칸 바로 아래 두 곳에 띄운다(모바일에서 위 카드가 화면 밖이어도 읽히게). 이전 결과는 흐리게 |

**3단계 행동 카드 문구** (`index.html`의 `ACTIONS` 한 곳에 둔다. 모델이 낸 값이 아니라 사람이 정한 상태별 고정 문구다)

| 상태 | ① | ② | ③ |
|---|---|---|---|
| `behind` | 🛡️ 1대1 싸움 멈추기 | 🌾 포탑 앞에서 미니언 챙기기 + 첫 목표 | 🏃 아군 강세 라인 합류 |
| `even` | 🌾 파밍 집중 | 🎯 첫 목표 채우기 | 👀 오브젝트 전에 모이기 |
| `ahead` | ⚔️ 적극 교전 가능 | 🎯 첫 목표로 격차 굴리기 | 🐉 오브젝트 압박 |

첫 목표가 없으면(`coaching`이 비면) ②는 `지금 페이스 유지`다. 이 문구는 상태에 따른 일반 원칙이고, 특정 시각·특정 오브젝트를 지금 하라는 지시가 아니다(12절).

### 3-4. 게임 중 오버레이 (`client/bridge.py --overlay`, `GET /overlay`)

기본 모드(3-2)가 10분에 한 번 알리는 것과 달리, 오버레이 모드는 게임 시작부터 끝까지 2초마다 로컬 게임 API를 읽어 게임 창 위의 작은 HUD(380×560)에 보여 준다. 쓰는 곳은 3-1과 같다(관전·리플레이·연습 모드, 랭크 게임 중에는 쓰지 않는다. 12절).

**10분 페이스 환산 (`extract_features_from_liveclient`)**

모델은 10분 스냅샷으로 학습했다. 다른 시각의 점수판을 그대로 넣으면 3분에는 격차가 작아 보이고 25분에는 모든 격차가 과장된다. 그래서 누적 값을 "지금 속도로 10분이 되었을 때"의 값으로 바꿔 넣는다.

| 항목 | 규칙 |
|---|---|
| 경과 분 | `minutes = max(gameData.gameTime / 60, 1.0)` (1분 전은 1분으로 본다) |
| 배율 | `pace_scale = 10 / minutes` |
| 누적 지표 `kills`·`assists`·`cs`·`dragons`·`heralds`·`towers` | 양 팀 각각 `값 × pace_scale`을 반올림(0.5는 올림)하고 3절 허용 범위 안으로 자른다(정수 칸이라 소수는 422) |
| `avg_level` | 바꾸지 않는다. 레벨은 시간에 비례해 쌓이지 않는다. 1~18로만 자른다 |
| `first_blood`·`my_side`·챔피언 칸 | 그대로 |

- 10:00(600초)에서는 배율이 1이라 기본 모드 입력과 같다.
- **한계**: 환산한 승률은 데이터로 검증하지 않은 가정이다. 성능 게이트와 Parity는 10분 스냅샷에서만 확인했다. 이른 시각에는 킬 한두 개도 크게 부풀려진다. 그래서 화면에 `10분 페이스 환산`을 함께 적는다.

**`/coach` 호출**: 첫 호출은 바로, 그 뒤에는 환산 입력이 직전 호출과 다르고 직전 호출에서 5초(`COACH_MIN_SECONDS`) 넘게 지났을 때만 부른다. 422 대응은 3-1과 같다. 호출이 실패하면 직전 응답을 그대로 두고 `coach_error`에 이유를 적는다.

**보유 아이템 `extract_owned_items(allgamedata)`**: `activePlayer`와 이름(`riotId`·`riotIdGameName`·`summonerName`)이 같은 선수, 없으면 `allPlayers`의 첫 선수의 `items`에서 `[{itemID, displayName, count, slot}]`.

**다음 스킬 `next_skill(level, order, abilities=None)`**

- 계획: 1~3레벨은 `order` 순서로 세 스킬을 하나씩, 6·11·16레벨은 R, 그 밖의 레벨은 `order`에서 레벨 상한에 닿지 않은 첫 스킬(기본 스킬 r랭크는 레벨 `2r − 1`부터, 최대 5랭크). `"Q > W > E"`면 1~18레벨이 `Q W E Q Q R Q W Q W R W W E E R E E`.
- `abilities`(로컬 API `activePlayer.abilities`의 `abilityLevel`)가 있으면 계획을 1레벨부터 따라가며 실제로 찍은 수가 계획보다 적은 첫 스킬을 `when: "now"`로, 다 찍었으면 다음 레벨의 계획 스킬을 `when: "next_level"`로 낸다. 없으면 이번 레벨의 계획 스킬을 `"now"`로 낸다.
- `order`가 `null`(규칙표에 없는 챔피언)이면 6·11·16레벨의 R만 내고 그 밖에는 `null`.

**로컬 상태 서버**: `--overlay`면 `127.0.0.1:<--listen-port, 기본 8765>`에만 HTTP 서버를 연다. `GET /bridge/state`는 아래 JSON을 `Access-Control-Allow-Origin: *`, `Access-Control-Allow-Private-Network: true`, `Cache-Control: no-store`와 함께 돌려준다(`OPTIONS`도 같은 헤더로 204). 그 밖의 경로는 404. 게임 API를 읽지 못해도 서버는 멈추지 않고 `connected: false`와 마지막 상태를 돌려준다.

| 필드 | 뜻 |
|---|---|
| `connected` / `source` | 게임 API(또는 `--file`)를 이번에 읽었는지, `"live"`·`"file"` |
| `in_game` / `game_time_sec` / `minutes` / `pace_scale` | 게임 중인지(`gameTime > 0`), 경과 초, 환산에 쓴 분과 배율 |
| `updated_at` | 마지막으로 읽은 시각(유닉스 초) |
| `champion` / `level` | 내 챔피언 영문 ID, 내 레벨(`activePlayer.level`) |
| `owned_items` | `extract_owned_items` 결과 |
| `next_skill` / `next_skill_when` | 위 규칙. 스킬 순서는 마지막 `coach.build_advice.skill_order.order` |
| `features` / `raw_features` | 환산한 `/coach` 입력, 환산 전 입력 |
| `coach` / `coach_error` / `error` | 마지막 `/coach` 응답(없으면 `null`), 호출 실패 이유, 게임 API 읽기 실패 이유 |

**팝업 `--launch-popup`**: `<--server>/overlay?bridge_url=http://127.0.0.1:<포트>/bridge/state`를 380×560 창으로 화면 우측 상단에 띄운다(`--server`는 `--api`와 같은 옵션).

1. macOS이고 `swiftc`가 있으면 `client/overlay_popup.swift`를 사용자 캐시 폴더에 컴파일(소스가 바뀌었을 때만)해 실행한다. `NSPanel`을 `.floating` 단계로 띄우고 모든 데스크톱·전체 화면 보조 창으로 두며, 눌러도 게임에서 입력 초점을 빼앗지 않는다(`nonactivatingPanel`).
2. 아니면 Chrome `--app=<URL> --window-size=380,560`(항상 위는 보장하지 않는다), 그것도 없으면 기본 브라우저.

LoL은 "테두리 없는 창 모드"여야 위에 뜬다. 전체 화면 독점 모드에서는 어떤 창도 게임 위에 뜨지 않는다.

**오버레이 화면 (`api/static/overlay.html`)**: 화면이 계산을 새로 하지 않고 `/coach` 응답과 브리지 상태만 옮긴다(데모 시나리오의 환산만 위 규칙 그대로 화면에서 한다).

| 칸 | 무엇을 보고 | 어떻게 보여 주나 |
|---|---|---|
| 상태 배지 | 브리지 `connected`, `game_time_sec` | 연결되면 `🟢 게임 중 LIVE (MM:SS)`, 아니면 `🟡 게임 중 시뮬레이션 (MM:SS)`(데모 시나리오 시각) |
| 이펙트 토글 | 사용자 | `이펙트 ON`·`이펙트 OFF`. 끄면 구매·스킬 강조 테두리의 빛과 애니메이션을 바로 없앤다(배지는 남김). `aria-pressed`, 브라우저에 기억(저장 실패해도 동작) |
| 승률 (`win_prob`) | `my_win_prob` | 제목 `실시간 게임 중 승률`, 그 아래 `10분 페이스 환산` |
| 전술 배지 (`tactical_action`) | `build_advice.state` | 3-3의 테두리 문구(`우세 · 적극 교전 가능` 등) |
| 한 줄 코칭 (`coach_message`) | `coaching[0].message` | 목표가 없으면 `지금 페이스 유지` |
| 시작 아이템 (`starter_items`) | `build_advice.starter_items` | 아이콘 카드 |
| 구매 순서 (`boots`, `core_items`) | `build_advice.recommended_items` | `1단계 신발 → 2단계 하위템 → 3단계 코어` 카드. 규칙표가 정한 구매 순서가 이 세 단계라 코어 아이템 3개를 따로 지어내지 않는다 |
| 상황별 아이템 (`situational_items`) | `build_advice.situational_items` | 아이콘 카드와 `why` |
| 보유 표시 | `owned_items[].itemID` | 같은 `item_id` 카드에 `✓ 보유 완료`와 반투명 |
| 지금 구매 추천 | 구매 순서 중 보유하지 않은 첫 카드 | `지금 구매 추천` 배지와 금색 맥박 테두리. 카드를 누르면 화면 안에서 보유를 바꿔 강조가 다음 카드로 옮겨 간다(브리지에 연결된 동안에는 2초마다 실제 인벤토리로 다시 맞춘다) |
| 스킬 키 | `skill_order.order`, `next_skill` | 키캡 줄과 이번에 찍을 키 카드(`[Q]`·`[R]` 등)에 청록 맥박 테두리. 데모에서는 시나리오 레벨로 위 계획을 계산 |
| 폴링 | `?bridge_url=`(기본 `http://127.0.0.1:8765/bridge/state`) | 2초마다. 실패하면 시뮬레이션으로 둔다 |
| 데모 | `초반 우세 06:30`·`중반 박빙 14:20`·`후반 열세 23:10` | 시나리오의 누적 점수판을 위 규칙으로 환산해 같은 출처의 `/coach`를 부른다. 데모를 누르면 상태 배지를 누를 때까지 LIVE 결과로 덮지 않는다 |

글자는 모두 `textContent`로 넣고(브리지·서버 응답을 HTML로 해석하지 않는다), 아이콘 주소는 정수 `item_id`와 `/meta/champions`의 `version`으로만 만든다. 동작 줄이기 설정이면 깜빡임 없이 색만.

## 4. 출력 계약

### `GET /health`

```json
{"status": "ok", "model": "onnx", "app_version": "v1.0", "model_version": "m20261118-2"}
```

`model_version`은 `MODEL_DIR/model.json`의 `model_version`이다. 파일이 없으면 `"baked"`(이미지에 구운 모델). **이 네 필드와 `MODEL_DIR`을 읽는 규칙은 어느 언어로 서버를 만들든 같아야 한다**(6절).

### `POST /predict` (도구 이름 `predict_pace`)

| 필드 | 보장 |
|---|---|
| `win_prob_blue` | 0~1 실수. **반올림하지 않는다**(표시 반올림은 쓰는 쪽). 반올림하면 원본과 ONNX 비교에서 경계값이 1e-4로 갈라진다 |
| `my_side` | 입력 그대로 |
| `my_win_prob` | `my_side`가 블루면 `win_prob_blue`, 레드면 `1 − win_prob_blue` |
| `pace` | `my_win_prob < 0.45`면 `"열세 페이스"`, `0.45 ≤ p ≤ 0.55`면 `"접전"`, `> 0.55`면 `"우세 페이스"` |
| `top_factors` | 정확히 3개. `abs(impact_pp)` 내림차순, 같으면 `FEATURES` 순서 |
| `top_factors[].feature` / `name` | 피처 키와 한국어 이름 |
| `top_factors[].my_value` / `enemy_value` | 내 팀 값, 상대 팀 값. `first_blood`는 가진 팀 1, 아닌 팀 0 |
| `top_factors[].diff` | `my_value − enemy_value` |
| `top_factors[].tier_avg_diff` | 티어 평균 경기의 같은 차이(내 팀 기준). 블루면 `feature_mean`, 레드면 `−feature_mean`. `first_blood`는 각각 `2m−1`, `1−2m` |
| `top_factors[].impact_pp` | 이 피처만 티어 평균 값으로 바꿨을 때보다 지금 승률이 몇 %p 높은가(내 팀 기준). 음수면 승률을 깎는 요인 |
| `vs_tier` | 팀 지표 7개 각각 `{stat, name, mine, tier_avg, gap}`. `tier_avg`는 학습 데이터의 팀당 평균(블루·레드 합쳐 계산), `gap = mine − tier_avg` |
| `tier` | 기준선이 된 데이터 설명(예: 다이아몬드 10분). `baseline.json`의 값 |
| `tier_percentile` | 같은 티어 경기 분포에서 내 위치. `overall_top_pct`(내 승률 페이스)와 팀 지표 7개의 `<지표>_top_pct`(내 팀 격차). 0~100 실수, 작을수록 앞선다(상위 10% = 10). 반올림 안 함 |
| `message` | 1초 안에 읽히는 한 줄. `승률 {p:.0f}% · {pace} · 티어 상위 {overall:.0f}%` |
| `warnings` | 문자열 목록. 비면 정상 |
| `model` / `model_version` | `/health`와 같은 값(어느 판이 낸 답인지 추적용) |

**요인 계산 정의**: 블루 기준 확률 `p(x)`에서 `x_f`만 `feature_mean_f`로 바꾼 `x'`를 만들고 `impact = (p(x) − p(x')) × 100`. 레드 기준이면 부호를 뒤집는다. 계수를 읽지 않고 예측만 다시 부르므로 규칙·로지스틱·신경망·ONNX 모두 같은 방식으로 계산된다.

**백분위 계산 정의**

- 학습할 때 `baseline.json`의 `quantiles`에 분위수 표를 저장한다. 백분위 점 `ps = [0, 5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100]`. 서빙은 원본 CSV 없이 이 표만 쓴다.
- 지표 표 `quantiles.<지표>`: 학습 표본의 **한 팀 기준 격차**(블루 관점 `블루 − 레드`와 레드 관점 `레드 − 블루`를 합친 것)의 분위수. 어느 팀으로 요청해도 같은 표를 쓰기 위해 두 관점을 합친다. 첫 킬은 0/1 값이라 백분위가 뜻이 없어 표를 만들지 않는다.
- 종합 표 `quantiles.win_prob`: 학습 표본에 대한 그 판 모델(sklearn)의 승률 `p`와 `1 − p`를 합친 분위수.
- 값 `v`의 백분위 `pct(v)`: 표에서 `v`와 같은 값이 있으면 그 값들이 차지한 백분위 점의 평균(드래곤처럼 −1·0·1뿐인 지표의 동점 구간), 없으면 이웃한 두 점 사이 선형 보간, 표의 최솟값보다 작으면 0, 최댓값보다 크면 100. `top_pct = 100 − pct(v)`.

### `POST /coach` (도구 이름 `coach_targets`)

`/predict`의 모든 필드(근거 수치 `top_factors`·`vs_tier` 포함)에 아래를 더해 돌려준다. AI 에이전트가 한 번만 불러도 승률, 원인, 목표를 함께 받게 하기 위해서다.

| 필드 | 보장 |
|---|---|
| `horizon_min` | 코칭 단계가 가정한 시간(분). `api/coach_steps.json`의 값 |
| `coaching` | 승률을 올리는 목표만(`gain_pp > 0`). `gain_pp` 내림차순, 같으면 단계 파일 순서 |
| `coaching[].stat` / `name` / `add` | 올릴 팀 지표, 이름, 올릴 양 |
| `coaching[].target_value` | 내 팀의 목표 값 = 현재 값 + `add` |
| `coaching[].with` | 함께 움직인 지표와 양(내 팀 기준), 예 `{"assists": 2.3}` |
| `coaching[].win_prob_after` | 목표를 채웠을 때 내 팀 승률(반올림 안 함) |
| `coaching[].gain_pp` | `(win_prob_after − my_win_prob) × 100` |
| `coaching[].top_pct_before` / `top_pct_after` | 그 지표 격차의 티어 내 위치, 목표 전과 후 |
| `coaching[].overall_top_pct_after` | 목표를 채웠을 때 종합 페이스의 위치(`win_prob_after`의 백분위) |
| `coaching[].message` | `{name} {add}{unit} 더 → 상위 {before:.0f}%에서 {after:.0f}%로 (승률 {now:.1f}% → {after:.1f}%, +{gain:.1f}%p)`. 비교 검사 대상이 아니다 |
| `message` | `/predict`의 한 줄 뒤에 ` → 목표: {첫 목표 name} +{add} (+{gain:.1f}%p)`. 목표가 없으면 ` → 지금 페이스 유지` |
| `toast` | 알림 카드용 2~3줄 목록. ① `승률 {p:.0f}% · 상위 {overall:.0f}%` ② 첫 목표가 있으면 `{name} +{add} → {after:.0f}% (상위 {overall_after:.0f}%)` ③ 경고가 있으면 `학습 범위 밖 입력 있음`, 없으면 `{1단계 아이템} → {2단계 아이템}`(스킬 순서가 있으면 ` · {order}`를 붙임) |
| `build_advice` | 상황별 아이템·스킬 가이드. 아래 "빌드 가이드 정의" |
| `how_to_read` | 읽는 법. 이 값은 개입 효과가 아니라 과거 경기의 동반 변화로 계산한 가정 계산이다 |

**빌드 가이드 정의 (`build_advice`)**

모델은 상태만 정한다. 아이템과 스킬은 **사람이 정한 규칙표** `api/build_rules.json`이 정하고, 아이템 ID·이름·가격은 `api/game_data.json`(Data Dragon에서 만든 표, 판 번호 기록)과 대조된 것만 쓴다. 승률·요인·목표처럼 모델이 계산한 값이 아니다.

| 필드 | 보장 |
|---|---|
| `state` | `my_win_prob > 0.55`면 `"ahead"`, `< 0.45`면 `"behind"`, 그 사이면 `"even"` (`pace`와 같은 경계) |
| `my_champion` / `my_class` / `my_damage` | 해석한 내 챔피언 한국어 이름(역할군으로 받았으면 `null`), 계열(`marksman`·`fighter`·`assassin`·`mage`·`tank`·`support`), 주 피해(`AD`·`AP`) |
| `enemy_top_threat` / `threat_type` | 해석한 상대 위협 챔피언 이름(유형으로 받았으면 `null`)과 위협 유형(`AD`·`AP`·`HEAL`·`TANK`) |
| `posture` | 상태별 빌드 방향 한 줄(규칙표의 문장) |
| `recommended_items` | 정확히 3개, `step` 1·2·3 순서. 1 = 신발, 2 = 하위템(대응 또는 피해), 3 = 코어. 각 `{step, slot, item_id, name, gold, why}` |
| `starter_items` | 게임 시작 때 살 아이템. 규칙표 `starter[내 계열]` 순서. 각 `{item_id, name, gold, why}` |
| `situational_items` | 상황이 바뀌면 바꿔 살 아이템, 최대 3개. 각 `{item_id, name, gold, why}`. 아래 적용 순서 8 |
| `skill_order` | `{order, focus, source}`. `order`는 규칙표에 그 챔피언이 있으면 `"Q > W > E"` 형식, 없으면 `null`. `focus`는 상태별 마스터 원칙 문장. `source`는 `"규칙표"` 또는 `"원칙만"` |
| `basis` | 근거 출처 문장(규칙표와 Data Dragon 판 번호). 모델 계산이 아님을 밝힌다 |

**규칙표 적용 순서**

1. 내 계열: 역할군이면 `AD → marksman`, `AP → mage`, `TANK → tank`, `SUPPORT → support`. 챔피언이면 Data Dragon `tags`의 첫 값. 주 피해는 계열이 `mage`이거나 Data Dragon `info.magic > info.attack`이면 `AP`, 아니면 `AD`.
2. 위협 유형: 유형으로 받으면 그대로. 챔피언이면 규칙표의 `heal_champions`에 있으면 `HEAL`, `tags` 첫 값이 `Tank`면 `TANK`, 아니면 그 챔피언의 주 피해(`AD`·`AP`).
3. 1단계 신발: `behind`이고 위협이 `AD`·`AP`면 그 피해를 막는 신발(`boots.defensive`), 그 밖에는 내 계열 신발(`boots.by_class`).
4. 2단계 하위템: 위협이 `HEAL`이면 치유 감소(`anti_heal[내 피해 또는 tank]`), `TANK`면 관통(`anti_tank[…]`), 그 밖에 `behind`면 방어 하위템(`defensive_component[위협]`), 아니면 피해 하위템(`damage_component[내 계열]`).
5. 3단계 코어: `behind`이고 위협이 `AD`·`AP`면 `core.behind[위협][내 계열]`, 그 밖에는 `core.ahead[내 계열]`.
6. 스킬: `skill_orders[챔피언 ID]`가 있으면 그 순서, `focus`는 `skill_focus[state]`.
7. 시작 아이템: `starter[내 계열]`, `why`는 `why.starter`.
8. 상황별 아이템: 후보를 이 순서로 놓는다. ① `anti_heal[내 피해 또는 tank]`(`why.situational_heal`) ② `anti_tank[…]`(`why.situational_tank`) ③ `defensive_component.AD`(`why.situational_ad`) ④ `defensive_component.AP`(`why.situational_ap`). `recommended_items`에 이미 있는 아이템과 앞 후보와 같은 아이템을 빼고 앞에서 3개.

규칙표와 `game_data.json`은 패치마다 사람이 다시 확인한다. `training/ddragon.py`가 Data Dragon 최신 판에서 `game_data.json`을 다시 만들고, 규칙표의 아이템 ID가 그 판에 없으면 실패한다.

**코칭 계산 정의**

1. `api/coach_steps.json`의 단계마다 내 팀 지표에 `add`를 더한다(블루면 차이 `+add`, 레드면 `−add`). 더한 값이 3절 허용 범위를 넘으면 그 단계는 뺀다.
2. **연쇄 반영**: 단계에 적힌 `with` 지표도 함께 움직인다. 움직이는 양은 `add × slope[stat][with]`이고, `slope`는 학습 데이터 차이값의 단순 회귀 기울기(`cov / var`)로 `baseline.json`에 저장된다. 예를 들어 킬을 2개 더 하면 어시스트와 평균 레벨도 과거 경기에서 함께 늘어난 만큼 올린다. 킬만 올리고 어시스트를 고정하는 것은 실제로 없는 상황이기 때문이다(원본 프로젝트의 골드 연쇄 보정과 같은 원리).
3. 바뀐 입력으로 `predict_proba`를 다시 불러 승률 변화를 잰다.

### `GET /meta/champions` (웹 시뮬레이터용, `include_in_schema=False`)

`api/game_data.json`을 화면용으로 줄여 돌려준다. 도구 스키마(`/openapi.json`)에는 나오지 않는다.

```json
{"version": "16.23.1", "source": "Data Dragon 16.23.1 (ko_KR)",
 "champions": [{"id": "Ahri", "name": "아리", "tags": ["Mage", "Assassin"]}],
 "roles": ["AD", "AP", "TANK", "SUPPORT"], "threat_types": ["AD", "AP", "HEAL", "TANK"]}
```

`champions`는 `game_data.json`의 모든 챔피언, 한국어 이름 순. `roles`는 규칙표 `role_class`의 키, `threat_types`는 규칙표 `threat_types` 그대로다.

### `GET /overlay` (게임 중 오버레이 HUD, `include_in_schema=False`)

`api/static/overlay.html`을 그대로 돌려준다(`text/html`). 표시 규칙과 브리지 연동은 3-4절.

### `GET /slides` (발표 슬라이드, `include_in_schema=False`)

`api/static/slides.html`을 그대로 돌려준다(`text/html`). 5분 발표용 7장 슬라이드이고 `발표대본.md`의 다섯 구간과 같은 순서다. 키보드 좌우 화살표·스페이스·클릭으로 넘기고, 위쪽 진행 막대와 `n / 7` 표시, 주소 `#n`으로 바로 가기를 지원한다. 외부 글꼴·CDN·그림 없이 한 파일로 완결한다(발표장 네트워크가 막혀도 뜬다). 시뮬레이터(`GET /`)와 서로 가는 링크를 둔다. 수치는 README 12절의 실측값만 쓴다.

### AI 에이전트 연동

- `GET /openapi.json`이 두 도구(`predict_pace`, `coach_targets`)의 입력·출력 스키마를 그대로 내준다. 에이전트는 이 스키마로 함수 호출 도구를 만든다.
- 에이전트는 승률·요인·목표를 스스로 계산하지 않고 응답의 숫자만 말한다. 원본 프로젝트의 정성 시험(SC-7)에서 언어 모델이 킬 수만 보고 틀린 조언을 낸 일이 있어서다. 답할 때 `model_version`을 함께 남겨 어느 판의 계산인지 추적한다.

**시연 스크립트 `client/agent_demo.py`** ("AI에게 서비스를 제공한다면 어떻게 하나"를 보여 주는 데모)

언어 모델 API를 부르지 않는다(키 없이 발표장에서 돌고, 테스트할 수 있게). 언어 모델이 할 일 중 "어느 도구를 어떤 인자로 부를지"는 시나리오에 적어 두고 그대로 재생하고, 나머지(도구 정의 만들기, 도구 실행, 응답으로 브리핑 쓰기)는 실제로 한다. 실제 언어 모델에 붙일 때는 `load_tool_schemas`가 만든 도구 정의를 그대로 넘기면 된다.

| 함수 | 계약 |
|---|---|
| `load_tool_schemas(base_url, get=None)` | `GET {base_url}/openapi.json`에서 `operationId`가 `predict_pace`·`coach_targets`인 POST 경로를 찾아 함수 호출 도구 정의 목록으로 바꾼다. 각 항목 `{name, description, input_schema, path}`. `description`은 경로의 설명 문장, `input_schema`는 요청 본문 스키마에서 `$ref`를 모두 풀어 넣은 독립 JSON Schema(`additionalProperties: false`, `required` 유지). `path`는 도구를 실행할 경로이고 언어 모델에 넘길 때는 뺀다. 두 도구 중 하나라도 없으면 `ValueError` |
| `run_agent_briefing(base_url, scenario=SCENARIO, get=None, post=None)` | 도구 정의 조회 → 시나리오의 도구 호출(`tool`, `input`) 실행(`POST {path}`) → `compose_briefing`. `{tools, tool_call, response, briefing}`을 돌려준다. 시나리오가 고른 도구가 정의 목록에 없으면 `ValueError`, 서버가 422면 그 오류를 그대로 올린다 |
| `compose_briefing(response)` | `/coach` 응답만으로 한국어 브리핑 한 문단을 만든다. 쓰는 값: `my_win_prob`, `pace`, `tier_percentile.overall_top_pct`, `top_factors`(앞 2개), `coaching[0]`(`name`·`add`·`win_prob_after`·`gain_pp`), `horizon_min`, `build_advice`(`state`·`enemy_top_threat`·`threat_type`·`recommended_items`·`skill_order.order`), `warnings`, `how_to_read`, `model_version`, `basis` |

- **지어낸 숫자 금지**: 브리핑에 나오는 모든 숫자는 응답 JSON에 있는 값이거나 그 값을 반올림한 것(확률은 ×100)이어야 한다. 문장 틀에 숫자를 직접 쓰지 않는다("1순위" 대신 "가장 크게"). 테스트가 브리핑의 숫자를 모두 뽑아 응답에서 나올 수 있는 값인지 확인한다.
- 목표가 없으면 "지금 페이스 유지", `warnings`가 있으면 학습 범위 밖 입력이라 조심하라는 문장을 넣는다. 끝에 `how_to_read`와 출처(`model_version`, `basis`)를 붙인다.
- 실행: `uv run python -m client.agent_demo --server http://127.0.0.1:8083` (`--json`이면 도구 정의와 응답 전체를 JSON으로).

### 오류

| 상황 | 응답 |
|---|---|
| 3절의 거절 입력 | 422, 본문 `{"detail": [{"loc", "msg", "type"}]}`. 받은 값은 다시 싣지 않는다(NaN·Infinity는 JSON으로 쓸 수 없어 기본 처리기가 500을 낸다) |
| 없는 경로 | 404 |
| 모델 파일이 없거나 깨짐 | 프로세스가 뜨지 않는다(시작할 때 불러 실패하면 바로 죽는다). 그래서 `/health`가 실패하고 `server.sh`가 이전 판으로 되돌린다 |

## 5. 규칙 기준선 (`MODEL_IMPL=rule`)

사람이 정한 골드 환산 규칙이다. 학습하지 않는다.

```
gold_est = 300 × kills_diff + 20 × cs_diff + 250 × towers_diff + 300 × dragons_diff
p_blue   = 1 / (1 + exp(−gold_est / 2500))
```

- 300(킬 기본 현상금), 20(초반 CS 1개 평균 골드), 250(포탑 1개 전역 골드), 300(드래곤 1마리를 골드로 친 값)은 게임 지식으로 정한 값이다.
- 2500은 "접전 근처에서 골드 1,000당 승률 약 +10%p"가 되게 고른 값이다(시그모이드의 0 근처 기울기 `1 / (4 × 2500)` = 1,000골드당 0.1).

## 6. 다른 언어 서버를 더할 때 (지금은 없음)

지금은 Python(ONNX Runtime) 서버 하나뿐이다. Go·Rust 등 서버를 더하면 다음을 지킨다.

- **`/health`와 `MODEL_DIR`**: 4절의 네 필드(`status`, `model`, `app_version`, `model_version`)를 돌려주고, 모델과 `model.json`을 `MODEL_DIR`에서 읽는다. 이것이 빠지면 `server.sh model`이 `model_version`을 확인하지 못해 승격·되돌리기가 늘 실패한다.
- **ONNX Runtime 받기**: 버전은 `uv.lock`의 `onnxruntime` 값을 읽고(숫자를 적지 않는다), `github.com/microsoft/onnxruntime/releases` 받기가 실패(404)하면 NuGet `https://www.nuget.org/api/v2/package/Microsoft.ML.OnnxRuntime/${ORT_VERSION}`(zip 형식, `runtimes/<os-arch>/native/`)에서 받는다.
- **입력 오류는 모두 422**로 맞추고, `training/check_parity.py`의 `TARGETS`에 넣어 Python 원본과 같은 답을 내는지 확인한다.

## 7. 테스트 작성 규칙 (`api/tests/`, `client/tests/`, `deploy/tests/`)

- **범위**: `/predict`·`/coach`의 응답 형식과 계산 정의, 규칙 기준선 값, 백분위 계산(보간·동점 구간·표 밖), 입력 검증(422 목록 전부), `/health`, `/openapi.json`의 도구 이름, 레드 기준 뒤집기, 경계값(허용 범위의 끝, 하나 밖). 빌드 가이드의 규칙표 적용 순서(상태·계열·위협 조합)와 선택 칸 해석(이름·역할군·생략·없는 이름 422). `GET /meta/champions`의 형식과 스키마 숨김, `GET /slides`·`GET /overlay`가 HTML을 돌려주는지(오버레이는 강조 테두리·토글·브리지 폴링·`게임 중` 표시 포함). 빌드 가이드의 `starter_items`·`situational_items`. 오버레이 모드의 10분 페이스 환산(3분·15분·1분 미만·10분), 보유 아이템 추출, 다음 스킬 계획, `/coach` 호출 간격, 로컬 상태 서버의 JSON·CORS 헤더·게임 API 실패 때 `connected: false`, 팝업 실행 방식 선택. `client/agent_demo.py`의 도구 정의 변환(`$ref` 풀기, 두 도구), 브리핑에 응답의 승률·목표·아이템·`model_version`·`basis`가 들어가고 응답에 없는 숫자가 없는지. 브리지의 응답 변환, 내 챔피언·상대 최다 킬(동률이면 최다 CS) 챔피언 찾기, 422 때 두 칸 빼고 한 번 다시 부르기, 10분 한 번 호출, `--effect`·`--tts` 옵션과 환경 변수의 우선순위. `server.sh`의 판 형식 검사와 실패 시 되돌리기.
- **범위 밖**: 학습한 모델의 성능(`training/validate_model.py` 담당), 원본과 ONNX의 일치(`training/check_parity.py` 담당), 실제 Docker 컨테이너 기동(`deploy/local_e2e.sh` 담당).
- **기대값의 출처**: 5절 규칙 식과 4절 정의, 고정 자료 `api/tests/fixtures/baseline.json`에서 테스트 안에서 손으로 계산한다. **현재 코드의 출력을 정답으로 옮겨 적지 않는다.** 옮겨 적으면 버그도 정답이 된다.
- API 테스트는 `MODEL_IMPL=rule`, `MODEL_DIR=api/tests/fixtures`로 돌린다. 학습 산출물·Docker가 없어도 돌아야 하고, **skipped는 0건**이어야 한다.
- `server.sh` 테스트는 가짜 `docker`·`curl`을 `PATH` 앞에 두고 돌린다(실제 컨테이너를 띄우지 않는다).
- 테스트를 통과시키려고 앱 코드를 고치지 않는다. 앱이 계약과 다르면 앱 버그로 보고한다.

## 8. 뮤테이션 확인 절차 (테스트가 실제로 잡는지)

테스트를 새로 쓰거나 크게 고친 뒤 매번 한다.

1. `uv run pytest -q`가 전부 통과하고 skipped 0건인지 본다.
2. 계산 코드에 일부러 버그를 하나씩 넣는다. 최소 세 가지: ① `rule.py`의 확률 변조(예: `gold + 1`) ② `service.py`의 레드 기준 부호 뒤집기 누락 ③ `server.sh` 실패 복구에서 `MODEL_DIR`을 `/models/$PREV`가 아니라 `/models/$VER`로 되돌리는 버그.
3. 각 버그에서 `uv run pytest -q`가 **실패**하는지 확인하고, 실패한 테스트 이름을 보고에 적는다.
4. 원래대로 되돌리고 앱 코드가 원상태인지 확인한 뒤 다시 전부 통과를 확인한다.

## 9. 검증 단계별 기준

| 단계 | 스크립트 | 통과 기준 | 실패하면 |
|---|---|---|---|
| 성능 게이트 | `training/validate_model.py <sklearn\|dl>` | 고정 평가셋에서 ① Brier < 규칙의 Brier ② LogLoss < 규칙의 LogLoss ③ 정확도 ≥ 0.68 | exit 1, 그 뒤 단계(ONNX·승격) 진행 안 함 |
| ONNX 변환 | `training/export_onnx.py` | 평가셋 앞 200건에서 원본 vs ONNX 블루 승률 최대 오차 < 1e-4 (sklearn·dl 둘 다) | exit 1 |
| Parity | `training/check_parity.py` | 원본 서버(`MODEL_IMPL=sklearn`) vs 각 대상 서버, 23건(고정 3 + 시드 0 무작위 20). `/predict`의 `win_prob_blue`, `top_factors[].impact_pp/100`, `/coach`의 `coaching[].win_prob_after` 최대 오차 < 1e-4, 요인과 목표의 종류·순서 일치, 대상 `/health`의 네 필드 존재 | exit 1 |

- **고정 평가셋**: 전체 CSV를 `random_state=42`, 20% 층화 추출로 한 번 나눈 평가셋이다. `DATA_N`·`DATA_SEED`는 나머지 80%(학습 풀)에서만 뽑는다. 판이 바뀌어도 같은 시험지로 비교해야 "새 판이 더 낫다"를 말할 수 있다.
- **정확도 0.68의 근거**: 사전 분석에서 규칙 기준선의 평가셋 정확도가 약 0.71이고, 공개 지표 로지스틱 회귀도 약 0.71이었다. 정확도로는 둘이 비슷하므로 주 기준은 확률 품질(Brier·LogLoss)로 두고, 정확도는 "규칙보다 3%p 넘게 떨어진 고장 난 모델"을 막는 하한으로만 쓴다.
- **Brier를 주 기준으로 두는 이유**: 이 서비스는 "승률 43%", "+10.2%p"처럼 확률을 숫자로 보여 준다. 맞힌 비율보다 확률이 실제 빈도와 맞는지가 중요하다.

## 10. 판 이름과 승격·되돌리기 절차

| 판 | 형식 | 정해지는 곳 | 바뀌는 것 |
|---|---|---|---|
| 코드 판 | `v<숫자>(.<숫자>)*` 예: `v1.0` | git 태그 → `ci.yml` → GHCR 이미지 태그, `APP_VERSION` | 이미지 |
| 모델 판 | `m<UTC 날짜 8자리>-<실행 번호>` 예: `m20261118-2` | `model.yml`의 실행 번호 | 버킷의 모델 폴더와 서버의 `MODEL_DIR` |

**버킷 구조** (`${{ vars.MODEL_BUCKET }}`, Lightsail 버킷)

```
models/onnx/<모델 판>/model_sklearn.onnx
models/onnx/<모델 판>/model.json
models/onnx/<모델 판>/baseline.json
models/onnx/current.json          {"model_version", "set_at", "run"}
```

- 한 번 올린 판 폴더는 덮어쓰지 않는다(이미 있으면 승격 실패). 그래서 옛 판이 그대로 남아 되돌리기에 쓸 수 있다.
- `current.json`은 서버가 그 판으로 뜨고 `/health`의 `model_version`이 그 판임을 확인한 **뒤에만** 바꾼다.

**서버 구조** (`LOL_HOME`, 기본 `/srv/lol`)

```
/srv/lol/.env                  IMAGE, TAG, MODEL_VERSION, MODEL_DIR (+ SITE_ADDRESS, 선택) 서버가 바라는 상태
/srv/lol/compose.yml, server.sh, Caddyfile
/srv/lol/models/<모델 판>/      버킷에서 받은 판 (컨테이너에는 /models 로 읽기 전용 연결)
```

**`server.sh` 명령**

| 명령 | 하는 일 | 실패 처리 |
|---|---|---|
| `server.sh image <태그> <이미지 경로>` | 태그 형식 검사 → (stdin 토큰이 있으면) GHCR 로그인·pull → `.env`의 `TAG` 변경 → 다시 띄움 → `/health` 확인 | 30초 안에 `/health`가 안 되면 이전 `TAG`로 되돌리고 exit 1 |
| `server.sh model <모델 판>` | 판 형식 검사 → 판 폴더에 세 파일이 있는지 확인 → `.env`의 `MODEL_*` 변경 → 다시 띄움 → `/health`의 `model_version`이 그 판인지 확인 | 아니면 `MODEL_VERSION=$PREV`, **`MODEL_DIR=/models/$PREV`** 로 되돌리고 exit 1. 이전 판이 없으면 두 줄을 지워 구운 모델로 |
| `server.sh status` | `.env`, 컨테이너 상태, `/health` | |

- `/health` 확인은 호스트 안에서만 열린 `127.0.0.1:${API_PORT:-8080}`으로 한다. 밖에서 들어오는 길(Caddy)과 별개로 컨테이너 자체가 살았는지 보기 위해서다.

**승격 순서** (`model.yml`): (학습 러너, 한 잡) 학습 → 게이트 → ONNX → 모델 카드 → Parity → 세 파일 묶기 → (서버 러너) 묶음 확인 → 낡은 실행 검사 → 버킷에 새 판 올리기 → 서버로 받기 → `server.sh model` → `current.json` 갱신.

**잡 사이 파일 전달 (Actions 아티팩트를 쓰지 않는다)**

2026-11-18 `ci.yml` 실행에서 학습·게이트·ONNX는 통과했지만 `actions/upload-artifact`가 `Artifact storage quota has been hit`(계정 무료 저장 한도 초과)로 실패해 배포가 멈췄다. 아티팩트 저장 한도는 저장소가 아니라 계정 단위라 이 저장소에서 지워도 다시 걸릴 수 있다. 그래서 두 워크플로 모두 아티팩트를 쓰지 않는다.

| 워크플로 | 방식 | 이유 |
|---|---|---|
| `ci.yml` | `build` 한 잡에서 학습 → 게이트 → ONNX → Parity → (태그면) 이미지 빌드·GHCR 푸시. `artifacts/`를 그 자리에서 그대로 쓴다 | 같은 GitHub 러너 안이라 넘길 일이 없다. 게이트·Parity가 실패하면 같은 잡의 이미지 단계가 돌지 않는 순서도 그대로다 |
| `model.yml` | `train` 한 잡에서 학습 → Parity까지 하고, 서버에 필요한 세 파일(`model_sklearn.onnx`·`model.json`·`baseline.json`, tar.gz 후 base64 약 14 KB)을 **잡 출력**(`bundle`, `sha256`)으로 `promote`에 넘긴다. `promote`는 sha256과 `model.json`의 판 이름을 확인한 뒤 푼다 | 학습 러너(GitHub 러너·EC2)는 Lightsail 버킷에 키 없이 쓸 수 없다. S3 임시 경로를 쓰려면 장기 액세스 키를 GitHub에 둬야 해 1절 규칙에 어긋난다. 2 GB 서빙 인스턴스에서 학습하면 PyTorch를 서버에 깔아야 해 12절에 어긋난다. 잡 출력은 키도 저장 한도도 필요 없다(한 잡 1 MB 한도, 묶음이 500,000자를 넘으면 학습 잡이 실패한다) |

**낡은 실행 검사**: 이 실행이 시작된 뒤 `current.json`의 `set_at`이 바뀌었으면(그사이 다른 승격이나 되돌리기가 있었으면) 승격하지 않는다. 늦게 끝난 옛 실행이 방금 되돌린 판을 다시 덮어쓰지 않게 하기 위해서다.

**되돌리기 순서** (`rollback.yml`): 판 이름 형식 검사(입력은 `env`로만 넘긴다) → 버킷에서 그 판 받기 → `server.sh model <판>` → `current.json` 갱신. 이미지와 학습은 건드리지 않는다.

- 서버를 바꾸는 job(`deploy`·`promote`·`rollback`)은 모두 `runs-on: [self-hosted, lightsail]`, `concurrency: { group: lightsail-lol, cancel-in-progress: false }`로 한 줄로 선다.

## 11. 인프라 구성과 분리 기준

| 역할 | 자원 | 자격 증명 | 언제 도나 |
|---|---|---|---|
| 상시 서빙 | Lightsail 인스턴스(2 GB) + Docker Compose(api + Caddy) | 없음 | 24시간 |
| 모델 레지스트리 | Lightsail 버킷 `${{ vars.MODEL_BUCKET }}` | 서빙 인스턴스에 리소스 액세스로 연결(키 없음) | 승격·되돌리기 때 |
| 학습·Parity 워커(선택) | EC2 인스턴스, GitHub self-hosted 러너 이름표 `ec2-trainer` | EC2 IAM Role(원천 데이터 S3 읽기만) | `model.yml`을 `trainer=ec2`로 돌릴 때만 켠다 |
| 워커 켜기·끄기 | GitHub OIDC로 맡는 IAM 역할 `${{ vars.AWS_ROLE_ARN }}` | 없음(OIDC 단기 토큰) | `model.yml` 시작·끝 |
| 원천 데이터(수집한 경기 기록) | 일반 S3 버킷 `${{ vars.DATA_BUCKET }}` | EC2 IAM Role | 학습 때 |

- **EC2는 Lightsail 버킷에 직접 쓰지 않는다.** Lightsail 버킷의 리소스 액세스는 같은 리전의 Lightsail 인스턴스만 받고, 그 밖에서는 장기 액세스 키가 필요하다(AWS Lightsail 문서 "Control access to Lightsail buckets for instances", "Secure Lightsail object storage buckets", 2026-11-18 확인). 그래서 워커는 서버에 필요한 세 파일을 잡 출력으로 넘기고(아래 "잡 사이 파일 전달"), 버킷에 올리는 일은 서빙 인스턴스의 러너(`promote`)가 한다. 이렇게 하면 어디에도 키가 생기지 않는다.
- 기본값은 `trainer=github`(GitHub 러너)다. EC2 없이도 같은 흐름이 돈다. EC2는 대량 수집·재학습처럼 GitHub 러너의 시간·메모리가 모자랄 때만 쓴다.
- **실습용 `eta-mini-aws` 자원과 섞지 않는다.** 이름은 `lol-` 접두어(인스턴스 `lol-mini`, 버킷 `lol-models-…`), 서버 경로 `/srv/lol`, Compose 프로젝트 이름 `lol`, 동시 실행 그룹 `lightsail-lol`을 쓴다. 같은 인스턴스에 두면 80·443 포트와 러너가 겹치므로 서빙 인스턴스를 따로 만든다.

**HTTPS (Caddy)**: Caddy가 80·443을 혼자 맡고, api 컨테이너는 Compose 내부 네트워크의 `api:8080`으로만 받는다(호스트에는 `127.0.0.1:8080`만 열어 상태 확인에 쓴다). api가 80을 쥐고 Caddy가 443만 쓰는 방식(`disable_http_challenge`, TLS-ALPN-01 검증)도 되지만, 그러면 api가 인터넷에 평문으로 열린 채 남고 Cloudflare 프록시를 켜면 TLS-ALPN 검증이 통과하지 못한다. Caddy 전담이면 HTTP-01 검증과 80→443 전환을 Caddy가 알아서 하고, 밖에서 api로 가는 길이 하나뿐이다. `.env`의 `SITE_ADDRESS`가 비어 있으면 Caddy는 80 HTTP로만 받는다(DNS가 준비되기 전, 로컬 검증).

**배포 전 확인 목록**

1. `nslookup <내 도메인>`이 `<고정 IP>`를 돌려주는지 본다. `NXDOMAIN`이면 아직 DNS가 퍼지지 않은 것이니 `SITE_ADDRESS`를 비워 HTTP로 먼저 띄운다.
2. Cloudflare에서 처음 인증서를 받을 때는 그 레코드를 "DNS only"(회색 구름)로 둔다. 프록시를 켤 때는 SSL 모드를 Full(strict)로 한다.
3. Lightsail 방화벽에서 80·443을 연다. 22번은 러너 등록이 끝나면 닫을 수 있다.
4. GitHub 저장소 변수 `MODEL_BUCKET`, `AWS_REGION`(EC2를 쓰면 `AWS_ROLE_ARN`, `TRAINER_INSTANCE_ID`, `DATA_BUCKET`도)과 비밀 `KAGGLE_USERNAME`, `KAGGLE_KEY`를 등록한다.

## 12. 하지 않는 것

- 상대 팀의 가려진 정보(골드, 경험치, 와드, 위치, 쿨다운)를 읽거나 추정해 입력으로 받지 않는다.
- 랭크 게임 중에 쓰는 오버레이를 만들지 않는다. 3-4절의 오버레이는 관전·리플레이·연습 모드용이다.
- 전술 지시("지금 바론 가라")를 내리지 않는다. API는 정량 목표치만 준다. 웹 시뮬레이터의 3단계 행동 카드(3-3절)는 상태별 일반 원칙 문구이고 시각·대상을 정한 지시가 아니다.
- 베팅·배당 산출에 쓰지 않는다.
- 서빙 서버에 scikit-learn·PyTorch를 설치하지 않는다(이미지는 `onnxruntime`만).
- Riot 타임라인 대량 수집기는 이번 범위에 넣지 않는다(개발용 키가 24시간마다 만료되고 아직 승인된 키가 없다). EC2 워커 자리는 그 수집기를 위해 비워 둔 것이다.
