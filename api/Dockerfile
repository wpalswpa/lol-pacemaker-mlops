# 서빙 이미지: ONNX Runtime 만 담는다 (scikit-learn·PyTorch 없음).
#   docker build -f api/Dockerfile --build-arg APP_VERSION=v1.0 -t lol-api:v1.0 .
# 빌드 전에 artifacts/ 에 model_sklearn.onnx·baseline.json 이 있어야 한다 (구운 모델 = model_version "baked")
FROM python:3.14-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-default-groups --no-cache
ENV PATH="/app/.venv/bin:$PATH"
COPY api/ api/
COPY client/bridge.py client/overlay_popup.swift client/
COPY artifacts/model_sklearn.onnx artifacts/baseline.json artifacts/
ARG MODEL_IMPL=onnx
ENV MODEL_IMPL=${MODEL_IMPL}
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}
EXPOSE 8080
USER nobody
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8080"]
