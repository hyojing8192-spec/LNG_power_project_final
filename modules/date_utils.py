"""
date_utils.py
=============
날짜 계산 유틸리티 — 프로젝트 전역 단일 소스.

주요 함수:
  - is_holiday(d)           : 공휴일/주말 여부
  - is_workday(d)           : 영업일 여부
  - calc_target_dates()     : 분석 대상 날짜 계산
  - calc_download_dates()   : KMOS 다운로드 대상 날짜 계산
"""
from __future__ import annotations
from datetime import date, timedelta
from config import LEGAL_HOLIDAYS


def is_holiday(d: date) -> bool:
    """공휴일 또는 주말 여부."""
    if d in LEGAL_HOLIDAYS:
        return True
    return d.weekday() >= 5  # 토(5), 일(6)


def is_workday(d: date) -> bool:
    """평일(영업일) 여부."""
    return not is_holiday(d)


def calc_target_dates(base_date: date, include_base: bool = False) -> list[date]:
    """
    분석 대상 날짜 계산.

    규칙:
      평일(월~목, 내일이 영업일): [내일]만
      금요일/공휴일 전날(내일이 휴일):
        include_base=True  → [기준일, 연속 휴일들]
        include_base=False → [내일, 연속 휴일들]

    Args:
        base_date    : 기준일 (보통 오늘)
        include_base : True면 기준일 포함 (스케줄러/분석용)
                       False면 내일부터 (수집용)
    Returns:
        정렬된 날짜 리스트
    """
    tomorrow = base_date + timedelta(days=1)

    if is_holiday(tomorrow):
        # 주말/공휴일: 기준일(옵션) + 연속 휴일
        dates = []
        if include_base:
            dates.append(base_date)
        dates.append(tomorrow)
        d = tomorrow
        while True:
            next_d = d + timedelta(days=1)
            if is_holiday(next_d):
                dates.append(next_d)
                d = next_d
            else:
                break
    else:
        # 평일: 내일만
        dates = [tomorrow]

    return sorted(set(dates))


def calc_download_dates(base_date: date) -> list[date]:
    """
    KMOS SMP 다운로드 대상 날짜 계산.

    calc_target_dates와 동일하되:
      - 기준일에 유효 SMP가 없으면 기준일도 포함
      - include_base는 항상 False (다운로드는 내일부터)
      - 기준일은 별도로 추가 판단

    Returns:
        정렬된 날짜 리스트 (유효 데이터 필터링은 호출자가 수행)
    """
    dates = set()

    # 기준일은 항상 포함 (호출자가 유효성 필터링)
    dates.add(base_date)

    # 내일 + 연속 휴일
    for d in calc_target_dates(base_date, include_base=False):
        dates.add(d)

    return sorted(dates)
