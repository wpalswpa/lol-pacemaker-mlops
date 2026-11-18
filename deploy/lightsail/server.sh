#!/bin/bash
# 서버에서 도는 배포 스크립트 (AGENTS.md 10절). 서버가 바라는 상태는 $LOL_HOME/.env 에 적는다.
#   server.sh image <태그> <이미지 경로>   stdin 으로 GHCR 토큰 한 줄. 빈 줄이면 로그인 없이 (로컬에 있거나 공개 이미지)
#   server.sh model <모델 판>              $LOL_HOME/models/<판>/ 의 모델로 바꾼다 (승격·되돌리기 공통)
#   server.sh status
# 실패하면 이전 판으로 스스로 되돌리고 exit 1 (워크플로가 거기서 멈춘다)
set -euo pipefail
export LOL_HOME=${LOL_HOME:-/srv/lol}
cd "$LOL_HOME"
touch .env

getenv() { { grep "^$1=" .env || true; } | tail -n 1 | cut -d= -f2-; }
setenv() { { grep -v "^$1=" .env || true; } > .env.new; echo "$1=$2" >> .env.new; mv .env.new .env; }
delenv() { { grep -v "^$1=" .env || true; } > .env.new; mv .env.new .env; }

API_PORT=$(getenv API_PORT)
HEALTH_URL=${HEALTH_URL:-http://127.0.0.1:${API_PORT:-8080}/health}
HEALTH_TRIES=${HEALTH_TRIES:-30}
HEALTH_INTERVAL=${HEALTH_INTERVAL:-1}

# health [판] : /health 가 응답할 때까지 기다린다. 판을 주면 model_version 이 그 판일 때만 성공
health() {
    local h
    for _ in $(seq 1 "$HEALTH_TRIES"); do
        if h=$(curl -fsS "$HEALTH_URL" 2>/dev/null); then
            if [ -z "${1:-}" ] || echo "$h" | grep -q "\"model_version\":\"$1\""; then
                echo "$h"; return 0
            fi
        fi
        sleep "$HEALTH_INTERVAL"
    done
    echo "health 실패${1:+ (기대한 판 $1)}" >&2
    return 1
}

case "${1:-}" in
    image) # 코드 배포: 새 이미지 → .env 의 TAG 바꿔 띄우기 → health 실패면 이전 TAG 로
        TAG=${2:?태그}; IMAGE=${3:?이미지 경로}
        [[ $TAG =~ ^v[0-9]+(\.[0-9]+)*$ ]] || { echo "태그 형식이 아니다: $TAG" >&2; exit 1; }
        [[ $IMAGE =~ ^[a-z0-9./_-]+$ ]] || { echo "이미지 경로 형식이 아니다: $IMAGE" >&2; exit 1; }
        TOKEN=""; read -r TOKEN || true   # 토큰은 stdin 으로만 받는다 (명령 인자·기록에 남지 않게)
        if [ -n "$TOKEN" ]; then
            DOCKER_CONFIG=$(mktemp -d); export DOCKER_CONFIG
            trap 'rm -rf "$DOCKER_CONFIG"' EXIT
            printf %s "$TOKEN" | docker login ghcr.io -u token --password-stdin >/dev/null
            docker pull -q "$IMAGE/lol-api:$TAG"
        elif ! docker image inspect "$IMAGE/lol-api:$TAG" >/dev/null 2>&1; then
            docker pull -q "$IMAGE/lol-api:$TAG"
        fi
        PREV=$(getenv TAG)
        setenv IMAGE "$IMAGE"; setenv TAG "$TAG"
        docker compose up -d
        if ! health; then
            if [ -n "$PREV" ]; then setenv TAG "$PREV"; docker compose up -d; fi
            exit 1
        fi
        ;;
    model) # 모델 배포·되돌리기: .env 의 MODEL_* 바꿔 띄우기 → 그 판이 안 뜨면 이전 판으로
        VER=${2:?모델 판}
        [[ $VER =~ ^m[0-9]{8}-[0-9]+$ ]] || { echo "모델 판 형식이 아니다: $VER" >&2; exit 1; }
        for f in model_sklearn.onnx model.json baseline.json; do
            [ -f "models/$VER/$f" ] || { echo "models/$VER/$f 가 없다" >&2; exit 1; }
        done
        PREV=$(getenv MODEL_VERSION)
        setenv MODEL_VERSION "$VER"; setenv MODEL_DIR "/models/$VER"
        docker compose up -d
        if ! health "$VER"; then
            # 되돌리는 곳은 새 판($VER)이 아니라 이전 판($PREV) 이다
            if [ -n "$PREV" ]; then setenv MODEL_VERSION "$PREV"; setenv MODEL_DIR "/models/$PREV"
            else delenv MODEL_VERSION; delenv MODEL_DIR; fi
            docker compose up -d
            health "$PREV" >&2 || true
            exit 1
        fi
        ;;
    status) # 지금 상태: .env, 컨테이너, /health
        cat .env
        docker compose ps --format '{{.Service}} {{.Image}} {{.Status}}'
        health
        ;;
    *) echo "사용: server.sh image|model|status" >&2; exit 2 ;;
esac
