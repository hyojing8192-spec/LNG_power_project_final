"""
config.py
=========
프로젝트 전역 설정 상수.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

# ── 경로 ──────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent   # 과제_최종/
DATA_PATH    = str(PROJECT_ROOT / "data" / "데이터.csv")
MODEL_DIR    = str(PROJECT_ROOT / "data" / "models")

# ── 운전 모드 ─────────────────────────────────────────────────────
MODES = ["1gi", "low2gi", "2gi"]

MODE_LABELS = {
    "1gi":    "1기",
    "low2gi": "2기저부하",
    "2gi":    "2기",
    "off":    "정지",
}

MODE_THRESHOLDS = {
    "off_max":    50_000,
    "1gi_max":    370_000,
    "low2gi_max": 460_000,
}

# ── 전기요금 (원/kWh) ─────────────────────────────────────────────
# 한전 수전단가.xlsx 기준: 실제 수전단가 = (기본요금 + 5 + 9) × 1.027
# 기본요금: 여름 125.1/172.9/236.9, 봄가을 125.1/142.9/160.1, 겨울 132/172.5/212.2
ELEC_RATES = {
    "summer": {
        "경부하":   142.86,   # (125.1+14)×1.027 = 142.8557
        "중간부하": 191.95,   # (172.9+14)×1.027 = 191.9463
        "최대부하": 257.67,   # (236.9+14)×1.027 = 257.6743
    },
    "spring_fall": {
        "경부하":   142.86,   # (125.1+14)×1.027 = 142.8557
        "중간부하": 161.14,   # (142.9+14)×1.027 = 161.1363
        "최대부하": 178.80,   # (160.1+14)×1.027 = 178.8007
    },
    "winter": {
        "경부하":   149.94,   # (132+14)×1.027 = 149.942
        "중간부하": 191.54,   # (172.5+14)×1.027 = 191.5355
        "최대부하": 232.31,   # (212.2+14)×1.027 = 232.3074
    },
}

# 봄가을 주말 특례할인: 11시~14시(11,12,13시) 50% 할인
SPRING_FALL_DISCOUNT_HOURS = {11, 12, 13}

# 법정 공휴일 (연도별 갱신 필요)
LEGAL_HOLIDAYS = set()

# ── 경제성 계산 ───────────────────────────────────────────────────
OVERHEAD_COST = 0.8           # Spot LNG 제세금 ($/MMBtu)
HOURS_PER_MONTH = 730         # 월 운전시간 (h)
LOW2GI_EFF_FALLBACK = 1.65   # 저부하 효율 폴백값 (Mcal/kWh)

# ── SMP 이상 탐지 ─────────────────────────────────────────────────
SMP_ZERO_THRESHOLD   = 0.0
SMP_HIGH_THRESHOLD   = 170.0
ECON_CHANGE_THRESHOLD = 20.0  # 원/kWh
COLOR_ANOMALY = "#FF4444"

# ── ML 설정 ───────────────────────────────────────────────────────
MODEL_FEATURES = [
    "hour", "weekday", "month",
    "smp", "lng_price", "lng_heat",
    "elec_price", "exchange_rate",
    "lng_gen", "net_load",
    "hour_sin", "hour_cos",
    "month_sin", "month_cos",
]

XGBOOST_PARAMS = {
    "n_estimators":  300,
    "max_depth":     6,
    "learning_rate": 0.05,
    "subsample":     0.8,
    "colsample_bytree": 0.8,
    "random_state":  42,
    "n_jobs":        -1,
}

CV_FOLDS = 5
MIN_R2   = 0.5
ML_TEST_FRACTION = 0.2

# ── 기본값 ────────────────────────────────────────────────────────
DEFAULT_LNG_PRICE = 11.0       # $/MMBtu
DEFAULT_LNG_HEAT  = 9.107      # Mcal/Nm³
FALLBACK_EXCHANGE_RATE = 1350  # 원/$
BOK_API_KEY = ""               # 한국은행 API 키 (없으면 폴백 환율 사용)
DATA_GO_KR_API_KEY = ""        # 공공데이터포털 API 키 (data.go.kr 가입 후 발급)
