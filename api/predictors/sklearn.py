"""scikit-learn 원본 모델 (StandardScaler + LogisticRegression 파이프라인).

서빙 이미지에는 들어가지 않는다. 학습 직후 확인과 ONNX 비교(check_parity)의 기준 서버로만 쓴다.
"""
import os

import joblib
import numpy as np


def load(model_dir: str):
    # joblib 파일은 열 때 코드가 실행될 수 있다. 우리 학습이 만든 MODEL_DIR 의 파일만 연다
    # (요청이나 외부 경로에서 받은 파일은 열지 않는다, AGENTS.md 1절). 서빙 이미지는 이 모듈을 쓰지 않는다.
    pipe = joblib.load(os.path.join(model_dir, "sklearn.joblib"))

    def predict_proba(X: np.ndarray) -> np.ndarray:
        return pipe.predict_proba(np.asarray(X, dtype=np.float32))[:, 1]

    return predict_proba
