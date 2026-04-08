"""
smp_collector.py  (F1.1)
========================
매일 17:00~19:30 사이 **익일(D+1)** 시간별 SMP를 자동 수집하여 JSON 저장.

수집 우선순위:
  1순위: KPX 웹사이트 크롤링 (new.kpx.or.kr/smpInland.es)
         → 평일은 대부분 여기서 성공
  2순위: ePower 마켓 엑셀 파일 (data/smp_excel/ 폴더 감시)
         → 주말/공휴일 등 KPX 웹에 없을 때, ePower 마켓에서
           엑셀 저장 → data/smp_excel/ 폴더에 넣으면 자동 파싱
  3순위: 전일 데이터 폴백

사용법:
  1) 스케줄러 자동 실행 (기본) — 익일 SMP 수집:
       python smp_collector.py
     → 17:30~19:30 5분 간격, 이후 23시까지 30분 간격

  2) 즉시 1회 수집:
       python smp_collector.py --now

  3) 특정 날짜:
       python smp_collector.py --now --date 2026-04-08

  4) Windows 작업 스케줄러:
       run_smp_collector.bat 매일 17:00 트리거 등록
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
import pandas as pd
from bs4 import BeautifulSoup

# ── 경로 ──────────────────────────────────────────────────────
PROJECT_ROOT   = Path(__file__).resolve().parent.parent   # 과제_최종/
DATA_DIR       = PROJECT_ROOT / "data"
SMP_CACHE_DIR  = DATA_DIR / "smp_cache"
SMP_EXCEL_DIR  = DATA_DIR / "smp_excel"   # ePower 마켓 엑셀 드롭 폴더

# ── 로깅 ──────────────────────────────────────────────────────
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "smp_collector.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ── 상수 ──────────────────────────────────────────────────────
KPX_SMP_URL     = "https://new.kpx.or.kr/smpInland.es"
REQUEST_TIMEOUT = 15
RETRY_COUNT     = 3
RETRY_DELAY     = 10   # seconds

# 공공데이터포털 API (Dataset 15131225: 하루전 SMP + 수요예측)
DATA_GO_KR_URL = (
    "https://apis.data.go.kr/B552115"
    "/SmpWithForecastDemand/getSmpWithForecastDemand"
)
# API 키: config.py의 DATA_GO_KR_API_KEY 또는 환경변수 DATA_GO_KR_API_KEY
_API_KEY: str | None = None

# 스케줄 설정
# 평일: 17:30이면 보통 익일 SMP 공시
# 주말/공휴일 포함 시: 공시가 늦어질 수 있음 → 19:30 이후 30분 간격 재시도
SCHEDULE_START_HOUR   = 17
SCHEDULE_START_MINUTE = 30
SCHEDULE_END_HOUR     = 19
SCHEDULE_END_MINUTE   = 30
POLL_INTERVAL_SEC     = 300   # 5분 (17:30~19:30 구간)
RETRY_INTERVAL_SEC    = 1800  # 30분 (19:30 이후 재시도)
RETRY_DEADLINE_HOUR   = 23    # 최대 23시까지 재시도


# ──────────────────────────────────────────────────────────────
# KPX 크롤링
# ──────────────────────────────────────────────────────────────

def _fetch_kpx_table() -> list[dict] | None:
    """
    new.kpx.or.kr/smpInland.es 에서 최근 7일 시간별 SMP 테이블을 파싱.

    Returns:
        [{date_str: "2026-04-07", smp: [float × 24]}, ...] 또는 None
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    for attempt in range(RETRY_COUNT):
        try:
            resp = requests.get(KPX_SMP_URL, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")
            table = soup.find("table")
            if table is None:
                logger.warning(f"[KPX] 테이블 없음 (시도 {attempt+1})")
                if attempt < RETRY_COUNT - 1:
                    time.sleep(RETRY_DELAY)
                continue

            rows = table.find_all("tr")
            if len(rows) < 25:
                logger.warning(f"[KPX] 행 부족: {len(rows)}행 (최소 25행 필요)")
                return None

            # Row 0: 헤더 — 날짜 컬럼 파싱
            header_cells = rows[0].find_all(["td", "th"])
            date_columns = _parse_header_dates(header_cells)

            if not date_columns:
                logger.warning("[KPX] 헤더에서 날짜 파싱 실패")
                return None

            logger.info(f"[KPX] 날짜 {len(date_columns)}개 감지: {[d['date_str'] for d in date_columns]}")

            # Row 1~24: 시간별 SMP
            for dc in date_columns:
                dc["smp"] = []

            for row_idx in range(1, 25):  # 1h ~ 24h
                cells = rows[row_idx].find_all(["td", "th"])
                for dc in date_columns:
                    col_idx = dc["col_idx"]
                    if col_idx < len(cells):
                        text = cells[col_idx].get_text(strip=True).replace(",", "")
                        try:
                            dc["smp"].append(float(text))
                        except ValueError:
                            dc["smp"].append(0.0)
                    else:
                        dc["smp"].append(0.0)

            # 24시간 완성된 것만 반환
            results = []
            for dc in date_columns:
                if len(dc["smp"]) == 24:
                    results.append({
                        "date_str": dc["date_str"],
                        "smp": dc["smp"],
                    })

            logger.info(f"[KPX] {len(results)}일 SMP 파싱 완료")
            return results

        except requests.RequestException as e:
            logger.warning(f"[KPX] 접속 재시도 {attempt+1}/{RETRY_COUNT}: {e}")
            if attempt < RETRY_COUNT - 1:
                time.sleep(RETRY_DELAY)

    return None


def _parse_header_dates(header_cells) -> list[dict]:
    """
    헤더 셀에서 날짜 정보 추출.

    헤더 예시: ['구분', '04.01(화)', '04.02(수)', ..., '04.07(월)']
    → [{col_idx: 1, date_str: "2026-04-01"}, ...]
    """
    today = date.today()
    year = today.year
    results = []

    for i, cell in enumerate(header_cells):
        text = cell.get_text(strip=True)

        # "MM.DD" 패턴 매칭 (괄호 안 요일은 무시)
        m = re.search(r"(\d{1,2})\.(\d{1,2})", text)
        if m:
            month = int(m.group(1))
            day = int(m.group(2))

            # 연도 추정: 12월 데이터가 1월에 조회될 수 있음
            try:
                d = date(year, month, day)
                # 미래 6개월 이상이면 전년도로 보정
                if (d - today).days > 180:
                    d = date(year - 1, month, day)
            except ValueError:
                continue

            results.append({
                "col_idx": i,
                "date_str": d.strftime("%Y-%m-%d"),
            })

    return results


# ──────────────────────────────────────────────────────────────
# 2순위: 공공데이터포털 API (하루전 SMP)
# ──────────────────────────────────────────────────────────────

def _get_api_key() -> str | None:
    """API 키를 환경변수 또는 config.py에서 로드."""
    global _API_KEY
    if _API_KEY is not None:
        return _API_KEY if _API_KEY != "" else None

    import os
    key = os.environ.get("DATA_GO_KR_API_KEY", "")
    if not key:
        try:
            import sys
            sys.path.insert(0, str(PROJECT_ROOT))
            from config import DATA_GO_KR_API_KEY
            key = DATA_GO_KR_API_KEY
        except (ImportError, AttributeError):
            pass
    _API_KEY = key
    return key if key else None


def _fetch_api_smp(target_date: date) -> list[float] | None:
    """
    공공데이터포털 API로 하루전 SMP 조회.

    Dataset 15131225: 한국전력거래소_계통한계가격 및 수요예측(하루전 발전계획용)
    → date 파라미터로 D+1 SMP 조회 가능 (매일 ~23시 업데이트)
    """
    api_key = _get_api_key()
    if not api_key:
        logger.info("[API] data.go.kr API 키 없음 → 스킵")
        return None

    date_str = target_date.strftime("%Y%m%d")
    params = {
        "serviceKey": api_key,
        "pageNo": "1",
        "numOfRows": "50",   # 24시간 × (육지+제주) = 48행
        "dataType": "json",
        "date": date_str,
    }

    for attempt in range(RETRY_COUNT):
        try:
            resp = requests.get(DATA_GO_KR_URL, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()

            items = (
                data.get("response", {})
                    .get("body", {})
                    .get("items", {})
                    .get("item", [])
            )

            if not items:
                logger.warning(f"[API] {date_str} 응답에 데이터 없음")
                return None

            # 육지(mainland) SMP만 추출
            smp_map: dict[int, float] = {}
            for item in items:
                area = item.get("areaName", "")
                if "육지" not in area and "1" not in str(item.get("areaCd", "")):
                    continue
                hour_val = item.get("hour")
                smp_val = item.get("smp")
                if hour_val is not None and smp_val is not None:
                    try:
                        h = int(str(hour_val).strip())
                        # API hour 1~24 → 인덱스 0~23 (1시=00:00~01:00)
                        smp_map[h - 1] = float(str(smp_val).replace(",", ""))
                    except (ValueError, TypeError):
                        pass

            if len(smp_map) >= 20:
                result = [smp_map.get(h, 0.0) for h in range(24)]
                avg = sum(result) / 24
                logger.info(f"[API] {date_str} SMP {len(smp_map)}시간 수집 성공: 평균 {avg:.2f}")
                return result
            else:
                logger.warning(f"[API] {date_str} 불완전 데이터: {len(smp_map)}시간")
                return None

        except requests.RequestException as e:
            logger.warning(f"[API] 재시도 {attempt+1}/{RETRY_COUNT}: {e}")
            if attempt < RETRY_COUNT - 1:
                time.sleep(RETRY_DELAY)
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"[API] 파싱 오류: {e}")
            return None

    return None


# ──────────────────────────────────────────────────────────────
# 3순위: ePower 마켓 엑셀 파싱 (data/smp_excel/ 폴더 감시)
# ──────────────────────────────────────────────────────────────

def _scan_epower_excel(target_date: date) -> list[float] | None:
    """
    data/smp_excel/ 폴더에서 ePower 마켓 엑셀 파일을 찾아 파싱.

    ePower 마켓 엑셀 구조 (계통한계가격_YYYYMMDD_육지.xlsx):
      Row 0 : 시간    | 04-07 (화)
      Row 1 : 최대    | 136.57
      Row 2 : 최소    | 98.76
      Row 3 : 평균    | 119.94
      Row 4 : 01시    | 125.70
      ...
      Row 27: 24시    | 124.87

    매칭 우선순위:
      1. 파일명에 대상 날짜 포함 (계통한계가격_20260408_육지.xlsx)
      2. 가장 최근 수정된 엑셀 파일 (헤더 날짜로 검증)
    """
    SMP_EXCEL_DIR.mkdir(parents=True, exist_ok=True)

    date_str = target_date.strftime("%Y%m%d")

    excel_files = sorted(
        list(SMP_EXCEL_DIR.glob("*.xlsx")) + list(SMP_EXCEL_DIR.glob("*.xls")),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )

    if not excel_files:
        logger.info("[ePower] data/smp_excel/ 폴더에 엑셀 파일 없음")
        return None

    # 파일명에 날짜 포함된 파일 우선, 없으면 최신 파일
    target_files = [f for f in excel_files if date_str in f.name]
    if not target_files:
        target_files = excel_files[:3]

    for fpath in target_files:
        logger.info(f"[ePower] 엑셀 파싱 시도: {fpath.name}")
        smp_list = _parse_epower_excel(fpath, target_date)
        if smp_list is not None:
            return smp_list

    return None


def _parse_epower_excel(fpath: Path, target_date: date) -> list[float] | None:
    """
    ePower 마켓 엑셀 파싱.

    구조: 2열(시간|값), Row 4~27이 01시~24시 SMP.
    헤더(Row 0, col 1)에 'MM-DD' 형식 날짜 포함.
    """
    try:
        df = pd.read_excel(fpath, header=None)
        nrows, ncols = df.shape
        logger.info(f"[ePower] {fpath.name}: {nrows}행 × {ncols}열")

        if nrows < 28 or ncols < 2:
            logger.warning(f"[ePower] 구조 불일치: {nrows}행 × {ncols}열 (28행 × 2열 이상 필요)")
            return None

        # 헤더 날짜 확인 (Row 0, 두번째 열: "04-07 (화)" 등)
        header_str = str(df.iloc[0, 1])
        target_mm_dd = target_date.strftime("%m-%d")
        # "04-07" 또는 "4-7" 패턴 매칭
        header_match = re.search(r"(\d{1,2})-(\d{1,2})", header_str)

        if header_match:
            h_month = int(header_match.group(1))
            h_day = int(header_match.group(2))
            if h_month == target_date.month and h_day == target_date.day:
                logger.info(f"[ePower] 날짜 일치: {header_str} → {target_date}")
            else:
                logger.info(f"[ePower] 날짜 불일치: 파일={header_str}, 대상={target_mm_dd}")
                # 파일명에 날짜가 있으면 그대로 진행, 아니면 스킵
                if target_date.strftime("%Y%m%d") not in fpath.name:
                    return None

        # Row 4~27 (01시~24시) 에서 SMP 추출 — 두번째 열(col 1)
        smp_list = []
        for row_idx in range(4, 28):
            try:
                val = float(str(df.iloc[row_idx, 1]).replace(",", ""))
                smp_list.append(val)
            except (ValueError, TypeError):
                smp_list.append(0.0)

        if len(smp_list) == 24:
            avg = sum(smp_list) / 24
            logger.info(f"[ePower] SMP 24시간 추출 성공: 평균 {avg:.2f} 원/kWh")
            return smp_list

        logger.warning(f"[ePower] SMP 추출 실패: {len(smp_list)}개")
        return None

    except Exception as e:
        logger.error(f"[ePower] 엑셀 파싱 오류 ({fpath.name}): {e}")
        return None


# ──────────────────────────────────────────────────────────────
# 통합 수집 함수
# ──────────────────────────────────────────────────────────────

def collect_smp(target_date: date | None = None) -> dict:
    """
    SMP 수집 (우선순위: KPX 웹 → ePower 엑셀 → 전일 폴백).

    Returns:
        {
            "date":         "2026-04-08",
            "smp":          [float × 24],
            "source":       "kpx" | "epower_excel" | "cache" | "fallback_prev" | "fallback_zero",
            "updated":      bool,
            "collected_at": "2026-04-07T17:35:00",
        }
    """
    if target_date is None:
        target_date = date.today()

    date_str = target_date.strftime("%Y-%m-%d")
    SMP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = SMP_CACHE_DIR / f"smp_{date_str}.json"

    # 이미 수집 완료된 캐시 확인
    if cache_path.is_file():
        with open(cache_path, encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("updated"):
            logger.info(f"[수집] {date_str} 이미 수집 완료 → 캐시 사용")
            cached["source"] = "cache"
            return cached

    now_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    # ── 1순위: KPX 웹 크롤링 ──────────────────────────────────
    logger.info(f"[수집] {date_str} KPX 크롤링 시도...")
    table_data = _fetch_kpx_table()

    if table_data:
        found = False
        for entry in table_data:
            entry_path = SMP_CACHE_DIR / f"smp_{entry['date_str']}.json"
            entry_result = {
                "date": entry["date_str"],
                "smp": entry["smp"],
                "source": "kpx",
                "updated": True,
                "collected_at": now_str,
            }
            _save_cache(entry_path, entry_result)

            if entry["date_str"] == date_str:
                found = True
                result = entry_result
                avg = sum(entry["smp"]) / 24
                logger.info(f"[수집 완료] {date_str} → KPX 평균 SMP {avg:.2f} 원/kWh")

        if found:
            return result
        else:
            logger.warning(f"[수집] KPX 테이블에 {date_str} 없음")

    # ── 2순위: 공공데이터포털 API (하루전 SMP) ───────────────────
    logger.info(f"[수집] {date_str} 공공데이터포털 API 시도...")
    smp_list = _fetch_api_smp(target_date)
    if smp_list is not None:
        result = {
            "date": date_str, "smp": smp_list,
            "source": "data_go_kr_api", "updated": True,
            "collected_at": now_str,
        }
        _save_cache(cache_path, result)
        avg = sum(smp_list) / 24
        logger.info(f"[수집 완료] {date_str} → API 평균 SMP {avg:.2f} 원/kWh")
        return result

    # ── 3순위: ePower 마켓 엑셀 (data/smp_excel/ 폴더 감시) ────
    logger.info(f"[수집] {date_str} ePower 마켓 엑셀 확인...")
    smp_list = _scan_epower_excel(target_date)
    if smp_list is not None:
        result = {
            "date": date_str, "smp": smp_list,
            "source": "epower_excel", "updated": True,
            "collected_at": now_str,
        }
        _save_cache(cache_path, result)
        avg = sum(smp_list) / 24
        logger.info(f"[수집 완료] {date_str} → ePower 엑셀 평균 SMP {avg:.2f} 원/kWh")
        return result

    logger.warning(
        f"[수집] {date_str} 모든 자동 소스 실패 → "
        f"ePower 마켓에서 엑셀 다운로드 후 data/smp_excel/ 폴더에 넣어주세요"
    )

    # ── 4순위: 전일 캐시 폴백 ─────────────────────────────────
    for days_back in range(1, 8):
        prev_date = target_date - timedelta(days=days_back)
        prev_cache = SMP_CACHE_DIR / f"smp_{prev_date.strftime('%Y-%m-%d')}.json"
        if prev_cache.is_file():
            with open(prev_cache, encoding="utf-8") as f:
                prev_data = json.load(f)
            logger.warning(f"[수집] 폴백 → {prev_date} 데이터 사용")
            return {
                "date": date_str,
                "smp": prev_data.get("smp", [0.0] * 24),
                "source": "fallback_prev",
                "updated": False,
                "collected_at": now_str,
            }

    logger.error("[수집] 모든 소스 및 폴백 실패 → 0으로 초기화")
    return {
        "date": date_str, "smp": [0.0] * 24,
        "source": "fallback_zero", "updated": False,
        "collected_at": now_str,
    }


def _save_cache(path: Path, data: dict) -> None:
    """수집 결과를 JSON 캐시 파일로 저장."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ──────────────────────────────────────────────────────────────
# 수집 대상 날짜 산출
# ──────────────────────────────────────────────────────────────

def _get_target_dates() -> list[date]:
    """
    오늘 수집해야 할 미래 날짜 목록을 산출.

    규칙:
      - 항상 내일(D+1) 포함
      - 금요일 → 토, 일, 월 (3일)
      - 토요일 → 일, 월 (2일)
      - 공휴일 전날 → 공휴일 + 그 다음 평일까지

    예) 금요일(4/4): [4/5(토), 4/6(일), 4/7(월)]
        수요일(4/9): [4/10(목)]
    """
    from config import LEGAL_HOLIDAYS

    today = date.today()
    targets = []

    # 내일부터 시작해서, 연속된 주말/공휴일을 넘어선 첫 평일까지 포함
    d = today + timedelta(days=1)
    while True:
        targets.append(d)
        next_d = d + timedelta(days=1)
        # 다음날이 주말 또는 공휴일이면 계속 추가
        if next_d.weekday() >= 5 or next_d in LEGAL_HOLIDAYS:
            d = next_d
        else:
            # 현재 d 자체가 주말/공휴일이면 그 다음 평일도 포함
            if d.weekday() >= 5 or d in LEGAL_HOLIDAYS:
                targets.append(next_d)
            break

    return sorted(set(targets))


def _is_cached(target_date: date) -> bool:
    """해당 날짜의 SMP가 이미 수집(updated=True)되어 있는지 확인."""
    cache_path = SMP_CACHE_DIR / f"smp_{target_date.strftime('%Y-%m-%d')}.json"
    if cache_path.is_file():
        with open(cache_path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("updated", False)
    return False


# ──────────────────────────────────────────────────────────────
# 스케줄러
# ──────────────────────────────────────────────────────────────

def run_scheduled() -> list[dict]:
    """
    미수집된 미래 날짜의 SMP를 자동 수집.

    [동작 흐름]
      1단계 (17:30~19:30): KPX 웹 + API — 5분 간격
        → 평일 D+1은 보통 여기서 성공
        → 금요일이면 토/일/월 3일치가 KPX에 한번에 나옴

      2단계 (19:30~23:00): API 중심 — 30분 간격
        → API에 19~23시 사이 데이터가 올라오는 대로 수집

      미수집 날짜가 0개가 되면 즉시 종료.

    Returns:
        수집된 날짜별 결과 리스트
    """
    all_targets = _get_target_dates()
    logger.info(f"[스케줄] 수집 대상 날짜: {[d.strftime('%m/%d') for d in all_targets]}")

    # 이미 수집 완료된 날짜 제외
    pending = [d for d in all_targets if not _is_cached(d)]
    if not pending:
        logger.info("[스케줄] 모든 대상 날짜 수집 완료!")
        results = []
        for d in all_targets:
            r = collect_smp(d)
            results.append(r)
        return results

    logger.info(f"[스케줄] 미수집: {[d.strftime('%m/%d') for d in pending]}")

    now = datetime.now()
    start_time = now.replace(hour=SCHEDULE_START_HOUR, minute=SCHEDULE_START_MINUTE, second=0)
    phase1_end = now.replace(hour=SCHEDULE_END_HOUR,   minute=SCHEDULE_END_MINUTE,   second=0)
    deadline   = now.replace(hour=RETRY_DEADLINE_HOUR,  minute=0, second=0)

    # 17:30 전이면 대기
    if now < start_time:
        wait_sec = (start_time - now).total_seconds()
        logger.info(f"[스케줄] {start_time.strftime('%H:%M')}까지 {wait_sec/60:.0f}분 대기...")
        time.sleep(wait_sec)

    # 23시 이후면 즉시 1회
    if datetime.now() > deadline:
        logger.warning("[스케줄] 마감 경과 → 즉시 수집")
        return [collect_smp(d) for d in all_targets]

    # ── 1단계: 17:30~19:30 — 5분 간격 ────────────────────────
    attempt = 0
    while datetime.now() <= phase1_end and pending:
        attempt += 1
        logger.info(
            f"[1단계] 시도 #{attempt} ({datetime.now().strftime('%H:%M')}) "
            f"미수집 {len(pending)}일: {[d.strftime('%m/%d') for d in pending]}"
        )

        for d in list(pending):
            result = collect_smp(d)
            if result["updated"]:
                pending.remove(d)
                logger.info(f"[1단계] {d} 수집 완료 ({result['source']})")

        if not pending:
            break

        remaining = (phase1_end - datetime.now()).total_seconds()
        if remaining > POLL_INTERVAL_SEC:
            logger.info(f"[1단계] {POLL_INTERVAL_SEC // 60}분 후 재시도...")
            time.sleep(POLL_INTERVAL_SEC)
        else:
            break

    # ── 2단계: 19:30~23:00 — 30분 간격 (API 데이터 대기) ─────
    if pending:
        logger.info(
            f"[2단계] 미수집 {len(pending)}일 → 30분 간격 재시도 "
            f"{[d.strftime('%m/%d') for d in pending]}"
        )

    while datetime.now() <= deadline and pending:
        attempt += 1
        logger.info(
            f"[2단계] 시도 #{attempt} ({datetime.now().strftime('%H:%M')}) "
            f"미수집: {[d.strftime('%m/%d') for d in pending]}"
        )

        for d in list(pending):
            result = collect_smp(d)
            if result["updated"]:
                pending.remove(d)
                logger.info(f"[2단계] {d} 수집 완료 ({result['source']})")

        if not pending:
            break

        remaining = (deadline - datetime.now()).total_seconds()
        if remaining > RETRY_INTERVAL_SEC:
            logger.info(f"[2단계] {RETRY_INTERVAL_SEC // 60}분 후 재시도...")
            time.sleep(RETRY_INTERVAL_SEC)
        else:
            break

    # 결과 정리
    if pending:
        logger.warning(f"[스케줄] 23시 마감 — 미수집 남음: {[d.strftime('%m/%d') for d in pending]}")
    else:
        logger.info("[스케줄] 모든 대상 날짜 수집 완료!")

    return [collect_smp(d) for d in all_targets]


# ──────────────────────────────────────────────────────────────
# 유틸 (다른 모듈에서 import 용)
# ──────────────────────────────────────────────────────────────

def load_cached_smp(target_date: date | None = None) -> dict | None:
    """캐시된 SMP 데이터 로드. 없으면 None."""
    if target_date is None:
        target_date = date.today()
    cache_path = SMP_CACHE_DIR / f"smp_{target_date.strftime('%Y-%m-%d')}.json"
    if cache_path.is_file():
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)
    return None


def list_cached_dates() -> list[str]:
    """캐시된 SMP 날짜 목록."""
    if not SMP_CACHE_DIR.is_dir():
        return []
    return sorted(f.stem.replace("smp_", "") for f in SMP_CACHE_DIR.glob("smp_*.json"))


# ──────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────

def _print_result(result: dict) -> None:
    """단일 날짜 수집 결과 출력."""
    ok = "OK" if result["updated"] else "폴백"
    avg = sum(result["smp"]) / 24 if result["smp"] else 0
    print(f"  {result['date']}  [{ok:>2}]  소스: {result['source']:<16}  평균: {avg:.2f} 원/kWh")


def main():
    parser = argparse.ArgumentParser(
        description="전력거래소 SMP 자동 수집기",
    )
    parser.add_argument("--now", action="store_true",
                        help="스케줄 무시, 즉시 수집")
    parser.add_argument("--date", type=str, default=None,
                        help="특정 날짜 1개 수집 (YYYY-MM-DD)")
    args = parser.parse_args()

    logger.info("=" * 60)

    if args.date:
        # 특정 날짜 1개 수집
        target = date.fromisoformat(args.date)
        logger.info(f"SMP 수집기 시작 — 대상: {target}")
        result = collect_smp(target)
        print("\n" + "=" * 55)
        _print_result(result)
        if result["smp"]:
            print(f"\n  시간별 SMP:")
            for h in range(24):
                print(f"    {h:02d}:00~{h+1:02d}:00  {result['smp'][h]:>7.2f} 원/kWh")
        print("=" * 55)

    elif args.now:
        # 대상 날짜 전부 즉시 수집
        targets = _get_target_dates()
        logger.info(f"SMP 즉시 수집 — 대상: {[d.strftime('%m/%d') for d in targets]}")
        results = [collect_smp(d) for d in targets]
        print("\n" + "=" * 55)
        for r in results:
            _print_result(r)
        print("=" * 55)

    else:
        # 스케줄 모드 (17:30~23:00 자동 수집)
        targets = _get_target_dates()
        logger.info(f"SMP 스케줄 수집 — 대상: {[d.strftime('%m/%d') for d in targets]}")
        results = run_scheduled()
        print("\n" + "=" * 55)
        for r in results:
            _print_result(r)
        n_ok = sum(1 for r in results if r["updated"])
        print(f"\n  {n_ok}/{len(results)}일 수집 완료")
        print("=" * 55)


if __name__ == "__main__":
    main()
