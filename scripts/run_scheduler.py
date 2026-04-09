"""
run_scheduler.py  (F7.1 + F7.2)
================================
SMP 수집 > 경제성 분석 > 가이던스 생성 > 메일 발송 통합 스케줄러.

실행 흐름:
  17:30  SMP 수집 시도 (KPX 크롤링)
  17:35  SMP 수집 성공 시 -> 경제성 분석 + 가이던스 + 메일 발송
         SMP 수집 실패 시 -> 18:00, 18:30, 19:00 재시도
  19:30  최종 시도 (폴백 데이터 포함)

사용법:
  python run_scheduler.py              # 스케줄러 시작 (백그라운드 상주)
  python run_scheduler.py --now        # 즉시 1회 실행 (테스트용)
  python run_scheduler.py --time 17:30 # 특정 시간에 1회 실행
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# 프로젝트 루트 및 모듈 경로
ROOT = Path(__file__).resolve().parent.parent
_MODULES = ROOT / "modules"
for p in [str(ROOT), str(_MODULES)]:
    if p not in sys.path:
        sys.path.insert(0, p)

# ── 로깅 설정 (F7.2) ────────────────────────────────────────
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "scheduler.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("scheduler")

# ── 모듈 임포트 ─────────────────────────────────────────────
from smp_collector import collect_smp
from economics_engine import build_hourly_table, get_elec_price
from anomaly_detector import calc_smp_thresholds
from ml_predictor import load_data, load_models, predict_day
from guidance_generator import generate_full_guidance
from mail_sender import send_daily_report, send_urgent_alert, _is_configured
from kakao_sender import send_kakao_guidance
from config import DEFAULT_LNG_PRICE, DEFAULT_LNG_HEAT, FALLBACK_EXCHANGE_RATE


def run_pipeline(target_date: date | None = None, lng_price: float = DEFAULT_LNG_PRICE, is_spot: bool = False):
    """
    전체 파이프라인 실행: SMP 수집 > 분석 > 가이던스 > 메일.

    Args:
        target_date : 분석 날짜 (None이면 익일)
        lng_price   : LNG 가격 ($/MMBtu)
        is_spot     : Spot LNG 여부
    """
    if target_date is None:
        target_date = date.today() + timedelta(days=1)

    logger.info(f"===== 파이프라인 시작: {target_date} =====")

    # ── 1. SMP 수집 ───────────────────────────────────
    logger.info("[1/5] SMP 수집 중...")
    try:
        smp_result = collect_smp(target_date)
        smp_series = smp_result["smp"]
        avg_smp = sum(smp_series) / 24
        status = "실시간" if smp_result["updated"] else "폴백(전일)"
        logger.info(f"  SMP 수집 완료: {smp_result['source']} ({status}), 평균 {avg_smp:.1f}원")

        if not smp_result["updated"]:
            logger.warning("  실제 SMP 미수집 - 전일 데이터로 분석")
    except Exception as e:
        logger.error(f"  SMP 수집 실패: {e}")
        return False

    # ── 2. 고정변수 ───────────────────────────────────
    logger.info("[2/5] 고정변수 설정...")
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

        logger.info(f"  LNG: {lng_price}$/MMBtu, 열량: {lng_heat}, 환율: {exchange_rate:,.0f}원/$")
    except Exception as e:
        logger.error(f"  고정변수 설정 실패: {e}")
        lng_heat = DEFAULT_LNG_HEAT
        exchange_rate = float(FALLBACK_EXCHANGE_RATE)

    # ── 3. ML 예측 + 경제성 계산 ──────────────────────
    logger.info("[3/5] ML 예측 및 경제성 계산...")
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
    except Exception as e:
        logger.error(f"  분석 실패: {e}")
        return False

    # ── 4. 가이던스 생성 (F5) ─────────────────────────
    logger.info("[4/5] 가이던스 생성...")
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

        # CSV 저장
        out_path = ROOT / "data" / f"경제성분석_{target_date}.csv"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        hourly_df.to_csv(out_path, index=False, encoding="utf-8-sig")
        logger.info(f"  CSV 저장: {out_path}")
    except Exception as e:
        logger.error(f"  가이던스 생성 실패: {e}")
        return False

    # ── 5. 메일 발송 (F6) ─────────────────────────────
    logger.info("[5/5] 메일 발송...")
    if _is_configured():
        try:
            # F6.2 긴급 알림 (critical/warning만)
            urgent_sent = send_urgent_alert(target_date, alerts)
            if urgent_sent:
                logger.info("  긴급 알림 발송 완료")

            # F6.1 정기 리포트 (차트 이미지 포함)
            daily_sent = send_daily_report(
                target_date, summary, alerts, plan,
                hourly_df, guidance["text_report"],
                smp_series=smp_series, thresholds=thresholds,
            )
            if daily_sent:
                logger.info("  정기 리포트 발송 완료")
            else:
                logger.warning("  정기 리포트 발송 실패")
        except Exception as e:
            logger.error(f"  메일 발송 실패: {e}")
    else:
        logger.info("  메일 설정 미완료 - 발송 생략 (config.py MAIL_* 설정 필요)")

    # ── 5-2. 카카오톡 발송 ────────────────────────────
    logger.info("[5-2/5] 카카오톡 발송...")
    try:
        kakao_sent = send_kakao_guidance(guidance["kakao_message"])
        if kakao_sent:
            logger.info("  카카오톡 발송 완료")
        else:
            logger.info("  카카오톡 미설정 또는 발송 실패")
    except Exception as e:
        logger.error(f"  카카오톡 발송 실패: {e}")

    # 콘솔 출력
    print(guidance["text_report"])
    print("\n" + "=" * 70)
    print("  [카카오톡 메시지]")
    print("=" * 70)
    print(guidance["kakao_message"])
    print("=" * 70)

    logger.info(f"===== 파이프라인 완료: {target_date} =====")
    return True


# ──────────────────────────────────────────────────────────────
# F7.1  APScheduler 스케줄링
# ──────────────────────────────────────────────────────────────

def start_scheduler():
    """
    APScheduler로 매일 자동 실행.

    스케줄:
      17:30  1차 시도
      18:00  2차 시도 (1차 실패 시)
      18:30  3차 시도
      19:00  4차 시도
      19:30  최종 시도
    """
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler()

    run_times = ["17:30", "18:00", "18:30", "19:00", "19:30"]

    for t in run_times:
        hour, minute = map(int, t.split(":"))
        scheduler.add_job(
            run_pipeline,
            trigger="cron",
            hour=hour,
            minute=minute,
            id=f"pipeline_{t.replace(':','')}",
            replace_existing=True,
            misfire_grace_time=300,
        )
        logger.info(f"  스케줄 등록: 매일 {t}")

    logger.info("스케줄러 시작. Ctrl+C로 종료.")
    logger.info(f"다음 실행 예정:")
    for job in scheduler.get_jobs():
        logger.info(f"  {job.id}: {job.next_run_time}")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("스케줄러 종료.")


def main():
    parser = argparse.ArgumentParser(description="LNG 발전 경제성 자동 분석 스케줄러")
    parser.add_argument("--now", action="store_true", help="즉시 1회 실행")
    parser.add_argument("--date", type=str, default=None, help="분석 날짜 (YYYY-MM-DD)")
    parser.add_argument("--lng-price", type=float, default=DEFAULT_LNG_PRICE, help="LNG 가격")
    parser.add_argument("--spot", action="store_true", help="Spot LNG")
    args = parser.parse_args()

    if args.now or args.date:
        target = date.today() + timedelta(days=1)
        if args.date:
            target = date.fromisoformat(args.date)
        run_pipeline(target, args.lng_price, args.spot)
    else:
        start_scheduler()


if __name__ == "__main__":
    main()
