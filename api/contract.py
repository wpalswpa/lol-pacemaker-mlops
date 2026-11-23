"""입력·출력 계약과 피처 변환. 정본은 AGENTS.md 3·4절이다.

입력은 10분 시점 점수판(Tab)과 이벤트에 공개되는 값만 받는다.
학습(training/data.py)과 서빙이 같은 FEATURES 순서와 같은 변환을 쓰도록 이 파일 한 곳에 둔다.
"""
from typing import Annotated, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator

from api.build import resolve_my, resolve_threat

Side = Literal["blue", "red"]

# 팀 지표 7개 (순서가 모델 입력 열 순서다)
TEAM_STATS = ["kills", "assists", "cs", "avg_level", "dragons", "heralds", "towers"]
FEATURES = ["first_blood", *TEAM_STATS]

NAMES = {
    "first_blood": "첫 킬",
    "kills": "킬",
    "assists": "어시스트",
    "cs": "CS",
    "avg_level": "평균 레벨",
    "dragons": "드래곤",
    "heralds": "전령",
    "towers": "포탑",
}

# 게임 규칙상 한 팀이 10분에 가질 수 없는 값은 422 로 거절한다 (AGENTS.md 3절 표)
LIMITS = {
    "kills": (0, 100),
    "assists": (0, 300),
    "cs": (0, 2000),
    "avg_level": (1, 18),
    "dragons": (0, 7),
    "heralds": (0, 2),
    "towers": (0, 11),
}


def _range(stat: str):
    """LIMITS 의 범위를 검증 규칙으로. strict: "3", true, 정수 칸의 3.5 를 바꿔 받지 않는다. NaN·Inf 거절."""
    lo, hi = LIMITS[stat]
    return Field(strict=True, ge=lo, le=hi, allow_inf_nan=False)


class TeamStats(BaseModel):
    """한 팀의 10분 시점 점수판 값."""

    model_config = ConfigDict(extra="forbid")

    kills: Annotated[int, _range("kills")]
    assists: Annotated[int, _range("assists")]
    cs: Annotated[int, _range("cs")]
    avg_level: Annotated[float, _range("avg_level")]
    dragons: Annotated[int, _range("dragons")]
    heralds: Annotated[int, _range("heralds")]
    towers: Annotated[int, _range("towers")]


class GameState(BaseModel):
    """/predict 와 /coach 의 요청 본문."""

    model_config = ConfigDict(extra="forbid")

    blue: TeamStats
    red: TeamStats
    first_blood: Side
    my_side: Side
    # 선택 칸: 승률 계산에 쓰지 않고 /coach 의 build_advice 에만 쓴다
    my_champion: Annotated[str | None, Field(max_length=40, description="내 챔피언(영문 ID·한국어 이름) 또는 역할군 AD·AP·TANK·SUPPORT")] = None
    enemy_top_threat: Annotated[str | None, Field(max_length=40, description="상대 최대 위협 챔피언 또는 유형 AD·AP·HEAL·TANK")] = None
    enemy_items: list[Annotated[int, Field(strict=True, ge=1, le=999999)]] = Field(
        default_factory=list, max_length=12, description="상대 최대 위협 챔피언이 보유한 공개 아이템 ID 목록(Tab 점수판)"
    )

    @field_validator("my_champion")
    @classmethod
    def _known_my(cls, v: str | None) -> str | None:
        if v is not None:
            resolve_my(v)  # 모르는 이름이면 ValueError → 422
        return v

    @field_validator("enemy_top_threat")
    @classmethod
    def _known_threat(cls, v: str | None) -> str | None:
        if v is not None:
            resolve_threat(v)
        return v


class MatchIn(GameState):
    """POST /matches 본문 (AGENTS.md 3-5절·4절). 3절 입력 + 게임 시각 + 실제 승패. 그 밖의 칸(소환사명 등)은 422."""

    game_time_sec: Annotated[int, Field(strict=True, ge=0, le=7200, description="저장 시점 게임 시각(초)")] = 600
    win: Annotated[bool | None, Field(strict=True, description="내 팀이 이겼는지. 모르면 null")] = None


def to_features(state: GameState) -> np.ndarray:
    """요청을 모델 입력 한 줄(float32 [8])로 바꾼다. first_blood 는 블루면 1, 나머지는 블루 − 레드."""
    row = [1.0 if state.first_blood == "blue" else 0.0]
    row += [float(getattr(state.blue, s)) - float(getattr(state.red, s)) for s in TEAM_STATS]
    return np.asarray(row, dtype=np.float32)


# ── 응답 (OpenAPI 스키마가 AI 에이전트의 도구 정의로 그대로 쓰인다) ──


class Factor(BaseModel):
    feature: str
    name: str
    my_value: float
    enemy_value: float
    diff: float
    tier_avg_diff: float
    impact_pp: float


class TierGap(BaseModel):
    stat: str
    name: str
    mine: float
    tier_avg: float
    gap: float


class PredictResponse(BaseModel):
    win_prob_blue: float = Field(description="블루 승리 확률 0~1 (반올림 안 함)")
    my_side: Side
    my_win_prob: float = Field(description="요청한 팀의 승리 확률 0~1")
    pace: str = Field(description="열세 페이스 / 접전 / 우세 페이스")
    top_factors: list[Factor] = Field(description="승률에 가장 크게 작용하는 지표 3개. impact_pp 가 음수면 승률을 깎는 요인")
    vs_tier: list[TierGap] = Field(description="내 팀 지표와 티어 10분 평균의 차이")
    tier: str = Field(description="기준선이 된 데이터")
    tier_percentile: dict[str, float] = Field(
        description="같은 티어 경기 분포에서 내 위치. overall_top_pct(종합 페이스)와 <지표>_top_pct(내 팀 격차). "
        "0~100, 작을수록 앞선다")
    message: str = Field(description="1초 안에 읽히는 한 줄 요약")
    warnings: list[str]
    model: str
    model_version: str = Field(description="답을 낸 모델 판. 답할 때 함께 남긴다")


class Coaching(BaseModel):
    stat: str
    name: str
    add: float
    target_value: float
    with_: dict[str, float] = Field(alias="with", serialization_alias="with",
                                    description="함께 움직인 지표와 양 (내 팀 기준)")
    win_prob_after: float
    gain_pp: float
    top_pct_before: float = Field(description="그 지표 격차의 티어 내 위치(목표 전)")
    top_pct_after: float = Field(description="그 지표 격차의 티어 내 위치(목표 후)")
    overall_top_pct_after: float = Field(description="목표를 채웠을 때 종합 페이스의 위치")
    message: str

    model_config = ConfigDict(populate_by_name=True)


class BuildItem(BaseModel):
    step: int
    slot: str
    item_id: int
    name: str
    gold: int
    why: str


class SkillOrder(BaseModel):
    order: str | None = Field(description="예 'Q > W > E'. 규칙표에 없는 챔피언이면 null")
    focus: str
    source: str


class ExtraItem(BaseModel):
    item_id: int
    name: str
    gold: int
    why: str


class BuildAdvice(BaseModel):
    state: Literal["ahead", "even", "behind"]
    my_champion: str | None
    my_class: str
    my_damage: str
    enemy_top_threat: str | None
    threat_type: str
    posture: str
    tactical_tip: str | None = None
    recommended_items: list[BuildItem] = Field(description="다음 구매 3칸. 우세·접전: 코어 → 신발 → 다음 코어(또는 대응), 열세: 신발 → 하위템 → 코어")
    starter_items: list[ExtraItem] = Field(description="게임 시작 때 살 아이템 (챔피언 빌드표, 포지션 시작템 또는 규칙표 starter)")
    situational_items: list[ExtraItem] = Field(description="상황이 바뀌면 바꿔 살 아이템, 최대 3개")
    skill_order: SkillOrder
    build_position: str | None = Field(default=None, description="챔피언 빌드표를 쓴 포지션(TOP·JUNGLE·MIDDLE·BOTTOM·UTILITY). 표에 없으면 null")
    basis: str = Field(description="근거 출처. 사람이 정한 규칙표이며 모델 계산이 아니다")


class CoachResponse(PredictResponse):
    horizon_min: float = Field(description="목표가 가정한 시간(분)")
    coaching: list[Coaching] = Field(description="승률을 올리는 목표, gain_pp 내림차순")
    build_advice: BuildAdvice = Field(description="상황별 아이템·스킬 가이드 (규칙표)")
    toast: list[str] = Field(description="알림 카드용 2~3줄 (승률·상위 %·핵심 목표 1개·빌드 한 줄)")
    how_to_read: str


class Health(BaseModel):
    status: str
    model: str
    app_version: str
    model_version: str
