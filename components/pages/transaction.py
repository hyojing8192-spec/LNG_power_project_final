"""transaction.py — 가동 가이던스 상세 페이지"""
from __future__ import annotations

from collections import Counter
from datetime import date

import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from guidance_generator import generate_full_guidance
from ml_predictor import predict_day
from economics_engine import build_hourly_table, get_elec_price


_WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]
DAY_HOURS = list(range(8, 22))
NIGHT_HOURS = list(range(22, 24)) + list(range(0, 8))


def render(ctx: dict) -> None:
    """
    가동 가이던스 상세 페이지 렌더링.

    ctx keys:
        data_loaded, has_real_smp, target_date, smp_series,
        hourly_df, thresholds, lng_price, lng_heat, exchange_rate, is_spot
    """
    data_loaded = ctx["data_loaded"]
    has_real_smp = ctx["has_real_smp"]

    if not data_loaded or not has_real_smp:
        st.warning("SMP 실데이터가 없어 가이던스를 생성할 수 없습니다.")
        return

    target_date: date = ctx["target_date"]
    smp_series = ctx["smp_series"]
    hourly_df = ctx["hourly_df"]
    thresholds = ctx["thresholds"]
    lng_price = ctx["lng_price"]
    lng_heat = ctx["lng_heat"]
    exchange_rate = ctx["exchange_rate"]
    is_spot = ctx["is_spot"]

    weekday_kr = _WEEKDAY_KR[target_date.weekday()]
    price_type = "Spot" if is_spot else "사용단가"

    # ── D일 가이던스 생성 ────────────────────────────────────────
    guidance = generate_full_guidance(
        target_date=target_date, hourly_df=hourly_df,
        smp_series=smp_series, thresholds=thresholds,
        lng_price=lng_price, exchange_rate=exchange_rate,
        lng_heat=lng_heat, is_spot=is_spot,
    )
    summary = guidance["daily_summary"]
    plan = guidance["hourly_plan"]

    # ── D+1 SMP 공시 여부 확인 (종합장표와 동일 기준) ───────────
    all_smp = ctx.get("all_smp", {})
    display_dates = ctx.get("display_dates", [target_date])
    models = ctx.get("models", {})

    _next_date = display_dates[1] if len(display_dates) > 1 else None
    _next_smp_raw = (
        all_smp.get(_next_date, ([float("nan")] * 24, "미공시", False))
        if _next_date else ([float("nan")] * 24, "미공시", False)
    )
    _next_smp_ok = bool(_next_date and _next_smp_raw[2])
    _next_smp_list = _next_smp_raw[0]
    _next_label = f"{_next_date.month}/{_next_date.day}" if _next_date else "익일"

    # D+1 SMP가 있는 경우에만 익일 가이던스 별도 생성
    _plan_next = None
    _plan_next_df = None
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
            import pandas as _pd_tmp
            _plan_next_df = _pd_tmp.DataFrame(_plan_next)
        except Exception:
            _next_smp_ok = False

    # ── 제목 카드 ────────────────────────────────────────────────
    st.markdown(
        f"""
        <div class="primary-card">
          <div style="font-family:'Sora',sans-serif; font-size:18px; font-weight:700;">
            📋 가동 가이던스
          </div>
          <div style="opacity:0.85; font-size:13px; font-family:'DM Sans',sans-serif; margin-top:4px;">
            {target_date} ({weekday_kr}) &nbsp;|&nbsp;
            LNG {lng_price} $/MMBtu ({price_type}) &nbsp;|&nbsp;
            환율 {exchange_rate:,.0f}원/$ &nbsp;|&nbsp;
            열량 {lng_heat} Mcal/Nm³
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── 상단 KPI ─────────────────────────────────────────────────
    kc1, kc2, kc3, kc4 = st.columns(4)
    n_anomaly = sum(len(v) for v in summary["anomaly_hours"].values())

    with kc1:
        st.markdown(
            f"""
            <div class="kpi-card">
              <div class="kpi-value">{summary['smp_avg']:.1f}</div>
              <div class="kpi-label">평균 SMP (원/kWh)</div>
            </div>
            """, unsafe_allow_html=True,
        )
    with kc2:
        st.markdown(
            f"""
            <div class="kpi-card">
              <div class="kpi-value" style="font-size:20px;">{summary['best_overall']}</div>
              <div class="kpi-label">최다 최적모드</div>
            </div>
            """, unsafe_allow_html=True,
        )
    with kc3:
        econ_val = summary["total_econ_best"]
        badge_cls = "kpi-badge-pos" if econ_val >= 0 else "kpi-badge-neg"
        st.markdown(
            f"""
            <div class="kpi-card">
              <div class="kpi-value">{econ_val:+.3f}</div>
              <div class="kpi-label">일일 경제성 (억원)</div>
            </div>
            """, unsafe_allow_html=True,
        )
    with kc4:
        anomaly_color = "#10B981" if n_anomaly == 0 else "#EF4444"
        st.markdown(
            f"""
            <div class="kpi-card">
              <div class="kpi-value" style="color:{anomaly_color};">{n_anomaly}</div>
              <div class="kpi-label">이상구간 (시간)</div>
            </div>
            """, unsafe_allow_html=True,
        )

    # ── 종합 운전 권고 ───────────────────────────────────────────
    st.markdown("<br>", unsafe_allow_html=True)
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

    import pandas as pd
    plan_df_all = pd.DataFrame(plan)   # D일 SMP — 22~23시 표시용

    TONIGHT_HOURS = list(range(22, 24))   # D일 SMP
    LATE_NIGHT_HOURS = list(range(0, 8))  # D+1 SMP 필요

    # ── 야간 가이던스 (먼저 표시) ─────────────────────────────────
    st.markdown('<div class="glass-card">', unsafe_allow_html=True)
    st.markdown(
        '<div class="section-title">야간 운전 가이던스 (22:00 ~ 익일 08:00)</div>',
        unsafe_allow_html=True,
    )

    # 22~23시: 당일 D일 SMP (항상 가용)
    st.markdown(
        f"**당일 {target_date.month}/{target_date.day} 22:00 ~ 23:59 (D일 SMP 기준)**"
    )
    tonight_lines = _build_period_summary(TONIGHT_HOURS, plan, smp_series)
    for line in tonight_lines:
        st.markdown(f"- {line}")

    tonight_plan = plan_df_all[plan_df_all["hour"].isin(TONIGHT_HOURS)].copy()
    tonight_display = tonight_plan[["time_str", "smp", "best_mode", "action", "bep", "econ_bil", "note"]].copy()
    tonight_display.columns = ["시간", "SMP(원/kWh)", "최적모드", "판단", "BEP($/MMBtu)", "경제성(억)", "비고"]
    st.dataframe(
        tonight_display.style.apply(_style_action, axis=1)
            .format({"SMP(원/kWh)": "{:.1f}", "BEP($/MMBtu)": "{:.2f}", "경제성(억)": "{:.3f}"}, na_rep="-"),
        use_container_width=True,
        height=min(len(TONIGHT_HOURS) * 38 + 40, 130),
    )

    # 00~07시: D+1 SMP 기반 (없으면 산출불가)
    st.markdown(f"**익일 {_next_label} 00:00 ~ 08:00**")
    if not _next_smp_ok:
        st.info(f"⚠️ {_next_label} SMP 미공시 — 익일 새벽 가이던스 산출불가")
    else:
        late_night_plan = _plan_next_df[_plan_next_df["hour"].isin(LATE_NIGHT_HOURS)].copy()
        late_night_display = late_night_plan[["time_str", "smp", "best_mode", "action", "bep", "econ_bil", "note"]].copy()
        late_night_display.columns = ["시간", "SMP(원/kWh)", "최적모드", "판단", "BEP($/MMBtu)", "경제성(억)", "비고"]
        st.dataframe(
            late_night_display.style.apply(_style_action, axis=1)
                .format({"SMP(원/kWh)": "{:.1f}", "BEP($/MMBtu)": "{:.2f}", "경제성(억)": "{:.3f}"}, na_rep="-"),
            use_container_width=True,
            height=min(len(LATE_NIGHT_HOURS) * 38 + 40, 350),
        )

    # 야간 차트: D+1 있으면 혼합, 없으면 22~23시만
    if _next_smp_ok:
        _mixed_plan = list(plan)
        _mixed_smp = list(smp_series)
        for _h in LATE_NIGHT_HOURS:
            _mixed_plan[_h] = _plan_next[_h]
            _mixed_smp[_h] = _next_smp_list[_h]
        fig_night = _build_guidance_chart(
            NIGHT_HOURS, _mixed_plan, _mixed_smp, hourly_df, lng_price, thresholds,
            title=f"야간 (22시~익일08시) SMP vs BEP 경제성 판단",
        )
    else:
        fig_night = _build_guidance_chart(
            TONIGHT_HOURS, plan, smp_series, hourly_df, lng_price, thresholds,
            title=f"야간 (22~23시) SMP vs BEP 경제성 판단 — 익일 미공시",
        )
    st.plotly_chart(fig_night, use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

    # ── 주간 가이던스 (D+1 SMP 기반) ─────────────────────────────
    st.markdown('<div class="glass-card">', unsafe_allow_html=True)
    st.markdown(
        f'<div class="section-title">주간 운전 가이던스 (08:00 ~ 22:00)'
        f'&nbsp;<span style="font-size:12px;font-weight:400;color:#6B7280;">'
        f'[{_next_label} D+1 SMP]</span></div>',
        unsafe_allow_html=True,
    )

    if not _next_smp_ok:
        st.info(f"⚠️ {_next_label} SMP 미공시 — 주간 가이던스 산출불가")
    else:
        day_lines = _build_period_summary(DAY_HOURS, _plan_next, _next_smp_list)
        for line in day_lines:
            st.markdown(f"- {line}")

        day_plan = _plan_next_df[_plan_next_df["hour"].isin(DAY_HOURS)].copy()
        day_display = day_plan[["time_str", "smp", "best_mode", "action", "bep", "econ_bil", "note"]].copy()
        day_display.columns = ["시간", "SMP(원/kWh)", "최적모드", "판단", "BEP($/MMBtu)", "경제성(억)", "비고"]

        st.dataframe(
            day_display.style.apply(_style_action, axis=1)
                .format({"SMP(원/kWh)": "{:.1f}", "BEP($/MMBtu)": "{:.2f}", "경제성(억)": "{:.3f}"}, na_rep="-"),
            use_container_width=True,
            height=min(len(DAY_HOURS) * 38 + 40, 600),
        )

        fig_day = _build_guidance_chart(
            DAY_HOURS, _plan_next, _next_smp_list, _hourly_next, lng_price, thresholds,
            title=f"주간 (08~22시) SMP vs BEP 경제성 판단 [{_next_label}]",
        )
        st.plotly_chart(fig_day, use_container_width=True)

    st.markdown("</div>", unsafe_allow_html=True)

    # ── 일일 경제성 요약 ──────────────────────────────────────────
    st.markdown('<div class="glass-card">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">일일 경제성 요약</div>', unsafe_allow_html=True)

    es1, es2 = st.columns(2)
    with es1:
        st.markdown("**모드별 분포**")
        for mode_name, hours_count in summary["mode_dist"].items():
            st.write(f"- {mode_name}: {hours_count}시간")
        if summary["anomaly_hours"]:
            st.markdown("**이상구간**")
            for atype, hrs in summary["anomaly_hours"].items():
                hr_str = ", ".join(f"{h}시" for h in hrs)
                st.write(f"- {atype}: {hr_str}")

    with es2:
        import pandas as pd
        econ_summary_df = pd.DataFrame([
            {"운전모드": m, "경제성(억원)": v}
            for m, v in summary["econ_totals"].items()
        ])
        if not econ_summary_df.empty:
            fig_econ_bar = go.Figure(go.Bar(
                x=econ_summary_df["운전모드"],
                y=econ_summary_df["경제성(억원)"],
                marker_color=["#4F46E5", "#10B981", "#F472B6"][:len(econ_summary_df)],
                text=econ_summary_df["경제성(억원)"].apply(lambda x: f"{x:+.3f}"),
                textposition="outside",
            ))
            fig_econ_bar.update_layout(
                title="모드별 일일 경제성 합계",
                yaxis_title="억원", height=300, margin=dict(t=40),
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig_econ_bar, use_container_width=True)

    st.markdown("</div>", unsafe_allow_html=True)

    # ── 다운로드 ──────────────────────────────────────────────────
    st.markdown('<div class="glass-card">', unsafe_allow_html=True)
    col_dl1, col_dl2 = st.columns(2)
    with col_dl1:
        st.download_button(
            "가이던스 텍스트 리포트 다운로드",
            guidance["text_report"],
            file_name=f"가동가이던스_{target_date}.txt",
            mime="text/plain",
        )
    with col_dl2:
        full_plan_display = plan_df_all[["time_str", "smp", "best_mode", "action", "bep", "econ_bil", "note"]].copy()
        full_plan_display.columns = ["시간", "SMP(원/kWh)", "최적모드", "판단", "BEP($/MMBtu)", "경제성(억)", "비고"]
        plan_csv = full_plan_display.to_csv(index=False, encoding="utf-8-sig")
        st.download_button(
            "가동계획표 CSV 다운로드",
            plan_csv,
            file_name=f"가동계획표_{target_date}.csv",
            mime="text/csv",
        )
    st.markdown("</div>", unsafe_allow_html=True)

    # ── 카카오톡/이메일 전파 ─────────────────────────────────────
    kakao_msg = guidance.get("kakao_message", "")
    if kakao_msg:
        st.markdown('<div class="glass-card">', unsafe_allow_html=True)
        st.markdown('<div class="section-title">📣 전파 메시지 발송</div>', unsafe_allow_html=True)
        st.code(kakao_msg, language=None)

        send_col1, send_col2 = st.columns(2)

        # ── 카카오톡 발송 ─────────────────────────────────────
        with send_col1:
            if st.button("💬 카카오톡 발송", use_container_width=True, key="btn_kakao"):
                try:
                    from kakao_sender import send_kakao_guidance, KAKAO_REST_API_KEY
                    if not KAKAO_REST_API_KEY:
                        st.warning(
                            "카카오 API 키 미설정\n\n"
                            "`config.py`의 `KAKAO_REST_API_KEY`를 입력하고 "
                            "`python kakao_sender.py --auth`로 인증하세요."
                        )
                    else:
                        with st.spinner("카카오톡 발송 중..."):
                            ok = send_kakao_guidance(kakao_msg)
                        if ok:
                            st.success("카카오톡 발송 완료!")
                        else:
                            st.error(
                                "카카오톡 발송 실패\n\n"
                                "토큰이 만료됐을 수 있습니다. "
                                "`python kakao_sender.py --auth`로 재인증하세요."
                            )
                except Exception as _e:
                    st.error(f"카카오톡 오류: {_e}")

        # ── 이메일 발송 ───────────────────────────────────────
        with send_col2:
            if st.button("📧 이메일 발송", use_container_width=True, key="btn_mail"):
                try:
                    from mail_sender import send_daily_report, _is_configured
                    if not _is_configured():
                        st.warning(
                            "메일 설정 미완료\n\n"
                            "`config.py`의 `MAIL_SENDER_EMAIL`, "
                            "`MAIL_SENDER_PASSWORD`, `MAIL_RECIPIENTS`를 설정하세요."
                        )
                    else:
                        with st.spinner("이메일 발송 중..."):
                            ok = send_daily_report(
                                target_date=target_date,
                                summary=summary,
                                alerts=guidance.get("alerts", []),
                                hourly_plan=plan,
                                hourly_df=hourly_df,
                                text_report=guidance.get("text_report", ""),
                                smp_series=smp_series,
                                thresholds=thresholds,
                                next_day_plan=_plan_next,
                                next_day_smp=_next_smp_list if _next_smp_ok else None,
                            )
                        if ok:
                            st.success("이메일 발송 완료!")
                        else:
                            st.error("이메일 발송 실패. SMTP 설정을 확인하세요.")
                except Exception as _e:
                    st.error(f"이메일 오류: {_e}")

        st.markdown("</div>", unsafe_allow_html=True)


# ── 내부 헬퍼 ───────────────────────────────────────────────────

def _build_period_summary(hours: list[int], plan_data: list[dict], smp_list: list) -> list[str]:
    period_plan = [plan_data[h] for h in hours]
    period_smp = [smp_list[h] for h in hours]
    mode_counter = Counter(p["best_mode"] for p in period_plan)
    avg_smp = sum(period_smp) / len(period_smp)

    lines = []
    mode_str = ", ".join(f"**{m}** {c}시간" for m, c in mode_counter.most_common())
    lines.append(f"운전모드: {mode_str}")
    lines.append(
        f"평균 SMP: **{avg_smp:.1f}** 원/kWh "
        f"(최대 {max(period_smp):.1f}, 최소 {min(period_smp):.1f})"
    )

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


def _style_action(row):
    action = row["판단"]
    if action == "정지":
        return ["background-color:#FEE2E2"] * len(row)
    elif action == "감발전환":
        return ["background-color:#FEF9C3"] * len(row)
    elif action == "기력점화검토":
        return ["background-color:#DBEAFE"] * len(row)
    return [""] * len(row)


def _build_guidance_chart(
    hours: list[int],
    plan_data: list[dict],
    smp_list: list,
    hourly_table,
    lng_price_val: float,
    thresholds_val: dict,
    title: str,
) -> go.Figure:
    x_labels = [f"{h:02d}시" for h in hours]
    smp_vals = [smp_list[h] for h in hours]
    bep_vals = [plan_data[h]["bep"] if plan_data[h]["bep"] is not None else 0 for h in hours]
    mode_labels_list = [plan_data[h]["best_mode"] for h in hours]

    bar_colors = []
    for h in hours:
        action = plan_data[h]["action"]
        if action == "가동":
            bar_colors.append("rgba(16,185,129,0.6)")
        elif action == "감발전환":
            bar_colors.append("rgba(245,158,11,0.6)")
        elif action == "기력점화검토":
            bar_colors.append("rgba(99,102,241,0.6)")
        else:
            bar_colors.append("rgba(239,68,68,0.6)")

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    fig.add_trace(
        go.Bar(
            x=x_labels, y=bep_vals, name="최적모드 BEP ($/MMBtu)",
            marker_color=bar_colors, opacity=0.8,
            text=[f"{m}<br>{b:.1f}" for m, b in zip(mode_labels_list, bep_vals)],
            textposition="outside", textfont=dict(size=10),
        ),
        secondary_y=True,
    )
    fig.add_trace(
        go.Scatter(
            x=x_labels, y=smp_vals, mode="lines+markers",
            name="SMP (원/kWh)",
            line=dict(color="#4F46E5", width=3),
            marker=dict(size=7),
        ),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=x_labels, y=[lng_price_val] * len(hours), mode="lines",
            name=f"LNG가격 {lng_price_val} $/MMBtu",
            line=dict(color="#F472B6", width=2.5, dash="dash"),
        ),
        secondary_y=True,
    )
    fig.add_trace(
        go.Scatter(
            x=x_labels, y=[thresholds_val["smp_low"]] * len(hours), mode="lines",
            name=f"감발 임계 SMP {thresholds_val['smp_low']:.0f}원",
            line=dict(color="#F59E0B", width=1.5, dash="dot"),
            hoverinfo="skip",
        ),
        secondary_y=False,
    )

    fig.update_layout(
        title=dict(text=title, font=dict(family="Sora", size=14, color="#1E1B4B")),
        height=420,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
        margin=dict(t=80, b=40), bargap=0.3, hovermode="x unified",
    )
    fig.update_yaxes(
        title_text="SMP (원/kWh)", secondary_y=False,
        gridcolor="rgba(99,102,241,0.1)",
    )
    fig.update_yaxes(title_text="BEP / LNG가격 ($/MMBtu)", secondary_y=True)

    return fig
