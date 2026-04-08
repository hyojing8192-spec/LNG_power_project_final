"""
LNG 발전소 경제성 자동판단 시스템 — Streamlit 대시보드
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

# ──────────────────────────────────────────────────────────────
# 페이지 설정
# ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="LNG 발전 경제성 자동판단",
    page_icon="⚡",
    layout="wide",
)

st.title("⚡ LNG 발전소 경제성 자동판단 시스템")
st.caption("SMP 기반 운전모드 최적화 · ML 예측 · 이상구간 탐지")

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

# SMP 추출: 수집 캐시 우선 → 학습 데이터 폴백
smp_series = None
smp_source = ""

# 1순위: smp_collector가 수집한 캐시 (data/smp_cache/)
cached = load_cached_smp(target_date)
if cached and len(cached.get("smp", [])) == 24:
    smp_series = cached["smp"]
    smp_source = f"수집 캐시 ({cached.get('source', '')})"

# 2순위: 학습 데이터에서 해당 날짜
if smp_series is None and data_loaded and "smp" in raw_df.columns and "datetime" in raw_df.columns:
    df_day = raw_df[raw_df["datetime"].dt.date == target_date]
    if len(df_day) >= 24:
        smp_series = df_day["smp"].head(24).tolist()
        smp_source = "학습 데이터"
    else:
        smp_series = raw_df["smp"].head(24).tolist()
        if len(smp_series) < 24:
            smp_series = smp_series + [0.0] * (24 - len(smp_series))
        smp_source = "학습 데이터 (첫날)"

if smp_series is None:
    smp_series = [80.0] * 24
    smp_source = "기본값"

# 수집 가능 날짜 표시
cached_dates = list_cached_dates()
if cached_dates:
    st.sidebar.markdown("---")
    st.sidebar.caption(f"SMP 수집 완료: {cached_dates[-1]} 까지 ({len(cached_dates)}일)")
st.sidebar.caption(f"SMP 소스: **{smp_source}**")

# ──────────────────────────────────────────────────────────────
# 탭 레이아웃
# ──────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs([
    "📈 경제성 분석", "🔍 이상구간 탐지", "🤖 ML 모델 성능", "📁 원시 데이터"
])

# ══════════════════════════════════════════════════════════════
# TAB 1: 경제성 분석
# ══════════════════════════════════════════════════════════════
with tab1:
    st.subheader(f"24시간 경제성 분석 — {target_date}")

    if data_loaded:
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

        price_type = "Spot" if is_spot else "사용단가"
        st.info(
            f"💡 LNG 가격 유형: **{price_type}** | 제세금: **{0.8 if is_spot else 0}$/MMBtu** | "
            f"열량: **{lng_heat}** Mcal/Nm³ | 환율: **{exchange_rate:,.2f}** 원/$"
        )

        # SMP 시계열 차트
        fig_smp = go.Figure()
        fig_smp.add_trace(go.Scatter(
            x=list(range(24)),
            y=smp_series,
            mode="lines+markers",
            name="SMP",
            line=dict(color="#4A90D9", width=2),
        ))
        fig_smp.add_hline(y=thresholds["smp_low"], line_dash="dash", line_color="#FF6B35",
                          annotation_text=f"LNG발전 BEP ({price_type}) {thresholds['smp_low']:.1f}원")
        fig_smp.add_hline(y=thresholds["smp_high"], line_dash="dash", line_color="#FF8C00",
                          annotation_text=f"기력발전 BEP ({price_type}) {thresholds['smp_high']:.1f}원")
        fig_smp.update_layout(
            title="시간별 SMP 및 BEP 임계선",
            xaxis_title="시간", yaxis_title="원/kWh",
            height=400,
        )
        st.plotly_chart(fig_smp, use_container_width=True)

        # 모드별 경제성 차이 차트
        fig_econ = go.Figure()
        colors = {"1기": "#4A90D9", "2기저부하": "#7ED321", "2기": "#FF6B35"}
        for label in ["1기", "2기저부하", "2기"]:
            col_name = f"경제성차이_{label}"
            if col_name in hourly_df.columns:
                fig_econ.add_trace(go.Scatter(
                    x=list(range(24)),
                    y=hourly_df[col_name],
                    mode="lines+markers",
                    name=label,
                    line=dict(color=colors.get(label, "#999"), width=2),
                ))
        fig_econ.add_hline(y=0, line_dash="dash", line_color="#999", opacity=0.5)
        fig_econ.update_layout(
            title="시간별 경제성 차이 (원/kWh) — 0 이상이면 가동 유리",
            xaxis_title="시간", yaxis_title="경제성 차이 (원/kWh)",
            height=400,
        )
        st.plotly_chart(fig_econ, use_container_width=True)

        # 최적모드 타임라인
        mode_color_map = {"1기": "#4A90D9", "2기저부하": "#7ED321", "2기": "#FF6B35", "정지": "#CCCCCC"}
        fig_mode = go.Figure()
        for hour in range(24):
            mode_name = hourly_df.iloc[hour]["최적모드"]
            fig_mode.add_trace(go.Bar(
                x=[hour], y=[1],
                marker_color=mode_color_map.get(mode_name, "#999"),
                name=mode_name,
                showlegend=(hour == 0 or hourly_df.iloc[hour - 1]["최적모드"] != mode_name),
                hovertext=f"{hour}시: {mode_name}",
            ))
        fig_mode.update_layout(
            title="시간별 최적 운전모드",
            xaxis_title="시간", yaxis=dict(visible=False),
            barmode="stack", height=200,
            showlegend=True,
        )
        st.plotly_chart(fig_mode, use_container_width=True)

        # 상세 테이블
        st.subheader("상세 경제성 테이블")
        st.dataframe(
            hourly_df.style.format(precision=2, na_rep="-"),
            use_container_width=True,
            height=600,
        )

        # CSV 다운로드
        csv_data = hourly_df.to_csv(index=False, encoding="utf-8-sig")
        st.download_button(
            "📥 경제성 테이블 CSV 다운로드",
            csv_data,
            file_name=f"경제성분석_{target_date}.csv",
            mime="text/csv",
        )

    else:
        st.warning("데이터를 로드하지 못했습니다. 데이터.csv 파일을 확인하세요.")


# ══════════════════════════════════════════════════════════════
# TAB 2: 이상구간 탐지
# ══════════════════════════════════════════════════════════════
with tab2:
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
# TAB 3: ML 모델 성능
# ══════════════════════════════════════════════════════════════
with tab3:
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
# TAB 4: 원시 데이터
# ══════════════════════════════════════════════════════════════
with tab4:
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
st.caption("LNG 발전소 경제성 자동판단 시스템 v1.0 | XGBoost 기반 ML 예측 + 동적 임계값 이상탐지")
