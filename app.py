"""
app.py — LNG-OPT 글래스모피즘 대시보드 메인 진입점
"""
from __future__ import annotations

import sys
from pathlib import Path

# ── 경로 설정 ──────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent          # 과제_최종/
_MODULES = _ROOT / "modules"
_COMPONENTS = _ROOT / "components"
for p in [str(_ROOT), str(_MODULES), str(_COMPONENTS)]:
    if p not in sys.path:
        sys.path.insert(0, p)

# ── Streamlit 임포트 (set_page_config 최우선) ────────────────
import streamlit as st

st.set_page_config(
    page_title="LNG-OPT",
    page_icon="⚡",
    layout="wide",
)

# ── 나머지 임포트 ─────────────────────────────────────────────
import numpy as np
import pandas as pd

from config import (
    DEFAULT_LNG_HEAT,
    DEFAULT_LNG_PRICE,
    FALLBACK_EXCHANGE_RATE,
)
from economics_engine import build_hourly_table, get_elec_price
from anomaly_detector import calc_smp_thresholds
from ml_predictor import load_data, load_models, predict_day
from smp_collector import list_cached_dates

from components.sidebar import render_sidebar
from components.right_panel import render_right_panel
from components.utils import (
    get_default_date,
    get_display_dates,
    is_holiday,
    load_smp_for_date,
    prev_workday,
    weekday_kr,
)
import components.pages.dashboard as pg_dashboard
import components.pages.wallet as pg_wallet
import components.pages.transaction as pg_transaction
import components.pages.anomaly as pg_anomaly
import components.pages.ml_model as pg_ml_model
import components.pages.rawdata as pg_rawdata


# ── CSS 주입 ──────────────────────────────────────────────────
_CSS_PATH = _ROOT / "styles" / "main.css"
if _CSS_PATH.exists():
    with open(_CSS_PATH, encoding="utf-8") as _f:
        st.markdown(f"<style>{_f.read()}</style>", unsafe_allow_html=True)

# 배경 블러 원 장식
st.markdown(
    '<div class="bg-blob-1"></div><div class="bg-blob-2"></div>',
    unsafe_allow_html=True,
)

# ── 자동 새로고침 ─────────────────────────────────────────────
try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=60_000, limit=None, key="data_refresh")
except ImportError:
    pass

# ── ML 모델 캐시 로드 ─────────────────────────────────────────
@st.cache_resource(show_spinner="ML 모델 로딩 중...")
def _load_models_cached():
    df = load_data()
    models, metrics = load_models(df)
    return models, metrics, df


try:
    models, metrics, raw_df = _load_models_cached()
    data_loaded = True
except Exception as _e:
    st.error(f"데이터/모델 로딩 실패: {_e}")
    data_loaded = False
    models, metrics, raw_df = {}, {}, pd.DataFrame()

# ── 열량/환율 자동 산출 ────────────────────────────────────────
if data_loaded and not raw_df.empty:
    lng_heat = round(float(raw_df["lng_heat"].mean()), 4) if "lng_heat" in raw_df.columns else DEFAULT_LNG_HEAT

    if "exchange_rate" in raw_df.columns and "datetime" in raw_df.columns:
        _tmp = raw_df.copy()
        _tmp["_date"] = _tmp["datetime"].dt.date
        _last_date = _tmp["_date"].max()
        _prev_rows = _tmp[_tmp["_date"] < _last_date]
        if not _prev_rows.empty:
            _prev_date = _prev_rows["_date"].max()
            exchange_rate = round(
                float(_prev_rows[_prev_rows["_date"] == _prev_date]["exchange_rate"].mean()), 2
            )
        else:
            exchange_rate = round(float(raw_df["exchange_rate"].mean()), 2)
    else:
        exchange_rate = float(FALLBACK_EXCHANGE_RATE)
else:
    lng_heat = DEFAULT_LNG_HEAT
    exchange_rate = float(FALLBACK_EXCHANGE_RATE)

# ── 3컬럼 레이아웃 ───────────────────────────────────────────
col_left, col_center, col_right = st.columns([2, 5.5, 2.5])

# ══════════════════════════════════════════════════════════════
# LEFT — 네비게이션 사이드바
# ══════════════════════════════════════════════════════════════
with col_left:
    render_sidebar()

# ══════════════════════════════════════════════════════════════
# RIGHT — 설정 패널 (center 이전에 계산해야 target_date 등 얻음)
# ══════════════════════════════════════════════════════════════
_cached_dates = list_cached_dates()

# 최초 1회만 기본 날짜 계산 (이후엔 세션 상태 유지)
if "user_target_date" not in st.session_state:
    st.session_state.user_target_date = get_default_date(_ROOT)
if "user_lng_price" not in st.session_state:
    st.session_state.user_lng_price = float(DEFAULT_LNG_PRICE)

with col_right:
    _panel_out = render_right_panel({
        "default_date": st.session_state.user_target_date,
        "default_lng_price": st.session_state.user_lng_price,
        "lng_heat": lng_heat,
        "exchange_rate": exchange_rate,
        "smp_status": [],
    })

target_date = _panel_out["target_date"]
lng_price   = _panel_out["lng_price"]
is_spot     = _panel_out["is_spot"]

# 주말/공휴일 보정
if is_holiday(target_date):
    _corrected = prev_workday(target_date)
    st.sidebar.warning(
        f"{target_date.month}/{target_date.day}({weekday_kr(target_date)})은 "
        f"휴일입니다. 직전 영업일 "
        f"{_corrected.month}/{_corrected.day}({weekday_kr(_corrected)})로 표시합니다."
    )
    target_date = _corrected

display_dates = get_display_dates(target_date)

# ── SMP 로드 ──────────────────────────────────────────────────
all_smp: dict = {}
for _d in display_dates:
    all_smp[_d] = load_smp_for_date(_d, raw_df if data_loaded else None)

smp_series, smp_source, has_real_smp = all_smp[target_date]

# ── 경제성/임계값 계산 ─────────────────────────────────────────
hourly_df = None
thresholds = None
pred_results = None

if data_loaded and has_real_smp:
    pred_results = predict_day(
        models, target_date, smp_series,
        lng_price, lng_heat, exchange_rate,
        elec_price_fn=get_elec_price,
    )
    thresholds = calc_smp_thresholds(lng_price, lng_heat, exchange_rate, is_spot=is_spot)
    hourly_df = build_hourly_table(
        target_date=target_date,
        smp_series=smp_series,
        lng_price=lng_price,
        lng_heat=lng_heat,
        exchange_rate=exchange_rate,
        pred_results=pred_results,
        is_spot=is_spot,
        smp_high_threshold=thresholds["smp_high"],
    )

# ── 공유 컨텍스트 딕셔너리 ─────────────────────────────────────
_ctx = {
    # 데이터
    "data_loaded": data_loaded,
    "has_real_smp": has_real_smp,
    "raw_df": raw_df,
    "models": models,
    "metrics": metrics,
    # 날짜
    "target_date": target_date,
    "display_dates": display_dates,
    # SMP
    "smp_series": smp_series,
    "smp_source": smp_source,
    "all_smp": all_smp,
    # 설정값
    "lng_price": lng_price,
    "lng_heat": lng_heat,
    "exchange_rate": exchange_rate,
    "is_spot": is_spot,
    # 계산 결과
    "hourly_df": hourly_df,
    "thresholds": thresholds,
    "pred_results": pred_results,
}

# ── SMP 수집 현황 캡션 (right panel 아래쪽에 추가 표시) ─────
with col_right:
    _weekday_kr_map = ["월","화","수","목","금","토","일"]
    _display_str = ", ".join(
        f"{d.month}/{d.day}({_weekday_kr_map[d.weekday()]})" for d in display_dates
    )
    st.caption(f"대상 날짜: **{_display_str}**")
    if _cached_dates:
        st.caption(f"SMP 수집 완료: {_cached_dates[-1]} 까지 ({len(_cached_dates)}일)")
    for _d in display_dates:
        _s, _src, _ok = all_smp[_d]
        _icon = "🟢" if _ok else "🔴"
        st.caption(f"{_icon} {_d.month}/{_d.day}: {_src}")

# ══════════════════════════════════════════════════════════════
# CENTER — 메인 콘텐츠 (active_page 에 따라 라우팅)
# ══════════════════════════════════════════════════════════════
active_page = st.session_state.get("active_page", "Dashboard")

with col_center:
    if active_page == "Dashboard":
        tab_dash, tab_guide = st.tabs(["📊 종합장표", "📋 가동 가이던스"])
        with tab_dash:
            pg_dashboard.render(_ctx)
        with tab_guide:
            pg_transaction.render(_ctx)
    elif active_page == "경제성 분석":
        pg_wallet.render(_ctx)
    elif active_page == "이상구간 탐지":
        pg_anomaly.render(_ctx)
    elif active_page == "ML 모델":
        pg_ml_model.render(_ctx)
    elif active_page == "원시 데이터":
        pg_rawdata.render(_ctx)

# ── 푸터 ──────────────────────────────────────────────────────
st.markdown(
    """
    <div style="text-align:center; padding:16px 0 8px 0;
                font-size:11px; color:#9CA3AF; font-family:'DM Sans',sans-serif;">
      LNG-OPT v2.0 &nbsp;|&nbsp; Glassmorphism Dashboard &nbsp;|&nbsp;
      XGBoost 기반 ML 예측 + 동적 임계값 이상탐지
    </div>
    """,
    unsafe_allow_html=True,
)
