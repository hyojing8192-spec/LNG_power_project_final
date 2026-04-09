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
) -> bytes | None:
    """SMP 꺾은선 + BEP 막대 + LNG가격 점선 차트를 PNG bytes로 반환."""
    try:
        x_labels = [f"{h:02d}" for h in range(24)]

        # 최적모드 BEP
        bep_vals = [p["bep"] if p["bep"] is not None else 0 for p in hourly_plan]

        fig = make_subplots(specs=[[{"secondary_y": True}]])

        # BEP 막대 (파란색)
        fig.add_trace(
            go.Bar(
                x=x_labels, y=bep_vals,
                name="LNG발전 BEP ($/MMBtu)",
                marker_color="#B4C7E7", opacity=0.85,
                text=[f"{b:.1f}" for b in bep_vals],
                textposition="outside", textfont=dict(size=9),
            ),
            secondary_y=True,
        )

        # SMP 꺾은선 (진한 파란색)
        fig.add_trace(
            go.Scatter(
                x=x_labels, y=smp_series,
                mode="lines+markers", name="SMP (원/kWh)",
                line=dict(color="#2F5597", width=3),
                marker=dict(size=6),
            ),
            secondary_y=False,
        )

        # LNG가격 점선 (주황색)
        fig.add_trace(
            go.Scatter(
                x=x_labels, y=[lng_price] * 24,
                mode="lines", name=f"LNG가격 {lng_price} $/MMBtu",
                line=dict(color="#ED7D31", width=2.5, dash="dash"),
            ),
            secondary_y=True,
        )

        fig.update_layout(
            title=dict(text="SMP vs LNG발전 BEP vs LNG가격", x=0.5, xanchor="center"),
            height=400, width=900,
            plot_bgcolor="white",
            paper_bgcolor="white",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
            margin=dict(t=80, b=60), bargap=0.3,
        )
        fig.update_xaxes(showgrid=True, gridcolor="#E0E0E0")
        fig.update_yaxes(title_text="SMP (원/kWh)", secondary_y=False,
                         showgrid=True, gridcolor="#E0E0E0")
        fig.update_yaxes(title_text="BEP / LNG가격 ($/MMBtu)", secondary_y=True)
        fig.add_annotation(
            xref="paper", yref="paper", x=0.5, y=-0.15,
            text=f"* 환율: {exchange_rate:,.0f} 원/$  |  막대(BEP) > 점선(LNG가격) = 가동(O)",
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
) -> bool:
    """
    F6.1 정기 메일 발송 - 주간/야간 가동계획 + SMP/BEP/LNG가격 차트.

    [이메일 포맷]
    1. M월D일 가동계획
       - 야간 (D일 22시 ~ D+1일 08시)
       - D+1일 주간 (08~22시)
    2. 금일 SMP 및 LNG발전 BEP, LNG가격 차트
       (환율 주석)
    """
    weekday_kr = ["월","화","수","목","금","토","일"][target_date.weekday()]
    next_date = target_date + timedelta(days=1)
    m1, d1 = target_date.month, target_date.day
    m2, d2 = next_date.month, next_date.day

    subject = f"[LNG가동계획] {m1}/{d1} 야간~{m2}/{d2} 주간"

    # 주간/야간 구간
    night_hours = list(range(22, 24)) + list(range(0, 8))
    day_hours = list(range(8, 22))
    night_html = _mode_ranges_html(hourly_plan, night_hours)
    day_html = _mode_ranges_html(hourly_plan, day_hours)

    lng_price = summary["lng_price"]
    exchange_rate = summary["exchange_rate"]

    html = f"""
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family:'Malgun Gothic',sans-serif;margin:20px;line-height:1.6">

        <p>안녕하십니까, 금일 LNG발전 가동계획 송부드립니다.</p>

        <h2>1. {m1}월{d1}일 가동계획</h2>

        <h3>야간 ({m1}월{d1}일 22시 ~ {m2}월{d2}일 08시)</h3>
        <ul style="font-size:1.05em">{night_html}</ul>

        <h3>{m2}월{d2}일 주간 (08~22시)</h3>
        <ul style="font-size:1.05em">{day_html}</ul>

        <hr>

        <h2>2. 금일 SMP 및 LNG발전 BEP, LNG가격</h2>
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

    # 차트 이미지 생성
    chart_bytes = None
    if smp_series and thresholds:
        chart_bytes = _build_chart_image(
            smp_series, hourly_plan, lng_price, thresholds, exchange_rate,
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

        night_html = _mode_ranges_html(plan, night_hours)
        day_html = _mode_ranges_html(plan, day_hours)

        chart_cid = f"smp_chart_{idx}"

        plan_sections += f"""
        <h2>{m1}월{d1}일({wk}) 가동계획</h2>
        <h3>야간 ({m1}월{d1}일 22시 ~ {m2}월{d2}일 08시)</h3>
        <ul style="font-size:1.05em">{night_html}</ul>
        <h3>{m2}월{d2}일 주간 (08~22시)</h3>
        <ul style="font-size:1.05em">{day_html}</ul>
        <img src="cid:{chart_cid}" style="max-width:100%;height:auto" alt="SMP/BEP 차트 {d}">
        <hr>
        """

        # 차트 생성
        smp_series = r.get("smp_series")
        thresholds = r.get("thresholds")
        summary = r.get("daily_summary", {})
        if smp_series and thresholds:
            chart_bytes = _build_chart_image(
                smp_series, plan,
                summary.get("lng_price", 0),
                thresholds,
                summary.get("exchange_rate", 0),
            )
            if chart_bytes:
                chart_images.append((chart_cid, chart_bytes))

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


def send_urgent_alert(
    target_date: date,
    alerts: list[dict],
    recipients: list[str] | None = None,
) -> bool:
    """
    F6.2 긴급 알림 발송 - SMP 이상치 탐지 즉시 트리거.

    critical/warning 등급의 알림만 발송.

    Returns:
        True: 발송됨, False: 발송 안 됨 (알림 없거나 설정 미완료)
    """
    urgent = [a for a in alerts if a["severity"] in ("critical", "warning")]
    if not urgent:
        return False

    weekday_kr = ["월","화","수","목","금","토","일"][target_date.weekday()]

    # 가장 심각한 알림 유형
    has_critical = any(a["severity"] == "critical" for a in urgent)
    severity_tag = "긴급" if has_critical else "주의"

    subject = f"[{severity_tag}] {target_date}({weekday_kr}) SMP 이상 {len(urgent)}건 탐지"

    alert_rows = ""
    for a in urgent:
        color = "#dc3545" if a["severity"] == "critical" else "#ffc107"
        alert_rows += f"""
        <tr>
            <td style="color:{color};font-weight:bold;font-size:1.1em">{a['title']}</td>
        </tr>
        <tr>
            <td style="padding:5px 20px">{a['message']}</td>
        </tr>
        <tr>
            <td style="padding:5px 20px;font-weight:bold">권고: {a['action']}</td>
        </tr>
        <tr><td><hr></td></tr>"""

    html = f"""
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family:'Malgun Gothic',sans-serif;margin:20px">
        <h2 style="color:#dc3545">[{severity_tag}] SMP 이상구간 탐지 - {target_date} ({weekday_kr})</h2>
        <table border="0" cellpadding="5">
            {alert_rows}
        </table>
        <hr>
        <p style="color:#888;font-size:0.8em">
            LNG 발전 경제성 자동판단 시스템 | 긴급 알림
        </p>
    </body>
    </html>
    """

    text = "\n".join(f"{a['title']}\n  {a['action']}" for a in urgent)

    return send_mail(subject, html, recipients, text_body=text)


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
