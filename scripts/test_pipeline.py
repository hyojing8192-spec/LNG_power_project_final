"""
test_pipeline.py
================
스케줄러 없이 전체 파이프라인을 1회 테스트.
SMP 수집 > 경제성 분석 > 가이던스 > 메일 > 카카오톡 전파까지 동일한 로직.

사용법:
  python scripts/test_pipeline.py --date 2026-04-13
  python scripts/test_pipeline.py --date 2026-04-13 --spot
  python scripts/test_pipeline.py --date 2026-04-13 --lng-price 12.5
  python scripts/test_pipeline.py --date 2026-04-13 --skip-kmos
  python scripts/test_pipeline.py --date 2026-04-13 --skip-send

옵션:
  --date        분석 대상 날짜 (필수, YYYY-MM-DD)
  --lng-price   LNG 가격 (기본 11.0 $/MMBtu)
  --spot        Spot LNG ���용
  --skip-kmos   ePower KMOS 다운로드 생략 (캐시/KPX만 사용)
  --skip-send   메일·카카오톡 발송 생략 (분석 결과만 확인)
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# 프로젝트 루트 및 모듈 경로
ROOT = Path(__file__).resolve().parent.parent
_MODULES = ROOT / "modules"
for p in [str(ROOT), str(_MODULES)]:
    if p not in sys.path:
        sys.path.insert(0, p)

# ── 로깅 ────────────────────────────────────────────────────
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "test_pipeline.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("test_pipeline")

# ── 모듈 임포트 ─────────────────────────────────────────────
from smp_collector import collect_smp
from economics_engine import build_hourly_table, get_elec_price
from anomaly_detector import calc_smp_thresholds
from ml_predictor import load_data, load_models, predict_day
from guidance_generator import generate_full_guidance
from mail_sender import send_daily_report, send_urgent_alert, _is_configured
from kakao_sender import send_kakao_guidance
from config import DEFAULT_LNG_PRICE, DEFAULT_LNG_HEAT, FALLBACK_EXCHANGE_RATE


def run_test(target_date: date, lng_price: float, is_spot: bool,
             skip_kmos: bool, skip_send: bool):
    """전체 파이프라인 1회 테스트 실행."""

    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    day_name = weekdays[target_date.weekday()]

    print(f"\n{'='*65}")
    print(f"  [테스트] 전체 파이프라인 - {target_date} ({day_name})")
    print(f"{'='*65}")

    results = {}  # 단계별 성공/실패 추적

    # ── 0. KMOS SMP 다운로드 ──────────────────────────────
    if skip_kmos:
        logger.info("[0/6] KMOS 다운로드 생략 (--skip-kmos)")
        results["KMOS 다운로드"] = "-- 생략"
    else:
        logger.info("[0/6] ePower 마켓 KMOS SMP 다운로드...")
        try:
            from kmos_smp_download import download_smp_from_kmos
            download_smp_from_kmos()
            logger.info("  KMOS 다운로드 완료")
            results["KMOS 다운로드"] = "[OK] 성공"
        except Exception as e:
            logger.warning(f"  KMOS 다운로드 실패: {e}")
            results["KMOS 다운로드"] = f"[WARN] 실패 ({e})"

    # ── 1. SMP 수집 ───────────────────────────────────────
    logger.info("[1/6] SMP 수집 중...")
    try:
        smp_result = collect_smp(target_date)
        smp_series = smp_result["smp"]
        avg_smp = sum(s for s in smp_series if not (isinstance(s, float) and math.isnan(s))) / 24
        status = "실시간" if smp_result["updated"] else "폴백(전일)"
        logger.info(f"  소스: {smp_result['source']} ({status}), 평균 {avg_smp:.1f}원")

        has_real_smp = smp_result.get("updated", False) and not all(
            (v == 0 or (isinstance(v, float) and math.isnan(v))) for v in smp_series
        )
        if not has_real_smp:
            logger.warning(f"  SMP 실데이터 없음 ({smp_result['source']}) → 파이프라인 중단")
            results["SMP 수집"] = f"[FAIL] 실데이터 없음 (source: {smp_result['source']})"
            _print_summary(results)
            return False

        results["SMP 수집"] = f"[OK] {smp_result['source']} (평균 {avg_smp:.1f}원)"
    except Exception as e:
        logger.error(f"  SMP 수집 실패: {e}")
        results["SMP 수집"] = f"[FAIL] {e}"
        _print_summary(results)
        return False

    # ── 2. 고정변수 ───────────────────────────────────────
    logger.info("[2/6] 고정변수 설정...")
    try:
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
        logger.info(f"  LNG: {lng_price}$/MMBtu ({price_type}), 열량: {lng_heat}, 환율: {exchange_rate:,.0f}원/$")
        results["고정변수"] = f"[OK] LNG {lng_price}$, 환율 {exchange_rate:,.0f}원"
    except Exception as e:
        logger.error(f"  고정변수 설정 실패: {e}")
        lng_heat = DEFAULT_LNG_HEAT
        exchange_rate = float(FALLBACK_EXCHANGE_RATE)
        results["고정변수"] = f"[WARN] 폴백값 사용 ({e})"

    # ── 3. ML 예측 + 경제성 계산 ──────────────────────────
    logger.info("[3/6] ML 예측 및 경제성 계산...")
    try:
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
        logger.info(f"  임계값: 감발 < {thresholds['smp_low']:.1f}원, 기력 >= {thresholds['smp_high']:.1f}원")
        results["경제성 분석"] = f"[OK] 임계 {thresholds['smp_low']:.0f}~{thresholds['smp_high']:.0f}원"
    except Exception as e:
        logger.error(f"  분석 실패: {e}")
        results["경제성 분석"] = f"[FAIL] {e}"
        _print_summary(results)
        return False

    # ── 4. 가이던스 생성 ──────────────────────────────────
    logger.info("[4/6] 가이던스 생성...")
    try:
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
        summary = guidance["daily_summary"]
        alerts = guidance["alerts"]
        plan = guidance["hourly_plan"]

        logger.info(f"  최적모드: {summary['best_overall']}, 이상구간: {len(alerts)}건")

        out_path = ROOT / "data" / f"경제성분석_{target_date}.csv"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        hourly_df.to_csv(out_path, index=False, encoding="utf-8-sig")
        logger.info(f"  CSV 저장: {out_path}")

        results["가이던스"] = f"[OK] 최적 {summary['best_overall']}, 이상 {len(alerts)}건"
    except Exception as e:
        logger.error(f"  가이던스 생성 실패: {e}")
        results["가이던스"] = f"[FAIL] {e}"
        _print_summary(results)
        return False

    # ── 가이던스 콘솔 출력 ────────────────────────────────
    print(guidance["text_report"])

    # ── 4-2. D+1일 분석 (메일 차트용, 대시보드 동기화) ──
    next_day_plan = None
    next_day_smp = None
    next_date = target_date + timedelta(days=1)
    try:
        from smp_collector import collect_smp as _collect
        smp_next = _collect(next_date)
        if smp_next.get("updated"):
            next_day_smp = smp_next["smp"]
            from economics_engine import build_hourly_table as _bht, get_elec_price as _gep
            from ml_predictor import load_models as _lm, predict_day as _pd
            from anomaly_detector import calc_smp_thresholds as _cst
            _models_n, _ = _lm(df)
            _pred_n = _pd(_models_n, next_date, next_day_smp,
                          lng_price, lng_heat, exchange_rate, elec_price_fn=_gep)
            _hourly_n = _bht(target_date=next_date, smp_series=next_day_smp,
                             lng_price=lng_price, lng_heat=lng_heat, exchange_rate=exchange_rate,
                             pred_results=_pred_n, is_spot=is_spot,
                             smp_high_threshold=thresholds["smp_high"])
            from guidance_generator import generate_full_guidance as _gfg
            _g_n = _gfg(target_date=next_date, hourly_df=_hourly_n,
                        smp_series=next_day_smp, thresholds=thresholds,
                        lng_price=lng_price, exchange_rate=exchange_rate,
                        lng_heat=lng_heat, is_spot=is_spot)
            next_day_plan = _g_n["hourly_plan"]
            logger.info(f"  D+1({next_date}) 분석 완료 -> 메일 차트에 반영")
    except Exception as e:
        logger.info(f"  D+1({next_date}) 분석 생략: {e}")

    # ── 5. 메일 발송 ─────────────────────────────────────
    if skip_send:
        logger.info("[5/6] 메일 발송 생략 (--skip-send)")
        results["메일 발송"] = "-- 생략"
    elif not _is_configured():
        logger.info("[5/6] 메일 설정 미완료 -> 생략")
        results["메일 발송"] = "-- 미설정"
    else:
        logger.info("[5/6] 메일 발송...")
        try:
            urgent_sent = send_urgent_alert(target_date, alerts,
                                                smp_series=smp_series, thresholds=thresholds)
            daily_sent = send_daily_report(
                target_date, summary, alerts, plan,
                hourly_df, guidance["text_report"],
                smp_series=smp_series, thresholds=thresholds,
                next_day_plan=next_day_plan, next_day_smp=next_day_smp,
            )
            if daily_sent:
                msg = "[OK] 정기 리포트"
                if urgent_sent:
                    msg += " + 긴급 알림"
                results["메일 발송"] = msg
            else:
                results["메일 발송"] = "[FAIL] 발송 실패"
        except Exception as e:
            logger.error(f"  메일 발송 실패: {e}")
            results["메일 발송"] = f"[FAIL] {e}"

    # ── 6. 카카오톡 발송 ─────────────────────────────────
    if skip_send:
        logger.info("[6/6] 카카오톡 발송 생략 (--skip-send)")
        results["카카오톡"] = "-- 생략"
    else:
        logger.info("[6/6] 카카오톡 발송...")
        try:
            kakao_sent = send_kakao_guidance(guidance["kakao_message"])
            if kakao_sent:
                results["카카오톡"] = "[OK] 발송 완료"
            else:
                results["카카오톡"] = "[FAIL] 미설정 또는 발송 실패"
        except Exception as e:
            logger.error(f"  카카오톡 발송 실패: {e}")
            results["카카오톡"] = f"[FAIL] {e}"

    # ── 카카오톡 메시지 출력 ──────────────────────────────
    print("\n" + "=" * 70)
    print("  [카카오톡 메시지]")
    print("=" * 70)
    print(guidance["kakao_message"])
    print("=" * 70)

    # ── 결과 요약 ─────────────────────────────────────────
    _print_summary(results)
    return True


def _print_summary(results: dict):
    """단계별 결과 요약 테이블 출력."""
    print(f"\n{'='*65}")
    print("  테스트 결과 요약")
    print(f"{'='*65}")
    for step, status in results.items():
        print(f"  {step:<14} {status}")

    all_ok = all("[OK]" in s or "--" in s for s in results.values())
    has_fail = any("[FAIL]" in s for s in results.values())
    print(f"{'='*65}")
    if has_fail:
        print("  [FAIL] 파이프라인 실패 -위 항목을 확인하세요")
    elif all_ok:
        print("  [OK] 전체 파이프라인 정상 완료!")
    else:
        print("  [WARN] 일부 경고 발생 -결과를 확인하세요")
    print(f"{'='*65}\n")


def run_test_auto(base_date: date, lng_price: float, is_spot: bool,
                   skip_kmos: bool, skip_send: bool):
    """
    자동 다중 날짜 테스트 (금요일/공휴일 전날 → 주말·연휴+영업일까지).
    run_scheduler.py run_pipeline_multi와 동일 로직.

    모든 날짜를 먼저 분석한 뒤, 메일/카톡을 1건으로 통합 발송.
    SMP 실데이터가 없는 날짜는 자동 제외.
    """
    from date_utils import calc_target_dates
    from smp_collector import collect_smp
    from economics_engine import build_hourly_table, get_elec_price
    from anomaly_detector import calc_smp_thresholds
    from ml_predictor import load_data, load_models, predict_day
    from guidance_generator import generate_full_guidance, format_kakao_message_multi
    from mail_sender import send_multi_day_report, send_daily_report, send_urgent_alert, _is_configured
    from kakao_sender import send_kakao_guidance

    weekdays = ["월", "화", "수", "목", "금", "토", "일"]

    # ── 대상 날짜 계산 ──
    all_dates = calc_target_dates(base_date, include_base=True)

    print(f"\n{'='*65}")
    print(f"  [테스트] 다중 날짜 파이프라인 - 기준일 {base_date} ({weekdays[base_date.weekday()]})")
    print(f"  대상: {[f'{d}({weekdays[d.weekday()]})' for d in all_dates]}")
    print(f"{'='*65}")

    results = {}

    # ── 1. 고정변수 1회 로드 ──
    logger.info("[1] 고정변수 설정...")
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

    models, _ = load_models(df)
    thresholds = calc_smp_thresholds(lng_price, lng_heat, exchange_rate, is_spot=is_spot)
    logger.info(f"  LNG: {lng_price}$/MMBtu, 환율: {exchange_rate:,.0f}원/$")

    # ── 2. 각 날짜 SMP 수집 + 분석 (SMP 없으면 제외) ──
    daily_results = []
    all_alerts = []

    for target_d in all_dates:
        d_label = f"{target_d} ({weekdays[target_d.weekday()]})"
        logger.info(f"\n[2] {d_label} SMP 수집 및 분석...")

        try:
            smp_result = collect_smp(target_d)
            smp_series = smp_result["smp"]

            has_real = smp_result.get("updated", False) and not all(
                (v == 0 or (isinstance(v, float) and math.isnan(v))) for v in smp_series
            )
            if not has_real:
                logger.warning(f"  {d_label} SMP 실데이터 없음 -> 제외")
                results[str(target_d)] = f"-- SMP 없음 ({smp_result['source']})"
                continue

            avg_smp = sum(smp_series) / 24
            logger.info(f"  SMP: {smp_result['source']}, 평균 {avg_smp:.1f}원")

            pred = predict_day(models, target_d, smp_series,
                               lng_price, lng_heat, exchange_rate,
                               elec_price_fn=get_elec_price)
            hourly_df = build_hourly_table(
                target_date=target_d, smp_series=smp_series,
                lng_price=lng_price, lng_heat=lng_heat, exchange_rate=exchange_rate,
                pred_results=pred, is_spot=is_spot,
                smp_high_threshold=thresholds["smp_high"])

            guidance = generate_full_guidance(
                target_date=target_d, hourly_df=hourly_df,
                smp_series=smp_series, thresholds=thresholds,
                lng_price=lng_price, exchange_rate=exchange_rate,
                lng_heat=lng_heat, is_spot=is_spot)

            daily_results.append({
                "date": target_d,
                "hourly_plan": guidance["hourly_plan"],
                "daily_summary": guidance["daily_summary"],
                "alerts": guidance["alerts"],
                "smp_series": smp_series,
                "hourly_df": hourly_df,
                "thresholds": thresholds,
                "text_report": guidance["text_report"],
            })
            all_alerts.extend(guidance["alerts"])

            # CSV 저장
            out_path = ROOT / "data" / f"경제성분석_{target_d}.csv"
            hourly_df.to_csv(out_path, index=False, encoding="utf-8-sig")

            results[str(target_d)] = f"[OK] 평균 SMP {avg_smp:.1f}원, 이상 {len(guidance['alerts'])}건"
        except Exception as e:
            logger.error(f"  {d_label} 분석 실패: {e}")
            results[str(target_d)] = f"[FAIL] {e}"

    if not daily_results:
        results["전파"] = "[FAIL] 분석 결과 없음"
        _print_summary(results)
        return False

    # ── 콘솔 출력 ──
    for r in daily_results:
        print(r["text_report"])

    # ── 2-2. 마지막 날짜 D+1 분석 (카톡/메일 마지막 주간용) ──
    last_next_day_plan = None
    last_d = daily_results[-1]["date"]
    last_next = last_d + timedelta(days=1)
    # daily_results에 이미 있으면 불필요
    has_next = any(r["date"] == last_next for r in daily_results)
    if not has_next:
        try:
            _smp_nx = collect_smp(last_next)
            if _smp_nx.get("updated"):
                _pred_nx = predict_day(models, last_next, _smp_nx["smp"],
                                       lng_price, lng_heat, exchange_rate,
                                       elec_price_fn=get_elec_price)
                _hdf_nx = build_hourly_table(
                    target_date=last_next, smp_series=_smp_nx["smp"],
                    lng_price=lng_price, lng_heat=lng_heat, exchange_rate=exchange_rate,
                    pred_results=_pred_nx, is_spot=is_spot,
                    smp_high_threshold=thresholds["smp_high"])
                _g_nx = generate_full_guidance(
                    target_date=last_next, hourly_df=_hdf_nx,
                    smp_series=_smp_nx["smp"], thresholds=thresholds,
                    lng_price=lng_price, exchange_rate=exchange_rate,
                    lng_heat=lng_heat, is_spot=is_spot)
                last_next_day_plan = _g_nx["hourly_plan"]
                logger.info(f"  D+1({last_next}) 주간 분석 완료 -> 카톡/메일 반영")
        except Exception as e:
            logger.info(f"  D+1({last_next}) 주간 분석 생략: {e}")

    # ── 3. 통합 전파 (메일 1건 + 카톡 1건) ──
    kakao_msg = format_kakao_message_multi(base_date, daily_results,
                                           last_next_day_plan=last_next_day_plan)

    print("\n" + "=" * 70)
    print("  [카카오톡 메시지 (통합)]")
    print("=" * 70)
    print(kakao_msg)
    print("=" * 70)

    if skip_send:
        results["전파"] = "-- 생략 (--skip-send)"
    else:
        # 카카오톡 (1건)
        try:
            kakao_ok = send_kakao_guidance(kakao_msg)
            results["카카오톡"] = "[OK] 통합 1건" if kakao_ok else "[FAIL]"
        except Exception as e:
            results["카카오톡"] = f"[FAIL] {e}"

        # 메일 (1건)
        if _is_configured():
            try:
                if len(daily_results) > 1:
                    mail_ok = send_multi_day_report(daily_results,
                                                     last_next_day_plan=last_next_day_plan)
                else:
                    first = daily_results[0]
                    mail_ok = send_daily_report(
                        first["date"], first["daily_summary"], all_alerts,
                        first["hourly_plan"], first["hourly_df"],
                        first["text_report"],
                        smp_series=first["smp_series"], thresholds=first["thresholds"],
                    )
                results["메일"] = "[OK] 통합 1건" if mail_ok else "[FAIL]"
            except Exception as e:
                results["메일"] = f"[FAIL] {e}"
        else:
            results["메일"] = "-- 미설정"

    _print_summary(results)


def main():
    parser = argparse.ArgumentParser(
        description="LNG 발전 경제성 전체 파이프라인 테스트 (스케줄러 없이 1회 실행)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  python scripts/test_pipeline.py --date 2026-04-13
  python scripts/test_pipeline.py --date 2026-04-13 --skip-kmos
  python scripts/test_pipeline.py --date 2026-04-13 --skip-send
  python scripts/test_pipeline.py --date 2026-04-10 --auto            # 금요일 → 토·일·월 자동
  python scripts/test_pipeline.py --date 2026-04-13 --spot --lng-price 12.5
        """,
    )
    parser.add_argument("--date", type=str, required=True, help="분석 대상 날짜 (YYYY-MM-DD)")
    parser.add_argument("--auto", action="store_true", help="금요일/공휴일 자동 다중 날짜 (토~월 등)")
    parser.add_argument("--lng-price", type=float, default=DEFAULT_LNG_PRICE, help="LNG 가격 (기본 11.0 $/MMBtu)")
    parser.add_argument("--spot", action="store_true", help="Spot LNG 적용")
    parser.add_argument("--skip-kmos", action="store_true", help="ePower KMOS 다운로드 생략")
    parser.add_argument("--skip-send", action="store_true", help="메일·카카오톡 발송 생략 (분석만)")
    args = parser.parse_args()

    target = date.fromisoformat(args.date)

    if args.auto:
        run_test_auto(target, args.lng_price, args.spot, args.skip_kmos, args.skip_send)
    else:
        run_test(target, args.lng_price, args.spot, args.skip_kmos, args.skip_send)


if __name__ == "__main__":
    main()
