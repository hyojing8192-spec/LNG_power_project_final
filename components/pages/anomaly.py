"""anomaly.py — 이상구간 탐지 페이지"""
from __future__ import annotations

from datetime import datetime

import plotly.graph_objects as go
import streamlit as st

from anomaly_detector import (
    build_econ_change_chart,
    build_smp_chart,
    calc_smp_thresholds,
    detect_econ_change,
    detect_smp_anomalies,
)


def render(ctx: dict) -> None:
    """
    이상구간 탐지 페이지 렌더링.

    ctx keys:
        data_loaded, has_real_smp, target_date, raw_df,
        hourly_df, lng_price, lng_heat, exchange_rate, is_spot
    """
    data_loaded = ctx["data_loaded"]
    has_real_smp = ctx["has_real_smp"]

    if not data_loaded:
        st.warning("데이터를 로드하지 못했습니다.")
        return

    target_date = ctx["target_date"]
    raw_df = ctx["raw_df"]
    hourly_df = ctx["hourly_df"]
    lng_price = ctx["lng_price"]
    lng_heat = ctx["lng_heat"]
    exchange_rate = ctx["exchange_rate"]
    is_spot = ctx["is_spot"]

    # ── 제목 카드 ────────────────────────────────────────────────
    st.markdown(
        """
        <div class="primary-card">
          <div style="font-family:'Sora',sans-serif; font-size:18px; font-weight:700;">
            🔍 이상구간 탐지
          </div>
          <div style="opacity:0.85; font-size:13px; font-family:'DM Sans',sans-serif; margin-top:4px;">
            SMP 이상구간 및 경제성 급변 탐지
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if not has_real_smp or raw_df.empty or "smp" not in raw_df.columns:
        st.warning("SMP 데이터가 없습니다.")
        return

    thresholds = calc_smp_thresholds(lng_price, lng_heat, exchange_rate, is_spot=is_spot)
    anomalies = detect_smp_anomalies(
        raw_df,
        smp_low=thresholds["smp_low"],
        smp_high=thresholds["smp_high"],
    )

    # ── KPI ─────────────────────────────────────────────────────
    kc1, kc2, kc3 = st.columns(3)
    n_zero = len(anomalies[anomalies["anomaly_type"] == "SMP 제로"]) if not anomalies.empty else 0
    n_low = len(anomalies[anomalies["anomaly_type"] == "SMP 경제성 한계"]) if not anomalies.empty else 0
    n_high = len(anomalies[anomalies["anomaly_type"] == "SMP 과대"]) if not anomalies.empty else 0

    with kc1:
        st.markdown(
            f"""
            <div class="kpi-card">
              <div class="kpi-value" style="color:#6B7280;">{n_zero}</div>
              <div class="kpi-label">SMP 제로 건수</div>
            </div>
            """, unsafe_allow_html=True,
        )
    with kc2:
        color = "#EF4444" if n_low > 0 else "#10B981"
        st.markdown(
            f"""
            <div class="kpi-card">
              <div class="kpi-value" style="color:{color};">{n_low}</div>
              <div class="kpi-label">SMP 경제성 한계</div>
            </div>
            """, unsafe_allow_html=True,
        )
    with kc3:
        color = "#F59E0B" if n_high > 0 else "#10B981"
        st.markdown(
            f"""
            <div class="kpi-card">
              <div class="kpi-value" style="color:{color};">{n_high}</div>
              <div class="kpi-label">SMP 과대</div>
            </div>
            """, unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── SMP 이상구간 차트 ────────────────────────────────────────
    st.markdown('<div class="glass-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">SMP 이상구간 차트</div>', unsafe_allow_html=True)

    fig = build_smp_chart(
        raw_df, anomalies,
        smp_low=thresholds["smp_low"],
        smp_high=thresholds["smp_high"],
        is_spot=is_spot,
    )
    # 투명 배경 적용
    fig.update_layout(
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True)

    if not anomalies.empty:
        st.markdown('<div class="section-title" style="font-size:14px;">이상구간 상세</div>', unsafe_allow_html=True)
        st.dataframe(anomalies, use_container_width=True)
    else:
        st.success("이상구간이 감지되지 않았습니다.")

    st.markdown("</div>", unsafe_allow_html=True)

    # ── 경제성 급변 탐지 ──────────────────────────────────────────
    if hourly_df is not None and "경제성차이_2기" in hourly_df.columns:
        import pandas as pd
        st.markdown('<div class="glass-card">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">경제성 급변 구간 탐지</div>', unsafe_allow_html=True)

        econ_df = pd.DataFrame({
            "datetime": pd.date_range(
                start=datetime.combine(target_date, datetime.min.time()),
                periods=24, freq="h",
            ),
            "econ_diff_2gi": hourly_df["경제성차이_2기"].values,
        })
        change_df = detect_econ_change(econ_df, econ_col="econ_diff_2gi")
        fig_econ_change = build_econ_change_chart(econ_df, change_df, econ_col="econ_diff_2gi")
        fig_econ_change.update_layout(
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_econ_change, use_container_width=True)

        if not change_df.empty:
            st.dataframe(change_df, use_container_width=True)
        else:
            st.success("급변 구간이 감지되지 않았습니다.")

        st.markdown("</div>", unsafe_allow_html=True)
