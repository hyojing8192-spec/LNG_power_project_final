"""
economics_engine.py  (F3)
=========================
주요 함수:
  - get_season()             : 월 → 계절 문자열
  - get_load_type()          : 날짜·시간 → 부하 유형
  - get_elec_price()         : 날짜·시간 → 수전단가 (원/kWh)
  - calc_replace_cost()      : 대체단가 계산
  - calc_bep()               : LNG발전 BEP 계산
  - calc_steam_bep()         : 기력발전 BEP 계산  ← NEW (F3.3-S)
  - calc_economics()         : 경제성 차이 + 경제성(억원)
  - get_best_mode()          : 시간별 최적 운전모드 선정
  - build_hourly_table()     : 24시간 경제성 테이블 생성

[기력발전 BEP 추가 배경]
context 정의:
  SMP ≥ 170원/kWh 이상이면 기력발전에 LNG를 추가 점화하여 가동 → 추가 수익 확보
  기력발전 효율 기준값: 2.3 Mcal/kWh

  BEP 공식 (사용단가):
    BEP = 대체단가 / 효율 × 열량 × (1293 Nm³/ton) / (52 MMBtu/ton) / 환율

  BEP 공식 (Spot LNG):
    BEP = 대체단가 / 효율 × 열량 × (1293 Nm³/ton) / (52 MMBtu/ton) / 환율 - 0.8

  → calc_steam_bep() 는 이 두 케이스를 is_spot 인자로 분기한다.
  → build_hourly_table() 은 SMP ≥ smp_high(동적 임계값)인 시간에
    calc_steam_bep() 결과를 함께 출력한다.
"""

from __future__ import annotations

import pandas as pd
import numpy as np
from datetime import date, datetime
from config import (
    ELEC_RATES, LEGAL_HOLIDAYS, SPRING_FALL_DISCOUNT_HOURS,
    OVERHEAD_COST,
    MODE_LABELS,
)

# 연료량 → MMBtu 환산
NM3_PER_TON = 1293.0      # Nm³/ton
MMBTU_PER_TON = 52.0      # MMBtu/ton

# 기력발전 기본 효율 (Mcal/kWh)
STEAM_EFFICIENCY: float = 2.3

# Spot LNG 제세금 ($/MMBtu)
OVERHEAD_SPOT: float = 0.8


# ──────────────────────────────────────────────────────────────
# F3.1  수전단가 결정
# ──────────────────────────────────────────────────────────────

def get_season(month: int) -> str:
    """월 번호 → 계절 키 반환."""
    if month in (6, 7, 8):
        return "summer"
    elif month in (3, 4, 5, 9, 10):
        return "spring_fall"
    return "winter"


def _is_legal_holiday(d: date) -> bool:
    return d in LEGAL_HOLIDAYS or d.weekday() == 6


def _is_saturday(d: date) -> bool:
    return d.weekday() == 5


def get_load_type(d: date, hour: int) -> str:
    """
    날짜·시간 → '경부하' / '중간부하' / '최대부하'.

    - 일요일·공휴일 → 전 시간 경부하
    - 토요일 → 08~22시 중간부하, 나머지 경부하
    - 겨울 평일 → 최대: 09~12, 16~19 / 중간: 08~09, 12~16, 19~22
    - 봄가을·여름 평일 → 최대: 15~21 / 중간: 08~15, 21~22
    """
    month = d.month
    if _is_legal_holiday(d):
        return "경부하"
    if _is_saturday(d):
        return "중간부하" if 8 <= hour < 22 else "경부하"

    season = get_season(month)
    if hour < 8 or hour >= 22:
        return "경부하"

    if season == "winter":
        if hour < 9 or (12 <= hour < 16) or (19 <= hour < 22):
            return "중간부하"
        return "최대부하"
    else:
        if hour < 15 or hour >= 21:
            return "중간부하"
        return "최대부하"


def get_elec_price(d: date, hour: int) -> float:
    """날짜·시간 → 실제 수전단가 (원/kWh). 봄가을 주말 11~13시 50% 할인 적용."""
    season    = get_season(d.month)
    load_type = get_load_type(d, hour)
    rate      = ELEC_RATES[season][load_type]

    is_weekend = _is_legal_holiday(d) or _is_saturday(d)
    if season == "spring_fall" and is_weekend and hour in SPRING_FALL_DISCOUNT_HOURS:
        rate = rate * 0.5

    return round(rate, 4)


# ──────────────────────────────────────────────────────────────
# F3.2  대체단가 계산
# ──────────────────────────────────────────────────────────────

def calc_replace_cost(
    mode: str,
    elec_price: float,
    smp: float,
    net_load_kw: float,
    lng_price: float,
    lng_heat: float,
    efficiency: float,
    exchange_rate: float,
) -> float:
    """
    대체단가 계산 (원/kWh).

    엑셀 공식 기준:
    - 1기:    (min(287.5, 순부하/1000) × 수전단가 + max(0, 287.5-순부하/1000) × SMP) / 287.5
    - 2기:    (max(0, min(575, 순부하/1000)-287.5) × 수전단가 + (287.5-위값) × SMP) / 287.5
    - low2gi: 수전단가
    """
    net_mw = net_load_kw / 1_000.0

    if mode == "1gi":
        mix = min(287.5, net_mw) * elec_price + max(0, 287.5 - net_mw) * smp
        return mix / 287.5

    elif mode == "2gi":
        above = max(0.0, min(575.0, net_mw) - 287.5)
        mix   = above * elec_price + (287.5 - above) * smp
        return mix / 287.5

    else:  # low2gi
        return elec_price


# ──────────────────────────────────────────────────────────────
# F3.3  BEP 계산 — LNG발전
# ──────────────────────────────────────────────────────────────

def calc_bep(
    mode: str,
    replace_cost: float,
    lng_heat: float,
    efficiency: float,
    exchange_rate: float,
) -> float | None:
    """
    LNG발전 BEP(Break-Even Point) 계산 ($/MMBtu).

    공식 (Spot 기준, OVERHEAD_COST=0.8):
        BEP = (대체단가 / 효율) × 열량 × (1293/52) / 환율 - 0.8

    사용단가 기준은 economics_engine 외부에서 OVERHEAD_COST=0으로 설정하거나
    calc_bep_by_type() 사용.

    저부하(low2gi) 효율이 0이면 LOW2GI_EFF_FALLBACK으로 대체.
    효율이 0이면 None 반환.
    """
    from config import LOW2GI_EFF_FALLBACK

    eff = efficiency
    if mode == "low2gi" and (eff == 0 or eff is None):
        eff = LOW2GI_EFF_FALLBACK
    if eff == 0 or eff is None:
        return None

    bep = (replace_cost / eff) * lng_heat * NM3_PER_TON / MMBTU_PER_TON / exchange_rate - OVERHEAD_COST
    return round(bep, 4)


def calc_bep_by_type(
    mode: str,
    replace_cost: float,
    lng_heat: float,
    efficiency: float,
    exchange_rate: float,
    is_spot: bool = False,
) -> float | None:
    """
    LNG발전 BEP를 사용단가 / Spot 기준으로 분기 계산.

    Args:
        is_spot : True → Spot LNG (제세금 0.8 가산)
                  False → 사용단가 (제세금 0)

    Returns:
        BEP ($/MMBtu) 또는 None
    """
    from config import LOW2GI_EFF_FALLBACK

    eff = efficiency
    if mode == "low2gi" and (eff == 0 or eff is None):
        eff = LOW2GI_EFF_FALLBACK
    if eff == 0 or eff is None:
        return None

    overhead = OVERHEAD_SPOT if is_spot else 0.0
    bep = (replace_cost / eff) * lng_heat * NM3_PER_TON / MMBTU_PER_TON / exchange_rate - overhead
    return round(bep, 4)


# ──────────────────────────────────────────────────────────────
# F3.3-S  BEP 계산 — 기력발전 (NEW)
# ──────────────────────────────────────────────────────────────

def calc_steam_bep(
    replace_cost: float,
    lng_heat: float,
    exchange_rate: float,
    is_spot: bool = False,
    efficiency: float = STEAM_EFFICIENCY,
) -> float | None:
    """
    기력발전 LNG 점화 BEP 계산 ($/MMBtu).

    context 정의:
      "역송단가가 170원/kWh 이상으로 올라가면 기력발전에도 LNG를 추가 점화"
      기력발전 효율 기준: 2.3 Mcal/kWh

    BEP 공식:
      사용단가: BEP = (대체단가 / 효율) × 열량 × 1293 / 52 / 환율
      Spot LNG: BEP = (대체단가 / 효율) × 열량 × 1293 / 52 / 환율 - 0.8

    Args:
        replace_cost  : 기력발전 대체단가 (원/kWh)
                        SMP ≥ smp_high 구간에서는 대체단가 ≈ SMP (역송 100% 근사)
        lng_heat      : LNG 열량 (Mcal/Nm³)
        exchange_rate : 환율 (원/$)
        is_spot       : True → Spot LNG 기준 (제세금 0.8$/MMBtu 차감)
                        False → 사용단가 기준 (제세금 없음)
        efficiency    : 기력발전 효율 (Mcal/kWh), 기본 2.3

    Returns:
        기력발전 BEP ($/MMBtu) 또는 None (효율=0 시)
    """
    if efficiency == 0 or efficiency is None:
        return None

    overhead = OVERHEAD_SPOT if is_spot else 0.0
    bep = (
        (replace_cost / efficiency)
        * lng_heat
        * NM3_PER_TON
        / MMBTU_PER_TON
        / exchange_rate
        - overhead
    )
    return round(bep, 4)


def is_steam_viable(
    steam_bep: float | None,
    lng_price: float,
) -> bool:
    """
    기력발전 LNG 점화가 경제성 있는지 판단.

    조건: steam_bep > lng_price (BEP보다 LNG가격이 낮아야 수익)
    """
    if steam_bep is None:
        return False
    return steam_bep > lng_price


# ──────────────────────────────────────────────────────────────
# F3.4  경제성 판단
# ──────────────────────────────────────────────────────────────

def calc_economics(
    mode: str,
    smp: float,
    replace_cost: float,
    exchange_rate: float,
    lng_gen_kw: float,
    efficiency: float,
    lng_heat: float,
    lng_price: float,
    bep: float | None,
) -> dict:
    """
    경제성 계산.

    경제성차이(원/kWh) = SMP - 대체단가
    경제성(억원) = (BEP - LNG가격)($/MMBtu)
                   × LNG발전량(kW)
                   × 효율(Mcal/kWh)
                   / 열량(Mcal/Nm³)
                   / 1293(Nm³/ton)
                   × 52(MMBtu/ton)
                   × 환율(원/$)
                   / 1e8

    Returns:
        {econ_diff, econ_bil, viable}
    """
    econ_diff = smp - replace_cost

    capacity = {
        "1gi":    287_500.0,
        "low2gi": 410_000.0,
        "2gi":    575_000.0,
    }.get(mode, lng_gen_kw if lng_gen_kw > 0 else 287_500.0)

    gen_kw = lng_gen_kw if (lng_gen_kw and lng_gen_kw > 0) else capacity

    if bep is not None and efficiency > 0 and lng_heat > 0:
        econ_bil = (
            (bep - lng_price)
            * gen_kw
            * efficiency
            / lng_heat
            / NM3_PER_TON
            * MMBTU_PER_TON
            * exchange_rate
            / 1e8
        )
    else:
        econ_bil = 0.0

    # 가동 판단: BEP > LNG가격이면 가동 (연료비 회수 가능)
    viable = bep is not None and bep > lng_price

    return {
        "bep":       round(float(bep), 4) if bep is not None else None,
        "econ_bil":  round(float(econ_bil), 6),
        "viable":    viable,
    }


# ──────────────────────────────────────────────────────────────
# F3.5  최적 운전모드 선정
# ──────────────────────────────────────────────────────────────

def get_best_mode(mode_results: dict[str, dict], lng_price: float) -> str:
    """
    최적 운전모드 반환.

    1기가 기본, 2기는 1기 + 추가 1기 가동이므로
    추가분의 BEP > LNG가격이면 무조건 2기가 유리.

    우선순위: 2기 > 2기저부하 > 1기 > 정지
      - 위에서부터 BEP > LNG가격 체크, 가동 가능하면 바로 선택
    """
    for mode in ["2gi", "low2gi", "1gi"]:
        res = mode_results.get(mode)
        if res and res.get("viable", False):
            return mode

    return "off"


# ──────────────────────────────────────────────────────────────
# 내부 유틸
# ──────────────────────────────────────────────────────────────

def _lng_gen_for_hour(
    hour: int,
    lng_gen_series: list[float | None] | None,
    net_load_kw: float,
) -> float:
    if lng_gen_series is not None and hour < len(lng_gen_series):
        v = lng_gen_series[hour]
        if v is not None:
            try:
                x = float(v)
                if x > 0 and np.isfinite(x):
                    return x
            except (TypeError, ValueError):
                pass
    return float(net_load_kw)


# ──────────────────────────────────────────────────────────────
# 24시간 경제성 테이블 생성
# ──────────────────────────────────────────────────────────────

def build_hourly_table(
    target_date: date,
    smp_series: list[float],
    lng_price: float,
    lng_heat: float,
    exchange_rate: float,
    pred_results: dict,
    net_load_kw: float = 280_000.0,
    lng_gen_series: list[float | None] | None = None,
    is_spot: bool = False,
    smp_high_threshold: float | None = None,
) -> pd.DataFrame:
    """
    24시간 경제성 분석 테이블 생성.

    smp_high_threshold를 넘는 시간에는 기력발전 BEP 컬럼도 함께 산출한다.
    is_spot 플래그로 사용단가 / Spot LNG BEP를 분기 계산한다.

    Args:
        target_date         : 분석 대상 날짜
        smp_series          : 시간별 SMP 24개
        lng_price           : 이번 달 LNG 가격 ($/MMBtu)
        lng_heat            : LNG 열량 (Mcal/Nm³)
        exchange_rate       : 환율 (원/$)
        pred_results        : {mode: {hour: {export, import_kw, efficiency}}}
        net_load_kw         : 기본 순부하 (발전량 미입력 시)
        lng_gen_series      : 시간별 LNG발전량 (None 가능)
        is_spot             : True → Spot LNG BEP 계산 (제세금 0.8 가산)
        smp_high_threshold  : 기력발전 검토 기준 SMP (None이면 기력 BEP 항상 계산)

    Returns:
        DataFrame — 컬럼:
          시간, SMP, 수전단가, 운전모드별 대체단가·BEP·경제성차이·경제성(억),
          최적모드, 기력발전BEP(사용단가), 기력발전BEP(Spot), 기력발전가동여부
    """
    from config import MODES

    records = []
    for hour in range(24):
        smp        = smp_series[hour] if hour < len(smp_series) else 0.0
        elec_price = get_elec_price(target_date, hour)
        lng_gen_h  = _lng_gen_for_hour(hour, lng_gen_series, net_load_kw)

        row_base = {
            "시간":            f"{hour:02d}:00",
            "SMP(원/kWh)":     smp,
            "수전단가(원/kWh)": elec_price,
        }

        mode_results: dict[str, dict] = {}

        for mode in MODES:
            preds = pred_results.get(mode, {}).get(hour, {})
            eff   = preds.get("efficiency", 0.0)

            if mode == "low2gi" and eff == 0.0:
                from config import LOW2GI_EFF_FALLBACK
                eff = LOW2GI_EFF_FALLBACK
            if eff == 0.0:
                eff = 1.595 if mode == "1gi" else 1.575

            rc   = calc_replace_cost(mode, elec_price, smp, net_load_kw,
                                     lng_price, lng_heat, eff, exchange_rate)
            bep  = calc_bep_by_type(mode, rc, lng_heat, eff, exchange_rate, is_spot=is_spot)
            econ = calc_economics(mode, smp, rc, exchange_rate,
                                  lng_gen_h, eff, lng_heat, lng_price, bep)

            mode_results[mode] = {**econ, "replace_cost": rc}

            bep_val = econ.get("bep")
            row_base[f"BEP_{MODE_LABELS[mode]}($/MMBtu)"] = (
                round(bep_val, 3) if bep_val is not None else np.nan
            )
            row_base[f"가동판단_{MODE_LABELS[mode]}"] = (
                "O" if econ["viable"] else "X"
            )
            row_base[f"경제성(억)_{MODE_LABELS[mode]}"] = round(econ["econ_bil"], 3)

        best = get_best_mode(mode_results, lng_price)
        row_base["최적모드"] = MODE_LABELS.get(best, best)

        # ── 기력발전 BEP (SMP 과대 구간이거나 threshold 미설정 시 항상 계산) ──
        need_steam = (smp_high_threshold is None) or (smp >= smp_high_threshold)
        if need_steam:
            # 기력발전 대체단가: SMP ≥ 170 이상이면 역송 100% → 대체단가 ≈ SMP
            steam_rc   = smp  # 역송 100% 근사
            bep_s_contract = calc_steam_bep(
                steam_rc, lng_heat, exchange_rate, is_spot=False
            )
            bep_s_spot = calc_steam_bep(
                steam_rc, lng_heat, exchange_rate, is_spot=True
            )
            viable_contract = is_steam_viable(bep_s_contract, lng_price)
            viable_spot     = is_steam_viable(bep_s_spot,     lng_price)

            row_base["기력BEP_사용단가($/MMBtu)"] = (
                round(bep_s_contract, 3) if bep_s_contract is not None else np.nan
            )
            row_base["기력BEP_Spot($/MMBtu)"]     = (
                round(bep_s_spot, 3)     if bep_s_spot     is not None else np.nan
            )
            row_base["기력발전_사용단가_가동가능"] = "✔ 가동" if viable_contract else "✘ 불가"
            row_base["기력발전_Spot_가동가능"]     = "✔ 가동" if viable_spot     else "✘ 불가"
        else:
            row_base["기력BEP_사용단가($/MMBtu)"] = np.nan
            row_base["기력BEP_Spot($/MMBtu)"]     = np.nan
            row_base["기력발전_사용단가_가동가능"] = "-"
            row_base["기력발전_Spot_가동가능"]     = "-"

        records.append(row_base)

    return pd.DataFrame(records)
