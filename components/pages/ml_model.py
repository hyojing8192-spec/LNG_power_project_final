"""ml_model.py — ML 모델 성능 페이지"""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from config import MODES, MODE_LABELS
from ml_predictor import retrain


def render(ctx: dict) -> None:
    """
    ML 모델 성능 페이지 렌더링.

    ctx keys:
        data_loaded, metrics
    """
    data_loaded = ctx["data_loaded"]
    metrics = ctx.get("metrics", {})

    # ── 제목 카드 ────────────────────────────────────────────────
    st.markdown(
        """
        <div class="primary-card">
          <div style="font-family:'Sora',sans-serif; font-size:18px; font-weight:700;">
            🤖 ML 모델
          </div>
          <div style="opacity:0.85; font-size:13px; font-family:'DM Sans',sans-serif; margin-top:4px;">
            XGBoost 모델 성능 요약 및 재학습
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if not data_loaded or not metrics:
        st.warning("모델이 로드되지 않았습니다.")
        return

    # ── 분할 정보 ────────────────────────────────────────────────
    split_info = metrics.get("_split", {})
    if split_info:
        kc1, kc2, kc3 = st.columns(3)
        with kc1:
            st.markdown(
                f"""
                <div class="kpi-card">
                  <div class="kpi-value">{split_info.get('n_all', '-'):,}</div>
                  <div class="kpi-label">전체 데이터 (행)</div>
                </div>
                """, unsafe_allow_html=True,
            )
        with kc2:
            st.markdown(
                f"""
                <div class="kpi-card">
                  <div class="kpi-value">{split_info.get('n_train', '-'):,}</div>
                  <div class="kpi-label">학습 데이터 (행)</div>
                </div>
                """, unsafe_allow_html=True,
            )
        with kc3:
            st.markdown(
                f"""
                <div class="kpi-card">
                  <div class="kpi-value">{split_info.get('n_test', '-'):,}</div>
                  <div class="kpi-label">테스트 데이터 (행)</div>
                </div>
                """, unsafe_allow_html=True,
            )
        st.markdown("<br>", unsafe_allow_html=True)

    # ── 모드별 성능 테이블 ────────────────────────────────────────
    import pandas as pd

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
        st.markdown('<div class="glass-card">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">모드별 성능 지표</div>', unsafe_allow_html=True)

        perf_df = pd.DataFrame(perf_rows)
        st.dataframe(
            perf_df.style.format(precision=4, na_rep="-")
                .background_gradient(subset=["Train R²", "CV R²"], cmap="RdYlGn", vmin=0, vmax=1),
            use_container_width=True,
        )
        st.markdown("</div>", unsafe_allow_html=True)

        # ── R² 시각화 ────────────────────────────────────────────
        st.markdown('<div class="glass-card">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">모드별 CV R² 점수</div>', unsafe_allow_html=True)

        fig_r2 = go.Figure()
        colors = ["#4F46E5", "#10B981", "#F472B6"]
        for idx, target in enumerate(["export", "import", "efficiency"]):
            sub = perf_df[perf_df["타깃"] == target]
            fig_r2.add_trace(go.Bar(
                x=sub["운전모드"],
                y=sub["CV R²"],
                name=target,
                marker_color=colors[idx % len(colors)],
            ))
        fig_r2.update_layout(
            xaxis_title="운전모드", yaxis_title="R²",
            barmode="group", height=400,
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(t=20),
        )
        fig_r2.update_xaxes(showgrid=False)
        fig_r2.update_yaxes(showgrid=True, gridcolor="rgba(99,102,241,0.1)")
        st.plotly_chart(fig_r2, use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)
    else:
        st.info("학습된 모델 메트릭이 없습니다.")

    # ── 재학습 ──────────────────────────────────────────────────
    st.markdown('<div class="glass-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">모델 재학습</div>', unsafe_allow_html=True)

    if st.button("🔄 모델 재학습", help="전체 데이터로 XGBoost 모델을 재학습합니다"):
        with st.spinner("재학습 중... (1~2분 소요)"):
            retrain()
            st.cache_resource.clear()
            st.success("재학습 완료! 페이지를 새로고침합니다.")
            st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)
