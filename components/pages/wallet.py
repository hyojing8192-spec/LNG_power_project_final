"""wallet.py — 경제성 분석 상세 페이지"""
from __future__ import annotations

import numpy as np
import plotly.graph_objects as go
import streamlit as st


def render(ctx: dict) -> None:
    """
    경제성 분석 상세 페이지 렌더링.

    ctx keys:
        data_loaded, has_real_smp, target_date, smp_series,
        hourly_df, thresholds, lng_price, lng_heat, exchange_rate, is_spot
    """
    data_loaded = ctx["data_loaded"]
    has_real_smp = ctx["has_real_smp"]

    if not data_loaded or not has_real_smp:
        st.warning("SMP 실데이터가 없어 경제성 분석을 수행할 수 없습니다.")
        return

    target_date = ctx["target_date"]
    smp_series = ctx["smp_series"]
    hourly_df = ctx["hourly_df"]
    thresholds = ctx["thresholds"]
    lng_price = ctx["lng_price"]

    # ── 제목 ────────────────────────────────────────────────────
    st.markdown(
        f"""
        <div class="primary-card">
          <div style="font-family:'Sora',sans-serif; font-size:18px; font-weight:700;">
            📈 경제성 분석
          </div>
          <div style="opacity:0.85; font-size:13px; font-family:'DM Sans',sans-serif; margin-top:4px;">
            {target_date} 기준 24시간 경제성 분석
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── 핵심 지표 KPI ────────────────────────────────────────────
    avg_smp = float(np.mean(smp_series))
    best_modes = hourly_df["최적모드"].value_counts()
    top_mode = best_modes.index[0] if len(best_modes) > 0 else "-"

    kc1, kc2, kc3, kc4 = st.columns(4)
    with kc1:
        st.markdown(
            f"""
            <div class="kpi-card">
              <div class="kpi-value">{avg_smp:.1f}</div>
              <div class="kpi-label">평균 SMP (원/kWh)</div>
            </div>
            """, unsafe_allow_html=True,
        )
    with kc2:
        st.markdown(
            f"""
            <div class="kpi-card">
              <div class="kpi-value">{thresholds['smp_low']:.1f}</div>
              <div class="kpi-label">LNG발전 BEP 임계 SMP (원/kWh)</div>
            </div>
            """, unsafe_allow_html=True,
        )
    with kc3:
        st.markdown(
            f"""
            <div class="kpi-card">
              <div class="kpi-value">{thresholds['smp_high']:.1f}</div>
              <div class="kpi-label">기력발전 BEP 임계 SMP (원/kWh)</div>
            </div>
            """, unsafe_allow_html=True,
        )
    with kc4:
        st.markdown(
            f"""
            <div class="kpi-card">
              <div class="kpi-value" style="font-size:20px;">{top_mode}</div>
              <div class="kpi-label">최다 최적모드</div>
            </div>
            """, unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── SMP 시계열 차트 ──────────────────────────────────────────
    st.markdown('<div class="glass-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">시간별 SMP 및 BEP 임계선</div>', unsafe_allow_html=True)

    fig_smp = go.Figure()
    fig_smp.add_trace(go.Scatter(
        x=list(range(24)), y=smp_series,
        mode="lines+markers", name="SMP",
        line=dict(color="#4F46E5", width=2.5),
        marker=dict(size=6, color="#4F46E5"),
    ))
    fig_smp.add_hline(
        y=thresholds["smp_low"], line_dash="dash", line_color="#F472B6",
        annotation_text=f"LNG발전 BEP {thresholds['smp_low']:.1f}원",
        annotation_font_color="#F472B6",
    )
    fig_smp.add_hline(
        y=thresholds["smp_high"], line_dash="dash", line_color="#F59E0B",
        annotation_text=f"기력발전 BEP {thresholds['smp_high']:.1f}원",
        annotation_font_color="#F59E0B",
    )
    fig_smp.update_layout(
        xaxis_title="시간", yaxis_title="원/kWh", height=400,
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=20, b=40),
    )
    fig_smp.update_xaxes(showgrid=True, gridcolor="rgba(99,102,241,0.1)")
    fig_smp.update_yaxes(showgrid=True, gridcolor="rgba(99,102,241,0.1)")
    st.plotly_chart(fig_smp, use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

    # ── 상세 테이블 ──────────────────────────────────────────────
    st.markdown('<div class="glass-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">24시간 경제성 상세 테이블</div>', unsafe_allow_html=True)
    st.dataframe(
        hourly_df.style.format(precision=2, na_rep="-"),
        use_container_width=True, height=600,
    )

    csv_data = hourly_df.to_csv(index=False, encoding="utf-8-sig")
    st.download_button(
        "📥 경제성 테이블 CSV 다운로드",
        csv_data,
        file_name=f"경제성분석_{target_date}.csv",
        mime="text/csv",
    )
    st.markdown("</div>", unsafe_allow_html=True)
