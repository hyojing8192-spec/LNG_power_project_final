"""utils.py — 날짜 유틸 함수 (LNG_project_final.py에서 추출)"""
from __future__ import annotations

import glob as _glob
import math
from datetime import date, timedelta
from pathlib import Path

# LEGAL_HOLIDAYS는 modules/config.py에서 임포트 (app.py에서 sys.path 설정 후 호출)
from config import LEGAL_HOLIDAYS

_WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]


def weekday_kr(d: date) -> str:
    return _WEEKDAY_KR[d.weekday()]


def is_holiday(d: date) -> bool:
    """주말 또는 공휴일 여부."""
    if d in LEGAL_HOLIDAYS:
        return True
    return d.weekday() >= 5


def prev_workday(d: date) -> date:
    """직전 영업일."""
    d = d - timedelta(days=1)
    while is_holiday(d):
        d = d - timedelta(days=1)
    return d


def get_default_date(root: Path) -> date:
    """
    오늘 날짜(휴일이면 직전 영업일) 반환.
    SMP 캐시가 있으면 오늘, 없으면 가장 최근 캐시 날짜로 폴백.
    """
    today = date.today()
    default = today if not is_holiday(today) else prev_workday(today)

    # 오늘 SMP 캐시 확인
    from pathlib import Path as _Path
    cache_today = root / "data" / "smp_cache" / f"smp_{default}.json"
    if cache_today.exists():
        return default

    # 캐시 없으면 가장 최근 캐시 날짜로 폴백
    cache_files = sorted(_glob.glob(str(root / "data" / "smp_cache" / "smp_*.json")))
    if cache_files:
        try:
            stem = _Path(cache_files[-1]).stem          # "smp_2026-04-13"
            parsed = date.fromisoformat(stem.replace("smp_", ""))
            if parsed <= today:
                return parsed if not is_holiday(parsed) else prev_workday(parsed)
        except Exception:
            pass

    return default


def get_display_dates(base_date: date) -> list[date]:
    """
    당일 + 다음날 + 연속 휴일이면 다음 영업일까지.
    """
    dates = [base_date]
    next_d = base_date + timedelta(days=1)
    dates.append(next_d)
    if is_holiday(next_d):
        d = next_d
        while True:
            d = d + timedelta(days=1)
            dates.append(d)
            if not is_holiday(d):
                break
    return sorted(set(dates))


def load_smp_for_date(d: date, raw_df=None) -> tuple[list, str, bool]:
    """
    날짜별 SMP 로드. (smp_list, source, has_real) 반환.
    raw_df: 학습 데이터프레임 (없으면 None)
    """
    from smp_collector import load_cached_smp

    smp = None
    src = ""

    # 캐시
    cached = load_cached_smp(d)
    if cached and len(cached.get("smp", [])) == 24:
        vals = cached["smp"]
        if any(isinstance(v, (int, float)) and not math.isnan(v) and v > 0 for v in vals):
            smp = vals
            src = f"캐시({cached.get('source', '')})"

    # ePower 엑셀
    if smp is None:
        try:
            from smp_collector import _scan_epower_excel
            vals = _scan_epower_excel(d)
            if vals and len(vals) == 24:
                smp = vals
                src = "ePower 엑셀"
        except Exception:
            pass

    # 학습 데이터
    if smp is None and raw_df is not None and not raw_df.empty:
        if "smp" in raw_df.columns and "datetime" in raw_df.columns:
            df_day = raw_df[raw_df["datetime"].dt.date == d]
            if len(df_day) >= 24:
                smp = df_day["smp"].head(24).tolist()
                src = "학습데이터"

    has_real = smp is not None
    if smp is None:
        smp = [float("nan")] * 24
        src = "미공시"

    return smp, src, has_real
