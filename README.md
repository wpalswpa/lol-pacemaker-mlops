# LoL 페이스메이커 (페이커)

> **연습모드·리플레이 중 10분 기준 모델의 페이스로 실시간 승률과 3분 행동 목표를 진단하고, 상대 공개 아이템(Tab)과 내 인벤토리에 맞춰 지금 살 아이템·찍을 스킬을 강조 테두리로 알려 주는 AI LoL 코치 & MLOps 파이프라인**

---

## 1. 바로 접속 및 원클릭 실행

| 용도 | 주소 | 설명 |
|---|---|---|
| **데스크톱 원클릭 다운로드** | **`https://jjm.eta.pyhub.kr/download`** | Windows(`.bat`) · macOS(`.zip`) 내려받아 더블클릭 실행 |
| **웹 시뮬레이터 · 전적 DB** | **`https://jjm.eta.pyhub.kr/`** | 설치 없이 브라우저에서 코칭 체험 및 PostgreSQL 전적 저장 |
| **인게임 HUD 미리보기** | **`https://jjm.eta.pyhub.kr/overlay`** | 초컴팩트 `[ㅁ]→[ㅁ]→[ㅁ]` 아이템 · 인라인 스킬 이펙트 · 최소화/투명도 |
| **서버·모델 상태 확인** | **`https://jjm.eta.pyhub.kr/health`** | 코드 판(`app_version`) · 모델 판(`model_version`) 조회 |

### PC 인게임 오버레이 10초 실행 (터미널 입력 불필요)
1. **`https://jjm.eta.pyhub.kr/download`** 에서 OS에 맞는 파일 다운로드
2. 더블클릭 실행 (Windows는 Python 3 자동 확인/설치, macOS는 `.app` 번들 실행)
3. 롤 화면 설정을 **`테두리 없음(Borderless)`**으로 두고 연습모드·리플레이 시작

---

## 2. 왜 만들었나 & 핵심 기능

기존 전적 사이트는 게임이 끝난 뒤에야 분석표를 보여 주고, 경기 중에는 고정된 통계 템트리만 나열합니다. **LoL 페이스메이커(페이커)**는 부정행위(안개 속 정보) 없이 **점수판(Tab) 공개 정보만** 읽어 프로 감독처럼 유연하게 코칭합니다.

1. **실시간 페이스 진단 (`POST /predict`)**: 10분 기준 ONNX 모델로 내 팀 승률과 같은 티어 내 백분위 위치 산출 *(10분 외 시간대는 분당 페이스 환산 가정)*
2. **3분 정량 행동 목표 (`POST /coach`)**: 승률을 가장 크게 깎는 요인 3가지와 앞으로 3분 안에 채울 목표(예: CS +20개 → 승률 +6.8%p) 제시
3. **상대 공개 아이템 기반 유연한 빌드·스킬 (`build_advice`)**: 상대 최다 위협 챔피언이 실제로 산 아이템(흡혈·방어 등)과 내 우세/열세 상태에 맞춰 신발·하위템·코어·운영 조언을 즉시 전환
4. **전적 축적 & 재학습 루프 (`POST /matches`, `GET /matches/export`)**: 경기 후 전처리된 8대 피처와 승패를 PostgreSQL에 저장하고, 학습 CSV 형식으로 내보내 모델 재학습에 환류

---

## 3. MLOps 아키텍처

![LoL 페이스메이커 MLOps 및 Task-Runner 전체 아키텍처](docs/images/mlops_architecture.png)

| 핵심 원칙 | 구현 방식 | 효과 |
|---|---|---|
| **성능 게이트** | 상식 규칙 기준선(`rule.py`)보다 Brier/LogLoss가 좋을 때만 배포 | 나쁜 모델 자동 차단 (`dl` 400회 Brier 0.2197 차단 실측) |
| **ONNX 일치(Parity)** | 전처리를 모델 안에 구워 `onnxruntime`으로 서빙, 원본과 오차 `<= 0.0001` 검증 | 학습·서빙 차이 제거, 로컬 p95 **0.73ms** |
| **코드·모델 분리** | 코드 판(`v1.x`)과 모델 판(`mYYYYMMDD-N`) 분리 | 코드 재배포 없이 모델만 교체 및 **16초 즉시 롤백** |
| **무키(Keyless) 배포** | Lightsail 내부 Self-Hosted Runner + 버킷 리소스 액세스 | 저장소에 SSH 키·AWS 키 0개 |
| **PostgreSQL 루프** | `postgres:16-alpine` (`shared_buffers=64MB`) + SQLite 테스트 폴백 | 2GB 서버 보호 및 동시 전적 적재·추출 지원 |

---

## 4. 실측 검증 결과

| 항목 | 실측 수치 | 근거 |
|---|---|---|
| **자동 테스트** | **390 passed**, skipped 0 | `.venv/bin/pytest -q` |
| **성능 게이트 (`m1` → `m2`)** | `m1`(500경기) Brier 0.1891 → `m2`(5,000경기) **0.1824** (규칙 대비 +4.84%) | [docs/model_comparison.md](docs/model_comparison.md) |
| **ONNX 일치 (Parity)** | 원본 sklearn vs ONNX 23건 최대 오차 `1.19e-07` (허용 `0.0001`) | `training/check_parity.py` |
| **서빙 부하 (로컬 / 실서버)** | 로컬 ONNX `/predict` p95 **0.73ms**, 실서버 HTTPS 60건·HTTP 160건 **오류율 0.00%** | [docs/benchmark_summary.json](docs/benchmark_summary.json), [docs/benchmark_cloud_summary.json](docs/benchmark_cloud_summary.json) |
| **클라우드 승격 · 롤백** | `model.yml` 2회 승격 · `rollback.yml` **16초 롤백** (약 1.5~4.2초 끊김 실측) | GitHub Actions 실행 이력 |

---

## 5. 로컬 개발 및 테스트 실행

```bash
git clone https://github.com/wpalswpa/lol-pace-mlops.git
cd lol-pace-mlops
uv sync --all-groups
uv run pytest -q
MODEL_IMPL=onnx uv run uvicorn api.app:app --port 8083
```

### 저장소 구조 & 읽는 순서

- **`api/`**: FastAPI 서버 (`app.py`, `contract.py`, `db.py`, `build.py`, `service.py`, `static/{index,overlay,download,slides}.html`)
- **`client/`**: 로컬 게임 브리지 (`bridge.py`), macOS 플로팅 패널 (`overlay_popup.swift`), OpenAPI 에이전트 데모 (`agent_demo.py`)
- **`training/`**: 데이터 로더, 규칙/sklearn/PyTorch 학습, 성능 게이트, ONNX 변환 및 Parity 검증
- **`deploy/lightsail/`**: `compose.yml` (`api` + `postgres:16-alpine` + `caddy`), `server.sh`, `Caddyfile`
- **상세 계약 정본**: [AGENTS.md](AGENTS.md) · **세션 상태 기록**: [인수인계.md](인수인계.md)
