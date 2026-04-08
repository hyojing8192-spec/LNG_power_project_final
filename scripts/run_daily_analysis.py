"""
run_daily_analysis.py
=====================
SMP 수집 > 경제성 분석 > 결과 출력 (일일 실행용)

사용법:
  python run_daily_analysis.py              # 익일 SMP 수집 후 경제성 분석
  python run_daily_analysis.py --date 2026-04-08
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

# 프로젝트 루트 및 모듈 경로 추가
ROOT = Path(__file__).resolve().parent.parent          # 과제_최종/
_MODULES = ROOT / "modules"
for p in [str(ROOT), str(_MODULES)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from smp_collector import collect_smp, _get_target_dates
from economics_engine import build_hourly_table, get_elec_price
from anomaly_detector import calc_smp_thresholds
from ml_predictor import load_data, load_models, predict_day
from config import DEFAULT_LNG_PRICE, DEFAULT_LNG_HEAT, FALLBACK_EXCHANGE_RATE, MODE_LABELS


def run_analysis(target_date: date, lng_price: float, is_spot: bool = False):
    """SMP 수집 > ML 예측 > 경제성 테이블 생성 > 결과 출력."""

    print(f"\n{'='*65}")
    print(f"  LNG 발전 경제성 분석 -{target_date} ({['월','화','수','목','금','토','일'][target_date.weekday()]})")
    print(f"{'='*65}")

    # ── 1. SMP 수집 ───────────────────────────────────────────
    print("\n[1/4] SMP 수집 중...")
    smp_result = collect_smp(target_date)
    smp_series = smp_result["smp"]
    avg_smp = sum(smp_series) / 24

    status = "실시간" if smp_result["updated"] else "폴백(전일)"
    print(f"  > 소스: {smp_result['source']} ({status})")
    print(f"  > 평균 SMP: {avg_smp:.2f} 원/kWh  (최대 {max(smp_series):.2f}, 최소 {min(smp_series):.2f})")

    if not smp_result["updated"]:
        print("  [!] 실제 SMP 미수집 -전일 데이터로 분석합니다")

    # ── 2. 고정변수 ───────────────────────────────────────────
    # 학습 데이터에서 열량·환율 산출
    print("\n[2/4] 고정변수 설정...")
    df = load_data()
    lng_heat = round(float(df["lng_heat"].mean()), 4) if "lng_heat" in df.columns else DEFAULT_LNG_HEAT

    if "exchange_rate" in df.columns and "datetime" in df.columns:
        df_tmp = df.copy()
        df_tmp["_date"] = df_tmp["datetime"].dt.date
        last_date = df_tmp["_date"].max()
        prev = df_tmp[df_tmp["_date"] < last_date]
        if not prev.empty:
            prev_date = prev["_date"].max()
            exchange_rate = round(float(prev[prev["_date"] == prev_date]["exchange_rate"].mean()), 2)
        else:
            exchange_rate = round(float(df["exchange_rate"].mean()), 2)
    else:
        exchange_rate = float(FALLBACK_EXCHANGE_RATE)

    price_type = "Spot" if is_spot else "사용단가"
    print(f"  > LNG 가격: {lng_price} $/MMBtu ({price_type})")
    print(f"  > LNG 열량: {lng_heat} Mcal/Nm³ (학습데이터 평균)")
    print(f"  > 환율: {exchange_rate:,.2f} 원/$ (전일 평균)")

    # ── 3. ML 예측 + 경제성 계산 ──────────────────────────────
    print("\n[3/4] ML 예측 및 경제성 계산 중...")
    models, metrics = load_models(df)

    pred_results = predict_day(
        models, target_date, smp_series,
        lng_price, lng_heat, exchange_rate,
        elec_price_fn=get_elec_price,
    )

    thresholds = calc_smp_thresholds(lng_price, lng_heat, exchange_rate, is_spot=is_spot)

    hourly_df = build_hourly_table(
        target_date=target_date,
        smp_series=smp_series,
        lng_price=lng_price,
        lng_heat=lng_heat,
        exchange_rate=exchange_rate,
        pred_results=pred_results,
        is_spot=is_spot,
        smp_high_threshold=thresholds["smp_high"],
    )

    # ── 4. 결과 출력 ─────────────────────────────────────────
    print(f"\n[4/4] 분석 결과")

    print(f"\n  동적 임계값:")
    print(f"    LNG발전 BEP 임계 SMP: {thresholds['smp_low']:.2f} 원/kWh (이하 > 감발 검토)")
    print(f"    기력발전 BEP 임계 SMP: {thresholds['smp_high']:.2f} 원/kWh (이상 > 점화 검토)")

    # 시간별 요약
    print(f"\n  LNG가격: {lng_price} $/MMBtu -- BEP > {lng_price}이면 가동(O), BEP < {lng_price}이면 정지(X)")
    print(f"\n  {'시간':>5}  {'SMP':>7}  {'BEP_1기':>8}  {'BEP_2기저':>9}  {'BEP_2기':>8}  {'최적모드':>8}  {'경제성(억)':>10}  {'비고'}")
    print(f"  {'-'*5}  {'-'*7}  {'-'*8}  {'-'*9}  {'-'*8}  {'-'*8}  {'-'*10}  {'-'*15}")

    for _, row in hourly_df.iterrows():
        hour_str = row["시간"]
        smp = row["SMP(원/kWh)"]
        mode = row["최적모드"]

        bep_1 = row.get("BEP_1기($/MMBtu)", float("nan"))
        bep_low = row.get("BEP_2기저부하($/MMBtu)", float("nan"))
        bep_2 = row.get("BEP_2기($/MMBtu)", float("nan"))

        # 최적모드의 경제성(억)
        econ_col = f"경제성(억)_{mode}" if mode != "정지" else "경제성(억)_1기"
        econ_bil = row.get(econ_col, 0)

        def fmt_bep(v):
            if v != v: return "   -   "  # NaN
            marker = "O" if v > lng_price else "X"
            return f"{v:>6.2f}{marker}"

        # 비고
        note = ""
        if smp <= 0:
            note = "[!] SMP 제로"
        elif smp < thresholds["smp_low"]:
            note = "[!] 감발 검토"
        elif smp >= thresholds["smp_high"]:
            steam_bep = row.get("기력BEP_사용단가($/MMBtu)", None)
            if steam_bep and steam_bep == steam_bep:
                note = f"[*] 기력 BEP {steam_bep:.1f}"
            else:
                note = "[*] 기력 점화"

        print(f"  {hour_str:>5}  {smp:>6.1f}  {fmt_bep(bep_1)}  {fmt_bep(bep_low)}  {fmt_bep(bep_2)}  {mode:>8}  {econ_bil:>9.3f}  {note}")

    # 일일 요약
    print(f"\n  {'='*65}")
    print(f"  일일 요약")
    print(f"  {'='*65}")

    best_modes = hourly_df["최적모드"].value_counts()
    print(f"  최적모드 분포: ", end="")
    for mode, count in best_modes.items():
        print(f"{mode} {count}시간  ", end="")
    print()

    for label in ["1기", "2기저부하", "2기"]:
        col = f"경제성(억)_{label}"
        if col in hourly_df.columns:
            total = hourly_df[col].sum()
            print(f"  {label} 일일 경제성 합계: {total:.3f} 억원")

    # SMP 이상구간
    low_hours = [i for i, s in enumerate(smp_series) if 0 < s < thresholds["smp_low"]]
    high_hours = [i for i, s in enumerate(smp_series) if s >= thresholds["smp_high"]]
    zero_hours = [i for i, s in enumerate(smp_series) if s <= 0]

    if zero_hours:
        print(f"\n  [!] SMP 제로 구간: {[f'{h}시' for h in zero_hours]}")
    if low_hours:
        print(f"  [!] SMP 경제성 한계 구간 (<{thresholds['smp_low']:.0f}원): {[f'{h}시' for h in low_hours]}")
    if high_hours:
        print(f"  [*] 기력발전 점화 검토 구간 (≥{thresholds['smp_high']:.0f}원): {[f'{h}시' for h in high_hours]}")

    if not (zero_hours or low_hours or high_hours):
        print(f"\n  SMP 정상 범위 -이상구간 없음")

    print(f"\n{'='*65}\n")

    # CSV 저장
    out_path = ROOT / "data" / f"경제성분석_{target_date}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    hourly_df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"  CSV 저장: {out_path}")

    return hourly_df


def main():
    parser = argparse.ArgumentParser(description="SMP 수집 + LNG 발전 경제성 분석")
    parser.add_argument("--date", type=str, default=None, help="분석 날짜 (기본: 내일)")
    parser.add_argument("--lng-price", type=float, default=DEFAULT_LNG_PRICE, help="LNG 가격 ($/MMBtu)")
    parser.add_argument("--spot", action="store_true", help="Spot LNG 적용")
    args = parser.parse_args()

    target = date.today() + timedelta(days=1)
    if args.date:
        target = date.fromisoformat(args.date)

    run_analysis(target, args.lng_price, args.spot)


if __name__ == "__main__":
    main()
