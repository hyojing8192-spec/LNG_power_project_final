"""
config.py
=========
프로젝트 전역 설정 상수.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

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
# 2026년 공휴일 (대체공휴일 포함)
LEGAL_HOLIDAYS = {
    # 2026년
    "2026-01-01",  # 신정
    "2026-01-28",  # 설날 연휴
    "2026-01-29",  # 설날
    "2026-01-30",  # 설날 연휴
    "2026-03-01",  # 삼일절
    "2026-05-05",  # 어린이날
    "2026-05-24",  # 부처님오신날
    "2026-06-06",  # 현충일
    "2026-08-15",  # 광복절
    "2026-08-17",  # 대체공휴일(광복절)
    "2026-09-24",  # 추석 연휴
    "2026-09-25",  # 추석
    "2026-09-26",  # 추석 연휴
    "2026-10-03",  # 개천절
    "2026-10-05",  # 대체공휴일(개천절)
    "2026-10-09",  # 한글날
    "2026-12-25",  # 성탄절
}
# date 객체 set으로 변환
from datetime import date as _date
LEGAL_HOLIDAYS = {_date.fromisoformat(d) for d in LEGAL_HOLIDAYS}

# ── 경제성 계산 ───────────────────────────────────────────────────
OVERHEAD_COST = 0.8           # Spot LNG 제세금 ($/MMBtu)
HOURS_PER_MONTH = 730         # 월 운전시간 (h)
LOW2GI_EFF_FALLBACK = 1.65   # 저부하 효율 폴백값 (Mcal/kWh)

# ── 모드별 고정 효율 (Mcal/kWh) ──────────────────────────────
# 가동패턴 유지 시 효율은 거의 상수 → ML 예측 대신 실측 중앙값 사용
MODE_EFFICIENCY = {
    "1gi":    1.592,    # 1기 실측 중앙값
    "low2gi": 1.679,    # 2기저부하 실측 중앙값
    "2gi":    1.574,    # 2기 실측 중앙값
}

# ── SMP 이상 탐지 ─────────────────────────────────────────────────
SMP_ZERO_THRESHOLD   = 0.0
SMP_HIGH_THRESHOLD   = 170.0
ECON_CHANGE_THRESHOLD = 20.0  # 원/kWh
COLOR_ANOMALY = "#FF4444"

# ── ML 설정 ───────────────────────────────────────────────────────
MODEL_FEATURES = [
    "weekday", "month",
    "smp", "lng_price", "lng_heat",
    "elec_price", "exchange_rate",
    "lng_gen", "net_load",
    "hour_sin", "hour_cos",
    "month_sin", "month_cos",
    "is_low_load_gen", "is_high_eff_season",
]

XGBOOST_PARAMS = {
    "n_estimators":  300,
    "max_depth":     5,
    "learning_rate": 0.05,
    "subsample":     0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 10,
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
BOK_API_KEY = os.getenv("BOK_API_KEY", "")
DATA_GO_KR_API_KEY = os.getenv("DATA_GO_KR_API_KEY", "")

# ── 메일 설정 (F6) ───────────────────────────────────────────
# .env 파일에 설정:
#   MAIL_SENDER_EMAIL=your.email@gmail.com
#   MAIL_SENDER_PASSWORD=abcdefghijklmnop
#   MAIL_RECIPIENTS=user1@company.com,user2@company.com
MAIL_SMTP_SERVER   = "smtp.gmail.com"
MAIL_SMTP_PORT     = 587
MAIL_SENDER_EMAIL    = os.getenv("MAIL_SENDER_EMAIL", "")
MAIL_SENDER_PASSWORD = os.getenv("MAIL_SENDER_PASSWORD", "")
MAIL_RECIPIENTS      = [
    r.strip() for r in os.getenv("MAIL_RECIPIENTS", "").split(",") if r.strip()
]

# ── 카카오톡 설정 (F6) ───────────────────────────────────────
# https://developers.kakao.com 에서 앱 생성 후 REST API 키 확인
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY", "")
KAKAO_CLIENT_SECRET = os.getenv("KAKAO_CLIENT_SECRET", "")
