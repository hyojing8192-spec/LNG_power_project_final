"""
run_scheduler.py  (F7.1 + F7.2)
================================
KMOS SMP 자동 다운로드 > 경제성 분석 > 가이던스 생성 > 메일/카톡 통합 발송 스케줄러.

실행 흐름:
  17:30  KMOS(ePower 마켓) SMP 엑셀 자동 다운로드 + SMP 확보 확인
         SMP 확보 시 -> 경제성 분석 + 가이던스 + 메일/카톡 통합 발송 -> 당일 완료
         SMP 미확보 시 -> 10분 후 재시도
  17:40  KMOS 재시도 (이전 성공 시 스킵)
  ...    10분 간격 반복
  19:30  최종 시도

대상 날짜:
  평일(월~목): 내일 1일만 분석 (메일: 오늘 야간~내일 주간)
  금요일/공휴일 전날: 기준일 + 연속 휴일 통합 분석 (메일/카톡 1건 통합 발송)

사용법:
  python run_scheduler.py              # 스케줄러 시작 (17:30~19:30 자동 실행)
  python run_scheduler.py --now        # 즉시 1회 실행 (테스트용)
  python run_scheduler.py --auto       # 자동 판단 실행 (평일/금요일/공휴일 자동)
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
from guidance_generator import generate_full_guidance, format_kakao_message_multi
from mail_sender import send_daily_report, send_multi_day_report, send_urgent_alert, _is_configured
from kakao_sender import send_kakao_guidance
from config import DEFAULT_LNG_PRICE, DEFAULT_LNG_HEAT, FALLBACK_EXCHANGE_RATE

# GUI 자동화 모듈: pyautogui 없는 환경(서버/GitHub Actions)에서는 생략
try:
    from kmos_smp_download import get_target_dates, download_multi_dates
    _KMOS_AVAILABLE = True
except Exception:
    _KMOS_AVAILABLE = False
    def download_multi_dates(*args, **kwargs): pass  # noqa: E301
    def get_target_dates(*args, **kwargs): return []  # noqa: E301


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

    # ── 0. ePower 마켓에서 SMP 엑셀 자동 다운로드 ──────────────
    logger.info("[0/5] ePower 마켓 KMOS SMP 다운로드...")
    try:
        from kmos_smp_download import download_smp_from_kmos
        download_smp_from_kmos()
        logger.info("  KMOS 다운로드 완료")
    except Exception as e:
        logger.warning(f"  KMOS 다운로드 실패 (수동 엑셀로 대체): {e}")

    # ── 1. SMP 수집 ───────────────────────────────────
    logger.info("[1/5] SMP 수집 중...")
    try:
        smp_result = collect_smp(target_date)
        smp_series = smp_result["smp"]
        avg_smp = sum(smp_series) / 24
        status = "실시간" if smp_result["updated"] else "폴백(전일)"
        logger.info(f"  SMP 수집 완료: {smp_result['source']} ({status}), 평균 {avg_smp:.1f}원")

        # SMP 실데이터가 없으면 전파하지 않음
        has_real_smp = smp_result.get("updated", False) and not all(
            (v == 0 or (isinstance(v, float) and math.isnan(v))) for v in smp_series
        )
        if not has_real_smp:
            logger.warning(f"  SMP 실데이터 없음 ({smp_result['source']}) → 분석·전파 생략, 다음 스케줄에서 재시도")
            return False
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

    # ── 4-2. D+1일 분석 (메일 차트용, 대시보드 동기화) ──
    next_day_plan = None
    next_day_smp = None
    next_date = target_date + timedelta(days=1)
    try:
        smp_next = collect_smp(next_date)
        if smp_next.get("updated"):
            next_day_smp = smp_next["smp"]
            pred_next = predict_day(
                models, next_date, next_day_smp,
                lng_price, lng_heat, exchange_rate,
                elec_price_fn=get_elec_price,
            )
            hourly_next = build_hourly_table(
                target_date=next_date, smp_series=next_day_smp,
                lng_price=lng_price, lng_heat=lng_heat, exchange_rate=exchange_rate,
                pred_results=pred_next, is_spot=is_spot,
                smp_high_threshold=thresholds["smp_high"],
            )
            guidance_next = generate_full_guidance(
                target_date=next_date, hourly_df=hourly_next,
                smp_series=next_day_smp, thresholds=thresholds,
                lng_price=lng_price, exchange_rate=exchange_rate,
                lng_heat=lng_heat, is_spot=is_spot,
            )
            next_day_plan = guidance_next["hourly_plan"]
            logger.info(f"  D+1({next_date}) 분석 완료 → 차트/메일에 반영")
    except Exception as e:
        logger.info(f"  D+1({next_date}) 분석 생략: {e}")

    # ── 5. 메일 발송 (F6) ─────────────────────────────
    logger.info("[5/5] 메일 발송...")
    if _is_configured():
        try:
            # F6.2 긴급 알림 (critical/warning만, 이상구간 차트 포함)
            urgent_sent = send_urgent_alert(target_date, alerts,
                                            smp_series=smp_series, thresholds=thresholds)
            if urgent_sent:
                logger.info("  긴급 알림 발송 완료")

            # F6.1 정기 리포트 (D일 22시 ~ D+1일 21시, 대시보드 동기화)
            daily_sent = send_daily_report(
                target_date, summary, alerts, plan,
                hourly_df, guidance["text_report"],
                smp_series=smp_series, thresholds=thresholds,
                next_day_plan=next_day_plan, next_day_smp=next_day_smp,
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


def run_pipeline_multi(
    base_date: date | None = None,
    lng_price: float = DEFAULT_LNG_PRICE,
    is_spot: bool = False,
):
    """
    다중 날짜 파이프라인: 자동으로 대상 날짜를 판단하여 전체 가이던스 생성 + 전파.

    - 평일(월~목): 오늘 22시 ~ 내일 22시
    - 금요일: 금 22시 ~ 월 22시
    - 공휴일 전날: 전날 22시 ~ 연휴 끝난 다음날 22시
    """
    if base_date is None:
        base_date = date.today()

    weekdays_kr = ["월","화","수","목","금","토","일"]

    from date_utils import calc_target_dates, is_holiday
    tomorrow = base_date + timedelta(days=1)
    if is_holiday(tomorrow):
        # 금요일/연휴 전날: 오늘 야간~연휴 끝 다음 영업일 주간
        target_dates = calc_target_dates(base_date, include_base=True)
    else:
        # 평일(월~목): 오늘 야간~내일 주간 (1일치)
        target_dates = [base_date]

    logger.info(f"===== 다중 날짜 파이프라인 시작: {base_date} ({weekdays_kr[base_date.weekday()]}) =====")
    logger.info(f"  대상 날짜: {len(target_dates)}일 - {[str(d) for d in target_dates]}")

    # ── 0. ePower 마켓에서 SMP 엑셀 자동 다운로드 ──────────────
    logger.info("[0] ePower 마켓 KMOS SMP 다운로드...")
    try:
        download_multi_dates(base_date)
        logger.info("  KMOS 다운로드 완료")
    except Exception as e:
        logger.warning(f"  KMOS 다운로드 실패 (수동 엑셀로 대체): {e}")

    # 고정변수 1회 로드
    logger.info("[1] 고정변수 설정...")
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

        models, metrics = load_models(df)
        thresholds = calc_smp_thresholds(lng_price, lng_heat, exchange_rate, is_spot=is_spot)
        logger.info(f"  LNG: {lng_price}$/MMBtu, 환율: {exchange_rate:,.0f}원/$")
    except Exception as e:
        logger.error(f"  고정변수/모델 로드 실패: {e}")
        return False

    # 각 날짜별 SMP 수집 + 경제성 분석
    daily_results = []
    all_alerts = []

    for target_d in target_dates:
        d_label = f"{target_d} ({weekdays_kr[target_d.weekday()]})"
        logger.info(f"\n[2] {d_label} SMP 수집 및 분석...")

        try:
            smp_result = collect_smp(target_d)
            smp_series = smp_result["smp"]
            avg_smp = sum(smp_series) / 24
            logger.info(f"  SMP: {smp_result['source']}, 평균 {avg_smp:.1f}원")

            # SMP 실데이터가 없으면 (폴백/nan) 분석·전파 건너뛰기
            has_real_smp = smp_result.get("updated", False) and not all(
                (v == 0 or (isinstance(v, float) and math.isnan(v))) for v in smp_series
            )
            if not has_real_smp:
                logger.warning(f"  {d_label} SMP 실데이터 없음 ({smp_result['source']}) → 전파 생략, 다음 스케줄에서 재시도")
                continue

            pred_results = predict_day(
                models, target_d, smp_series,
                lng_price, lng_heat, exchange_rate,
                elec_price_fn=get_elec_price,
            )
            hourly_df = build_hourly_table(
                target_date=target_d,
                smp_series=smp_series,
                lng_price=lng_price,
                lng_heat=lng_heat,
                exchange_rate=exchange_rate,
                pred_results=pred_results,
                is_spot=is_spot,
                smp_high_threshold=thresholds["smp_high"],
            )

            guidance = generate_full_guidance(
                target_date=target_d,
                hourly_df=hourly_df,
                smp_series=smp_series,
                thresholds=thresholds,
                lng_price=lng_price,
                exchange_rate=exchange_rate,
                lng_heat=lng_heat,
                is_spot=is_spot,
            )

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
            logger.info(f"  CSV: {out_path}")

        except Exception as e:
            logger.error(f"  {d_label} 분석 실패: {e}")

    if not daily_results:
        logger.error("분석 결과 없음. 종료.")
        return False

    # 필수 날짜 SMP 미확보 시 전파 차단
    # 평일: target_dates=[오늘]이지만, D+1(내일) SMP도 있어야 주간 가이던스 완성
    from date_utils import calc_target_dates as _calc
    required_dates = sorted(set(target_dates) | set(_calc(base_date, include_base=False)))
    collected_dates = {r["date"] for r in daily_results}
    missing = [d for d in required_dates if d not in collected_dates]
    if missing:
        # D+1은 daily_results가 아닌 별도 수집이므로 실제 확보 여부 재확인
        from modules.smp_collector import collect_smp as _cs
        import math as _math
        still_missing = []
        for d in missing:
            r = _cs(d)
            has = r.get("updated", False) and not all(
                (v == 0 or (isinstance(v, float) and _math.isnan(v))) for v in r.get("smp", [])
            )
            if not has:
                still_missing.append(d)
        if still_missing:
            logger.warning(f"  필수 날짜 SMP 미확보: {[str(d) for d in still_missing]} → 전파 생략, 재시도 대기")
            return False

    # 마지막 날짜 D+1 주간 분석 (카톡/메일 마지막 주간용)
    last_next_day_plan = None
    last_d = daily_results[-1]["date"]
    last_next = last_d + timedelta(days=1)
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
                logger.info(f"  D+1({last_next}) 주간 분석 완료 → 카톡/메일 반영")
        except Exception as e:
            logger.info(f"  D+1({last_next}) 주간 분석 생략: {e}")

    # 다중 날짜 카톡 메시지 생성
    kakao_msg = format_kakao_message_multi(base_date, daily_results,
                                           last_next_day_plan=last_next_day_plan)

    # ── 전파 ──
    logger.info(f"\n[3] 전파 ({len(daily_results)}일치)...")

    # 카카오톡
    try:
        kakao_sent = send_kakao_guidance(kakao_msg)
        if kakao_sent:
            logger.info("  카카오톡 발송 완료")
    except Exception as e:
        logger.error(f"  카카오톡 발송 실패: {e}")

    # 이메일 (다중 날짜면 전체, 단일이면 기존 방식)
    if _is_configured() and daily_results:
        try:
            if len(daily_results) > 1:
                send_multi_day_report(daily_results,
                                             last_next_day_plan=last_next_day_plan)
            else:
                first = daily_results[0]
                send_daily_report(
                    first["date"], first["daily_summary"], all_alerts,
                    first["hourly_plan"], first["hourly_df"],
                    first["text_report"],
                    smp_series=first["smp_series"], thresholds=first["thresholds"],
                )
            logger.info("  이메일 발송 완료")
        except Exception as e:
            logger.error(f"  이메일 발송 실패: {e}")

    # 콘솔 출력
    for r in daily_results:
        print(r["text_report"])

    print("\n" + "=" * 70)
    print("  [카카오톡 메시지]")
    print("=" * 70)
    print(kakao_msg)
    print("=" * 70)

    logger.info(f"===== 다중 날짜 파이프라인 완료: {len(daily_results)}일 =====")
    return True


# ──────────────────────────────────────────────────────────────
# F7.1  APScheduler 스케줄링
# ──────────────────────────────────────────────────────────────

def _try_kmos_and_collect(target_dates: list) -> bool:
    """
    KMOS에서 SMP 엑셀 다운로드 후 수집 시도.

    Returns:
        True: 모든 대상 날짜의 SMP 실데이터 확보됨
        False: 일부 또는 전체 미확보
    """
    # KMOS 다운로드
    try:
        download_multi_dates()
        logger.info("  KMOS 다운로드 완료")
    except Exception as e:
        logger.warning(f"  KMOS 다운로드 실패: {e}")

    # 수집 확인
    all_ok = True
    for d in target_dates:
        result = collect_smp(d)
        has_real = result.get("updated", False) and not all(
            (v == 0 or (isinstance(v, float) and math.isnan(v))) for v in result.get("smp", [])
        )
        if not has_real:
            logger.info(f"  {d} SMP 미확보 (source: {result['source']})")
            all_ok = False
        else:
            logger.info(f"  {d} SMP 확보 (source: {result['source']}, 평균 {sum(result['smp'])/24:.1f}원)")
    return all_ok


def start_scheduler():
    """
    매일 17:30~19:30 사이 10분 간격으로 KMOS SMP 다운로드 시도.
    SMP 확보되면 즉시 분석+전파 파이프라인 실행 후 당일 완료.
    확보 못 하면 10분 후 재시도, 19:30까지 반복.
    """
    from apscheduler.schedulers.blocking import BlockingScheduler

    _pipeline_done_date = {"value": None}  # 당일 파이프라인 완료 여부

    def _scheduled_job():
        now = datetime.now()
        today = date.today()

        # 17:30 이전 또는 19:30 이후면 스킵
        now_minutes = now.hour * 60 + now.minute
        if now_minutes < 17 * 60 + 30 or now_minutes > 19 * 60 + 30:
            return

        # 이미 오늘 파이프라인 완료했으면 스킵
        if _pipeline_done_date["value"] == today:
            logger.info(f"  오늘({today}) 이미 파이프라인 완료 -> 스킵")
            return

        from date_utils import calc_target_dates
        target_dates = calc_target_dates(today, include_base=True)

        weekdays_kr = ["월","화","수","목","금","토","일"]
        logger.info(f"===== KMOS 수집 시도: {today} ({weekdays_kr[today.weekday()]}) =====")
        logger.info(f"  대상: {[str(d) for d in target_dates]}")

        # KMOS 다운로드 + SMP 수집 확인
        all_ok = _try_kmos_and_collect(target_dates)

        if all_ok:
            logger.info("  전체 SMP 확보 → 파이프라인 실행")
            success = run_pipeline_multi(today)
            if success:
                _pipeline_done_date["value"] = today
                logger.info(f"  파이프라인 완료! 다음 스케줄은 내일 17:30")
        else:
            logger.info("  SMP 미확보 → 10분 후 재시도")

    scheduler = BlockingScheduler()

    # 17:30~19:30 사이 10분 간격 (17:30, 17:40, 17:50, 18:00, ..., 19:30)
    scheduler.add_job(
        _scheduled_job,
        trigger="cron",
        hour="17-19",
        minute="0,10,20,30,40,50",
        id="kmos_pipeline",
        replace_existing=True,
        misfire_grace_time=300,
    )
    # 17:00~17:20은 제외, 19:40 이후도 제외 → job 내부에서 시간 체크
    logger.info("  스케줄 등록: 매일 17:30~19:30, 10분 간격")
    logger.info("  SMP 확보 시 즉시 분석+전파, 이후 당일 스킵")

    logger.info("스케줄러 시작. Ctrl+C로 종료.")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("스케줄러 종료.")


def main():
    parser = argparse.ArgumentParser(description="LNG 발전 경제성 자동 분석 스케줄러")
    parser.add_argument("--now", action="store_true", help="즉시 1회 실행 (1일치)")
    parser.add_argument("--auto", action="store_true", help="자동 판단 실행 (평일/금요일/공휴일 자동)")
    parser.add_argument("--date", type=str, default=None, help="기준일 (YYYY-MM-DD)")
    parser.add_argument("--lng-price", type=float, default=DEFAULT_LNG_PRICE, help="LNG 가격")
    parser.add_argument("--spot", action="store_true", help="Spot LNG")
    args = parser.parse_args()

    if args.auto:
        base = date.today()
        if args.date:
            base = date.fromisoformat(args.date)
        success = run_pipeline_multi(base, args.lng_price, args.spot)
        sys.exit(0 if success else 1)
    elif args.now or args.date:
        target = date.today() + timedelta(days=1)
        if args.date:
            target = date.fromisoformat(args.date)
        success = run_pipeline(target, args.lng_price, args.spot)
        sys.exit(0 if success else 1)
    else:
        start_scheduler()


if __name__ == "__main__":
    main()
