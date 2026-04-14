"""
LNG발전 최적 가이던스 제공 프로그램 — Streamlit 대시보드
"""

from __future__ import annotations

import sys
from pathlib import Path
from datetime import date, datetime

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go

# 프로젝트 루트 및 모듈 경로 추가
_ROOT = Path(__file__).resolve().parent.parent          # 과제_최종/
_MODULES = _ROOT / "modules"
for p in [str(_ROOT), str(_MODULES)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from config import (
    MODES, MODE_LABELS, DEFAULT_LNG_PRICE, DEFAULT_LNG_HEAT,
    FALLBACK_EXCHANGE_RATE, SMP_HIGH_THRESHOLD,
)
from economics_engine import (
    get_elec_price, build_hourly_table,
)
from anomaly_detector import (
    calc_smp_thresholds, detect_smp_anomalies,
    build_smp_chart, build_econ_change_chart, detect_econ_change,
)
from ml_predictor import load_data, load_models, predict_day, retrain
from smp_collector import load_cached_smp, list_cached_dates
from guidance_generator import generate_full_guidance

# ──────────────────────────────────────────────────────────────
# 페이지 설정
# ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="LNG발전 최적 가이던스",
    page_icon="⚡",
    layout="wide",
)

st.title("⚡ LNG발전 최적 가이던스 제공 프로그램")
st.caption("SMP 기반 운전모드 최적화 · ML 예측 · 이상구간 탐지")

# 자동 새로고침 (5분 간격) — 스케줄러가 새 데이터 저장 시 반영
# 1분 자동 새로고침 — 스케줄러가 새 데이터 저장 시 빠르게 반영
try:
    from streamlit_autorefresh import st_autorefresh
    _refresh_count = st_autorefresh(interval=60_000, limit=None, key="data_refresh")
except ImportError:
    _refresh_count = 0

# ── 스케줄러 상태 API 서버 시작 (백그라운드) ─────────────────
try:
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "scheduler_status_server",
        str(Path(__file__).resolve().parent / "scheduler_status_server.py"),
    )
    _ssm = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_ssm)
    _ssm.start_in_background()
except Exception:
    pass

# ── 스케줄러 상태 표시 (우측 상단, CSS 깜빡임) ──────────────
import subprocess as _sp
import platform

def _check_scheduler_status():
    """running | fetching | stopped"""
    import platform
    if platform.system() != "Windows":
        return "stopped"  # Streamlit Cloud(Linux)에서는 로컬 스케줄러 감지 불가

    # wmic으로 스케줄러 프로세스 확인 (PowerShell보다 안정적)
    try:
        r = _sp.run(
            ["wmic", "process", "where", "name='python.exe'",
             "get", "CommandLine"],
            capture_output=True, text=True, timeout=5,
        )
        if "run_scheduler" not in r.stdout:
            return "stopped"
    except Exception:
        return "stopped"

    # KMOS(XPlatform) 실행 중이면 데이터 수집 중
    try:
        r = _sp.run(
            ["tasklist", "/FI", "IMAGENAME eq XPlatform.exe"],
            capture_output=True, text=True, timeout=5,
        )
        if "XPlatform.exe" in r.stdout:
            return "fetching"
    except Exception:
        pass
    return "running"

_sched_status = _check_scheduler_status()

# 로컬(Windows)에서만 스케줄러 인디케이터 표시, Cloud(Linux)에서는 숨김
if _sched_status != "stopped" or platform.system() == "Windows":
    _STATUS_CFG = {
        "running":  ("#28a745", "스케줄러 정상 가동중"),
        "fetching": ("#FF8C00", "SMP 데이터 수집중"),
        "stopped":  ("#dc3545", "스케줄러 미실행"),
    }
    _sc_color, _sc_label = _STATUS_CFG.get(_sched_status, _STATUS_CFG["stopped"])

    st.markdown(f"""
    <style>
    @keyframes sched-blink {{
        0%, 100% {{ opacity: 1; }}
        50% {{ opacity: 0.25; }}
    }}
    .sched-badge {{
        position: fixed;
        top: 14px;
        right: 20px;
        z-index: 999999;
        display: flex;
        align-items: center;
        gap: 8px;
        background: rgba(255,255,255,0.97);
        padding: 7px 16px;
        border-radius: 20px;
        box-shadow: 0 2px 10px rgba(0,0,0,0.13);
        font-size: 0.82em;
        font-weight: 700;
        color: {_sc_color};
        animation: sched-blink 1.4s ease-in-out infinite;
        pointer-events: none;
    }}
    .sched-badge-dot {{
        width: 11px;
        height: 11px;
        border-radius: 50%;
        background: {_sc_color};
        flex-shrink: 0;
    }}
    </style>
    <div class="sched-badge">
        <div class="sched-badge-dot"></div>
        {_sc_label}
    </div>
    """, unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────
# 데이터 로드 및 ML 모델 준비 (사이드바보다 먼저 — 열량·환율 추출용)
# ──────────────────────────────────────────────────────────────
@st.cache_resource(show_spinner="ML 모델 로딩 중...")
def _load_models_cached():
    df = load_data()
    models, metrics = load_models(df)
    return models, metrics, df


try:
    models, metrics, raw_df = _load_models_cached()
    data_loaded = True
except Exception as e:
    st.error(f"데이터/모델 로딩 실패: {e}")
    data_loaded = False
    models, metrics, raw_df = {}, {}, pd.DataFrame()

# 학습 데이터에서 LNG 열량 평균, 전일 환율 평균 자동 산출
if data_loaded and not raw_df.empty:
    lng_heat = round(float(raw_df["lng_heat"].mean()), 4) if "lng_heat" in raw_df.columns else DEFAULT_LNG_HEAT

    if "exchange_rate" in raw_df.columns and "datetime" in raw_df.columns:
        raw_df_tmp = raw_df.copy()
        raw_df_tmp["_date"] = raw_df_tmp["datetime"].dt.date
        last_date = raw_df_tmp["_date"].max()
        prev_dates = raw_df_tmp[raw_df_tmp["_date"] < last_date]
        if not prev_dates.empty:
            prev_date = prev_dates["_date"].max()
            exchange_rate = round(float(prev_dates[prev_dates["_date"] == prev_date]["exchange_rate"].mean()), 2)
        else:
            exchange_rate = round(float(raw_df["exchange_rate"].mean()), 2)
    else:
        exchange_rate = float(FALLBACK_EXCHANGE_RATE)
else:
    lng_heat = DEFAULT_LNG_HEAT
    exchange_rate = float(FALLBACK_EXCHANGE_RATE)

# ──────────────────────────────────────────────────────────────
# 사이드바 — 입력 변수
# ──────────────────────────────────────────────────────────────
st.sidebar.header("📋 설정")

# 달력 날짜 선택 + 주말/공휴일 자동 보정
from config import LEGAL_HOLIDAYS
from datetime import timedelta as _timedelta

_weekday_kr_map = ["월","화","수","목","금","토","일"]

def _is_holiday_check(d):
    """주말 또는 공휴일 여부."""
    if d in LEGAL_HOLIDAYS:
        return True
    return d.weekday() >= 5

def _prev_workday(d):
    """직전 영업일."""
    d = d - _timedelta(days=1)
    while _is_holiday_check(d):
        d = d - _timedelta(days=1)
    return d

_today = date.today()
_default_date = _today if not _is_holiday_check(_today) else _prev_workday(_today)

# 최신 경제성분석 CSV 날짜 확인 → 자동으로 해당 날짜 표시
import glob as _glob
_latest_csv_date = _default_date
_csv_files = sorted(_glob.glob(str(_ROOT / "data" / "경제성분석_*.csv")))
if _csv_files:
    try:
        _latest_name = Path(_csv_files[-1]).stem  # 경제성분석_2026-04-10
        _latest_csv_date_str = _latest_name.replace("경제성분석_", "")
        _latest_csv_date_parsed = date.fromisoformat(_latest_csv_date_str)
        # 미래 날짜 CSV(금요일에 미리 생성한 주말/월요일분)는 무시 → 오늘 기준 유지
        if _latest_csv_date_parsed <= _today:
            if not _is_holiday_check(_latest_csv_date_parsed):
                _default_date = _latest_csv_date_parsed
            else:
                _default_date = _prev_workday(_latest_csv_date_parsed)
    except Exception:
        pass

_picked_date = st.sidebar.date_input("분석 기준일", value=_default_date)

# 주말/공휴일 선택 시 직전 영업일로 자동 보정
if _is_holiday_check(_picked_date):
    _corrected = _prev_workday(_picked_date)
    st.sidebar.warning(
        f"{_picked_date.month}/{_picked_date.day}({_weekday_kr_map[_picked_date.weekday()]})은 "
        f"휴일입니다. 직전 영업일 "
        f"{_corrected.month}/{_corrected.day}({_weekday_kr_map[_corrected.weekday()]})로 표시합니다."
    )
    target_date = _corrected
else:
    target_date = _picked_date

# 대상 날짜 계산: 당일 + 다음날 + 연속 휴일이면 다음 영업일까지
def _get_display_dates(base_date):
    dates = [base_date]
    next_d = base_date + _timedelta(days=1)
    dates.append(next_d)
    if _is_holiday_check(next_d):
        d = next_d
        while True:
            d = d + _timedelta(days=1)
            dates.append(d)
            if not _is_holiday_check(d):
                break
    return sorted(set(dates))

_display_dates = _get_display_dates(target_date)
_display_str = ", ".join(f"{d.month}/{d.day}({_weekday_kr_map[d.weekday()]})" for d in _display_dates)
st.sidebar.caption(f"대상 날짜: **{_display_str}**")

if "lng_price" not in st.session_state:
    st.session_state["lng_price"] = DEFAULT_LNG_PRICE
if "is_spot" not in st.session_state:
    st.session_state["is_spot"] = False

lng_price = st.sidebar.number_input(
    "LNG 가격 ($/MMBtu)", min_value=0.0, step=0.5, format="%.2f", key="lng_price"
)
is_spot = st.sidebar.checkbox("Spot LNG (제세금 0.8$/MMBtu 적용)", key="is_spot")

st.sidebar.markdown("---")
st.sidebar.caption(f"LNG 열량: **{lng_heat}** Mcal/Nm³ (학습데이터 평균)")
st.sidebar.caption(f"환율: **{exchange_rate:,.2f}** 원/$ (전일 평균)")

# ── SMP 다중 날짜 로드 함수 ──────────────────────────────────
import math as _math

def _load_smp_for_date(d):
    """날짜별 SMP 로드. (smp_list, source, has_real) 반환."""
    smp = None
    src = ""
    # 캐시
    cached = load_cached_smp(d)
    if cached and len(cached.get("smp", [])) == 24:
        vals = cached["smp"]
        if any(isinstance(v, (int, float)) and not _math.isnan(v) and v > 0 for v in vals):
            smp = vals
            src = f"캐시({cached.get('source', '')})"
    # ePower 엑셀
    if smp is None:
        try:
            from smp_collector import _scan_epower_excel
            vals = _scan_epower_excel(d)
            if vals and len(vals) == 24:
                smp = vals
                src = "ePower 엑셀"
        except Exception:
            pass
    # 학습 데이터
    if smp is None and data_loaded and "smp" in raw_df.columns and "datetime" in raw_df.columns:
        df_day = raw_df[raw_df["datetime"].dt.date == d]
        if len(df_day) >= 24:
            smp = df_day["smp"].head(24).tolist()
            src = "학습데이터"
    has_real = smp is not None
    if smp is None:
        smp = [float('nan')] * 24
        src = "미공시"
    return smp, src, has_real

# 대상 날짜별 SMP 로드
_all_smp = {}
for _d in _display_dates:
    _all_smp[_d] = _load_smp_for_date(_d)

# 기준일(D일) SMP
smp_series, smp_source, _has_real_smp = _all_smp[target_date]

# 사이드바 상태 표시
cached_dates = list_cached_dates()
st.sidebar.markdown("---")
if cached_dates:
    st.sidebar.caption(f"SMP 수집 완료: {cached_dates[-1]} 까지 ({len(cached_dates)}일)")
for _d in _display_dates:
    _s, _src, _ok = _all_smp[_d]
    _icon = "🟢" if _ok else "🔴"
    st.sidebar.caption(f"{_icon} {_d.month}/{_d.day}: {_src}")

# ══════════════════════════════════════════════════════════════
# 종합화면 (메인)
# ══════════════════════════════════════════════════════════════

if data_loaded and _has_real_smp:
    # ML 예측
    pred_results = predict_day(
        models, target_date, smp_series,
        lng_price, lng_heat, exchange_rate,
        elec_price_fn=get_elec_price,
    )

    # 동적 임계값
    thresholds = calc_smp_thresholds(
        lng_price, lng_heat, exchange_rate, is_spot=is_spot
    )

    # 경제성 테이블 생성
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

    # ── 카드 UI CSS ──────────────────────────────────────
    st.markdown("""
    <style>
    .card {
        background: white;
        border-radius: 12px;
        box-shadow: 0 2px 12px rgba(0,0,0,0.08);
        padding: 20px 24px;
        margin-bottom: 16px;
    }
    .card-header {
        font-size: 1.1em;
        font-weight: 700;
        color: #2F5597;
        margin-bottom: 12px;
        padding-bottom: 8px;
        border-bottom: 2px solid #B4C7E7;
    }
    .card-metric {
        background: linear-gradient(135deg, #f8f9fa, #e9ecef);
        border-radius: 10px;
        padding: 16px;
        text-align: center;
        box-shadow: 0 1px 4px rgba(0,0,0,0.06);
    }
    .card-metric .value {
        font-size: 1.5em;
        font-weight: 800;
        color: #2F5597;
    }
    .card-metric .label {
        font-size: 0.8em;
        color: #666;
        margin-top: 4px;
    }
    .title-card {
        background: linear-gradient(135deg, #2F5597, #4472C4);
        color: white;
        border-radius: 12px;
        padding: 24px;
        text-align: center;
        margin-bottom: 16px;
        box-shadow: 0 4px 16px rgba(47,85,151,0.3);
    }
    .title-card h2 { margin: 0; font-size: 1.6em; }
    .title-card p { margin: 8px 0 0 0; opacity: 0.9; font-size: 0.95em; }
    .section-title {
        font-size: 1.15em;
        font-weight: 700;
        color: #333;
        margin: 20px 0 10px 0;
        padding: 10px 16px;
        background: #f0f2f6;
        border-radius: 8px;
        border-left: 4px solid #2F5597;
    }
    </style>
    """, unsafe_allow_html=True)

    # ── 종합화면: 제목 카드 ──────────────────────────────
    from datetime import timedelta as _td
    weekday_kr = ["월","화","수","목","금","토","일"][target_date.weekday()]
    next_date = target_date + _td(days=1)
    price_type = "Spot" if is_spot else "사용단가"
    avg_elec = hourly_df['수전단가(원/kWh)'].mean()

    st.markdown(
        f"""<div class="title-card">
            <h2>LNG발전 가동 경제성 판단 결과</h2>
            <p>{target_date.month}월 {target_date.day}일({weekday_kr}) 기준</p>
        </div>""",
        unsafe_allow_html=True,
    )

    # ── 핵심 지표 카드 ───────────────────────────────────
    avg_smp_val = np.mean(smp_series)
    best_modes = hourly_df["최적모드"].value_counts()
    top_mode = best_modes.index[0] if len(best_modes) > 0 else "-"

    mc1, mc2, mc3, mc4 = st.columns(4)
    with mc1:
        st.markdown(f"""<div class="card-metric">
            <div class="value">{lng_price} $/MMBtu</div>
            <div class="label">LNG 가격 ({price_type})</div>
        </div>""", unsafe_allow_html=True)
    with mc2:
        st.markdown(f"""<div class="card-metric">
            <div class="value">{exchange_rate:,.0f} 원/$</div>
            <div class="label">환율</div>
        </div>""", unsafe_allow_html=True)
    with mc3:
        st.markdown(f"""<div class="card-metric">
            <div class="value">{avg_smp_val:.1f} 원/kWh</div>
            <div class="label">평균 SMP</div>
        </div>""", unsafe_allow_html=True)
    with mc4:
        st.markdown(f"""<div class="card-metric">
            <div class="value">{top_mode}</div>
            <div class="label">최적 운전모드</div>
        </div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # SMP 실데이터 없으면 경고
    if not _has_real_smp:
        st.warning(
            f"**{target_date} SMP 실데이터가 없습니다.** "
            f"스케줄러가 SMP를 수집하면 자동으로 갱신됩니다."
        )

    # ── 종합 차트: 전체 대상 날짜 시계열 (D일 22시 ~ 마지막 날 21시) ──
    from plotly.subplots import make_subplots

    # 날짜쌍 목록
    _date_pairs = []
    for i in range(len(_display_dates) - 1):
        _date_pairs.append((_display_dates[i], _display_dates[i + 1]))

    # 전체 시계열 구성: 첫날 22~23시, 그 이후 각 날짜 00~21시, 마지막 전날 22~23시, 마지막날 00~21시
    x_labels = []
    smp_chart = []
    bep_vals = []

    for pair_idx, (_d_from, _d_to) in enumerate(_date_pairs):
        _smp_from, _, _ok_from = _all_smp[_d_from]
        _smp_to, _, _ok_to = _all_smp[_d_to]

        # D일 22~23시 (첫 페어 또는 이전 페어의 주간 다음)
        if pair_idx == 0 or _d_from != _date_pairs[pair_idx - 1][1]:
            for h in [22, 23]:
                x_labels.append(f"{_d_from.month}/{_d_from.day} {h:02d}시")
                if _ok_from:
                    smp_chart.append(_smp_from[h])
                    bep_vals.append(hourly_df[f"BEP_{hourly_df['최적모드'].iloc[h].replace('기저부하','기저부하')}($/MMBtu)"].iloc[h]
                                    if hourly_df['최적모드'].iloc[h] != "정지" else 0)
                else:
                    smp_chart.append(None)
                    bep_vals.append(None)

        # D+1일 00~21시
        for h in range(0, 22):
            x_labels.append(f"{_d_to.month}/{_d_to.day} {h:02d}시")
            if _ok_to:
                smp_chart.append(_smp_to[h])
                # BEP는 D일 모델 기준
                mode = hourly_df['최적모드'].iloc[h]
                if mode == "1기":
                    bep_vals.append(hourly_df["BEP_1기($/MMBtu)"].iloc[h])
                elif mode == "2기저부하":
                    bep_vals.append(hourly_df["BEP_2기저부하($/MMBtu)"].iloc[h])
                elif mode == "2기":
                    bep_vals.append(hourly_df["BEP_2기($/MMBtu)"].iloc[h])
                else:
                    bep_vals.append(0)
            else:
                smp_chart.append(None)
                bep_vals.append(None)

    _chart_len = len(x_labels)
    _chart_height = max(450, min(600, 350 + _chart_len * 2))

    fig_main = make_subplots(specs=[[{"secondary_y": True}]])

    fig_main.add_trace(
        go.Bar(x=x_labels, y=bep_vals, name="LNG발전 BEP ($/MMBtu)",
               marker_color="#B4C7E7", opacity=0.85,
               text=[f"{b:.1f}" if b is not None else "" for b in bep_vals],
               textposition="outside",
               textfont=dict(size=11 if _chart_len <= 30 else 8, color="black", family="Arial Black")),
        secondary_y=True,
    )
    fig_main.add_trace(
        go.Scatter(x=x_labels, y=smp_chart, mode="lines+markers",
                   name="SMP (원/kWh)", line=dict(color="#2F5597", width=3),
                   marker=dict(size=5), connectgaps=False),
        secondary_y=False,
    )
    fig_main.add_trace(
        go.Scatter(x=x_labels, y=[lng_price]*_chart_len, mode="lines",
                   name=f"LNG가격 {lng_price} $/MMBtu",
                   line=dict(color="#ED7D31", width=2.5, dash="dash")),
        secondary_y=True,
    )

    # 날짜 경계선 추가
    for _d in _display_dates[1:]:
        _boundary = f"{_d.month}/{_d.day} 00시"
        if _boundary in x_labels:
            fig_main.add_vline(x=_boundary, line_dash="dot", line_color="#ccc", opacity=0.7)

    fig_main.update_layout(
        title=dict(text="SMP vs LNG발전 BEP vs LNG가격", x=0.5, xanchor="center"),
        height=_chart_height, plot_bgcolor="white", paper_bgcolor="white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
        margin=dict(t=80, b=100), bargap=0.25,
        xaxis=dict(tickangle=-45, dtick=2 if _chart_len > 48 else 1),
    )
    fig_main.update_xaxes(showgrid=True, gridcolor="#E0E0E0")
    fig_main.update_yaxes(title_text="SMP (원/kWh)", secondary_y=False,
                          showgrid=True, gridcolor="#E0E0E0")
    fig_main.update_yaxes(title_text="BEP / LNG가격 ($/MMBtu)", secondary_y=True)

    st.markdown('<div class="card"><div class="card-header">SMP vs LNG발전 BEP vs LNG가격</div>', unsafe_allow_html=True)
    st.plotly_chart(fig_main, use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)

    # ── 종합 테이블 생성 함수 ────────────────────────────
    def _build_summary_table(hours: list[int], mixed_dates: bool = False):
        """시간을 열, 항목을 행으로 하는 종합 테이블 생성.

        mixed_dates=True (야간): 22~23시는 D일 SMP, 00~07시는 D+1일 SMP
        mixed_dates=False (주간): 전체가 D+1일 SMP
        """
        guidance_local = generate_full_guidance(
            target_date=target_date, hourly_df=hourly_df,
            smp_series=smp_series, thresholds=thresholds,
            lng_price=lng_price, exchange_rate=exchange_rate,
            lng_heat=lng_heat, is_spot=is_spot,
        )
        plan = guidance_local["hourly_plan"]

        col_headers = [f"{h:02d}시" for h in hours]
        MODE_DISPLAY = {"2기": "2기 full", "2기저부하": "2기 저부하", "1기": "1기 full", "정지": "정지"}

        rows = {"최적운전모드": [], "SMP(원/kWh)": [], "수전단가(원/kWh)": [],
                "대체단가(원/kWh)": [], "LNG발전 BEP($/MMBtu)": [], "경제성(억원)": []}

        for h in hours:
            # 일자 기준으로 SMP 매칭: 22~23시=D일, 00~21시=D+1일
            is_d_day = (h >= 22)  # 22~23시는 D일
            if is_d_day:
                # D일 SMP (항상 있음)
                smp_val = smp_series[h]
                data_available = True
            else:
                # D+1일 SMP (없을 수 있음)
                if _has_next_smp:
                    smp_val = _next_smp[h]
                    data_available = True
                else:
                    data_available = False

            if not data_available:
                rows["최적운전모드"].append("-")
                rows["SMP(원/kWh)"].append("-")
                rows["수전단가(원/kWh)"].append("-")
                rows["대체단가(원/kWh)"].append("-")
                rows["LNG발전 BEP($/MMBtu)"].append("-")
                rows["경제성(억원)"].append("-")
                continue

            rows["최적운전모드"].append(
                MODE_DISPLAY.get(plan[h]["best_mode"], plan[h]["best_mode"]))
            rows["SMP(원/kWh)"].append(
                f"{smp_val:.1f}" if isinstance(smp_val, (int, float)) and smp_val == smp_val else "-")
            rows["수전단가(원/kWh)"].append(f"{hourly_df['수전단가(원/kWh)'].iloc[h]:.1f}")
            rows["LNG발전 BEP($/MMBtu)"].append(
                f"{plan[h]['bep']:.2f}" if plan[h]['bep'] is not None else "-")
            rows["경제성(억원)"].append(
                f"{plan[h]['econ_bil']:.3f}" if plan[h]['econ_bil'] is not None else "-")

            mode = plan[h]["best_mode"]
            elec_val = hourly_df['수전단가(원/kWh)'].iloc[h]
            if mode == "2기":
                rows["대체단가(원/kWh)"].append(f"{smp_val * 0.7 + elec_val * 0.3:.1f}")
            elif mode in ("2기저부하", "1기"):
                rows["대체단가(원/kWh)"].append(f"{elec_val:.1f}")
            elif mode == "정지":
                # 정지여도 가장 높은 BEP 모드 기준 대체단가 표시
                # 2기 BEP가 가장 높으면 SMP 기반, 아니면 수전단가
                bep_2gi = hourly_df.get("BEP_2기($/MMBtu)")
                if bep_2gi is not None and h < len(bep_2gi) and bep_2gi.iloc[h] == bep_2gi.iloc[h]:
                    rows["대체단가(원/kWh)"].append(f"{smp_val * 0.7 + elec_val * 0.3:.1f}")
                else:
                    rows["대체단가(원/kWh)"].append(f"{elec_val:.1f}")
            else:
                rows["대체단가(원/kWh)"].append("-")

        table_df = pd.DataFrame(rows, index=col_headers).T
        return table_df

    # ── 모드 행 색상 스타일 함수 ────────────────────────────
    def _style_summary(df):
        styles = pd.DataFrame("", index=df.index, columns=df.columns)
        if "최적운전모드" in df.index:
            for col in df.columns:
                val = df.loc["최적운전모드", col]
                if "full" in str(val):
                    styles.loc["최적운전모드", col] = "background-color: #D6E4F0; font-weight: bold"
                elif "저부하" in str(val):
                    styles.loc["최적운전모드", col] = "background-color: #E2EFDA; font-weight: bold"
                elif "1기" in str(val):
                    styles.loc["최적운전모드", col] = "background-color: #FFF2CC; font-weight: bold"
                elif "정지" in str(val):
                    styles.loc["최적운전모드", col] = "background-color: #F8D7DA; font-weight: bold"
        return styles

    NIGHT_HOURS = list(range(22, 24)) + list(range(0, 8))
    DAY_HOURS = list(range(8, 22))

    # ── 다중 날짜 가동계획 표시 (날짜쌍 루프) ─────────────
    # _display_dates에서 연속 날짜쌍 생성: (D일 야간 22시~, D+1일 주간 ~22시)
    _date_pairs = []
    for i in range(len(_display_dates) - 1):
        _date_pairs.append((_display_dates[i], _display_dates[i + 1]))

    for _d_night, _d_day in _date_pairs:
        _wk_night = _weekday_kr_map[_d_night.weekday()]
        _wk_day = _weekday_kr_map[_d_day.weekday()]

        _smp_night, _src_night, _ok_night = _all_smp[_d_night]
        _smp_day, _src_day, _ok_day = _all_smp[_d_day]

        # ── 야간 테이블 ──
        st.markdown(
            f'<div class="section-title">야간 {_d_night.month}월{_d_night.day}일({_wk_night}) 22시 ~ '
            f'{_d_day.month}월{_d_day.day}일({_wk_day}) 08시</div>',
            unsafe_allow_html=True,
        )

        if _ok_night:
            # 야간: 22~23시=D일 SMP, 00~07시=D+1일 SMP
            _night_rows = {"최적운전모드": [], "SMP(원/kWh)": [], "수전단가(원/kWh)": [],
                           "대체단가(원/kWh)": [], "LNG발전 BEP($/MMBtu)": [], "경제성(억원)": []}
            _night_headers = [f"{h:02d}시" for h in NIGHT_HOURS]
            MODE_DISPLAY = {"2기": "2기 full", "2기저부하": "2기 저부하", "1기": "1기 full", "정지": "정지"}

            # D일 기준 가이던스
            _g_night = generate_full_guidance(
                target_date=_d_night, hourly_df=hourly_df,
                smp_series=_smp_night, thresholds=thresholds,
                lng_price=lng_price, exchange_rate=exchange_rate,
                lng_heat=lng_heat, is_spot=is_spot,
            )
            _plan_night = _g_night["hourly_plan"]

            for h in NIGHT_HOURS:
                if h >= 22:
                    smp_val = _smp_night[h]
                    avail = True
                else:
                    if _ok_day:
                        smp_val = _smp_day[h]
                        avail = True
                    else:
                        avail = False

                if not avail:
                    for k in _night_rows:
                        _night_rows[k].append("-")
                    continue

                p = _plan_night[h]
                _night_rows["최적운전모드"].append(MODE_DISPLAY.get(p["best_mode"], p["best_mode"]))
                _night_rows["SMP(원/kWh)"].append(f"{smp_val:.1f}")
                _night_rows["수전단가(원/kWh)"].append(f"{hourly_df['수전단가(원/kWh)'].iloc[h]:.1f}")
                _night_rows["LNG발전 BEP($/MMBtu)"].append(f"{p['bep']:.2f}" if p['bep'] is not None else "-")
                _night_rows["경제성(억원)"].append(f"{p['econ_bil']:.3f}" if p['econ_bil'] is not None else "-")
                mode = p["best_mode"]
                elec_val = hourly_df['수전단가(원/kWh)'].iloc[h]
                if mode == "2기":
                    _night_rows["대체단가(원/kWh)"].append(f"{smp_val * 0.7 + elec_val * 0.3:.1f}")
                elif mode in ("2기저부하", "1기"):
                    _night_rows["대체단가(원/kWh)"].append(f"{elec_val:.1f}")
                elif mode == "정지":
                    _night_rows["대체단가(원/kWh)"].append(f"{smp_val * 0.7 + elec_val * 0.3:.1f}")
                else:
                    _night_rows["대체단가(원/kWh)"].append("-")

            _night_df = pd.DataFrame(_night_rows, index=_night_headers).T
            st.dataframe(_night_df.style.apply(_style_summary, axis=None),
                         use_container_width=True, height=280)
        else:
            st.info(f"{_d_night.month}/{_d_night.day} SMP 미공시 — 산출불가")

        # ── 주간 테이블 ──
        st.markdown(
            f'<div class="section-title">주간 {_d_day.month}월{_d_day.day}일({_wk_day}) 08시 ~ 22시</div>',
            unsafe_allow_html=True,
        )

        if _ok_day:
            _day_rows = {"최적운전모드": [], "SMP(원/kWh)": [], "수전단가(원/kWh)": [],
                         "대체단가(원/kWh)": [], "LNG발전 BEP($/MMBtu)": [], "경제성(억원)": []}
            _day_headers = [f"{h:02d}시" for h in DAY_HOURS]

            # D+1일 기준 가이던스 (D+1 SMP로 계산)
            _pred_day = predict_day(models, _d_day, _smp_day, lng_price, lng_heat, exchange_rate, elec_price_fn=get_elec_price)
            _hourly_day = build_hourly_table(target_date=_d_day, smp_series=_smp_day, lng_price=lng_price,
                                             lng_heat=lng_heat, exchange_rate=exchange_rate, pred_results=_pred_day,
                                             is_spot=is_spot, smp_high_threshold=thresholds["smp_high"])
            _g_day = generate_full_guidance(
                target_date=_d_day, hourly_df=_hourly_day,
                smp_series=_smp_day, thresholds=thresholds,
                lng_price=lng_price, exchange_rate=exchange_rate,
                lng_heat=lng_heat, is_spot=is_spot,
            )
            _plan_day = _g_day["hourly_plan"]

            for h in DAY_HOURS:
                smp_val = _smp_day[h]
                p = _plan_day[h]
                _day_rows["최적운전모드"].append(MODE_DISPLAY.get(p["best_mode"], p["best_mode"]))
                _day_rows["SMP(원/kWh)"].append(f"{smp_val:.1f}")
                _day_rows["수전단가(원/kWh)"].append(f"{_hourly_day['수전단가(원/kWh)'].iloc[h]:.1f}")
                _day_rows["LNG발전 BEP($/MMBtu)"].append(f"{p['bep']:.2f}" if p['bep'] is not None else "-")
                _day_rows["경제성(억원)"].append(f"{p['econ_bil']:.3f}" if p['econ_bil'] is not None else "-")
                mode = p["best_mode"]
                elec_val = _hourly_day['수전단가(원/kWh)'].iloc[h]
                if mode == "2기":
                    _day_rows["대체단가(원/kWh)"].append(f"{smp_val * 0.7 + elec_val * 0.3:.1f}")
                elif mode in ("2기저부하", "1기"):
                    _day_rows["대체단가(원/kWh)"].append(f"{elec_val:.1f}")
                elif mode == "정지":
                    _day_rows["대체단가(원/kWh)"].append(f"{smp_val * 0.7 + elec_val * 0.3:.1f}")
                else:
                    _day_rows["대체단가(원/kWh)"].append("-")

            _day_df = pd.DataFrame(_day_rows, index=_day_headers).T
            st.dataframe(_day_df.style.apply(_style_summary, axis=None),
                         use_container_width=True, height=280)
        else:
            st.info(f"{_d_day.month}/{_d_day.day} SMP 미공시 — 산출불가")

    st.markdown("---")

elif data_loaded and not _has_real_smp:
    # SMP 미공시 → 산출불가 표시
    from datetime import timedelta as _td
    weekday_kr = ["월","화","수","목","금","토","일"][target_date.weekday()]
    st.markdown("""
    <style>
    .unavail-card {
        background: linear-gradient(135deg, #fff5f5, #ffe3e3);
        border: 2px solid #dc3545;
        border-radius: 12px;
        padding: 40px;
        text-align: center;
        margin: 20px 0;
    }
    .unavail-card h2 { color: #dc3545; margin-bottom: 12px; }
    .unavail-card p { color: #666; font-size: 1.05em; }
    </style>
    """, unsafe_allow_html=True)
    st.markdown(
        f"""<div class="unavail-card">
            <h2>{target_date.month}월{target_date.day}일({weekday_kr}) — 산출불가</h2>
            <p>해당 날짜의 SMP 데이터가 아직 공시되지 않아<br>경제성 판단을 수행할 수 없습니다.</p>
            <p style="margin-top:16px;color:#999;font-size:0.9em">
                SMP 소스: {smp_source}<br>
                스케줄러가 SMP를 수집하면 자동으로 갱신됩니다.
            </p>
        </div>""",
        unsafe_allow_html=True,
    )
else:
    st.warning("데이터를 로드하지 못했습니다. 데이터.csv 파일을 확인하세요.")


# ══════════════════════════════════════════════════════════════
# 상세 분석 (Expander로 접기)
# ══════════════════════════════════════════════════════════════

if data_loaded and _has_real_smp:
    # ── 경제성 분석 ──────────────────────────────────────
    with st.expander("📈 경제성 분석 (상세)", expanded=False):
        st.subheader(f"24시간 경제성 분석 — {target_date}")

        # 핵심 지표 카드
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            avg_smp = np.mean(smp_series)
            st.metric("평균 SMP", f"{avg_smp:.1f} 원/kWh")
        with col2:
            st.metric("LNG 발전 BEP 임계 SMP", f"{thresholds['smp_low']:.1f} 원/kWh")
        with col3:
            st.metric("기력발전 BEP 임계 SMP", f"{thresholds['smp_high']:.1f} 원/kWh")
        with col4:
            best_modes = hourly_df["최적모드"].value_counts()
            top_mode = best_modes.index[0] if len(best_modes) > 0 else "-"
            st.metric("최다 최적모드", top_mode)

        # SMP 시계열 차트
        fig_smp = go.Figure()
        fig_smp.add_trace(go.Scatter(
            x=list(range(24)), y=smp_series,
            mode="lines+markers", name="SMP",
            line=dict(color="#2F5597", width=2),
        ))
        fig_smp.add_hline(y=thresholds["smp_low"], line_dash="dash", line_color="#FF6B35",
                          annotation_text=f"LNG발전 BEP {thresholds['smp_low']:.1f}원")
        fig_smp.add_hline(y=thresholds["smp_high"], line_dash="dash", line_color="#FF8C00",
                          annotation_text=f"기력발전 BEP {thresholds['smp_high']:.1f}원")
        fig_smp.update_layout(title="시간별 SMP 및 BEP 임계선",
                              xaxis_title="시간", yaxis_title="원/kWh", height=400,
                              plot_bgcolor="white")
        st.plotly_chart(fig_smp, use_container_width=True)

        # 상세 테이블
        st.dataframe(
            hourly_df.style.format(precision=2, na_rep="-"),
            use_container_width=True, height=600,
        )

        csv_data = hourly_df.to_csv(index=False, encoding="utf-8-sig")
        st.download_button("📥 경제성 테이블 CSV 다운로드", csv_data,
                           file_name=f"경제성분석_{target_date}.csv", mime="text/csv")


# ══════════════════════════════════════════════════════════════
# 가동 가이던스 (Expander)
# ══════════════════════════════════════════════════════════════

def _build_guidance_chart(
    hours: list[int],
    plan_data: list[dict],
    smp_list: list[float],
    hourly_table: pd.DataFrame,
    lng_price_val: float,
    thresholds_val: dict,
    title: str,
):
    """SMP 꺾은선 + BEP 막대 + LNG가격 점선 복합 차트 생성."""
    from plotly.subplots import make_subplots

    x_labels = [f"{h:02d}시" for h in hours]
    smp_vals = [smp_list[h] for h in hours]

    # 최적모드 BEP 추출
    bep_vals = []
    mode_labels_list = []
    for h in hours:
        p = plan_data[h]
        bep_vals.append(p["bep"] if p["bep"] is not None else 0)
        mode_labels_list.append(p["best_mode"])

    # BEP 막대 색상: 가동=초록, 감발=노랑, 정지=빨강
    bar_colors = []
    for h in hours:
        action = plan_data[h]["action"]
        if action == "가동":
            bar_colors.append("#2ecc71")
        elif action == "감발전환":
            bar_colors.append("#f39c12")
        elif action == "기력점화검토":
            bar_colors.append("#3498db")
        else:
            bar_colors.append("#e74c3c")

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # BEP 막대 ($/MMBtu) - 보조 Y축
    fig.add_trace(
        go.Bar(
            x=x_labels, y=bep_vals,
            name="최적모드 BEP ($/MMBtu)",
            marker_color=bar_colors,
            opacity=0.6,
            text=[f"{m}<br>{b:.1f}" for m, b in zip(mode_labels_list, bep_vals)],
            textposition="outside",
            textfont=dict(size=10),
            hovertemplate="%{x}<br>BEP: %{y:.2f} $/MMBtu<br>모드: %{text}<extra></extra>",
        ),
        secondary_y=True,
    )

    # SMP 꺾은선 (원/kWh) - 주 Y축
    fig.add_trace(
        go.Scatter(
            x=x_labels, y=smp_vals,
            mode="lines+markers",
            name="SMP (원/kWh)",
            line=dict(color="#e74c3c", width=3),
            marker=dict(size=7),
            hovertemplate="%{x}<br>SMP: %{y:.1f} 원/kWh<extra></extra>",
        ),
        secondary_y=False,
    )

    # LNG가격 점선 ($/MMBtu) - 보조 Y축
    fig.add_trace(
        go.Scatter(
            x=x_labels,
            y=[lng_price_val] * len(hours),
            mode="lines",
            name=f"LNG가격 {lng_price_val} $/MMBtu",
            line=dict(color="#2c3e50", width=2.5, dash="dash"),
            hovertemplate=f"LNG가격: {lng_price_val} $/MMBtu<extra></extra>",
        ),
        secondary_y=True,
    )

    # SMP 임계선 - 주 Y축
    fig.add_trace(
        go.Scatter(
            x=x_labels,
            y=[thresholds_val["smp_low"]] * len(hours),
            mode="lines",
            name=f"감발 임계 SMP {thresholds_val['smp_low']:.0f}원",
            line=dict(color="#e67e22", width=1.5, dash="dot"),
            hoverinfo="skip",
        ),
        secondary_y=False,
    )

    fig.update_layout(
        title=dict(text=title, font=dict(size=16)),
        height=420,
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02,
            xanchor="center", x=0.5,
            font=dict(size=11),
        ),
        margin=dict(t=80, b=40),
        bargap=0.3,
        hovermode="x unified",
    )
    fig.update_yaxes(
        title_text="SMP (원/kWh)", secondary_y=False,
        gridcolor="#eee",
    )
    fig.update_yaxes(
        title_text="BEP / LNG가격 ($/MMBtu)", secondary_y=True,
        gridcolor="rgba(0,0,0,0)",
    )

    # BEP > LNG가격 → 가동(O), BEP < LNG가격 → 정지(X) 주석
    fig.add_annotation(
        xref="paper", yref="paper", x=1.0, y=-0.12,
        text="막대(BEP)가 점선(LNG가격) 위 = 가동(O) / 아래 = 정지(X)",
        showarrow=False, font=dict(size=11, color="#666"),
    )

    return fig


def _build_period_summary(hours, plan_data, smp_list):
    """주간/야간 구간 요약 텍스트 생성."""
    period_plan = [plan_data[h] for h in hours]
    period_smp = [smp_list[h] for h in hours]

    # 모드 분포
    from collections import Counter
    mode_counter = Counter(p["best_mode"] for p in period_plan)
    action_counter = Counter(p["action"] for p in period_plan)

    avg_smp = sum(period_smp) / len(period_smp)

    # 주 운전모드
    main_mode = mode_counter.most_common(1)[0]

    lines = []
    mode_str = ", ".join(f"**{m}** {c}시간" for m, c in mode_counter.most_common())
    lines.append(f"운전모드: {mode_str}")
    lines.append(f"평균 SMP: **{avg_smp:.1f}** 원/kWh "
                 f"(최대 {max(period_smp):.1f}, 최소 {min(period_smp):.1f})")

    # 이상구간 경고
    warn_hours = [p for p in period_plan if p["action"] == "감발전환"]
    stop_hours = [p for p in period_plan if p["action"] == "정지"]
    steam_hours = [p for p in period_plan if p["action"] == "기력점화검토"]

    if stop_hours:
        hrs = ", ".join(f"{p['hour']}시" for p in stop_hours)
        lines.append(f":red[정지 권고: {hrs}]")
    if warn_hours:
        hrs = ", ".join(f"{p['hour']}시" for p in warn_hours)
        lines.append(f":orange[감발 전환 검토: {hrs}]")
    if steam_hours:
        hrs = ", ".join(f"{p['hour']}시" for p in steam_hours)
        lines.append(f":blue[기력발전 점화 검토: {hrs}]")
    if not (warn_hours or stop_hours or steam_hours):
        lines.append(":green[이상구간 없음 - 정상 가동]")

    return lines


if data_loaded and _has_real_smp:
  with st.expander("📋 가동 가이던스 (상세)", expanded=False):
    st.subheader(f"가동 가이던스 - {target_date}")

    if smp_series:
        # 데이터 준비
        if "thresholds" not in dir() or thresholds is None:
            thresholds = calc_smp_thresholds(
                lng_price, lng_heat, exchange_rate, is_spot=is_spot
            )
        if "hourly_df" not in dir() or hourly_df is None:
            pred_results = predict_day(
                models, target_date, smp_series,
                lng_price, lng_heat, exchange_rate,
                elec_price_fn=get_elec_price,
            )
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

        guidance = generate_full_guidance(
            target_date=target_date,
            hourly_df=hourly_df,
            smp_series=smp_series,
            thresholds=thresholds,
            lng_price=lng_price,
            exchange_rate=exchange_rate,
            lng_heat=lng_heat,
            is_spot=is_spot,
        )

        summary = guidance["daily_summary"]
        alerts = guidance["alerts"]
        plan = guidance["hourly_plan"]

        price_type = "Spot" if is_spot else "사용단가"
        weekday_kr = ["월","화","수","목","금","토","일"][target_date.weekday()]

        # ── 상단 요약 카드 ──────────────────────────────
        st.markdown(
            f"**{target_date} ({weekday_kr})** | "
            f"LNG {lng_price} $/MMBtu ({price_type}) | "
            f"환율 {exchange_rate:,.0f}원/$ | "
            f"열량 {lng_heat} Mcal/Nm3"
        )

        g_col1, g_col2, g_col3, g_col4 = st.columns(4)
        with g_col1:
            st.metric("평균 SMP", f"{summary['smp_avg']:.1f} 원/kWh")
        with g_col2:
            st.metric("최다 최적모드", summary["best_overall"])
        with g_col3:
            st.metric("일일 경제성", f"{summary['total_econ_best']:+.3f} 억원")
        with g_col4:
            n_anomaly = sum(len(v) for v in summary["anomaly_hours"].values())
            delta_color = "off" if n_anomaly == 0 else "normal"
            st.metric("이상구간", f"{n_anomaly}시간",
                       delta="정상" if n_anomaly == 0 else f"{n_anomaly}건 주의",
                       delta_color="off" if n_anomaly == 0 else "inverse")

        # ── 종합 운전 권고 ──────────────────────────────
        rec_lines = summary["recommendation"].split("\n")
        for line in rec_lines:
            if "[긴급]" in line:
                st.error(line)
            elif "[주의]" in line:
                st.warning(line)
            elif "[참고]" in line:
                st.info(line)
            else:
                st.success(line)

        st.markdown("---")

        # ══════════════════════════════════════════════════
        # 주간 가이던스 (08~22시)
        # ══════════════════════════════════════════════════
        DAY_HOURS = list(range(8, 22))    # 08~21시
        NIGHT_HOURS = list(range(22, 24)) + list(range(0, 8))  # 22~23시, 00~07시
        LATE_NIGHT_HOURS = list(range(0, 8))   # 00~07시 — D+1 SMP 필요

        # D+1 SMP 공시 여부 확인 (종합화면과 동일한 로직)
        _next_date = _display_dates[1] if len(_display_dates) > 1 else None
        _next_smp_raw = _all_smp.get(_next_date, ([float('nan')]*24, "미공시", False)) if _next_date else ([float('nan')]*24, "미공시", False)
        _next_smp_ok = bool(_next_date and _next_smp_raw[2])  # has_real — 종합장표의 _ok_day와 동일 기준
        _next_label = f"{_next_date.month}/{_next_date.day}" if _next_date else "익일"

        # ── D+1 SMP로 익일 가이던스 별도 생성 (종합장표와 동일한 방식) ──
        _next_smp_list = _next_smp_raw[0]
        _plan_next = None
        _plan_next_df = pd.DataFrame()
        _hourly_next = None

        if _next_smp_ok and _next_date:
            try:
                _pred_next = predict_day(
                    models, _next_date, _next_smp_list,
                    lng_price, lng_heat, exchange_rate,
                    elec_price_fn=get_elec_price,
                )
                _hourly_next = build_hourly_table(
                    target_date=_next_date, smp_series=_next_smp_list,
                    lng_price=lng_price, lng_heat=lng_heat,
                    exchange_rate=exchange_rate, pred_results=_pred_next,
                    is_spot=is_spot, smp_high_threshold=thresholds["smp_high"],
                )
                _guidance_next = generate_full_guidance(
                    target_date=_next_date, hourly_df=_hourly_next,
                    smp_series=_next_smp_list, thresholds=thresholds,
                    lng_price=lng_price, exchange_rate=exchange_rate,
                    lng_heat=lng_heat, is_spot=is_spot,
                )
                _plan_next = _guidance_next["hourly_plan"]
                _plan_next_df = pd.DataFrame(_plan_next)
            except Exception:
                _next_smp_ok = False  # 생성 실패 → 익일 미공시 처리

        # 공통 DataFrame 및 스타일 함수 (D일 기준)
        plan_df_all = pd.DataFrame(plan)

        def _style_action(row):
            action = row["판단"]
            if action == "정지":
                return ["background-color: #ffcccc"] * len(row)
            elif action == "감발전환":
                return ["background-color: #fff3cd"] * len(row)
            elif action == "기력점화검토":
                return ["background-color: #cce5ff"] * len(row)
            return [""] * len(row)

        st.markdown("### 주간 운전 가이던스 (08:00 ~ 22:00)")

        if not _next_smp_ok:
            st.info(f"⚠️ {_next_label} SMP 미공시 — 주간 가이던스 산출불가")
        else:
            # D+1 SMP 기반 가이던스 표시 (종합장표와 동일)
            day_summary_lines = _build_period_summary(DAY_HOURS, _plan_next, _next_smp_list)
            for line in day_summary_lines:
                st.markdown(f"- {line}")

            day_plan = _plan_next_df[_plan_next_df["hour"].isin(DAY_HOURS)].copy()
            day_display = day_plan[["time_str","smp","best_mode","action","bep","econ_bil","note"]].copy()
            day_display.columns = ["시간","SMP(원/kWh)","최적모드","판단","BEP($/MMBtu)","경제성(억)","비고"]

            st.dataframe(
                day_display.style
                    .apply(_style_action, axis=1)
                    .format({"SMP(원/kWh)": "{:.1f}", "BEP($/MMBtu)": "{:.2f}", "경제성(억)": "{:.3f}"}, na_rep="-"),
                use_container_width=True,
                height=min(len(DAY_HOURS) * 38 + 40, 600),
            )

            fig_day = _build_guidance_chart(
                DAY_HOURS, _plan_next, _next_smp_list, _hourly_next,
                lng_price, thresholds,
                title=f"주간 (08~22시) SMP vs BEP 경제성 판단 [{_next_label}]",
            )
            st.plotly_chart(fig_day, use_container_width=True)

        st.markdown("---")

        # ══════════════════════════════════════════════════
        # 야간 가이던스 (22시 ~ 명일 08시)
        # ══════════════════════════════════════════════════
        st.markdown("### 야간 운전 가이던스 (22:00 ~ 익일 08:00)")

        # 22~23시: 당일 D일 SMP (항상 가용)
        TONIGHT_HOURS = list(range(22, 24))
        st.markdown(f"**당일 {target_date.month}/{target_date.day} 22:00 ~ 23:59 (D일 SMP 기준)**")
        tonight_summary_lines = _build_period_summary(TONIGHT_HOURS, plan, smp_series)
        for line in tonight_summary_lines:
            st.markdown(f"- {line}")

        tonight_plan = plan_df_all[plan_df_all["hour"].isin(TONIGHT_HOURS)].copy()
        tonight_display = tonight_plan[["time_str","smp","best_mode","action","bep","econ_bil","note"]].copy()
        tonight_display.columns = ["시간","SMP(원/kWh)","최적모드","판단","BEP($/MMBtu)","경제성(억)","비고"]
        st.dataframe(
            tonight_display.style
                .apply(_style_action, axis=1)
                .format({"SMP(원/kWh)": "{:.1f}", "BEP($/MMBtu)": "{:.2f}", "경제성(억)": "{:.3f}"}, na_rep="-"),
            use_container_width=True,
            height=min(len(TONIGHT_HOURS) * 38 + 40, 160),
        )

        # 00~07시: D+1 SMP 필요 — 종합장표와 동일 기준 적용
        st.markdown(f"**익일 {_next_label} 00:00 ~ 08:00**")
        if not _next_smp_ok:
            st.info(f"⚠️ {_next_label} SMP 미공시 — 익일 새벽 가이던스 산출불가")
        else:
            # D+1 SMP 기반 가이던스 표시 (종합장표와 동일)
            late_night_plan = _plan_next_df[_plan_next_df["hour"].isin(LATE_NIGHT_HOURS)].copy()
            late_night_display = late_night_plan[["time_str","smp","best_mode","action","bep","econ_bil","note"]].copy()
            late_night_display.columns = ["시간","SMP(원/kWh)","최적모드","판단","BEP($/MMBtu)","경제성(억)","비고"]
            st.dataframe(
                late_night_display.style
                    .apply(_style_action, axis=1)
                    .format({"SMP(원/kWh)": "{:.1f}", "BEP($/MMBtu)": "{:.2f}", "경제성(억)": "{:.3f}"}, na_rep="-"),
                use_container_width=True,
                height=min(len(LATE_NIGHT_HOURS) * 38 + 40, 350),
            )

        # 야간 차트: 22~23시(D일) + 00~07시(D+1) 혼합 또는 22~23시만
        if _next_smp_ok:
            # D일 22~23시 plan + D+1 00~07시 plan 혼합
            _mixed_plan = list(plan)
            _mixed_smp = list(smp_series)
            for _h in LATE_NIGHT_HOURS:
                _mixed_plan[_h] = _plan_next[_h]
                _mixed_smp[_h] = _next_smp_list[_h]
            fig_night = _build_guidance_chart(
                NIGHT_HOURS, _mixed_plan, _mixed_smp, hourly_df,
                lng_price, thresholds,
                title=f"야간 (22시~익일08시) SMP vs BEP 경제성 판단",
            )
        else:
            fig_night = _build_guidance_chart(
                TONIGHT_HOURS, plan, smp_series, hourly_df,
                lng_price, thresholds,
                title=f"야간 (22~23시) SMP vs BEP 경제성 판단 — 익일 미공시",
            )
        st.plotly_chart(fig_night, use_container_width=True)

        st.markdown("---")

        # ── 모드별 일일 경제성 요약 ─────────────────────
        st.markdown("### 일일 경제성 요약")
        es_col1, es_col2 = st.columns(2)

        with es_col1:
            st.markdown("**모드별 분포**")
            for mode_name, hours_count in summary["mode_dist"].items():
                st.write(f"- {mode_name}: {hours_count}시간")

            if summary["anomaly_hours"]:
                st.markdown("**이상구간**")
                for atype, hrs in summary["anomaly_hours"].items():
                    hr_str = ", ".join(f"{h}시" for h in hrs)
                    st.write(f"- {atype}: {hr_str}")

        with es_col2:
            econ_summary_df = pd.DataFrame([
                {"운전모드": m, "경제성(억원)": v}
                for m, v in summary["econ_totals"].items()
            ])
            if not econ_summary_df.empty:
                fig_econ_bar = go.Figure(go.Bar(
                    x=econ_summary_df["운전모드"],
                    y=econ_summary_df["경제성(억원)"],
                    marker_color=["#4A90D9", "#7ED321", "#FF6B35"][:len(econ_summary_df)],
                    text=econ_summary_df["경제성(억원)"].apply(lambda x: f"{x:+.3f}"),
                    textposition="outside",
                ))
                fig_econ_bar.update_layout(
                    title="모드별 일일 경제성 합계",
                    yaxis_title="억원", height=300,
                    margin=dict(t=40),
                )
                st.plotly_chart(fig_econ_bar, use_container_width=True)

        # ── 다운로드 ────────────────────────────────────
        st.markdown("---")
        col_dl1, col_dl2 = st.columns(2)
        with col_dl1:
            st.download_button(
                "가이던스 텍스트 리포트 다운로드",
                guidance["text_report"],
                file_name=f"가동가이던스_{target_date}.txt",
                mime="text/plain",
            )
        with col_dl2:
            full_plan_display = plan_df_all[["time_str","smp","best_mode","action","bep","econ_bil","note"]].copy()
            full_plan_display.columns = ["시간","SMP(원/kWh)","최적모드","판단","BEP($/MMBtu)","경제성(억)","비고"]
            plan_csv = full_plan_display.to_csv(index=False, encoding="utf-8-sig")
            st.download_button(
                "가동계획표 CSV 다운로드",
                plan_csv,
                file_name=f"가동계획표_{target_date}.csv",
                mime="text/csv",
            )

        # ── 카카오톡 메시지 ─────────────────────────────
        st.markdown("### 카카오톡 전파 메시지")
        kakao_msg = guidance.get("kakao_message", "")
        if kakao_msg:
            st.code(kakao_msg, language=None)
    else:
        st.warning("데이터를 로드하지 못했습니다.")


# ══════════════════════════════════════════════════════════════
# 이상구간 탐지 (Expander)
# ══════════════════════════════════════════════════════════════
if data_loaded and _has_real_smp:
  with st.expander("🔍 이상구간 탐지", expanded=False):
    st.subheader("SMP 이상구간 탐지")

    if data_loaded and "smp" in raw_df.columns:
        thresholds = calc_smp_thresholds(
            lng_price, lng_heat, exchange_rate, is_spot=is_spot
        )

        anomalies = detect_smp_anomalies(
            raw_df,
            smp_low=thresholds["smp_low"],
            smp_high=thresholds["smp_high"],
        )

        col1, col2, col3 = st.columns(3)
        with col1:
            n_zero = len(anomalies[anomalies["anomaly_type"] == "SMP 제로"]) if not anomalies.empty else 0
            st.metric("SMP 제로 건수", n_zero)
        with col2:
            n_low = len(anomalies[anomalies["anomaly_type"] == "SMP 경제성 한계"]) if not anomalies.empty else 0
            st.metric("SMP 경제성 한계", n_low)
        with col3:
            n_high = len(anomalies[anomalies["anomaly_type"] == "SMP 과대"]) if not anomalies.empty else 0
            st.metric("SMP 과대", n_high)

        # SMP 이상구간 차트
        fig = build_smp_chart(
            raw_df, anomalies,
            smp_low=thresholds["smp_low"],
            smp_high=thresholds["smp_high"],
            is_spot=is_spot,
        )
        st.plotly_chart(fig, use_container_width=True)

        if not anomalies.empty:
            st.subheader("이상구간 상세")
            st.dataframe(anomalies, use_container_width=True)
        else:
            st.success("이상구간이 감지되지 않았습니다.")

        # 경제성 급변 탐지
        st.markdown("---")
        st.subheader("경제성 급변 구간 탐지")

        # hourly_df에서 econ_diff 컬럼이 있으면 급변 탐지
        if "경제성차이_2기" in hourly_df.columns:
            econ_df = pd.DataFrame({
                "datetime": pd.date_range(
                    start=datetime.combine(target_date, datetime.min.time()),
                    periods=24, freq="h"
                ),
                "econ_diff_2gi": hourly_df["경제성차이_2기"].values,
            })
            change_df = detect_econ_change(econ_df, econ_col="econ_diff_2gi")
            fig_econ_change = build_econ_change_chart(econ_df, change_df, econ_col="econ_diff_2gi")
            st.plotly_chart(fig_econ_change, use_container_width=True)

            if not change_df.empty:
                st.dataframe(change_df, use_container_width=True)
            else:
                st.success("급변 구간이 감지되지 않았습니다.")
    else:
        st.warning("SMP 데이터가 없습니다.")


# ══════════════════════════════════════════════════════════════
# ML 모델 성능 (Expander)
# ══════════════════════════════════════════════════════════════
if data_loaded:
  with st.expander("🤖 ML 모델 성능", expanded=False):
    st.subheader("🤖 XGBoost 모델 성능 요약")

    if data_loaded and metrics:
        # 분할 정보
        split_info = metrics.get("_split", {})
        if split_info:
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("전체 데이터", f"{split_info.get('n_all', '-')}행")
            with col2:
                st.metric("학습 데이터", f"{split_info.get('n_train', '-')}행")
            with col3:
                st.metric("테스트 데이터", f"{split_info.get('n_test', '-')}행")

        # 모드별 성능 테이블
        perf_rows = []
        for mode in MODES:
            mode_metrics = metrics.get(mode, {})
            for target, m in mode_metrics.items():
                perf_rows.append({
                    "운전모드": MODE_LABELS.get(mode, mode),
                    "타깃": target,
                    "Train MAE": m.get("mae"),
                    "Train R²": m.get("r2"),
                    "CV R²": m.get("r2_cv"),
                    "Test MAE": m.get("mae_test"),
                    "Test R²": m.get("r2_test"),
                    "학습 샘플": m.get("n_samples"),
                    "테스트 샘플": m.get("n_samples_test"),
                })

        if perf_rows:
            perf_df = pd.DataFrame(perf_rows)
            st.dataframe(
                perf_df.style.format(precision=4, na_rep="-")
                    .background_gradient(subset=["Train R²", "CV R²"], cmap="RdYlGn", vmin=0, vmax=1),
                use_container_width=True,
            )

            # R² 시각화
            fig_r2 = go.Figure()
            for target in ["export", "import", "efficiency"]:
                sub = perf_df[perf_df["타깃"] == target]
                fig_r2.add_trace(go.Bar(
                    x=sub["운전모드"],
                    y=sub["CV R²"],
                    name=target,
                ))
            fig_r2.update_layout(
                title="모드별 CV R² 점수",
                xaxis_title="운전모드", yaxis_title="R²",
                barmode="group", height=400,
            )
            st.plotly_chart(fig_r2, use_container_width=True)
        else:
            st.info("학습된 모델 메트릭이 없습니다.")

        # 재학습 버튼
        st.markdown("---")
        if st.button("🔄 모델 재학습", help="전체 데이터로 XGBoost 모델을 재학습합니다"):
            with st.spinner("재학습 중... (1~2분 소요)"):
                new_metrics = retrain()
                st.cache_resource.clear()
                st.success("재학습 완료! 페이지를 새로고침합니다.")
                st.rerun()
    else:
        st.warning("모델이 로드되지 않았습니다.")


# ══════════════════════════════════════════════════════════════
# 원시 데이터 (Expander)
# ══════════════════════════════════════════════════════════════
if data_loaded:
  with st.expander("📁 원시 데이터", expanded=False):
    st.subheader("📁 학습 데이터 미리보기")

    if data_loaded and not raw_df.empty:
        st.write(f"총 {len(raw_df):,}행 × {len(raw_df.columns)}열")

        # 기본 통계
        st.markdown("**기본 통계**")
        num_cols = raw_df.select_dtypes(include=[np.number]).columns.tolist()
        if num_cols:
            st.dataframe(raw_df[num_cols].describe().T.style.format(precision=2), use_container_width=True)

        # 데이터 프리뷰
        st.markdown("**데이터 미리보기 (상위 100행)**")
        st.dataframe(raw_df.head(100), use_container_width=True, height=400)

        # SMP 히스토그램
        if "smp" in raw_df.columns:
            fig_hist = go.Figure()
            smp_valid = raw_df["smp"].dropna()
            fig_hist.add_trace(go.Histogram(x=smp_valid, nbinsx=50, name="SMP 분포"))
            fig_hist.update_layout(
                title="SMP 분포 히스토그램",
                xaxis_title="SMP (원/kWh)",
                yaxis_title="빈도",
                height=350,
            )
            st.plotly_chart(fig_hist, use_container_width=True)
    else:
        st.warning("데이터를 로드하지 못했습니다.")

# ── 푸터 ──────────────────────────────────────────────────────
st.markdown("---")
st.caption("LNG발전 최적 가이던스 제공 프로그램 v1.0 | XGBoost 기반 ML 예측 + 동적 임계값 이상탐지")
