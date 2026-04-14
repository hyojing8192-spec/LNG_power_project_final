"""dashboard.py — Dashboard 페이지 (종합화면)"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from economics_engine import build_hourly_table
from guidance_generator import generate_full_guidance
from ml_predictor import predict_day
from anomaly_detector import calc_smp_thresholds
from economics_engine import get_elec_price


_WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]
_MODE_DISPLAY = {
    "2기": "2기 full",
    "2기저부하": "2기 저부하",
    "1기": "1기 full",
    "정지": "정지",
}
NIGHT_HOURS = list(range(22, 24)) + list(range(0, 8))
DAY_HOURS = list(range(8, 22))


def render(ctx: dict) -> None:
    """
    Dashboard 페이지 렌더링.

    ctx keys:
        models, target_date, smp_series, smp_source, has_real_smp,
        lng_price, lng_heat, exchange_rate, is_spot,
        all_smp, display_dates, hourly_df, thresholds, pred_results,
        data_loaded
    """
    data_loaded = ctx["data_loaded"]
    has_real_smp = ctx["has_real_smp"]
    target_date = ctx["target_date"]
    smp_series = ctx["smp_series"]
    lng_price = ctx["lng_price"]
    lng_heat = ctx["lng_heat"]
    exchange_rate = ctx["exchange_rate"]
    is_spot = ctx["is_spot"]
    hourly_df = ctx["hourly_df"]
    thresholds = ctx["thresholds"]
    all_smp = ctx["all_smp"]
    display_dates = ctx["display_dates"]
    models = ctx["models"]

    if not data_loaded:
        st.warning("데이터를 로드하지 못했습니다. 데이터.csv 파일을 확인하세요.")
        return

    if not has_real_smp:
        weekday_kr = _WEEKDAY_KR[target_date.weekday()]
        st.markdown(
            f"""
            <div style="background:linear-gradient(135deg,#fff5f5,#ffe3e3);
                        border:2px solid #EF4444; border-radius:20px;
                        padding:40px; text-align:center; margin:20px 0;">
              <div style="font-family:'Sora',sans-serif; font-size:22px; font-weight:700;
                          color:#EF4444; margin-bottom:12px;">
                {target_date.month}월{target_date.day}일({weekday_kr}) — 산출불가
              </div>
              <p style="color:#6B7280; font-size:1.05em; font-family:'DM Sans',sans-serif;">
                해당 날짜의 SMP 데이터가 아직 공시되지 않아<br>경제성 판단을 수행할 수 없습니다.
              </p>
              <p style="margin-top:16px; color:#9CA3AF; font-size:0.9em;">
                SMP 소스: {ctx['smp_source']}<br>
                스케줄러가 SMP를 수집하면 자동으로 갱신됩니다.
              </p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    weekday_kr = _WEEKDAY_KR[target_date.weekday()]
    price_type = "Spot" if is_spot else "사용단가"
    avg_smp_val = float(np.mean(smp_series))
    best_modes = hourly_df["최적모드"].value_counts()
    top_mode = best_modes.index[0] if len(best_modes) > 0 else "-"

    # ── Primary KPI 카드 ────────────────────────────────────────
    st.markdown(
        f"""
        <div class="primary-card">
          <div style="font-family:'Sora',sans-serif; font-size:20px; font-weight:700;
                      margin-bottom:6px;">LNG발전 가동 경제성 판단 결과</div>
          <div style="opacity:0.85; font-family:'DM Sans',sans-serif; font-size:14px;">
            {target_date.month}월 {target_date.day}일({weekday_kr}) 기준 &nbsp;|&nbsp;
            LNG {lng_price} $/MMBtu ({price_type}) &nbsp;|&nbsp;
            환율 {exchange_rate:,.0f}원/$
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── 4개 KPI 글래스 카드 ─────────────────────────────────────
    kc1, kc2, kc3, kc4 = st.columns(4)

    with kc1:
        st.markdown(
            f"""
            <div class="kpi-card">
              <div class="kpi-value">{lng_price} <span style="font-size:14px;">$/MMBtu</span></div>
              <div class="kpi-label">LNG 가격 ({price_type})</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with kc2:
        st.markdown(
            f"""
            <div class="kpi-card">
              <div class="kpi-value">{exchange_rate:,.0f} <span style="font-size:14px;">원/$</span></div>
              <div class="kpi-label">환율</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with kc3:
        st.markdown(
            f"""
            <div class="kpi-card">
              <div class="kpi-value">{avg_smp_val:.1f} <span style="font-size:14px;">원/kWh</span></div>
              <div class="kpi-label">평균 SMP</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with kc4:
        st.markdown(
            f"""
            <div class="kpi-card">
              <div class="kpi-value" style="font-size:20px;">{top_mode}</div>
              <div class="kpi-label">최적 운전모드 (최다)</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── 종합 시계열 차트 ─────────────────────────────────────────
    date_pairs = [
        (display_dates[i], display_dates[i + 1])
        for i in range(len(display_dates) - 1)
    ]

    x_labels: list[str] = []
    smp_chart: list = []
    bep_vals: list = []

    for pair_idx, (_d_from, _d_to) in enumerate(date_pairs):
        _smp_from, _, _ok_from = all_smp[_d_from]
        _smp_to, _, _ok_to = all_smp[_d_to]

        if pair_idx == 0 or _d_from != date_pairs[pair_idx - 1][1]:
            for h in [22, 23]:
                x_labels.append(f"{_d_from.month}/{_d_from.day} {h:02d}시")
                if _ok_from:
                    smp_chart.append(_smp_from[h])
                    mode = hourly_df["최적모드"].iloc[h]
                    bep_col = _bep_col_for_mode(mode, hourly_df)
                    bep_vals.append(bep_col.iloc[h] if bep_col is not None else 0)
                else:
                    smp_chart.append(None)
                    bep_vals.append(None)

        for h in range(0, 22):
            x_labels.append(f"{_d_to.month}/{_d_to.day} {h:02d}시")
            if _ok_to:
                smp_chart.append(_smp_to[h])
                mode = hourly_df["최적모드"].iloc[h]
                bep_col = _bep_col_for_mode(mode, hourly_df)
                bep_vals.append(bep_col.iloc[h] if bep_col is not None else 0)
            else:
                smp_chart.append(None)
                bep_vals.append(None)

    chart_len = len(x_labels)
    chart_height = max(450, min(600, 350 + chart_len * 2))

    fig_main = make_subplots(specs=[[{"secondary_y": True}]])

    fig_main.add_trace(
        go.Bar(
            x=x_labels, y=bep_vals, name="LNG발전 BEP ($/MMBtu)",
            marker_color="rgba(99,102,241,0.35)", opacity=0.85,
            text=[f"{b:.1f}" if b is not None else "" for b in bep_vals],
            textposition="outside",
            textfont=dict(size=11 if chart_len <= 30 else 8, color="#4F46E5"),
        ),
        secondary_y=True,
    )
    fig_main.add_trace(
        go.Scatter(
            x=x_labels, y=smp_chart, mode="lines+markers",
            name="SMP (원/kWh)",
            line=dict(color="#4F46E5", width=3),
            marker=dict(size=5), connectgaps=False,
        ),
        secondary_y=False,
    )
    fig_main.add_trace(
        go.Scatter(
            x=x_labels, y=[lng_price] * chart_len, mode="lines",
            name=f"LNG가격 {lng_price} $/MMBtu",
            line=dict(color="#F472B6", width=2.5, dash="dash"),
        ),
        secondary_y=True,
    )

    for _d in display_dates[1:]:
        _boundary = f"{_d.month}/{_d.day} 00시"
        if _boundary in x_labels:
            fig_main.add_vline(x=_boundary, line_dash="dot", line_color="rgba(99,102,241,0.3)", opacity=0.7)

    fig_main.update_layout(
        title=dict(text="SMP vs LNG발전 BEP vs LNG가격", x=0.5, xanchor="center",
                   font=dict(family="Sora", size=15, color="#1E1B4B")),
        height=chart_height,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
        margin=dict(t=80, b=100), bargap=0.25,
        xaxis=dict(tickangle=-45, dtick=2 if chart_len > 48 else 1),
    )
    fig_main.update_xaxes(showgrid=True, gridcolor="rgba(99,102,241,0.1)")
    fig_main.update_yaxes(
        title_text="SMP (원/kWh)", secondary_y=False,
        showgrid=True, gridcolor="rgba(99,102,241,0.1)",
    )
    fig_main.update_yaxes(title_text="BEP / LNG가격 ($/MMBtu)", secondary_y=True)

    st.markdown('<div class="glass-card">', unsafe_allow_html=True)
    st.markdown(
        '<div class="section-title">SMP vs LNG발전 BEP vs LNG가격</div>',
        unsafe_allow_html=True,
    )
    st.plotly_chart(fig_main, use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

    # ── 야간/주간 가동계획 테이블 ────────────────────────────────
    for _d_night, _d_day in date_pairs:
        wk_night = _WEEKDAY_KR[_d_night.weekday()]
        wk_day = _WEEKDAY_KR[_d_day.weekday()]

        _smp_night, _src_night, _ok_night = all_smp[_d_night]
        _smp_day, _src_day, _ok_day = all_smp[_d_day]

        # 야간 테이블
        st.markdown(
            f"""
            <div class="glass-card" style="margin-bottom:8px;">
              <div class="section-title">
                야간 {_d_night.month}월{_d_night.day}일({wk_night}) 22시 ~
                {_d_day.month}월{_d_day.day}일({wk_day}) 08시
              </div>
            """,
            unsafe_allow_html=True,
        )

        if _ok_night:
            _g_night = generate_full_guidance(
                target_date=_d_night, hourly_df=hourly_df,
                smp_series=_smp_night, thresholds=thresholds,
                lng_price=lng_price, exchange_rate=exchange_rate,
                lng_heat=lng_heat, is_spot=is_spot,
            )
            _plan_night = _g_night["hourly_plan"]

            night_rows: dict[str, list] = {
                "최적운전모드": [], "SMP(원/kWh)": [], "수전단가(원/kWh)": [],
                "대체단가(원/kWh)": [], "LNG발전 BEP($/MMBtu)": [], "경제성(억원)": [],
            }
            night_headers = [f"{h:02d}시" for h in NIGHT_HOURS]

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
                    for k in night_rows:
                        night_rows[k].append("-")
                    continue

                p = _plan_night[h]
                night_rows["최적운전모드"].append(_MODE_DISPLAY.get(p["best_mode"], p["best_mode"]))
                night_rows["SMP(원/kWh)"].append(f"{smp_val:.1f}")
                night_rows["수전단가(원/kWh)"].append(f"{hourly_df['수전단가(원/kWh)'].iloc[h]:.1f}")
                night_rows["LNG발전 BEP($/MMBtu)"].append(f"{p['bep']:.2f}" if p["bep"] is not None else "-")
                night_rows["경제성(억원)"].append(f"{p['econ_bil']:.3f}" if p["econ_bil"] is not None else "-")

                elec_val = hourly_df["수전단가(원/kWh)"].iloc[h]
                night_rows["대체단가(원/kWh)"].append(_calc_alt_price(p["best_mode"], smp_val, elec_val))

            night_df = pd.DataFrame(night_rows, index=night_headers).T
            st.dataframe(
                night_df.style.apply(_style_summary, axis=None),
                use_container_width=True, height=280,
            )
        else:
            st.info(f"{_d_night.month}/{_d_night.day} SMP 미공시 — 산출불가")

        st.markdown("</div>", unsafe_allow_html=True)

        # 주간 테이블
        st.markdown(
            f"""
            <div class="glass-card" style="margin-bottom:8px;">
              <div class="section-title">
                주간 {_d_day.month}월{_d_day.day}일({wk_day}) 08시 ~ 22시
              </div>
            """,
            unsafe_allow_html=True,
        )

        if _ok_day:
            _pred_day = predict_day(
                models, _d_day, _smp_day, lng_price, lng_heat, exchange_rate,
                elec_price_fn=get_elec_price,
            )
            _hourly_day = build_hourly_table(
                target_date=_d_day, smp_series=_smp_day, lng_price=lng_price,
                lng_heat=lng_heat, exchange_rate=exchange_rate,
                pred_results=_pred_day, is_spot=is_spot,
                smp_high_threshold=thresholds["smp_high"],
            )
            _g_day = generate_full_guidance(
                target_date=_d_day, hourly_df=_hourly_day,
                smp_series=_smp_day, thresholds=thresholds,
                lng_price=lng_price, exchange_rate=exchange_rate,
                lng_heat=lng_heat, is_spot=is_spot,
            )
            _plan_day = _g_day["hourly_plan"]

            day_rows: dict[str, list] = {
                "최적운전모드": [], "SMP(원/kWh)": [], "수전단가(원/kWh)": [],
                "대체단가(원/kWh)": [], "LNG발전 BEP($/MMBtu)": [], "경제성(억원)": [],
            }
            day_headers = [f"{h:02d}시" for h in DAY_HOURS]

            for h in DAY_HOURS:
                smp_val = _smp_day[h]
                p = _plan_day[h]
                day_rows["최적운전모드"].append(_MODE_DISPLAY.get(p["best_mode"], p["best_mode"]))
                day_rows["SMP(원/kWh)"].append(f"{smp_val:.1f}")
                day_rows["수전단가(원/kWh)"].append(f"{_hourly_day['수전단가(원/kWh)'].iloc[h]:.1f}")
                day_rows["LNG발전 BEP($/MMBtu)"].append(f"{p['bep']:.2f}" if p["bep"] is not None else "-")
                day_rows["경제성(억원)"].append(f"{p['econ_bil']:.3f}" if p["econ_bil"] is not None else "-")

                elec_val = _hourly_day["수전단가(원/kWh)"].iloc[h]
                day_rows["대체단가(원/kWh)"].append(_calc_alt_price(p["best_mode"], smp_val, elec_val))

            day_df = pd.DataFrame(day_rows, index=day_headers).T
            st.dataframe(
                day_df.style.apply(_style_summary, axis=None),
                use_container_width=True, height=280,
            )
        else:
            st.info(f"{_d_day.month}/{_d_day.day} SMP 미공시 — 산출불가")

        st.markdown("</div>", unsafe_allow_html=True)


# ── 내부 헬퍼 ───────────────────────────────────────────────────

def _bep_col_for_mode(mode: str, hourly_df: pd.DataFrame):
    if mode == "1기" and "BEP_1기($/MMBtu)" in hourly_df.columns:
        return hourly_df["BEP_1기($/MMBtu)"]
    if mode == "2기저부하" and "BEP_2기저부하($/MMBtu)" in hourly_df.columns:
        return hourly_df["BEP_2기저부하($/MMBtu)"]
    if mode == "2기" and "BEP_2기($/MMBtu)" in hourly_df.columns:
        return hourly_df["BEP_2기($/MMBtu)"]
    return None


def _calc_alt_price(mode: str, smp_val: float, elec_val: float) -> str:
    if mode == "2기":
        return f"{smp_val * 0.7 + elec_val * 0.3:.1f}"
    elif mode in ("2기저부하", "1기"):
        return f"{elec_val:.1f}"
    elif mode == "정지":
        return f"{smp_val * 0.7 + elec_val * 0.3:.1f}"
    return "-"


def _style_summary(df: pd.DataFrame) -> pd.DataFrame:
    styles = pd.DataFrame("", index=df.index, columns=df.columns)
    if "최적운전모드" in df.index:
        for col in df.columns:
            val = str(df.loc["최적운전모드", col])
            if "full" in val:
                styles.loc["최적운전모드", col] = "background-color:#DBEAFE; font-weight:bold"
            elif "저부하" in val:
                styles.loc["최적운전모드", col] = "background-color:#DCFCE7; font-weight:bold"
            elif "1기" in val:
                styles.loc["최적운전모드", col] = "background-color:#FEF9C3; font-weight:bold"
            elif "정지" in val:
                styles.loc["최적운전모드", col] = "background-color:#FEE2E2; font-weight:bold"
    return styles
