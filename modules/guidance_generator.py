"""
guidance_generator.py  (F5)
============================
주요 함수:
  - generate_hourly_plan()     : F5.1 시간별 가동계획표 생성
  - generate_daily_summary()   : F5.2 일간 요약 리포트 생성
  - generate_alert_messages()  : F5.3 이상구간 경고 메시지 생성
  - generate_full_guidance()   : F5.1~F5.3 통합 가이던스 생성

입력:
  - hourly_df    : build_hourly_table() 반환 DataFrame (24행)
  - thresholds   : calc_smp_thresholds() 반환 dict
  - smp_series   : 시간별 SMP 리스트 (24개)
  - target_date  : 분석 대상 날짜
  - lng_price    : LNG 가격 ($/MMBtu)
  - is_spot      : Spot LNG 여부

출력:
  - 구조화된 dict (텍스트 리포트 + HTML 테이블 포함)
"""

from __future__ import annotations

import pandas as pd
from datetime import date
from config import MODE_LABELS


# ──────────────────────────────────────────────────────────────
# F5.1  시간별 가동계획표 생성
# ──────────────────────────────────────────────────────────────

def generate_hourly_plan(
    hourly_df: pd.DataFrame,
    smp_series: list[float],
    thresholds: dict,
    lng_price: float,
) -> list[dict]:
    """
    시간별 가동계획표 생성.

    각 시간에 대해 최적 운전모드, 가동/정지 판단, 주의사항을 포함한 계획 생성.

    Returns:
        list of dict:
            hour        : 시간 (0~23)
            time_str    : "HH:00" 형식
            smp         : SMP (원/kWh)
            best_mode   : 최적 운전모드 한글명
            action      : "가동" / "정지" / "감발전환" / "기력점화검토"
            bep         : 최적모드 BEP ($/MMBtu)
            econ_bil    : 최적모드 경제성 (억원)
            note        : 비고/주의사항
    """
    smp_low = thresholds["smp_low"]
    smp_high = thresholds["smp_high"]

    plan = []
    for idx, row in hourly_df.iterrows():
        hour = idx if isinstance(idx, int) else int(idx)
        smp = smp_series[hour] if hour < len(smp_series) else 0.0
        best_mode = row["최적모드"]

        # BEP와 경제성 추출
        bep_val = None
        econ_val = 0.0
        for label in ["1기", "2기저부하", "2기"]:
            if best_mode == label:
                bep_col = f"BEP_{label}($/MMBtu)"
                econ_col = f"경제성(억)_{label}"
                if bep_col in row:
                    bep_val = row[bep_col]
                if econ_col in row:
                    econ_val = row[econ_col]
                break

        # 가동 판단 및 비고
        if best_mode == "정지":
            action = "정지"
            note = "전 모드 BEP < LNG가격 → 가동 시 손실"
        elif smp <= 0:
            action = "정지"
            note = "SMP 제로 → LNG발전 즉시 감발/정지 검토"
        elif smp < smp_low:
            action = "감발전환"
            note = f"SMP {smp:.1f}원 < 임계 {smp_low:.0f}원 → 1기 또는 저부하 전환 검토"
        elif smp >= smp_high:
            action = "기력점화검토"
            steam_bep = row.get("기력BEP_사용단가($/MMBtu)", None)
            if steam_bep is not None and steam_bep == steam_bep:  # not NaN
                viable = "가능" if steam_bep > lng_price else "불가"
                note = f"SMP {smp:.1f}원 ≥ 임계 {smp_high:.0f}원 → 기력 BEP {steam_bep:.1f} ({viable})"
            else:
                note = f"SMP {smp:.1f}원 ≥ 임계 {smp_high:.0f}원 → 기력발전 점화 검토"
        else:
            action = "가동"
            note = ""

        plan.append({
            "hour": hour,
            "time_str": f"{hour:02d}:00",
            "smp": round(smp, 2),
            "best_mode": best_mode,
            "action": action,
            "bep": round(float(bep_val), 2) if bep_val is not None and bep_val == bep_val else None,
            "econ_bil": round(float(econ_val), 3),
            "note": note,
        })

    return plan


# ──────────────────────────────────────────────────────────────
# F5.2  일간 요약 리포트 생성
# ──────────────────────────────────────────────────────────────

def generate_daily_summary(
    target_date: date,
    hourly_df: pd.DataFrame,
    smp_series: list[float],
    thresholds: dict,
    lng_price: float,
    exchange_rate: float,
    lng_heat: float,
    is_spot: bool = False,
    hourly_plan: list[dict] | None = None,
) -> dict:
    """
    일간 요약 리포트 생성.

    Returns:
        dict:
            date            : 분석 날짜
            weekday         : 요일 (한글)
            price_type      : "사용단가" / "Spot"
            smp_avg         : 평균 SMP
            smp_max         : 최대 SMP
            smp_min         : 최소 SMP
            smp_low_thresh  : 감발 검토 임계값
            smp_high_thresh : 기력 점화 임계값
            lng_price       : LNG 가격
            exchange_rate   : 환율
            lng_heat        : 열량
            mode_dist       : {모드명: 시간수} 최적모드 분포
            econ_totals     : {모드명: 일일경제성합(억)} 모드별 합계
            best_overall    : 일일 최다 최적모드
            total_econ_best : 최다 모드 기준 일일 경제성 합계
            anomaly_hours   : {유형: [시간]} 이상구간
            recommendation  : 종합 운전 권고 문자열
    """
    weekdays_kr = ["월", "화", "수", "목", "금", "토", "일"]
    smp_low = thresholds["smp_low"]
    smp_high = thresholds["smp_high"]

    # 모드 분포
    mode_dist = hourly_df["최적모드"].value_counts().to_dict()

    # 모드별 경제성 합계
    econ_totals = {}
    for label in ["1기", "2기저부하", "2기"]:
        col = f"경제성(억)_{label}"
        if col in hourly_df.columns:
            econ_totals[label] = round(float(hourly_df[col].sum()), 3)

    # 최다 최적모드
    best_overall = hourly_df["최적모드"].value_counts().idxmax()

    # 이상구간 분류
    zero_hours = [h for h, s in enumerate(smp_series) if s <= 0]
    low_hours = [h for h, s in enumerate(smp_series) if 0 < s < smp_low]
    high_hours = [h for h, s in enumerate(smp_series) if s >= smp_high]

    anomaly_hours = {}
    if zero_hours:
        anomaly_hours["SMP 제로"] = zero_hours
    if low_hours:
        anomaly_hours["SMP 경제성 한계"] = low_hours
    if high_hours:
        anomaly_hours["SMP 과대"] = high_hours

    # 종합 운전 권고
    recommendation = _build_recommendation(
        mode_dist, econ_totals, anomaly_hours,
        smp_low, smp_high, best_overall, lng_price,
    )

    return {
        "date": target_date.isoformat(),
        "weekday": weekdays_kr[target_date.weekday()],
        "price_type": "Spot" if is_spot else "사용단가",
        "smp_avg": round(sum(smp_series) / len(smp_series), 2),
        "smp_max": round(max(smp_series), 2),
        "smp_min": round(min(smp_series), 2),
        "smp_low_thresh": smp_low,
        "smp_high_thresh": smp_high,
        "lng_price": lng_price,
        "exchange_rate": exchange_rate,
        "lng_heat": lng_heat,
        "mode_dist": mode_dist,
        "econ_totals": econ_totals,
        "best_overall": best_overall,
        "total_econ_best": econ_totals.get(best_overall, 0.0),
        "anomaly_hours": anomaly_hours,
        "recommendation": recommendation,
    }


def _build_recommendation(
    mode_dist: dict,
    econ_totals: dict,
    anomaly_hours: dict,
    smp_low: float,
    smp_high: float,
    best_overall: str,
    lng_price: float,
) -> str:
    """종합 운전 권고문 생성."""
    lines = []

    # 기본 운전 권고
    if best_overall == "정지":
        lines.append(f"금일 전 시간 LNG발전 경제성 없음 → 정지 유지 권고")
    else:
        total_hours = mode_dist.get(best_overall, 0)
        econ = econ_totals.get(best_overall, 0.0)
        lines.append(
            f"금일 최적 운전모드: {best_overall} ({total_hours}시간, "
            f"일일 경제성 {econ:+.3f}억원)"
        )

    # 복수 모드 전환 필요 시
    active_modes = [m for m in mode_dist if m != "정지"]
    if len(active_modes) > 1:
        transitions = ", ".join(f"{m} {mode_dist[m]}시간" for m in active_modes)
        lines.append(f"모드 전환 필요: {transitions}")

    # 이상구간 경고 요약
    if "SMP 제로" in anomaly_hours:
        hrs = anomaly_hours["SMP 제로"]
        lines.append(
            f"[긴급] SMP 제로 {len(hrs)}시간 ({_fmt_hours(hrs)}) "
            f"→ 즉시 감발/정지 검토 필요"
        )

    if "SMP 경제성 한계" in anomaly_hours:
        hrs = anomaly_hours["SMP 경제성 한계"]
        lines.append(
            f"[주의] SMP 경제성 한계 {len(hrs)}시간 ({_fmt_hours(hrs)}) "
            f"→ 1기 또는 저부하 전환 검토"
        )

    if "SMP 과대" in anomaly_hours:
        hrs = anomaly_hours["SMP 과대"]
        lines.append(
            f"[참고] SMP 과대 {len(hrs)}시간 ({_fmt_hours(hrs)}) "
            f"→ 기력발전 LNG 점화 추가 검토"
        )

    if not anomaly_hours:
        lines.append("SMP 정상 범위 - 이상구간 없음")

    return "\n".join(lines)


def _fmt_hours(hours: list[int]) -> str:
    """시간 리스트를 연속 구간으로 압축 표현. 예: [1,2,3,5,6] → '01~03시, 05~06시'"""
    if not hours:
        return ""
    hours = sorted(hours)
    ranges = []
    start = hours[0]
    end = hours[0]
    for h in hours[1:]:
        if h == end + 1:
            end = h
        else:
            ranges.append((start, end))
            start = end = h
    ranges.append((start, end))

    parts = []
    for s, e in ranges:
        if s == e:
            parts.append(f"{s:02d}시")
        else:
            parts.append(f"{s:02d}~{e:02d}시")
    return ", ".join(parts)


# ──────────────────────────────────────────────────────────────
# F5.3  이상구간 경고 메시지 생성
# ──────────────────────────────────────────────────────────────

def generate_alert_messages(
    target_date: date,
    smp_series: list[float],
    thresholds: dict,
    hourly_df: pd.DataFrame,
    lng_price: float,
) -> list[dict]:
    """
    이상구간별 경고 메시지 생성.

    각 이상 시간에 대해 구체적 조치 안내를 포함한 메시지 생성.

    Returns:
        list of dict:
            hour        : 시간
            time_str    : "HH:00"
            smp         : SMP 값
            severity    : "critical" / "warning" / "info"
            alert_type  : "SMP 제로" / "SMP 경제성 한계" / "SMP 과대"
            title       : 경고 제목
            message     : 상세 메시지
            action      : 권고 조치
    """
    smp_low = thresholds["smp_low"]
    smp_high = thresholds["smp_high"]

    alerts = []
    for hour, smp in enumerate(smp_series):
        row = hourly_df.iloc[hour] if hour < len(hourly_df) else None

        if smp <= 0:
            alerts.append({
                "hour": hour,
                "time_str": f"{hour:02d}:00",
                "smp": round(smp, 2),
                "severity": "critical",
                "alert_type": "SMP 제로",
                "title": f"[긴급] {hour:02d}시 SMP 제로 ({smp:.1f}원/kWh)",
                "message": (
                    f"{target_date} {hour:02d}시 SMP가 {smp:.1f}원/kWh로 "
                    f"제로 수준입니다. 역송 수익이 발생하지 않아 "
                    f"LNG발전 가동 시 연료비 전액 손실이 발생합니다."
                ),
                "action": "LNG발전 즉시 감발 또는 정지를 검토하십시오.",
            })

        elif smp < smp_low:
            best_mode = row["최적모드"] if row is not None else "-"
            alerts.append({
                "hour": hour,
                "time_str": f"{hour:02d}:00",
                "smp": round(smp, 2),
                "severity": "warning",
                "alert_type": "SMP 경제성 한계",
                "title": f"[주의] {hour:02d}시 SMP 경제성 한계 ({smp:.1f}원 < {smp_low:.0f}원)",
                "message": (
                    f"{target_date} {hour:02d}시 SMP가 {smp:.1f}원/kWh로 "
                    f"LNG발전 2기 손익분기 SMP({smp_low:.1f}원) 미만입니다. "
                    f"2기 가동 시 연료비 회수가 어려울 수 있습니다. "
                    f"현재 최적모드: {best_mode}."
                ),
                "action": "1기 또는 2기 저부하 운전으로의 전환을 검토하십시오.",
            })

        elif smp >= smp_high:
            steam_bep = None
            steam_viable = "-"
            if row is not None:
                steam_bep = row.get("기력BEP_사용단가($/MMBtu)", None)
                if steam_bep is not None and steam_bep == steam_bep:  # not NaN
                    steam_viable = "가동 가능" if steam_bep > lng_price else "가동 불가"

            bep_info = ""
            if steam_bep is not None and steam_bep == steam_bep:
                bep_info = f" 기력발전 BEP: {steam_bep:.1f}$/MMBtu ({steam_viable})."

            alerts.append({
                "hour": hour,
                "time_str": f"{hour:02d}:00",
                "smp": round(smp, 2),
                "severity": "info",
                "alert_type": "SMP 과대",
                "title": f"[참고] {hour:02d}시 SMP 과대 ({smp:.1f}원 ≥ {smp_high:.0f}원)",
                "message": (
                    f"{target_date} {hour:02d}시 SMP가 {smp:.1f}원/kWh로 "
                    f"기력발전 손익분기 SMP({smp_high:.1f}원) 이상입니다. "
                    f"기력발전에 LNG 추가 점화 시 추가 수익 확보가 가능할 수 있습니다."
                    f"{bep_info}"
                ),
                "action": "기력발전 LNG 점화를 검토하십시오.",
            })

    return alerts


# ──────────────────────────────────────────────────────────────
# 통합 가이던스 생성
# ──────────────────────────────────────────────────────────────

def generate_full_guidance(
    target_date: date,
    hourly_df: pd.DataFrame,
    smp_series: list[float],
    thresholds: dict,
    lng_price: float,
    exchange_rate: float,
    lng_heat: float,
    is_spot: bool = False,
) -> dict:
    """
    F5.1~F5.3 통합 가이던스 생성.

    Returns:
        dict:
            hourly_plan     : F5.1 시간별 가동계획표 (list[dict])
            daily_summary   : F5.2 일간 요약 리포트 (dict)
            alerts          : F5.3 이상구간 경고 메시지 (list[dict])
            text_report     : 전체 텍스트 리포트 (str)
    """
    hourly_plan = generate_hourly_plan(hourly_df, smp_series, thresholds, lng_price)
    daily_summary = generate_daily_summary(
        target_date, hourly_df, smp_series, thresholds,
        lng_price, exchange_rate, lng_heat, is_spot, hourly_plan,
    )
    alerts = generate_alert_messages(
        target_date, smp_series, thresholds, hourly_df, lng_price,
    )

    text_report = format_text_report(target_date, hourly_plan, daily_summary, alerts)
    kakao_message = format_kakao_message(target_date, hourly_plan, daily_summary)

    return {
        "hourly_plan": hourly_plan,
        "daily_summary": daily_summary,
        "alerts": alerts,
        "text_report": text_report,
        "kakao_message": kakao_message,
    }


# ──────────────────────────────────────────────────────────────
# 텍스트 리포트 포매터
# ──────────────────────────────────────────────────────────────

def format_text_report(
    target_date: date,
    hourly_plan: list[dict],
    daily_summary: dict,
    alerts: list[dict],
) -> str:
    """구조화된 텍스트 리포트 생성."""
    weekday = daily_summary["weekday"]
    price_type = daily_summary["price_type"]

    lines = []
    lines.append("=" * 70)
    lines.append(f"  LNG 발전 가동 가이던스 - {target_date} ({weekday})")
    lines.append(f"  LNG가격: {daily_summary['lng_price']} $/MMBtu ({price_type})"
                 f"  |  환율: {daily_summary['exchange_rate']:,.0f}원/$"
                 f"  |  열량: {daily_summary['lng_heat']} Mcal/Nm³")
    lines.append("=" * 70)

    # ── 경고 메시지 (F5.3) ────────────────────────────────
    if alerts:
        lines.append("")
        lines.append("  ■ 이상구간 경고")
        lines.append("  " + "-" * 66)
        for a in alerts:
            lines.append(f"  {a['title']}")
            lines.append(f"    → {a['action']}")
        lines.append("")

    # ── 시간별 가동계획표 (F5.1) ──────────────────────────
    lines.append("  ■ 시간별 가동계획표")
    lines.append("  " + "-" * 66)
    lines.append(
        f"  {'시간':>5}  {'SMP':>7}  {'최적모드':>8}  {'판단':>10}  "
        f"{'BEP':>7}  {'경제성(억)':>10}  {'비고'}"
    )
    lines.append(
        f"  {'-----':>5}  {'-------':>7}  {'--------':>8}  {'----------':>10}  "
        f"{'-------':>7}  {'----------':>10}  {'----'}"
    )
    for p in hourly_plan:
        bep_str = f"{p['bep']:>6.2f}" if p["bep"] is not None else "     -"
        lines.append(
            f"  {p['time_str']:>5}  {p['smp']:>6.1f}  {p['best_mode']:>8}  "
            f"{p['action']:>10}  {bep_str}  {p['econ_bil']:>9.3f}  {p['note']}"
        )

    # ── 일간 요약 (F5.2) ─────────────────────────────────
    lines.append("")
    lines.append("  ■ 일간 요약")
    lines.append("  " + "-" * 66)
    lines.append(
        f"  SMP: 평균 {daily_summary['smp_avg']:.1f} | "
        f"최대 {daily_summary['smp_max']:.1f} | "
        f"최소 {daily_summary['smp_min']:.1f} 원/kWh"
    )
    lines.append(
        f"  임계값: 감발 < {daily_summary['smp_low_thresh']:.1f}원 | "
        f"기력점화 ≥ {daily_summary['smp_high_thresh']:.1f}원"
    )

    lines.append(f"  최적모드 분포: ", )
    mode_parts = [f"{m} {h}시간" for m, h in daily_summary["mode_dist"].items()]
    lines[-1] += "  ".join(mode_parts)

    lines.append(f"  모드별 일일 경제성:")
    for m, v in daily_summary["econ_totals"].items():
        lines.append(f"    {m}: {v:+.3f} 억원")

    # 이상구간 요약
    if daily_summary["anomaly_hours"]:
        lines.append(f"  이상구간:")
        for atype, hrs in daily_summary["anomaly_hours"].items():
            lines.append(f"    {atype}: {_fmt_hours(hrs)}")

    # 종합 권고
    lines.append("")
    lines.append("  ■ 종합 운전 권고")
    lines.append("  " + "-" * 66)
    for rec_line in daily_summary["recommendation"].split("\n"):
        lines.append(f"  {rec_line}")

    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# 카카오톡 메시지 포매터
# ──────────────────────────────────────────────────────────────

def _summarize_mode_ranges(hourly_plan: list[dict], hours: list[int]) -> list[str]:
    """
    시간 리스트에서 연속 동일 모드 구간을 압축.
    예: [(22,'2기'),(23,'2기'),(0,'1기'),(1,'1기')] -> ['22~23시 : 2기 full', '00~01시 : 1기']
    """
    MODE_DISPLAY = {
        "2기": "2기 full",
        "2기저부하": "2기 half",
        "1기": "1기",
        "정지": "정지",
    }

    if not hours:
        return []

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

    lines = []
    for s, e, mode in ranges:
        display = MODE_DISPLAY.get(mode, mode)
        if s == e:
            lines.append(f" - {s:02d}시 : {display}")
        else:
            lines.append(f" - {s:02d}~{e:02d}시 : {display}")
    return lines


def format_kakao_message(
    target_date: date,
    hourly_plan: list[dict],
    daily_summary: dict,
) -> str:
    """
    카카오톡 전파용 메시지 생성.

    포맷:
      안녕하십니까, M월D일 야간~M월D+1일 LNG발전 가동계획 안내드립니다.
      [M월D일 22시~M월D+1일 08시]
       - 00~00시 : 1기 or 2기 full or 2기 half
      [M월D+1일 주간(08~22시)]
       - 00~00시 : 1기 or 2기 full or 2기 half

      추가 안내드릴사항이 있으면 연락드리겠습니다.
      문의사항이 있으시면 연락주시기바랍니다.
      감사합니다!
    """
    from datetime import timedelta

    next_date = target_date + timedelta(days=1)

    m1, d1 = target_date.month, target_date.day
    m2, d2 = next_date.month, next_date.day

    # 야간: 22~23시(당일) + 00~07시(익일)
    night_hours = list(range(22, 24)) + list(range(0, 8))
    # 주간: 08~21시(익일)
    day_hours = list(range(8, 22))

    night_lines = _summarize_mode_ranges(hourly_plan, night_hours)
    day_lines = _summarize_mode_ranges(hourly_plan, day_hours)

    lines = []
    lines.append(f"안녕하십니까, {m1}월{d1}일 야간~{m2}월{d2}일 LNG발전 가동계획 안내드립니다.")
    lines.append("")
    lines.append(f"[{m1}월{d1}일 22시~{m2}월{d2}일 08시]")
    lines.extend(night_lines)
    lines.append("")
    lines.append(f"[{m2}월{d2}일 주간(08~22시)]")
    lines.extend(day_lines)
    lines.append("")
    lines.append("추가 안내드릴사항이 있으면 연락드리겠습니다.")
    lines.append("문의사항이 있으시면 연락주시기바랍니다.")
    lines.append("감사합니다!")

    return "\n".join(lines)
