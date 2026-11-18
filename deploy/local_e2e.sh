#!/bin/bash
# 로컬 E2E: 학습 → 게이트 → ONNX → parity → 승격 → 실패 자동 복구 → 되돌리기 (AGENTS.md 10절)
# 실제 AWS 대신  버킷 = $E2E_DIR/bucket/models/onnx/,  서버 = $E2E_DIR/srv (LOL_HOME),  GHCR = 로컬 이미지.
# server.sh · compose.yml · Caddyfile 은 배포용 파일을 그대로 쓴다.
#   E2E_DIR=<임시 폴더> bash deploy/local_e2e.sh        (KEEP=1 이면 끝나도 컨테이너를 남긴다)
set -euo pipefail
cd "$(dirname "$0")/.."
WORK=${E2E_DIR:?E2E_DIR 에 임시 작업 폴더를 준다}
DATE=$(date -u +%Y%m%d)
V1="m$DATE-1"; V2="m$DATE-2"; VBAD="m$DATE-3"
APP=v1.0; IMAGE=ghcr.io/local/lol-mlops
HTTP_PORT=${E2E_HTTP_PORT:-18080}; API_PORT=${E2E_API_PORT:-18081}
PY=.venv/bin/python
export LOL_HOME="$WORK/srv"
BUCKET="$WORK/bucket/models/onnx"

step() { printf '\n== %s ==\n' "$*"; }
health() { curl -fsS "http://127.0.0.1:$API_PORT/health"; echo; }

parity() {
    MODEL_IMPL=sklearn $PY -m uvicorn api.app:app --port 18090 >/dev/null 2>&1 & local p1=$!
    MODEL_IMPL=onnx $PY -m uvicorn api.app:app --port 18093 >/dev/null 2>&1 & local p2=$!
    for _ in $(seq 1 60); do
        sleep 0.5
        curl -sf -o /dev/null localhost:18090/health && curl -sf -o /dev/null localhost:18093/health && break
    done
    local rc=0
    PY_URL=http://127.0.0.1:18090 TARGETS=onnx=http://127.0.0.1:18093 $PY -m training.check_parity || rc=$?
    kill "$p1" "$p2"; wait "$p1" "$p2" 2>/dev/null || true
    return $rc
}

train_round() { # 판 건수 시드: 학습 → 게이트 → ONNX → 카드 → parity → 버킷에 올리기
    local ver=$1
    export DATA_N=$2 DATA_SEED=$3
    $PY -m training.train_sklearn >/dev/null
    $PY -m training.validate_model sklearn
    $PY -m training.train_dl >/dev/null
    $PY -m training.validate_model dl
    $PY -m training.export_onnx
    MODEL_VERSION=$ver $PY -m training.model_card >/dev/null
    parity
    [ ! -e "$BUCKET/$ver" ] || { echo "$ver 은 이미 버킷에 있다" >&2; return 1; }
    mkdir -p "$BUCKET/$ver"
    cp artifacts/model_sklearn.onnx artifacts/model.json artifacts/baseline.json "$BUCKET/$ver/"
    echo "버킷에 올림: $ver ($(ls "$BUCKET/$ver" | tr '\n' ' '))"
}

promote() { # 버킷 → 서버 → server.sh model → (성공하면) current.json
    local ver=$1
    mkdir -p "$LOL_HOME/models/$ver"
    cp "$BUCKET/$ver/"* "$LOL_HOME/models/$ver/"
    bash "$LOL_HOME/server.sh" model "$ver"
    printf '{"model_version": "%s", "set_at": "%s", "run": "local"}\n' "$ver" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        > "$BUCKET/current.json"
    echo "current.json → $(cat "$BUCKET/current.json")"
}

rm -rf "$WORK/srv" "$WORK/bucket"
mkdir -p "$LOL_HOME/models" "$BUCKET"

step "0. 계약 테스트"
$PY -m pytest -q

step "1. 첫 패치 데이터 학습 (DATA_N=500, DATA_SEED=0) → $V1"
train_round "$V1" 500 0

step "2. 코드 판 $APP 이미지 빌드 (ONNX Runtime 만, 구운 모델 포함)"
docker build -q -f api/Dockerfile --build-arg "APP_VERSION=$APP" -t "$IMAGE/lol-api:$APP" .
docker image ls "$IMAGE/lol-api:$APP" --format '{{.Repository}}:{{.Tag}} {{.Size}}'

step "3. 서버 준비 + server.sh image $APP"
cp deploy/lightsail/compose.yml deploy/lightsail/server.sh deploy/lightsail/Caddyfile "$LOL_HOME/"
printf 'API_PORT=%s\nHTTP_PORT=%s\nHTTPS_PORT=%s\n' "$API_PORT" "$HTTP_PORT" "${E2E_HTTPS_PORT:-18443}" > "$LOL_HOME/.env"
echo "" | bash "$LOL_HOME/server.sh" image "$APP" "$IMAGE"

step "4. 승격 $V1"
promote "$V1"

step "5. 새 패치 데이터 재학습 (DATA_N=5000, DATA_SEED=1) → $V2, 이미지는 그대로"
train_round "$V2" 5000 1
promote "$V2"

step "6. Caddy 를 거쳐 /predict · /coach 호출 (HTTP $HTTP_PORT)"
BODY='{"blue":{"kills":5,"assists":6,"cs":250,"avg_level":6.8,"dragons":0,"heralds":0,"towers":0},"red":{"kills":7,"assists":9,"cs":268,"avg_level":7.0,"dragons":1,"heralds":0,"towers":0},"first_blood":"red","my_side":"blue"}'
curl -fsS -X POST "http://127.0.0.1:$HTTP_PORT/predict" -H 'Content-Type: application/json' -d "$BODY" \
    | $PY -c 'import sys,json; r=json.load(sys.stdin); print("predict:", r["message"], "|", r["model_version"])'
curl -fsS -X POST "http://127.0.0.1:$HTTP_PORT/coach" -H 'Content-Type: application/json' -d "$BODY" \
    | $PY -c 'import sys,json; r=json.load(sys.stdin); print("coach:", r["message"]); print("toast:", r["toast"]); [print("  ", c["message"]) for c in r["coaching"]]'

step "7. 깨진 판 $VBAD 승격 시도 → /health 실패 → 이전 판 $V2 로 자동 복구"
mkdir -p "$LOL_HOME/models/$VBAD"
cp "$BUCKET/$V2/"* "$LOL_HOME/models/$VBAD/"
printf 'not an onnx file' > "$LOL_HOME/models/$VBAD/model_sklearn.onnx"
$PY -c "import json,sys; p=sys.argv[1]; d=json.load(open(p)); d['model_version']=sys.argv[2]; json.dump(d,open(p,'w'))" \
    "$LOL_HOME/models/$VBAD/model.json" "$VBAD"
if HEALTH_TRIES=15 bash "$LOL_HOME/server.sh" model "$VBAD"; then
    echo "깨진 판이 승격됐다 (기대와 다름)" >&2; exit 1
fi
echo "server.sh exit 1 (기대대로). .env 의 모델 줄:"; grep '^MODEL_' "$LOL_HOME/.env"
health

step "8. 되돌리기 → $V1 (재학습·이미지 빌드 없이)"
START=$(date +%s)
promote "$V1"
echo "되돌리기 걸린 시간(로컬): $(( $(date +%s) - START ))초"
health

step "9. 상태"
bash "$LOL_HOME/server.sh" status

if [ "${KEEP:-0}" != 1 ]; then
    step "정리"
    (cd "$LOL_HOME" && docker compose down -v >/dev/null 2>&1) && echo "컨테이너 정리 완료"
fi
