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

from smp_collector import collect_smp
from economics_engine import build_hourly_table, get_elec_price
from anomaly_detector import calc_smp_thresholds
from ml_predictor import load_data, load_models, predict_day
from guidance_generator import generate_full_guidance
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

    # ── 4. 가이던스 생성 (F5) ──────────────────────────────────
    print("\n[4/4] 가동 가이던스 생성 중...")

    guidance = generate_full_guidance(
        target_date=target_date,
        hourly_df=hourly_df,
        smp_series=smp_series,
        thresholds=thresholds,
        lng_price=lng_price,
        exchange_rate=exchange_rate,
        lng_heat=lng_heat,
        is_spot=is_spot,
    )

    print(guidance["text_report"])

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
