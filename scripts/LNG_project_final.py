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
try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=300_000, limit=None, key="data_refresh")
except ImportError:
    pass

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

target_date = st.sidebar.date_input("분석 날짜", value=date.today())
lng_price = st.sidebar.number_input(
    "LNG 가격 ($/MMBtu)", value=DEFAULT_LNG_PRICE, min_value=0.0, step=0.5, format="%.2f"
)
is_spot = st.sidebar.checkbox("Spot LNG (제세금 0.8$/MMBtu 적용)", value=False)

st.sidebar.markdown("---")
st.sidebar.caption(f"LNG 열량: **{lng_heat}** Mcal/Nm³ (학습데이터 평균)")
st.sidebar.caption(f"환율: **{exchange_rate:,.2f}** 원/$ (전일 평균)")

# SMP 추출: 수집 캐시 우선 → ePower 엑셀 → 학습 데이터 폴백
import math as _math
smp_series = None
smp_source = ""

# 1순위: smp_collector가 수집한 캐시 (data/smp_cache/)
cached = load_cached_smp(target_date)
if cached and len(cached.get("smp", [])) == 24:
    _smp_vals = cached["smp"]
    _has_valid = any(
        isinstance(v, (int, float)) and not _math.isnan(v) and v > 0
        for v in _smp_vals
    )
    if _has_valid:
        smp_series = _smp_vals
        smp_source = f"수집 캐시 ({cached.get('source', '')})"

# 1-2순위: ePower 엑셀에서 직접 읽기 (캐시에 없을 때)
if smp_series is None:
    try:
        from smp_collector import _scan_epower_excel
        _excel_smp = _scan_epower_excel(target_date)
        if _excel_smp and len(_excel_smp) == 24:
            smp_series = _excel_smp
            smp_source = "ePower 엑셀"
    except Exception:
        pass

# 2순위: 학습 데이터에서 해당 날짜 (정확히 일치하는 날짜만)
if smp_series is None and data_loaded and "smp" in raw_df.columns and "datetime" in raw_df.columns:
    df_day = raw_df[raw_df["datetime"].dt.date == target_date]
    if len(df_day) >= 24:
        smp_series = df_day["smp"].head(24).tolist()
        smp_source = "학습 데이터"
    # 해당 날짜 데이터가 없으면 폴백하지 않음 (다른 날짜 데이터를 넣지 않음)

# SMP 실데이터 존재 여부 플래그 (캐시/엑셀/학습데이터에서 가져온 경우만 True)
_has_real_smp = smp_series is not None

if smp_series is None:
    smp_series = [float('nan')] * 24
    smp_source = "SMP 미공시 (산출불가)"

# 수집 가능 날짜 표시
cached_dates = list_cached_dates()
st.sidebar.markdown("---")
if cached_dates:
    st.sidebar.caption(f"SMP 수집 완료: {cached_dates[-1]} 까지 ({len(cached_dates)}일)")
st.sidebar.caption(f"SMP 소스: **{smp_source}**")

# 스케줄러 저장 CSV 존재 여부
_csv_path = _ROOT / "data" / f"경제성분석_{target_date}.csv"
if _csv_path.exists():
    st.sidebar.success(f"경제성분석 CSV 존재 ({target_date})")
else:
    st.sidebar.info(f"경제성분석 CSV 미생성 ({target_date}) — 스케줄러 실행 후 자동 생성")

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

    # ── 종합화면: 제목 + 조건 ──────────────────────────────
    from datetime import timedelta as _td
    weekday_kr = ["월","화","수","목","금","토","일"][target_date.weekday()]
    next_date = target_date + _td(days=1)
    price_type = "Spot" if is_spot else "사용단가"

    st.markdown(
        f"<h2 style='text-align:center;margin-bottom:0'>LNG발전 가동 경제성 판단 결과</h2>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"<p style='text-align:center;color:#555;font-size:1.05em'>"
        f"※ LNG 가격 : <b>{lng_price}</b>$/MMBtu, "
        f"환율 : <b>{exchange_rate:,.0f}</b>원/$, "
        f"수전단가 : <b>{hourly_df['수전단가(원/kWh)'].mean():.1f}</b>원/kWh (평균)"
        f"</p>",
        unsafe_allow_html=True,
    )

    # SMP 실데이터 없으면 경고
    if not _has_real_smp:
        st.warning(
            f"**{target_date} SMP 실데이터가 없습니다.** "
            f"현재 기본값(80원/kWh)으로 표시 중이며, 실제 가동판단에 활용할 수 없습니다. "
            f"스케줄러가 SMP를 수집하면 자동으로 갱신됩니다."
        )

    # ── D+1 SMP 존재 여부 확인 ─────────────────────────────
    _next_smp = None
    _next_cached = load_cached_smp(next_date)
    if _next_cached and len(_next_cached.get("smp", [])) == 24:
        _nv = _next_cached["smp"]
        if any(isinstance(v, (int, float)) and not _math.isnan(v) and v > 0 for v in _nv):
            _next_smp = _nv
    if _next_smp is None:
        try:
            from smp_collector import _scan_epower_excel
            _nv = _scan_epower_excel(next_date)
            if _nv and len(_nv) == 24:
                _next_smp = _nv
        except Exception:
            pass
    _has_next_smp = _next_smp is not None

    # ── 종합 차트: SMP vs BEP vs LNG가격 (22시~다음날 22시) ──
    from plotly.subplots import make_subplots

    # 시계열: D일 22시~D+1일 21시 (야간+주간 = 24시간)
    CHART_HOURS = list(range(22, 24)) + list(range(0, 22))
    m1, d1 = target_date.month, target_date.day
    m2, d2 = next_date.month, next_date.day
    x_labels = []
    smp_chart = []
    bep_vals = []
    best_mode_col = hourly_df["최적모드"]

    for h in CHART_HOURS:
        if h >= 22:
            # D일 22~23시: 실데이터
            x_labels.append(f"{m1}/{d1} {h:02d}시")
            smp_chart.append(smp_series[h])
            mode = best_mode_col.iloc[h]
            if mode == "1기":
                bep_vals.append(hourly_df["BEP_1기($/MMBtu)"].iloc[h])
            elif mode == "2기저부하":
                bep_vals.append(hourly_df["BEP_2기저부하($/MMBtu)"].iloc[h])
            elif mode == "2기":
                bep_vals.append(hourly_df["BEP_2기($/MMBtu)"].iloc[h])
            else:
                bep_vals.append(0)
        else:
            # D+1일 00~21시
            x_labels.append(f"{m2}/{d2} {h:02d}시")
            if h < 8:
                # 야간(00~07시): D일 SMP 기준 (오늘 공시 SMP로 판단)
                smp_chart.append(smp_series[h])
                mode = best_mode_col.iloc[h]
                if mode == "1기":
                    bep_vals.append(hourly_df["BEP_1기($/MMBtu)"].iloc[h])
                elif mode == "2기저부하":
                    bep_vals.append(hourly_df["BEP_2기저부하($/MMBtu)"].iloc[h])
                elif mode == "2기":
                    bep_vals.append(hourly_df["BEP_2기($/MMBtu)"].iloc[h])
                else:
                    bep_vals.append(0)
            elif _has_next_smp:
                # 주간(08~21시): D+1 SMP가 있을 때만 표시
                smp_chart.append(_next_smp[h])
                mode = best_mode_col.iloc[h]
                if mode == "1기":
                    bep_vals.append(hourly_df["BEP_1기($/MMBtu)"].iloc[h])
                elif mode == "2기저부하":
                    bep_vals.append(hourly_df["BEP_2기저부하($/MMBtu)"].iloc[h])
                elif mode == "2기":
                    bep_vals.append(hourly_df["BEP_2기($/MMBtu)"].iloc[h])
                else:
                    bep_vals.append(0)
            else:
                # 주간 D+1 SMP 없음 → 공란
                smp_chart.append(None)
                bep_vals.append(None)

    fig_main = make_subplots(specs=[[{"secondary_y": True}]])

    # BEP 막대 (먼저 추가 → 뒤에 깔림)
    fig_main.add_trace(
        go.Bar(x=x_labels, y=bep_vals, name="LNG발전 BEP ($/MMBtu)",
               marker_color="#B4C7E7", opacity=0.85,
               text=[f"{b:.1f}" if b is not None else "" for b in bep_vals],
               textposition="outside",
               textfont=dict(size=12, color="black", family="Arial Black")),
        secondary_y=True,
    )

    # SMP 꺾은선 (나중에 추가 → 막대 위에 표시)
    fig_main.add_trace(
        go.Scatter(x=x_labels, y=smp_chart, mode="lines+markers",
                   name="SMP (원/kWh)", line=dict(color="#2F5597", width=3),
                   marker=dict(size=6), connectgaps=False),
        secondary_y=False,
    )

    # LNG가격 점선
    fig_main.add_trace(
        go.Scatter(x=x_labels, y=[lng_price]*len(CHART_HOURS), mode="lines",
                   name=f"LNG가격 {lng_price} $/MMBtu",
                   line=dict(color="#ED7D31", width=2.5, dash="dash")),
        secondary_y=True,
    )

    fig_main.update_layout(
        title=dict(text="SMP vs LNG발전 BEP vs LNG가격", x=0.5, xanchor="center"),
        height=450, plot_bgcolor="white", paper_bgcolor="white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
        margin=dict(t=80, b=80), bargap=0.3,
        xaxis=dict(tickangle=-45),
    )
    fig_main.update_xaxes(showgrid=True, gridcolor="#E0E0E0")
    fig_main.update_yaxes(title_text="SMP (원/kWh)", secondary_y=False,
                          showgrid=True, gridcolor="#E0E0E0")
    fig_main.update_yaxes(title_text="BEP / LNG가격 ($/MMBtu)", secondary_y=True)
    st.plotly_chart(fig_main, use_container_width=True)

    # ── 종합 테이블 생성 함수 ────────────────────────────
    def _build_summary_table(hours: list[int], use_next_smp: bool = False):
        """시간을 열, 항목을 행으로 하는 종합 테이블 생성.

        야간(22~07시): D일 SMP 기준으로 판단 (오늘 공시 SMP)
        주간(08~21시): use_next_smp=True → D+1 SMP가 있으면 사용, 없으면 공란
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
            # 주간 테이블에서 D+1 SMP 필요한데 없으면 공란
            if use_next_smp and not _has_next_smp:
                rows["최적운전모드"].append("-")
                rows["SMP(원/kWh)"].append("-")
                rows["수전단가(원/kWh)"].append("-")
                rows["대체단가(원/kWh)"].append("-")
                rows["LNG발전 BEP($/MMBtu)"].append("-")
                rows["경제성(억원)"].append("-")
                continue

            rows["최적운전모드"].append(
                MODE_DISPLAY.get(plan[h]["best_mode"], plan[h]["best_mode"]))

            smp_val = smp_series[h]
            rows["SMP(원/kWh)"].append(
                f"{smp_val:.1f}" if isinstance(smp_val, (int, float)) and smp_val == smp_val else "-")
            rows["수전단가(원/kWh)"].append(f"{hourly_df['수전단가(원/kWh)'].iloc[h]:.1f}")
            rows["LNG발전 BEP($/MMBtu)"].append(
                f"{plan[h]['bep']:.2f}" if plan[h]['bep'] else "-")
            rows["경제성(억원)"].append(
                f"{plan[h]['econ_bil']:.3f}" if plan[h]['econ_bil'] else "-")

            mode = plan[h]["best_mode"]
            elec_val = hourly_df['수전단가(원/kWh)'].iloc[h]
            if mode == "2기":
                rows["대체단가(원/kWh)"].append(f"{smp_val * 0.7 + elec_val * 0.3:.1f}")
            elif mode in ("2기저부하", "1기"):
                rows["대체단가(원/kWh)"].append(f"{elec_val:.1f}")
            else:
                rows["대체단가(원/kWh)"].append("-")

        table_df = pd.DataFrame(rows, index=col_headers).T
        return table_df

    # ── 야간 테이블 (D일 22시 ~ D+1일 07시) ─────────────
    NIGHT_HOURS = list(range(22, 24)) + list(range(0, 8))
    DAY_HOURS = list(range(8, 22))

    st.markdown(
        f"<h3 style='text-align:center'>야간 {target_date.month}월{target_date.day}일({weekday_kr}) 22시 ~ "
        f"{next_date.month}월{next_date.day}일 08시</h3>",
        unsafe_allow_html=True,
    )
    night_table = _build_summary_table(NIGHT_HOURS, use_next_smp=False)

    # 모드 행에 색상 적용
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

    st.dataframe(
        night_table.style.apply(_style_summary, axis=None),
        use_container_width=True, height=280,
    )

    # ── 주간 테이블 (D+1일 08시 ~ 21시) ─────────────────
    next_weekday = ["월","화","수","목","금","토","일"][next_date.weekday()]
    st.markdown(
        f"<h3 style='text-align:center'>주간 {next_date.month}월{next_date.day}일({next_weekday}) 08시 ~ 22시</h3>",
        unsafe_allow_html=True,
    )
    day_table = _build_summary_table(DAY_HOURS, use_next_smp=True)
    st.dataframe(
        day_table.style.apply(_style_summary, axis=None),
        use_container_width=True, height=280,
    )

    st.markdown("---")

elif data_loaded and not _has_real_smp:
    # SMP 미공시 → 산출불가 표시
    from datetime import timedelta as _td
    weekday_kr = ["월","화","수","목","금","토","일"][target_date.weekday()]
    st.markdown(
        f"<h2 style='text-align:center;margin-bottom:0'>LNG발전 가동 경제성 판단 결과</h2>",
        unsafe_allow_html=True,
    )
    st.markdown("---")
    st.error(
        f"**{target_date.month}월{target_date.day}일({weekday_kr}) — 산출불가**\n\n"
        f"해당 날짜의 SMP 데이터가 아직 공시되지 않아 경제성 판단을 수행할 수 없습니다.\n\n"
        f"SMP 소스: **{smp_source}**\n\n"
        f"스케줄러가 SMP를 수집하면 자동으로 갱신됩니다."
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

        st.markdown("### 주간 운전 가이던스 (08:00 ~ 22:00)")

        day_summary_lines = _build_period_summary(DAY_HOURS, plan, smp_series)
        for line in day_summary_lines:
            st.markdown(f"- {line}")

        # 주간 가동계획표
        plan_df_all = pd.DataFrame(plan)
        day_plan = plan_df_all[plan_df_all["hour"].isin(DAY_HOURS)].copy()
        day_display = day_plan[["time_str","smp","best_mode","action","bep","econ_bil","note"]].copy()
        day_display.columns = ["시간","SMP(원/kWh)","최적모드","판단","BEP($/MMBtu)","경제성(억)","비고"]

        def _style_action(row):
            action = row["판단"]
            if action == "정지":
                return ["background-color: #ffcccc"] * len(row)
            elif action == "감발전환":
                return ["background-color: #fff3cd"] * len(row)
            elif action == "기력점화검토":
                return ["background-color: #cce5ff"] * len(row)
            return [""] * len(row)

        st.dataframe(
            day_display.style
                .apply(_style_action, axis=1)
                .format({"SMP(원/kWh)": "{:.1f}", "BEP($/MMBtu)": "{:.2f}", "경제성(억)": "{:.3f}"}, na_rep="-"),
            use_container_width=True,
            height=min(len(DAY_HOURS) * 38 + 40, 600),
        )

        # 주간 차트
        fig_day = _build_guidance_chart(
            DAY_HOURS, plan, smp_series, hourly_df,
            lng_price, thresholds,
            title=f"주간 (08~22시) SMP vs BEP 경제성 판단",
        )
        st.plotly_chart(fig_day, use_container_width=True)

        st.markdown("---")

        # ══════════════════════════════════════════════════
        # 야간 가이던스 (22시 ~ 명일 08시)
        # ══════════════════════════════════════════════════
        st.markdown("### 야간 운전 가이던스 (22:00 ~ 익일 08:00)")

        night_summary_lines = _build_period_summary(NIGHT_HOURS, plan, smp_series)
        for line in night_summary_lines:
            st.markdown(f"- {line}")

        # 야간 가동계획표
        night_plan = plan_df_all[plan_df_all["hour"].isin(NIGHT_HOURS)].copy()
        # 정렬: 22,23,0,1,...,7 순서
        night_order = {h: i for i, h in enumerate(NIGHT_HOURS)}
        night_plan["_sort"] = night_plan["hour"].map(night_order)
        night_plan = night_plan.sort_values("_sort").drop(columns=["_sort"])
        night_display = night_plan[["time_str","smp","best_mode","action","bep","econ_bil","note"]].copy()
        night_display.columns = ["시간","SMP(원/kWh)","최적모드","판단","BEP($/MMBtu)","경제성(억)","비고"]

        st.dataframe(
            night_display.style
                .apply(_style_action, axis=1)
                .format({"SMP(원/kWh)": "{:.1f}", "BEP($/MMBtu)": "{:.2f}", "경제성(억)": "{:.3f}"}, na_rep="-"),
            use_container_width=True,
            height=min(len(NIGHT_HOURS) * 38 + 40, 450),
        )

        # 야간 차트
        fig_night = _build_guidance_chart(
            NIGHT_HOURS, plan, smp_series, hourly_df,
            lng_price, thresholds,
            title=f"야간 (22시~익일08시) SMP vs BEP 경제성 판단",
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
