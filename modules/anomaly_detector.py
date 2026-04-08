"""
anomaly_detector.py  (F4)
=========================
주요 함수:
  - calc_smp_thresholds()        : LNG가격·환율·모드로 SMP 동적 임계값 역산 (F4.0)
  - detect_smp_anomalies()       : SMP 이상구간 탐지 (F4.1)
  - detect_econ_change()         : 경제성 급변 구간 탐지 (F4.2)
  - build_smp_chart()            : Plotly SMP 시계열 + 이상구간 차트
  - build_econ_change_chart()    : Plotly 경제성 급변 차트

[동적 임계값 역산 원리]
BEP 정방향:
    BEP($/MMBtu) = (대체단가 / 효율) × 열량 × (1293 Nm³/ton) / (52 MMBtu/ton) / 환율 - 제세금

역산 목표: BEP = LNG가격 일 때의 대체단가(원/kWh)를 구한다.
    대체단가_BEP = (LNG가격 + 제세금) × 환율 × 52 / (1293 × 열량) × 효율

LNG발전 2기 가동 시 역송 비율이 높아 대체단가 ≈ SMP (역송 100% 근사)
따라서:
    smp_low  = 대체단가_BEP(eff_lng)   → 이 이하면 LNG발전 수익 불가, 감발 검토
    smp_high = 대체단가_BEP(eff_steam) → 이 이상이면 기력발전 LNG 점화 검토

LNG 가격 유형에 따른 제세금:
    사용단가 (계약분) : 이미 도입된 가격 → 제세금 0 (가격에 포함)
    Spot LNG          : 시장 구매가 → 도입 시 제세금 0.8$/MMBtu 추가
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from config import (
    SMP_ZERO_THRESHOLD,
    SMP_HIGH_THRESHOLD,   # 정적 폴백용 (기본 170)
    ECON_CHANGE_THRESHOLD,
    COLOR_ANOMALY,
)

# BEP 역산 물리 상수 (economics_engine.py와 동일 값 유지)
_NM3_PER_TON: float = 1293.0    # Nm³/ton
_MMBTU_PER_TON: float = 52.0    # MMBtu/ton
_OVERHEAD_SPOT: float = 0.8     # 제세금 — Spot LNG ($/MMBtu)
_OVERHEAD_CONTRACT: float = 0.0 # 제세금 — 사용단가/계약분 (가격에 이미 포함)

# 모드별 대표 발전효율 기본값 (Mcal/kWh)
_EFF_2GI: float = 1.575    # LNG발전 2기 정격
_EFF_STEAM: float = 2.3    # 기력발전 (증기터빈 LNG 점화)


# ──────────────────────────────────────────────────────────────
# F4.0  SMP 동적 임계값 역산
# ──────────────────────────────────────────────────────────────

def calc_smp_thresholds(
    lng_price: float,
    lng_heat: float,
    exchange_rate: float,
    is_spot: bool = False,
    eff_lng: float = _EFF_2GI,
    eff_steam: float = _EFF_STEAM,
) -> dict[str, float]:
    """
    이번 달 LNG가격·환율·열량을 기반으로 SMP 동적 임계값을 역산한다.

    Args:
        lng_price     : 이번 달 적용 LNG 가격 ($/MMBtu)
        lng_heat      : LNG 열량 (Mcal/Nm³), 예) 9.107
        exchange_rate : 환율 (원/$)
        is_spot       : True → Spot LNG (제세금 0.8$/MMBtu 가산)
                        False → 사용단가/계약분 (제세금 0, 가격에 포함)
        eff_lng       : LNG발전 효율 (Mcal/kWh), 기본 2기 대표값 1.575
        eff_steam     : 기력발전 효율 (Mcal/kWh), 기본 2.3

    Returns:
        dict:
            smp_low        : LNG발전 2기 손익분기 SMP (원/kWh) — 이 이하면 감발 검토
            smp_high       : 기력발전 LNG 점화 손익분기 SMP (원/kWh) — 이 이상이면 점화 검토
            lng_price      : 입력값 그대로
            is_spot        : 입력 플래그 그대로
            overhead_used  : 실제 적용한 제세금 ($/MMBtu)

    역산 공식:
        대체단가_BEP(원/kWh) = (lng_price + overhead)
                               × exchange_rate × MMBTU_PER_TON
                               / (NM3_PER_TON × lng_heat)
                               × eff
    """
    overhead = _OVERHEAD_SPOT if is_spot else _OVERHEAD_CONTRACT

    def _inverse_bep(eff: float) -> float:
        return (
            (lng_price + overhead)
            * exchange_rate
            * _MMBTU_PER_TON
            / (_NM3_PER_TON * lng_heat)
            * eff
        )

    return {
        "smp_low":       round(_inverse_bep(eff_lng),   2),
        "smp_high":      round(_inverse_bep(eff_steam),  2),
        "lng_price":     lng_price,
        "is_spot":       is_spot,
        "overhead_used": overhead,
    }


# ──────────────────────────────────────────────────────────────
# F4.1  SMP 이상구간 탐지
# ──────────────────────────────────────────────────────────────

def detect_smp_anomalies(
    df: pd.DataFrame,
    smp_low: float | None = None,
    smp_high: float | None = None,
    zero_threshold: float = SMP_ZERO_THRESHOLD,
    fallback_low: float = 100.0,
    fallback_high: float = SMP_HIGH_THRESHOLD,
) -> pd.DataFrame:
    """
    SMP 이상구간 탐지.

    이상 유형 (anomaly_type):
      "SMP 제로"        : SMP ≤ zero_threshold
                          → 역송 자체가 불가한 극단 구간
      "SMP 경제성 한계" : zero_threshold < SMP < smp_low
                          → LNG발전 가동 시 연료비 회수 불가, 감발 또는 정지 검토
      "SMP 과대"        : SMP ≥ smp_high
                          → 기력발전 LNG 점화까지 추가 수익 가능, 점화 검토

    Args:
        df             : 'datetime', 'smp' 컬럼 포함 DataFrame
        smp_low        : calc_smp_thresholds()['smp_low'] (동적 역산값)
                         None이면 fallback_low(기본 100 원/kWh) 사용
        smp_high       : calc_smp_thresholds()['smp_high'] (동적 역산값)
                         None이면 fallback_high(기본 SMP_HIGH_THRESHOLD) 사용
        zero_threshold : SMP 제로 기준 (기본 0)
        fallback_low   : smp_low 미입력 시 고정 하한 (원/kWh)
        fallback_high  : smp_high 미입력 시 고정 상한 (원/kWh)

    Returns:
        이상 행 DataFrame:
            columns = [datetime, smp, anomaly_type, threshold_low, threshold_high]
    """
    if "smp" not in df.columns:
        return pd.DataFrame(
            columns=["datetime", "smp", "anomaly_type", "threshold_low", "threshold_high"]
        )

    low  = smp_low  if smp_low  is not None else fallback_low
    high = smp_high if smp_high is not None else fallback_high

    mask_zero = df["smp"] <= zero_threshold
    mask_low  = (df["smp"] > zero_threshold) & (df["smp"] < low)
    mask_high = df["smp"] >= high

    results = []
    for _, row in df[mask_zero | mask_low | mask_high].iterrows():
        s = row["smp"]
        if s <= zero_threshold:
            atype = "SMP 제로"
        elif s < low:
            atype = "SMP 경제성 한계"
        else:
            atype = "SMP 과대"

        results.append({
            "datetime":       row["datetime"],
            "smp":            s,
            "anomaly_type":   atype,
            "threshold_low":  low,
            "threshold_high": high,
        })

    return pd.DataFrame(results)


# ──────────────────────────────────────────────────────────────
# F4.2  경제성 급변 구간 탐지
# ──────────────────────────────────────────────────────────────

def detect_econ_change(
    df: pd.DataFrame,
    econ_col: str = "econ_diff_2gi",
    threshold: float = ECON_CHANGE_THRESHOLD,
) -> pd.DataFrame:
    """
    전 시간 대비 경제성 변화량이 임계값을 초과하는 구간 탐지.

    Args:
        df:        'datetime', econ_col 컬럼 포함 DataFrame
        econ_col:  경제성 컬럼명
        threshold: 변화량 임계값 (원/kWh)

    Returns:
        급변 행 DataFrame [datetime, econ_val, delta, direction]
    """
    if econ_col not in df.columns:
        return pd.DataFrame()

    df = df.copy().sort_values("datetime")
    df["_delta"] = df[econ_col].diff().abs()

    mask   = df["_delta"] >= threshold
    result = df[mask][["datetime", econ_col, "_delta"]].copy()
    result.columns = ["datetime", "econ_val", "delta"]
    result["direction"] = df.loc[mask, econ_col].diff().apply(
        lambda x: "↑ 급등" if x > 0 else "↓ 급락"
    )
    return result.reset_index(drop=True)


# ──────────────────────────────────────────────────────────────
# Plotly 차트 — SMP 이상구간 시각화
# ──────────────────────────────────────────────────────────────

def build_smp_chart(
    df: pd.DataFrame,
    anomalies: pd.DataFrame,
    smp_low: float | None  = None,
    smp_high: float | None = None,
    fallback_low: float    = 100.0,
    fallback_high: float   = SMP_HIGH_THRESHOLD,
    is_spot: bool          = False,
) -> go.Figure:
    """
    SMP 시계열 라인차트 + 이상구간 마커 오버레이.

    임계선 3종:
      · 빨간 점선  : SMP=0
      · 주황 파선  : smp_low  (LNG발전 손익분기, 동적 or 100원 폴백)
      · 진주황 파선: smp_high (기력발전 손익분기, 동적 or 170원 폴백)

    Args:
        df         : 'datetime', 'smp' 컬럼 포함 DataFrame
        anomalies  : detect_smp_anomalies() 반환값
        smp_low    : 동적 하한 (None이면 fallback_low)
        smp_high   : 동적 상한 (None이면 fallback_high)
        is_spot    : 범례 표시용 구분 (Spot / 사용단가)
    """
    low  = smp_low  if smp_low  is not None else fallback_low
    high = smp_high if smp_high is not None else fallback_high
    price_type = "Spot" if is_spot else "사용단가"

    fig = go.Figure()

    # SMP 라인
    fig.add_trace(go.Scatter(
        x=df["datetime"], y=df["smp"],
        mode="lines", name="SMP (원/kWh)",
        line=dict(color="#4A90D9", width=1.5),
    ))

    # 이상치 마커 3종
    if not anomalies.empty:
        for atype, color, symbol in [
            ("SMP 제로",        COLOR_ANOMALY, "circle"),
            ("SMP 경제성 한계", "#FF6B35",     "square"),
            ("SMP 과대",        "#FF8C00",     "triangle-up"),
        ]:
            sub = anomalies[anomalies["anomaly_type"] == atype]
            if sub.empty:
                continue
            label = {
                "SMP 제로":        "SMP 제로",
                "SMP 경제성 한계": f"SMP 경제성 한계 (<{low:.0f}원, {price_type} BEP)",
                "SMP 과대":        f"SMP 과대 (≥{high:.0f}원, 기력발전 점화 검토)",
            }[atype]
            fig.add_trace(go.Scatter(
                x=sub["datetime"], y=sub["smp"],
                mode="markers", name=label,
                marker=dict(color=color, size=8, symbol=symbol),
            ))

    # 임계선 3종
    fig.add_hline(
        y=0, line_dash="dot", line_color=COLOR_ANOMALY, opacity=0.6,
        annotation_text="SMP=0",
    )
    fig.add_hline(
        y=low, line_dash="dash", line_color="#FF6B35", opacity=0.7,
        annotation_text=f"LNG발전 BEP ({price_type}) {low:.1f}원",
        annotation_position="top left",
    )
    fig.add_hline(
        y=high, line_dash="dash", line_color="#FF8C00", opacity=0.7,
        annotation_text=f"기력발전 BEP ({price_type}) {high:.1f}원",
        annotation_position="top left",
    )

    fig.update_layout(
        title=f"SMP 이상구간 탐지 — {price_type} 기준 동적 임계값",
        xaxis_title="시간",
        yaxis_title="SMP (원/kWh)",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        height=460,
    )
    return fig


def build_econ_change_chart(
    df: pd.DataFrame,
    change_df: pd.DataFrame,
    econ_col: str = "econ_diff_2gi",
) -> go.Figure:
    """
    경제성 시계열 + 급변 구간 마커 강조.

    Args:
        df:        원본 데이터 (datetime + econ_col)
        change_df: detect_econ_change() 반환값
    """
    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=df["datetime"], y=df[econ_col],
        mode="lines", name="경제성 차이 (원/kWh)",
        line=dict(color="#7ED321", width=1.5),
    ))

    fig.add_hline(y=0, line_dash="dash", line_color="#999", opacity=0.5)

    if not change_df.empty:
        fig.add_trace(go.Scatter(
            x=change_df["datetime"],
            y=change_df["econ_val"],
            mode="markers",
            name=f"급변 (|Δ|≥{ECON_CHANGE_THRESHOLD})",
            marker=dict(color=COLOR_ANOMALY, size=10, symbol="x"),
            hovertemplate="시간: %{x}<br>경제성: %{y:.2f}<extra></extra>",
        ))

    fig.update_layout(
        title="경제성 급변 구간 탐지",
        xaxis_title="시간",
        yaxis_title="경제성 차이 (원/kWh)",
        hovermode="x unified",
        height=420,
    )
    return fig
