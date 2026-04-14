"""rawdata.py — 원시 데이터 페이지"""
from __future__ import annotations

import numpy as np
import plotly.graph_objects as go
import streamlit as st


def render(ctx: dict) -> None:
    """
    원시 데이터 페이지 렌더링.

    ctx keys:
        data_loaded, raw_df
    """
    data_loaded = ctx["data_loaded"]
    raw_df = ctx.get("raw_df")

    # ── 제목 카드 ────────────────────────────────────────────────
    st.markdown(
        """
        <div class="primary-card">
          <div style="font-family:'Sora',sans-serif; font-size:18px; font-weight:700;">
            📁 원시 데이터
          </div>
          <div style="opacity:0.85; font-size:13px; font-family:'DM Sans',sans-serif; margin-top:4px;">
            학습 데이터 미리보기 및 통계
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if not data_loaded or raw_df is None or raw_df.empty:
        st.warning("데이터를 로드하지 못했습니다.")
        return

    # ── 기본 정보 ────────────────────────────────────────────────
    kc1, kc2 = st.columns(2)
    with kc1:
        st.markdown(
            f"""
            <div class="kpi-card">
              <div class="kpi-value">{len(raw_df):,}</div>
              <div class="kpi-label">전체 행 수</div>
            </div>
            """, unsafe_allow_html=True,
        )
    with kc2:
        st.markdown(
            f"""
            <div class="kpi-card">
              <div class="kpi-value">{len(raw_df.columns)}</div>
              <div class="kpi-label">컬럼 수</div>
            </div>
            """, unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── 기본 통계 ────────────────────────────────────────────────
    st.markdown('<div class="glass-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">기본 통계</div>', unsafe_allow_html=True)

    num_cols = raw_df.select_dtypes(include=[np.number]).columns.tolist()
    if num_cols:
        st.dataframe(
            raw_df[num_cols].describe().T.style.format(precision=2),
            use_container_width=True,
        )
    st.markdown("</div>", unsafe_allow_html=True)

    # ── 데이터 미리보기 ─────────────────────────────────────────
    st.markdown('<div class="glass-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">데이터 미리보기 (상위 100행)</div>', unsafe_allow_html=True)
    st.dataframe(raw_df.head(100), use_container_width=True, height=400)
    st.markdown("</div>", unsafe_allow_html=True)

    # ── SMP 분포 히스토그램 ──────────────────────────────────────
    if "smp" in raw_df.columns:
        st.markdown('<div class="glass-card">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">SMP 분포 히스토그램</div>', unsafe_allow_html=True)

        fig_hist = go.Figure()
        smp_valid = raw_df["smp"].dropna()
        fig_hist.add_trace(go.Histogram(
            x=smp_valid, nbinsx=50, name="SMP 분포",
            marker_color="rgba(99,102,241,0.7)",
        ))
        fig_hist.update_layout(
            xaxis_title="SMP (원/kWh)",
            yaxis_title="빈도",
            height=350,
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(t=10),
        )
        fig_hist.update_xaxes(showgrid=True, gridcolor="rgba(99,102,241,0.1)")
        fig_hist.update_yaxes(showgrid=True, gridcolor="rgba(99,102,241,0.1)")
        st.plotly_chart(fig_hist, use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)
