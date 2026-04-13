"""
mail_sender.py  (F6)
====================
주요 함수:
  - send_daily_report()     : F6.1 정기 메일 발송 (HTML 경제성 테이블 + 가이던스)
  - send_urgent_alert()     : F6.2 긴급 알림 발송 (SMP 이상치 탐지 즉시)
  - send_mail()             : 공통 메일 발송 함수

Gmail SMTP 설정:
  1. Google 계정 > 보안 > 2단계 인증 활성화
  2. Google 계정 > 보안 > 앱 비밀번호 생성 (메일용)
  3. config.py의 MAIL_* 상수에 설정

사용법:
  python mail_sender.py --test          # 테스트 메일 발송
  python mail_sender.py --daily         # 오늘 경제성 리포트 발송
"""

from __future__ import annotations

import smtplib
import logging
import base64
import io
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

logger = logging.getLogger("mail_sender")

# ── 메일 설정 (config.py에서 관리) ─────────────────────────
# Gmail SMTP
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

# 발신자/수신자 — config.py 또는 환경변수로 관리 권장
SENDER_EMAIL = ""       # 예: "your.email@gmail.com"
SENDER_PASSWORD = ""    # Gmail 앱 비밀번호 (2단계 인증 후 생성)
RECIPIENTS = []         # 예: ["recipient1@company.com", "recipient2@company.com"]

# config.py에 설정이 있으면 덮어쓰기
try:
    from config import (
        MAIL_SENDER_EMAIL, MAIL_SENDER_PASSWORD, MAIL_RECIPIENTS,
        MAIL_SMTP_SERVER, MAIL_SMTP_PORT,
    )
    SENDER_EMAIL = MAIL_SENDER_EMAIL
    SENDER_PASSWORD = MAIL_SENDER_PASSWORD
    RECIPIENTS = MAIL_RECIPIENTS
    SMTP_SERVER = MAIL_SMTP_SERVER
    SMTP_PORT = MAIL_SMTP_PORT
except ImportError:
    pass


def _is_configured() -> bool:
    """메일 설정이 완료되었는지 확인."""
    return bool(SENDER_EMAIL and SENDER_PASSWORD and RECIPIENTS)


def send_mail(
    subject: str,
    html_body: str,
    recipients: list[str] | None = None,
    text_body: str | None = None,
) -> bool:
    """
    HTML 메일 발송.

    Args:
        subject     : 메일 제목
        html_body   : HTML 본문
        recipients  : 수신자 리스트 (None이면 기본 RECIPIENTS)
        text_body   : 텍스트 본문 (HTML 미지원 클라이언트용)

    Returns:
        True: 발송 성공, False: 실패
    """
    to_list = recipients or RECIPIENTS
    if not to_list or not SENDER_EMAIL or not SENDER_PASSWORD:
        logger.warning("메일 설정 미완료. config.py의 MAIL_* 상수를 확인하세요.")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = ", ".join(to_list)

    if text_body:
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        # TLS (587) 시도, 실패 시 SSL (465) 폴백
        # local_hostname: Windows 호스트명에 공백이 포함되면 Gmail EHLO 거부 방지
        try:
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30,
                              local_hostname="localhost") as server:
                server.starttls()
                server.login(SENDER_EMAIL, SENDER_PASSWORD)
                server.sendmail(SENDER_EMAIL, to_list, msg.as_string())
        except (smtplib.SMTPException, ConnectionError, OSError):
            logger.info("TLS 연결 실패, SSL(465)로 재시도...")
            with smtplib.SMTP_SSL(SMTP_SERVER, 465, timeout=30,
                                  local_hostname="localhost") as server:
                server.login(SENDER_EMAIL, SENDER_PASSWORD)
                server.sendmail(SENDER_EMAIL, to_list, msg.as_string())
        logger.info(f"메일 발송 완료: {subject} -> {to_list}")
        return True
    except Exception as e:
        logger.error(f"메일 발송 실패: {e}")
        return False


# ──────────────────────────────────────────────────────────────
# 차트 이미지 생성 (SMP + BEP + LNG가격)
# ──────────────────────────────────────────────────────────────

def _build_chart_image(
    smp_series: list[float],
    hourly_plan: list[dict],
    lng_price: float,
    thresholds: dict,
    exchange_rate: float,
    target_date: date | None = None,
    prev_smp_series: list[float] | None = None,
    prev_hourly_plan: list[dict] | None = None,
) -> bytes | None:
    """
    SMP vs BEP vs LNG가격 차트 (대시보드 동기화).

    X축: D일 22시~23시 + D+1일 00시~21시 (총 24시간 시계열)
    - D일 22~23시: prev_smp_series + prev_hourly_plan (D일 데이터)
    - D+1일 00~21시: smp_series + hourly_plan (D+1일 데이터)
    - BEP 막대: 가동=초록, 감발=노랑, 기력점화=파랑, 정지=빨강
    - SMP 꺾은선 + LNG가격 점선 + 감발 임계 SMP 점선
    """
    try:
        # ── X축 시계열 구성: D일 22~23시 + D+1일 00~21시 ──
        d_label = ""
        d1_label = ""
        if target_date:
            d = target_date
            d1 = target_date + timedelta(days=1)
            d_label = f"{d.month}/{d.day}"
            d1_label = f"{d1.month}/{d1.day}"

        night_hours = [22, 23]
        day_hours = list(range(0, 22))

        x_labels = []
        smp_vals = []
        bep_vals = []
        bar_colors = []
        mode_labels = []

        # D일 22~23시 (prev 데이터 사용, 없으면 폴백)
        _prev_smp = prev_smp_series or smp_series
        _prev_plan = prev_hourly_plan or hourly_plan
        for h in night_hours:
            x_labels.append(f"{d_label} {h:02d}시" if d_label else f"{h:02d}시")
            smp_vals.append(_prev_smp[h])

            p = _prev_plan[h]
            bep_vals.append(p["bep"] if p["bep"] is not None else 0)
            mode_labels.append(p["best_mode"])

            action = p["action"]
            if action == "가동":
                bar_colors.append("#2ecc71")
            elif action == "감발전환":
                bar_colors.append("#f39c12")
            elif action == "기력점화검토":
                bar_colors.append("#3498db")
            else:
                bar_colors.append("#e74c3c")

        # D+1일 00~21시 (smp_series / hourly_plan = D+1 데이터)
        for h in day_hours:
            x_labels.append(f"{d1_label} {h:02d}시" if d1_label else f"{h:02d}시")
            smp_vals.append(smp_series[h])

            p = hourly_plan[h]
            bep_vals.append(p["bep"] if p["bep"] is not None else 0)
            mode_labels.append(p["best_mode"])

            action = p["action"]
            if action == "가동":
                bar_colors.append("#2ecc71")
            elif action == "감발전환":
                bar_colors.append("#f39c12")
            elif action == "기력점화검토":
                bar_colors.append("#3498db")
            else:
                bar_colors.append("#e74c3c")

        chart_len = len(x_labels)
        fig = make_subplots(specs=[[{"secondary_y": True}]])

        # BEP 막대 (가동상태별 색상)
        fig.add_trace(
            go.Bar(
                x=x_labels, y=bep_vals,
                name="최적모드 BEP ($/MMBtu)",
                marker_color=bar_colors, opacity=0.6,
                text=[f"{m}\n{b:.1f}" for m, b in zip(mode_labels, bep_vals)],
                textposition="outside",
                textfont=dict(size=9),
            ),
            secondary_y=True,
        )

        # SMP 꺾은선
        fig.add_trace(
            go.Scatter(
                x=x_labels, y=smp_vals,
                mode="lines+markers", name="SMP (원/kWh)",
                line=dict(color="#e74c3c", width=3),
                marker=dict(size=6),
            ),
            secondary_y=False,
        )

        # LNG가격 점선
        fig.add_trace(
            go.Scatter(
                x=x_labels, y=[lng_price] * chart_len,
                mode="lines", name=f"LNG가격 {lng_price} $/MMBtu",
                line=dict(color="#2c3e50", width=2.5, dash="dash"),
            ),
            secondary_y=True,
        )

        # 감발 임계 SMP 점선
        smp_low = thresholds.get("smp_low")
        if smp_low:
            fig.add_trace(
                go.Scatter(
                    x=x_labels, y=[smp_low] * chart_len,
                    mode="lines",
                    name=f"감발 임계 SMP {smp_low:.0f}원",
                    line=dict(color="#e67e22", width=1.5, dash="dot"),
                ),
                secondary_y=False,
            )

        # 날짜 경계선 (D+1일 00시)
        boundary = f"{d1_label} 00시" if d1_label else "00시"
        if boundary in x_labels:
            fig.add_vline(x=boundary, line_dash="dot", line_color="#ccc", opacity=0.7)

        fig.update_layout(
            title=dict(
                text=f"SMP vs LNG발전 BEP vs LNG가격 ({d_label} 22시 ~ {d1_label} 21시)",
                x=0.5, xanchor="center", font=dict(size=14),
            ),
            height=450, width=950,
            plot_bgcolor="white", paper_bgcolor="white",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5,
                        font=dict(size=10)),
            margin=dict(t=90, b=100), bargap=0.25,
            xaxis=dict(tickangle=-45),
        )
        fig.update_xaxes(showgrid=True, gridcolor="#E0E0E0")
        fig.update_yaxes(title_text="SMP (원/kWh)", secondary_y=False,
                         showgrid=True, gridcolor="#E0E0E0")
        fig.update_yaxes(title_text="BEP / LNG가격 ($/MMBtu)", secondary_y=True,
                         gridcolor="rgba(0,0,0,0)")
        fig.add_annotation(
            xref="paper", yref="paper", x=1.0, y=-0.18,
            text=f"환율: {exchange_rate:,.0f}원/$  |  막대(BEP) > 점선(LNG가격) = 가동(O) / 아래 = 정지(X)",
            showarrow=False, font=dict(size=10, color="#888"),
        )

        img_bytes = fig.to_image(format="png", scale=2)
        return img_bytes
    except Exception as e:
        logger.warning(f"차트 이미지 생성 실패: {e}")
        return None


# ──────────────────────────────────────────────────────────────
# F6.1  정기 메일 발송
# ──────────────────────────────────────────────────────────────

def _mode_ranges_html(hourly_plan: list[dict], hours: list[int]) -> str:
    """시간 리스트에서 연속 동일 모드 구간을 HTML 리스트로."""
    MODE_DISPLAY = {"2기": "2기 full", "2기저부하": "2기 half", "1기": "1기", "정지": "정지"}
    if not hours:
        return "<li>-</li>"

    entries = [(h, hourly_plan[h]["best_mode"]) for h in hours]
    ranges = []
    start_h, cur_mode = entries[0]
    end_h = start_h
    for h, mode in entries[1:]:
        if mode == cur_mode:
            end_h = h
        else:
            ranges.append((start_h, end_h, cur_mode))
            start_h, cur_mode, end_h = h, mode, h
    ranges.append((start_h, end_h, cur_mode))

    items = []
    for s, e, mode in ranges:
        display = MODE_DISPLAY.get(mode, mode)
        MODE_COLOR = {"2기 full": "#28a745", "2기 half": "#17a2b8", "1기": "#ffc107", "정지": "#dc3545"}
        color = MODE_COLOR.get(display, "#333")
        if s == e:
            items.append(f'<li>{s:02d}시 : <b style="color:{color}">{display}</b></li>')
        else:
            items.append(f'<li>{s:02d}~{e:02d}시 : <b style="color:{color}">{display}</b></li>')
    return "\n".join(items)


def send_daily_report(
    target_date: date,
    summary: dict,
    alerts: list[dict],
    hourly_plan: list[dict],
    hourly_df: pd.DataFrame,
    text_report: str,
    smp_series: list[float] | None = None,
    thresholds: dict | None = None,
    recipients: list[str] | None = None,
    next_day_plan: list[dict] | None = None,
    next_day_smp: list[float] | None = None,
) -> bool:
    """
    F6.1 정기 메일 발송 - 주간/야간 가동계획 + SMP/BEP/LNG가격 차트.

    대시보드 동기화: D일 22~23시 + D+1일 00~21시 조합.
      - 야간 22~23시: D일(target_date) SMP + plan
      - 야간 00~07시 + 주간 08~21시: D+1일(next_day) SMP + plan
      - next_day_plan/smp가 없으면 target_date 데이터로 폴백

    [이메일 포맷]
    1. M월D일 가동계획
       - 야간 (D일 22시 ~ D+1일 08시)
       - D+1일 주간 (08~22시)
    2. SMP vs LNG발전 BEP vs LNG가격 차트 (D일 22시 ~ D+1일 21시)
    """
    weekday_kr = ["월","화","수","목","금","토","일"][target_date.weekday()]
    next_date = target_date + timedelta(days=1)
    m1, d1 = target_date.month, target_date.day
    m2, d2 = next_date.month, next_date.day

    subject = f"[LNG가동계획] {m1}/{d1} 야간~{m2}/{d2} 주간"

    # D+1 데이터가 있으면 대시보드와 동일하게 조합, 없으면 target_date 폴백
    plan_d = hourly_plan                          # D일 plan (22~23시용)
    plan_d1 = next_day_plan or hourly_plan         # D+1일 plan (00~21시용)
    smp_d1 = next_day_smp or smp_series            # D+1일 SMP (00~21시용)

    # 야간: 22~23시(D일 plan) + 00~07시(D+1일 plan)
    night_hours_d = [22, 23]
    night_hours_d1 = list(range(0, 8))
    day_hours_d1 = list(range(8, 22))

    night_html_d = _mode_ranges_html(plan_d, night_hours_d)
    night_html_d1 = _mode_ranges_html(plan_d1, night_hours_d1)
    day_html = _mode_ranges_html(plan_d1, day_hours_d1)

    lng_price = summary["lng_price"]
    exchange_rate = summary["exchange_rate"]

    html = f"""
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family:'Malgun Gothic',sans-serif;margin:20px;line-height:1.6">

        <p>안녕하십니까, 금일 LNG발전 가동계획 송부드립니다.</p>

        <h2>1. {m1}월{d1}일 가동계획</h2>

        <h3>야간 ({m1}월{d1}일 22시 ~ {m2}월{d2}일 08시)</h3>
        <ul style="font-size:1.05em">
            {night_html_d}
            {night_html_d1}
        </ul>

        <h3>{m2}월{d2}일 주간 (08~22시)</h3>
        <ul style="font-size:1.05em">{day_html}</ul>

        <hr>

        <h2>2. SMP vs LNG발전 BEP vs LNG가격</h2>
        <img src="cid:smp_chart" style="max-width:100%;height:auto" alt="SMP/BEP 차트">
        <p style="color:#888;font-size:0.85em">
            * 환율: {exchange_rate:,.0f} 원/$ | LNG가격: {lng_price} $/MMBtu ({summary['price_type']})
            | 열량: {summary['lng_heat']} Mcal/Nm3
        </p>

        <hr>
        <p>문의사항이 있으면 연락주시기 바랍니다. 감사합니다.</p>
    </body>
    </html>
    """

    # 차트 이미지 생성 (D일 22시 ~ D+1일 21시, 대시보드 동기화)
    chart_bytes = None
    if smp_series and thresholds:
        chart_bytes = _build_chart_image(
            smp_d1, plan_d1, lng_price, thresholds, exchange_rate,
            target_date=target_date,
            prev_smp_series=smp_series,
            prev_hourly_plan=plan_d,
        )

    # 이미지 첨부 메일 발송
    to_list = recipients or RECIPIENTS
    if not to_list or not SENDER_EMAIL or not SENDER_PASSWORD:
        logger.warning("메일 설정 미완료.")
        return False

    msg = MIMEMultipart("related")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = ", ".join(to_list)

    # HTML 본문
    alt = MIMEMultipart("alternative")
    if text_report:
        alt.attach(MIMEText(text_report, "plain", "utf-8"))
    alt.attach(MIMEText(html, "html", "utf-8"))
    msg.attach(alt)

    # 차트 이미지 인라인 첨부
    if chart_bytes:
        img_part = MIMEImage(chart_bytes, _subtype="png")
        img_part.add_header("Content-ID", "<smp_chart>")
        img_part.add_header("Content-Disposition", "inline", filename="smp_bep_chart.png")
        msg.attach(img_part)

    try:
        try:
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30,
                              local_hostname="localhost") as server:
                server.starttls()
                server.login(SENDER_EMAIL, SENDER_PASSWORD)
                server.sendmail(SENDER_EMAIL, to_list, msg.as_string())
        except (smtplib.SMTPException, ConnectionError, OSError):
            logger.info("TLS 실패, SSL(465)로 재시도...")
            with smtplib.SMTP_SSL(SMTP_SERVER, 465, timeout=30,
                                  local_hostname="localhost") as server:
                server.login(SENDER_EMAIL, SENDER_PASSWORD)
                server.sendmail(SENDER_EMAIL, to_list, msg.as_string())
        logger.info(f"정기 리포트 발송 완료: {subject}")
        return True
    except Exception as e:
        logger.error(f"정기 리포트 발송 실패: {e}")
        return False


# ──────────────────────────────────────────────────────────────
# F6.2  긴급 알림 발송
# ──────────────────────────────────────────────────────────────

def send_multi_day_report(
    daily_results: list[dict],
    recipients: list[str] | None = None,
    last_next_day_plan: list[dict] | None = None,
) -> bool:
    """
    다중 날짜 정기 메일 발송 (금요일: 금~월 4일치 등).

    daily_results: [{date, hourly_plan, daily_summary, smp_series, thresholds, ...}, ...]
    """
    if not daily_results:
        return False

    weekday_kr = ["월","화","수","목","금","토","일"]

    first_date = daily_results[0]["date"]
    last_date = daily_results[-1]["date"]
    last_next = last_date + timedelta(days=1)

    subject = (
        f"[LNG가동계획] {first_date.month}/{first_date.day}"
        f"~{last_next.month}/{last_next.day} 가동계획"
    )

    # 날짜별 가동계획 HTML 생성
    plan_sections = ""
    chart_images = []  # (cid, bytes)

    night_hours = list(range(22, 24)) + list(range(0, 8))
    day_hours = list(range(8, 22))

    for idx, r in enumerate(daily_results):
        d = r["date"]
        d_next = d + timedelta(days=1)
        m1, d1 = d.month, d.day
        m2, d2 = d_next.month, d_next.day
        wk = weekday_kr[d.weekday()]
        plan = r["hourly_plan"]

        # D+1 데이터 찾기
        _next_smp = None
        _next_plan = None
        for r2 in daily_results:
            if r2["date"] == d_next:
                _next_smp = r2.get("smp_series")
                _next_plan = r2.get("hourly_plan")
                break

        night_html = _mode_ranges_html(plan, night_hours)
        chart_cid = f"smp_chart_{idx}"

        if idx == 0:
            # 첫 날짜: 야간만 (D일 22시~D+1일 08시)
            # D+1 주간은 다음 루프에서 표시됨
            plan_sections += f"""
            <h2>{m1}월{d1}일({wk}) 22시 ~ {m2}월{d2}일 08시</h2>
            <ul style="font-size:1.05em">{night_html}</ul>
            """
        else:
            # 이후: D일 주간 + D일 야간
            day_html = _mode_ranges_html(plan, day_hours)
            plan_sections += f"""
            <h2>{m1}월{d1}일({weekday_kr[d.weekday()]}) 주간 (08~22시)</h2>
            <ul style="font-size:1.05em">{day_html}</ul>
            <h2>{m1}월{d1}일 22시 ~ {m2}월{d2}일 08시</h2>
            <ul style="font-size:1.05em">{night_html}</ul>
            """

        # 차트 생성 (D일 22시 ~ D+1일 21시)
        smp_series = r.get("smp_series")
        thresholds = r.get("thresholds")
        summary = r.get("daily_summary", {})

        # 마지막 날짜일 때 last_next_day_plan 활용
        _chart_next_plan = _next_plan
        _chart_next_smp = _next_smp
        if _chart_next_plan is None and last_next_day_plan is not None and idx == len(daily_results) - 1:
            _chart_next_plan = last_next_day_plan
        if _chart_next_smp is None and idx == len(daily_results) - 1:
            # D+1 SMP 캐시에서 로드 시도
            try:
                import json
                from pathlib import Path
                _nx_cache = Path(__file__).resolve().parent.parent / "data" / "smp_cache" / f"smp_{d_next}.json"
                if _nx_cache.is_file():
                    with open(_nx_cache, encoding="utf-8") as _f:
                        _nx_data = json.load(_f)
                    if _nx_data.get("updated"):
                        _chart_next_smp = _nx_data["smp"]
            except Exception:
                pass

        if smp_series and thresholds:
            chart_bytes = _build_chart_image(
                _chart_next_smp or smp_series,
                _chart_next_plan or plan,
                summary.get("lng_price", 0),
                thresholds,
                summary.get("exchange_rate", 0),
                target_date=d,
                prev_smp_series=smp_series,
                prev_hourly_plan=plan,
            )
            if chart_bytes:
                chart_images.append((chart_cid, chart_bytes))

        plan_sections += f"""
        <img src="cid:{chart_cid}" style="max-width:100%;height:auto" alt="SMP/BEP 차트 {d}">
        <hr>
        """

    # 마지막 날짜의 D+1 주간
    _last_d = daily_results[-1]["date"]
    _last_next = _last_d + timedelta(days=1)
    _d1_plan = last_next_day_plan
    if _d1_plan is None:
        for r2 in daily_results:
            if r2["date"] == _last_next:
                _d1_plan = r2.get("hourly_plan")
                break
    if _d1_plan is None:
        _d1_plan = daily_results[-1]["hourly_plan"]
    _d1_day_html = _mode_ranges_html(_d1_plan, day_hours)
    plan_sections += f"""
    <h2>{_last_next.month}월{_last_next.day}일({weekday_kr[_last_next.weekday()]}) 주간 (08~22시)</h2>
    <ul style="font-size:1.05em">{_d1_day_html}</ul>
    """

    first_summary = daily_results[0].get("daily_summary", {})
    lng_price = first_summary.get("lng_price", "")
    exchange_rate = first_summary.get("exchange_rate", "")
    price_type = first_summary.get("price_type", "")
    lng_heat = first_summary.get("lng_heat", "")

    html = f"""
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family:'Malgun Gothic',sans-serif;margin:20px;line-height:1.6">

        <p>안녕하십니까, LNG발전 가동계획 송부드립니다.</p>

        {plan_sections}

        <p style="color:#888;font-size:0.85em">
            * 환율: {exchange_rate:,.0f} 원/$ | LNG가격: {lng_price} $/MMBtu ({price_type})
            | 열량: {lng_heat} Mcal/Nm3
        </p>

        <p>문의사항이 있으면 연락주시기 바랍니다. 감사합니다.</p>
    </body>
    </html>
    """

    to_list = recipients or RECIPIENTS
    if not to_list or not SENDER_EMAIL or not SENDER_PASSWORD:
        logger.warning("메일 설정 미완료.")
        return False

    msg = MIMEMultipart("related")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = ", ".join(to_list)

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(html, "html", "utf-8"))
    msg.attach(alt)

    for cid, img_bytes in chart_images:
        img_part = MIMEImage(img_bytes, _subtype="png")
        img_part.add_header("Content-ID", f"<{cid}>")
        img_part.add_header("Content-Disposition", "inline", filename=f"{cid}.png")
        msg.attach(img_part)

    try:
        try:
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30,
                              local_hostname="localhost") as server:
                server.starttls()
                server.login(SENDER_EMAIL, SENDER_PASSWORD)
                server.sendmail(SENDER_EMAIL, to_list, msg.as_string())
        except (smtplib.SMTPException, ConnectionError, OSError):
            logger.info("TLS 실패, SSL(465)로 재시도...")
            with smtplib.SMTP_SSL(SMTP_SERVER, 465, timeout=30,
                                  local_hostname="localhost") as server:
                server.login(SENDER_EMAIL, SENDER_PASSWORD)
                server.sendmail(SENDER_EMAIL, to_list, msg.as_string())
        logger.info(f"다중 날짜 리포트 발송 완료: {subject}")
        return True
    except Exception as e:
        logger.error(f"다중 날짜 리포트 발송 실패: {e}")
        return False


def _build_anomaly_chart(
    smp_series: list[float],
    thresholds: dict,
    alerts: list[dict],
    target_date: date,
) -> bytes | None:
    """SMP 24시간 차트 + 임계값 라인 + 이상구간 강조 시각화."""
    try:
        x_labels = [f"{h:02d}" for h in range(24)]
        smp_low = thresholds["smp_low"]
        smp_high = thresholds["smp_high"]

        fig = go.Figure()

        # 이상구간 배경 강조 (세로 띠)
        for a in alerts:
            h = a["hour"]
            if a["alert_type"] == "SMP 제로":
                color = "rgba(220,53,69,0.15)"
            elif a["alert_type"] == "SMP 경제성 한계":
                color = "rgba(255,193,7,0.18)"
            elif a["alert_type"] == "SMP 과대":
                color = "rgba(0,123,255,0.12)"
            else:
                continue
            fig.add_vrect(
                x0=h - 0.5, x1=h + 0.5,
                fillcolor=color, line_width=0, layer="below",
            )

        # SMP 꺾은선
        fig.add_trace(go.Scatter(
            x=list(range(24)), y=smp_series,
            mode="lines+markers", name="SMP (원/kWh)",
            line=dict(color="#2F5597", width=3),
            marker=dict(size=7),
        ))

        # smp_low 임계선 (경제성 한계)
        fig.add_hline(
            y=smp_low, line_dash="dash", line_color="#FF8C00", line_width=2,
            annotation_text=f"경제성 한계 ({smp_low:.0f}원)",
            annotation_position="top left",
            annotation_font=dict(color="#FF8C00", size=11),
        )

        # smp_high 임계선 (기력발전 점화)
        fig.add_hline(
            y=smp_high, line_dash="dash", line_color="#dc3545", line_width=2,
            annotation_text=f"기력발전 점화 ({smp_high:.0f}원)",
            annotation_position="top left",
            annotation_font=dict(color="#dc3545", size=11),
        )

        # SMP 제로 라인
        fig.add_hline(
            y=0, line_dash="dot", line_color="#888", line_width=1,
        )

        # 이상구간 SMP 포인트 강조
        for a in alerts:
            h = a["hour"]
            if a["alert_type"] == "SMP 제로":
                color, symbol = "#dc3545", "x"
            elif a["alert_type"] == "SMP 경제성 한계":
                color, symbol = "#FF8C00", "diamond"
            else:
                color, symbol = "#0d6efd", "triangle-up"
            fig.add_trace(go.Scatter(
                x=[h], y=[a["smp"]],
                mode="markers",
                marker=dict(color=color, size=12, symbol=symbol, line=dict(width=2, color="white")),
                showlegend=False,
            ))

        weekday_kr = ["월","화","수","목","금","토","일"][target_date.weekday()]
        fig.update_layout(
            title=dict(
                text=f"SMP 이상구간 탐지 — {target_date} ({weekday_kr})",
                x=0.5, xanchor="center", font=dict(size=16),
            ),
            height=420, width=900,
            plot_bgcolor="white", paper_bgcolor="white",
            xaxis=dict(
                title="시간", tickmode="array",
                tickvals=list(range(24)), ticktext=x_labels,
                showgrid=True, gridcolor="#E8E8E8",
            ),
            yaxis=dict(
                title="SMP (원/kWh)",
                showgrid=True, gridcolor="#E8E8E8",
            ),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
            margin=dict(t=90, b=60, l=60, r=30),
        )

        return fig.to_image(format="png", scale=2)
    except Exception as e:
        logger.warning(f"이상구간 차트 생성 실패: {e}")
        return None


def _group_alert_ranges(alerts: list[dict]) -> list[dict]:
    """
    개별 시간 알림을 연속 구간으로 병합.

    Returns:
        [{alert_type, severity, start_hour, end_hour, smp_min, smp_max, action}, ...]
    """
    if not alerts:
        return []

    sorted_alerts = sorted(alerts, key=lambda a: a["hour"])
    groups = []
    cur = {
        "alert_type": sorted_alerts[0]["alert_type"],
        "severity": sorted_alerts[0]["severity"],
        "start_hour": sorted_alerts[0]["hour"],
        "end_hour": sorted_alerts[0]["hour"],
        "smp_min": sorted_alerts[0]["smp"],
        "smp_max": sorted_alerts[0]["smp"],
        "action": sorted_alerts[0]["action"],
    }

    for a in sorted_alerts[1:]:
        if a["alert_type"] == cur["alert_type"] and a["hour"] == cur["end_hour"] + 1:
            cur["end_hour"] = a["hour"]
            cur["smp_min"] = min(cur["smp_min"], a["smp"])
            cur["smp_max"] = max(cur["smp_max"], a["smp"])
        else:
            groups.append(cur)
            cur = {
                "alert_type": a["alert_type"],
                "severity": a["severity"],
                "start_hour": a["hour"],
                "end_hour": a["hour"],
                "smp_min": a["smp"],
                "smp_max": a["smp"],
                "action": a["action"],
            }
    groups.append(cur)
    return groups


def send_urgent_alert(
    target_date: date,
    alerts: list[dict],
    smp_series: list[float] | None = None,
    thresholds: dict | None = None,
    recipients: list[str] | None = None,
) -> bool:
    """
    F6.2 긴급 알림 발송 - SMP 이상구간 시각화 + 구간별 권고.

    개선사항:
      - SMP 차트에 임계값(smp_low/smp_high) 라인 + 이상구간 색칠
      - 시간대별 나열 대신 연속구간 병합 (예: "01~05시")
      - 구간별 유형·SMP 범위·권고 조치를 테이블로 표시

    Returns:
        True: 발송됨, False: 발송 안 됨 (알림 없거나 설정 미완료)
    """
    if not alerts:
        return False

    weekday_kr = ["월","화","수","목","금","토","일"][target_date.weekday()]

    has_critical = any(a["severity"] == "critical" for a in alerts)
    has_warning = any(a["severity"] == "warning" for a in alerts)
    if not has_critical and not has_warning:
        return False

    severity_tag = "긴급" if has_critical else "주의"
    subject = f"[{severity_tag}] {target_date}({weekday_kr}) SMP 이상구간 탐지"

    # 연속구간 병합
    groups = _group_alert_ranges(alerts)

    # 구간별 HTML 테이블
    TYPE_STYLE = {
        "SMP 제로":       {"color": "#fff", "bg": "#dc3545", "icon": "🔴"},
        "SMP 경제성 한계": {"color": "#333", "bg": "#fff3cd", "icon": "🟡"},
        "SMP 과대":       {"color": "#fff", "bg": "#0d6efd", "icon": "🔵"},
    }

    table_rows = ""
    for g in groups:
        style = TYPE_STYLE.get(g["alert_type"], {"color": "#333", "bg": "#f8f9fa", "icon": ""})

        if g["start_hour"] == g["end_hour"]:
            time_str = f"{g['start_hour']:02d}시"
        else:
            time_str = f"{g['start_hour']:02d}~{g['end_hour']:02d}시"

        if g["smp_min"] == g["smp_max"]:
            smp_str = f"{g['smp_min']:.1f}"
        else:
            smp_str = f"{g['smp_min']:.1f}~{g['smp_max']:.1f}"

        table_rows += f"""
        <tr>
            <td style="padding:10px 14px;font-weight:bold;font-size:1.05em;
                        background:{style['bg']};color:{style['color']};
                        border-radius:4px;text-align:center;white-space:nowrap">
                {style['icon']} {g['alert_type']}
            </td>
            <td style="padding:10px 14px;text-align:center;font-size:1.1em;font-weight:bold">
                {time_str}
            </td>
            <td style="padding:10px 14px;text-align:center">
                {smp_str} 원/kWh
            </td>
            <td style="padding:10px 14px">
                {g['action']}
            </td>
        </tr>"""

    # 임계값 정보
    threshold_info = ""
    if thresholds:
        threshold_info = f"""
        <p style="margin:15px 0;padding:10px 15px;background:#f0f4f8;border-left:4px solid #2F5597;border-radius:4px">
            <b>임계값 기준</b><br>
            경제성 한계 (smp_low): <b>{thresholds['smp_low']:.1f}원/kWh</b>
            — LNG발전 2기 BEP 역산<br>
            기력발전 점화 (smp_high): <b>{thresholds['smp_high']:.1f}원/kWh</b>
            — 기력발전 BEP 역산
        </p>"""

    html = f"""
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif;margin:20px;line-height:1.7">

        <h2 style="color:#dc3545;margin-bottom:5px">
            [{severity_tag}] SMP 이상구간 탐지 — {target_date} ({weekday_kr})
        </h2>

        {threshold_info}

        <h3 style="margin-top:25px">SMP 이상구간 차트</h3>
        <img src="cid:anomaly_chart" style="max-width:100%;height:auto" alt="SMP 이상구간 차트">

        <h3 style="margin-top:25px">이상구간 요약 및 권고</h3>
        <table border="0" cellpadding="0" cellspacing="0"
               style="border-collapse:collapse;width:100%;margin:10px 0">
            <thead>
                <tr style="background:#2F5597;color:white">
                    <th style="padding:10px 14px;text-align:center">유형</th>
                    <th style="padding:10px 14px;text-align:center">시간대</th>
                    <th style="padding:10px 14px;text-align:center">SMP</th>
                    <th style="padding:10px 14px;text-align:left">권고 조치</th>
                </tr>
            </thead>
            <tbody style="border:1px solid #dee2e6">
                {table_rows}
            </tbody>
        </table>

        <hr style="margin-top:30px">
        <p style="color:#888;font-size:0.8em">
            LNG 발전 경제성 자동판단 시스템 | 이상구간 알림<br>
            문의사항이 있으시면 연락주시기 바랍니다.
        </p>
    </body>
    </html>
    """

    # 텍스트 본문 (HTML 미지원용)
    text_lines = [f"[{severity_tag}] {target_date}({weekday_kr}) SMP 이상구간 탐지\n"]
    for g in groups:
        if g["start_hour"] == g["end_hour"]:
            time_str = f"{g['start_hour']:02d}시"
        else:
            time_str = f"{g['start_hour']:02d}~{g['end_hour']:02d}시"
        text_lines.append(f"  {g['alert_type']} | {time_str} | SMP {g['smp_min']:.1f}~{g['smp_max']:.1f}원")
        text_lines.append(f"    → {g['action']}")
    text = "\n".join(text_lines)

    # 차트 이미지 생성
    chart_bytes = None
    if smp_series and thresholds:
        chart_bytes = _build_anomaly_chart(smp_series, thresholds, alerts, target_date)

    # 이미지 첨부 메일 발송
    to_list = recipients or RECIPIENTS
    if not to_list or not SENDER_EMAIL or not SENDER_PASSWORD:
        logger.warning("메일 설정 미완료.")
        return False

    msg = MIMEMultipart("related")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = ", ".join(to_list)

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(text, "plain", "utf-8"))
    alt.attach(MIMEText(html, "html", "utf-8"))
    msg.attach(alt)

    if chart_bytes:
        img_part = MIMEImage(chart_bytes, _subtype="png")
        img_part.add_header("Content-ID", "<anomaly_chart>")
        img_part.add_header("Content-Disposition", "inline", filename="smp_anomaly_chart.png")
        msg.attach(img_part)

    try:
        try:
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30,
                              local_hostname="localhost") as server:
                server.starttls()
                server.login(SENDER_EMAIL, SENDER_PASSWORD)
                server.sendmail(SENDER_EMAIL, to_list, msg.as_string())
        except (smtplib.SMTPException, ConnectionError, OSError):
            logger.info("TLS 실패, SSL(465)로 재시도...")
            with smtplib.SMTP_SSL(SMTP_SERVER, 465, timeout=30,
                                  local_hostname="localhost") as server:
                server.login(SENDER_EMAIL, SENDER_PASSWORD)
                server.sendmail(SENDER_EMAIL, to_list, msg.as_string())
        logger.info(f"메일 발송 완료: {subject} -> {to_list}")
        return True
    except Exception as e:
        logger.error(f"메일 발송 실패: {e}")
        return False


# ──────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import sys

    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(PROJECT_ROOT / "modules"))

    parser = argparse.ArgumentParser(description="메일 발송 테스트")
    parser.add_argument("--test", action="store_true", help="테스트 메일 발송")
    parser.add_argument("--daily", action="store_true", help="오늘 경제성 리포트 발송")
    args = parser.parse_args()

    if args.test:
        if not _is_configured():
            print("[!] 메일 설정 미완료. config.py에 MAIL_* 상수를 추가하세요.")
            print("    MAIL_SENDER_EMAIL = 'your@gmail.com'")
            print("    MAIL_SENDER_PASSWORD = '앱비밀번호'")
            print("    MAIL_RECIPIENTS = ['recipient@company.com']")
        else:
            ok = send_mail(
                subject="[테스트] LNG 발전 경제성 시스템 메일 테스트",
                html_body="<h2>테스트 메일</h2><p>메일 발송이 정상 작동합니다.</p>",
            )
            print("발송 성공!" if ok else "발송 실패.")
