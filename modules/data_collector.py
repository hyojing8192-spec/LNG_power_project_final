"""
data_collector.py  (F1)
=======================
주요 함수:
  - fetch_smp_today()          : 전력거래소 SMP 자동 크롤링 (F1.1)
  - load_fixed_variables()     : 월간 고정변수 로드 (F1.2)
  - save_fixed_variables()     : 월간 고정변수 저장 (F1.2)
  - fetch_exchange_rate()      : 당일 환율 자동 수집 (F1.3)
  - build_daily_input()        : 수집 데이터를 경제성 계산용 구조로 조립 (F1.4)

[수집 흐름]
  매일 17~19시 스케줄러 호출
  → fetch_smp_today()   : 전력거래소 공개 API / 크롤링으로 당일 24시간 SMP 수집
  → fetch_exchange_rate(): 한국은행 Open API로 당일 USD/KRW 환율 수집
  → load_fixed_variables(): JSON 파일에서 이번 달 LNG가격·열량·is_spot 로드
  → build_daily_input() : 위 세 결과를 합쳐 economics_engine / anomaly_detector 입력형으로 반환

[SMP 크롤링 대상]
  전력거래소 SMP 공시: https://www.kpx.or.kr/menu.es?mid=a10606030000
  - 당일 시간대별 SMP가 당일 오후 ~ 익일 새벽 사이에 공시됨
  - 실제 파싱 로직은 해당 사이트 DOM 구조에 맞게 조정 필요
  - 공시 전이라면 전일 SMP를 임시 사용하고 updated=False로 반환

[환율 수집 대상]
  한국은행 경제통계시스템 OPEN API (인증키 필요)
  https://ecos.bok.or.kr/api/StatisticSearch/{API_KEY}/json/kr/1/1/036Y001/DD/{date}/{date}/0000001
  - 인증키는 config.py 또는 환경변수 BOK_API_KEY 에 저장
  - 수집 실패 시 config.py 의 FALLBACK_EXCHANGE_RATE 사용

[월간 고정변수 관리]
  data/fixed_variables.json 에 월별로 저장
  {
    "2025-07": {"lng_price": 11.0, "lng_heat": 9.107, "is_spot": false},
    "2025-08": {"lng_price": 12.5, "lng_heat": 9.107, "is_spot": true},
    ...
  }
  Streamlit 사이드바에서 수동 입력 후 save_fixed_variables()로 갱신
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ── 경로 상수 ──────────────────────────────────────────────────
_PROJECT_ROOT   = Path(__file__).resolve().parent.parent
_FIXED_VAR_PATH = _PROJECT_ROOT / "data" / "fixed_variables.json"
_SMP_CACHE_DIR  = _PROJECT_ROOT / "data" / "smp_cache"

# ── 네트워크 상수 ──────────────────────────────────────────────
_KPX_URL          = "https://www.kpx.or.kr/menu.es?mid=a10606030000"
_BOK_API_TEMPLATE = (
    "https://ecos.bok.or.kr/api/StatisticSearch"
    "/{api_key}/json/kr/1/1/036Y001/DD/{date}/{date}/0000001"
)
_REQUEST_TIMEOUT  = 10   # seconds
_RETRY_COUNT      = 3
_RETRY_DELAY      = 5    # seconds


# ──────────────────────────────────────────────────────────────
# F1.1  SMP 자동 수집
# ──────────────────────────────────────────────────────────────

def fetch_smp_today(
    target_date: date | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """
    전력거래소에서 당일(또는 지정일) 시간별 SMP를 수집한다.

    수집 우선순위:
      1. 캐시 파일 존재 시 캐시 반환 (use_cache=True일 때)
      2. 전력거래소 크롤링
      3. 전일 캐시 폴백 (크롤링 실패 시)

    Args:
        target_date : 수집 대상 날짜 (None이면 오늘)
        use_cache   : True이면 당일 캐시 파일 우선 사용

    Returns:
        {
          "date"    : "2025-07-15",
          "smp"     : [float × 24],   # 시간 0~23
          "updated" : bool,            # 실제 크롤링 성공 여부
          "source"  : "live" | "cache" | "fallback_prev" | "fallback_zero"
        }
    """
    if target_date is None:
        target_date = date.today()

    date_str = target_date.strftime("%Y-%m-%d")
    _SMP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _SMP_CACHE_DIR / f"smp_{date_str}.json"

    # 1. 캐시 확인
    if use_cache and cache_path.is_file():
        with open(cache_path, encoding="utf-8") as f:
            cached = json.load(f)
        logger.info(f"[F1.1] SMP 캐시 로드: {cache_path}")
        cached["source"] = "cache"
        return cached

    # 2. 크롤링 시도
    smp_list = _crawl_smp_kpx(target_date)

    if smp_list is not None:
        result = {"date": date_str, "smp": smp_list, "updated": True, "source": "live"}
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        logger.info(f"[F1.1] SMP 크롤링 성공: {date_str}")
        return result

    # 3. 전일 캐시 폴백
    prev_date     = target_date - timedelta(days=1)
    prev_cache    = _SMP_CACHE_DIR / f"smp_{prev_date.strftime('%Y-%m-%d')}.json"
    if prev_cache.is_file():
        with open(prev_cache, encoding="utf-8") as f:
            prev_data = json.load(f)
        logger.warning(f"[F1.1] SMP 크롤링 실패 → 전일({prev_date}) 데이터 사용")
        return {
            "date":    date_str,
            "smp":     prev_data.get("smp", [0.0] * 24),
            "updated": False,
            "source":  "fallback_prev",
        }

    # 4. 최후 폴백: 0으로 채움
    logger.error("[F1.1] SMP 수집 완전 실패 → 0으로 초기화")
    return {"date": date_str, "smp": [0.0] * 24, "updated": False, "source": "fallback_zero"}


def _crawl_smp_kpx(target_date: date) -> list[float] | None:
    """
    전력거래소 SMP 공시 페이지에서 시간별 SMP를 파싱한다.

    [실제 운영 시 조정 필요]
    전력거래소는 공개 API 대신 HTML 테이블 구조로 SMP를 공시한다.
    파싱 로직은 해당 사이트 구조 변경 시 함께 수정해야 한다.
    아래는 requests + BeautifulSoup 기반 파싱 스켈레톤이며,
    실제 태그명·클래스명은 사이트 DOM 확인 후 교체한다.

    Returns:
        [float × 24] 성공 시 / None 실패 시
    """
    try:
        import urllib.parse
        date_str = target_date.strftime("%Y%m%d")
        # 전력거래소 SMP 데이터 URL (실제 엔드포인트 확인 필요)
        url = f"https://www.kpx.or.kr/smp_list.do?date={date_str}"

        for attempt in range(_RETRY_COUNT):
            try:
                resp = requests.get(url, timeout=_REQUEST_TIMEOUT,
                                    headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
                break
            except requests.RequestException as e:
                logger.warning(f"[F1.1] 크롤링 재시도 {attempt+1}/{_RETRY_COUNT}: {e}")
                if attempt < _RETRY_COUNT - 1:
                    time.sleep(_RETRY_DELAY)
        else:
            return None

        # ── DOM 파싱 (BeautifulSoup 사용 시) ──────────────────
        # from bs4 import BeautifulSoup
        # soup = BeautifulSoup(resp.text, "html.parser")
        # rows = soup.select("table.smp_table tbody tr")
        # smp_values = []
        # for row in rows:
        #     cells = row.find_all("td")
        #     if len(cells) >= 2:
        #         val_str = cells[1].text.strip().replace(",", "")
        #         smp_values.append(float(val_str))
        # if len(smp_values) == 24:
        #     return smp_values

        # ── JSON API 사용 시 ──────────────────────────────────
        # data = resp.json()
        # return [float(item["smp"]) for item in data["items"]]

        logger.warning("[F1.1] SMP 파싱 로직 미구현 — 실제 URL·DOM 확인 후 위 주석 해제")
        return None

    except Exception as e:
        logger.error(f"[F1.1] SMP 크롤링 오류: {e}")
        return None


# ──────────────────────────────────────────────────────────────
# F1.2  월간 고정변수 관리
# ──────────────────────────────────────────────────────────────

def load_fixed_variables(
    target_month: str | None = None,
) -> dict[str, Any]:
    """
    이번 달(또는 지정 월) LNG가격·열량·is_spot을 JSON 파일에서 로드한다.

    Args:
        target_month : "YYYY-MM" 형식 (None이면 이번 달)

    Returns:
        {
          "lng_price"  : float,   # $/MMBtu
          "lng_heat"   : float,   # Mcal/Nm³
          "is_spot"    : bool,    # True=Spot LNG, False=사용단가(계약분)
          "month"      : str,     # "YYYY-MM"
          "found"      : bool,    # JSON에 해당 월 데이터가 있었는지
        }
    """
    from config import DEFAULT_LNG_PRICE, DEFAULT_LNG_HEAT

    if target_month is None:
        target_month = date.today().strftime("%Y-%m")

    defaults = {
        "lng_price": DEFAULT_LNG_PRICE,
        "lng_heat":  DEFAULT_LNG_HEAT,
        "is_spot":   False,
        "month":     target_month,
        "found":     False,
    }

    if not _FIXED_VAR_PATH.is_file():
        logger.warning(f"[F1.2] 고정변수 파일 없음: {_FIXED_VAR_PATH} → 기본값 사용")
        return defaults

    with open(_FIXED_VAR_PATH, encoding="utf-8") as f:
        data = json.load(f)

    if target_month not in data:
        logger.warning(f"[F1.2] 고정변수 {target_month} 없음 → 기본값 사용")
        return defaults

    entry = data[target_month]
    return {
        "lng_price": float(entry.get("lng_price", defaults["lng_price"])),
        "lng_heat":  float(entry.get("lng_heat",  defaults["lng_heat"])),
        "is_spot":   bool( entry.get("is_spot",   defaults["is_spot"])),
        "month":     target_month,
        "found":     True,
    }


def save_fixed_variables(
    lng_price: float,
    lng_heat: float,
    is_spot: bool,
    target_month: str | None = None,
) -> None:
    """
    월간 고정변수를 JSON 파일에 저장(upsert)한다.

    Args:
        lng_price    : LNG 가격 ($/MMBtu)
        lng_heat     : LNG 열량 (Mcal/Nm³)
        is_spot      : True=Spot LNG, False=사용단가
        target_month : "YYYY-MM" (None이면 이번 달)
    """
    if target_month is None:
        target_month = date.today().strftime("%Y-%m")

    _FIXED_VAR_PATH.parent.mkdir(parents=True, exist_ok=True)

    data: dict = {}
    if _FIXED_VAR_PATH.is_file():
        with open(_FIXED_VAR_PATH, encoding="utf-8") as f:
            data = json.load(f)

    data[target_month] = {
        "lng_price": lng_price,
        "lng_heat":  lng_heat,
        "is_spot":   is_spot,
    }

    with open(_FIXED_VAR_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    logger.info(f"[F1.2] 고정변수 저장: {target_month} → {data[target_month]}")


def list_fixed_variables() -> dict[str, dict]:
    """
    저장된 모든 월별 고정변수를 반환한다. (Streamlit 관리 화면용)
    """
    if not _FIXED_VAR_PATH.is_file():
        return {}
    with open(_FIXED_VAR_PATH, encoding="utf-8") as f:
        return json.load(f)


# ──────────────────────────────────────────────────────────────
# F1.3  당일 환율 자동 수집
# ──────────────────────────────────────────────────────────────

def fetch_exchange_rate(
    target_date: date | None = None,
) -> dict[str, Any]:
    """
    한국은행 ECOS Open API로 USD/KRW 환율을 수집한다.

    API 인증키:
      환경변수 BOK_API_KEY 또는 config.py BOK_API_KEY 에 저장

    Args:
        target_date : 조회 날짜 (None이면 오늘, 주말·공휴일이면 직전 영업일)

    Returns:
        {
          "date"         : "2025-07-15",
          "exchange_rate": float,    # 원/$
          "updated"      : bool,
          "source"       : "api" | "fallback"
        }
    """
    from config import FALLBACK_EXCHANGE_RATE

    if target_date is None:
        target_date = date.today()

    api_key = os.environ.get("BOK_API_KEY", "")
    try:
        from config import BOK_API_KEY as _cfg_key
        if not api_key:
            api_key = _cfg_key
    except (ImportError, AttributeError):
        pass

    if not api_key:
        logger.warning("[F1.3] BOK_API_KEY 없음 → 폴백 환율 사용")
        return {
            "date":          target_date.strftime("%Y-%m-%d"),
            "exchange_rate": FALLBACK_EXCHANGE_RATE,
            "updated":       False,
            "source":        "fallback",
        }

    # 주말이면 직전 금요일로 후퇴
    query_date = _last_business_day(target_date)
    date_str   = query_date.strftime("%Y%m%d")

    url = _BOK_API_TEMPLATE.format(api_key=api_key, date=date_str)

    for attempt in range(_RETRY_COUNT):
        try:
            resp = requests.get(url, timeout=_REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()

            # ECOS API 응답 구조: data["StatisticSearch"]["row"][0]["DATA_VALUE"]
            rows = (
                data.get("StatisticSearch", {})
                    .get("row", [])
            )
            if rows:
                rate = float(rows[0]["DATA_VALUE"].replace(",", ""))
                logger.info(f"[F1.3] 환율 수집: {rate} 원/$ ({query_date})")
                return {
                    "date":          target_date.strftime("%Y-%m-%d"),
                    "exchange_rate": rate,
                    "updated":       True,
                    "source":        "api",
                }
            break

        except requests.RequestException as e:
            logger.warning(f"[F1.3] 환율 API 재시도 {attempt+1}/{_RETRY_COUNT}: {e}")
            if attempt < _RETRY_COUNT - 1:
                time.sleep(_RETRY_DELAY)
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            logger.error(f"[F1.3] 환율 파싱 오류: {e}")
            break

    logger.warning(f"[F1.3] 환율 수집 실패 → 폴백 {FALLBACK_EXCHANGE_RATE} 원/$ 사용")
    return {
        "date":          target_date.strftime("%Y-%m-%d"),
        "exchange_rate": FALLBACK_EXCHANGE_RATE,
        "updated":       False,
        "source":        "fallback",
    }


def _last_business_day(d: date) -> date:
    """주말이면 직전 금요일(영업일) 반환."""
    while d.weekday() >= 5:  # 5=토, 6=일
        d -= timedelta(days=1)
    return d


# ──────────────────────────────────────────────────────────────
# F1.4  데이터 전처리 및 조립
# ──────────────────────────────────────────────────────────────

def build_daily_input(
    target_date: date | None = None,
    use_smp_cache: bool = True,
) -> dict[str, Any]:
    """
    SMP·환율·고정변수를 수집·조립하여 경제성 계산 엔진의 입력 딕셔너리를 반환한다.

    이 함수 하나를 호출하면 F1의 모든 수집 결과가 통합된다.
    Streamlit 앱·스케줄러 양쪽에서 동일하게 사용한다.

    Args:
        target_date   : 분석 대상 날짜 (None이면 오늘)
        use_smp_cache : True이면 SMP 당일 캐시 우선 사용

    Returns:
        {
          "date"          : date,
          "smp_series"    : [float × 24],
          "lng_price"     : float,
          "lng_heat"      : float,
          "is_spot"       : bool,
          "exchange_rate" : float,
          "smp_updated"   : bool,
          "rate_updated"  : bool,
          "warnings"      : [str],   # 수집 실패·폴백 경고 메시지 목록
        }
    """
    if target_date is None:
        target_date = date.today()

    warnings: list[str] = []

    # SMP 수집
    smp_result = fetch_smp_today(target_date, use_cache=use_smp_cache)
    if not smp_result["updated"]:
        warnings.append(
            f"SMP 실시간 수집 실패 — {smp_result['source']} 데이터 사용 중"
        )

    # 환율 수집
    rate_result = fetch_exchange_rate(target_date)
    if not rate_result["updated"]:
        warnings.append(
            f"환율 실시간 수집 실패 — 폴백값({rate_result['exchange_rate']:,.0f} 원/$) 사용 중"
        )

    # 고정변수 로드
    month_str  = target_date.strftime("%Y-%m")
    fixed_vars = load_fixed_variables(month_str)
    if not fixed_vars["found"]:
        warnings.append(
            f"고정변수({month_str}) 없음 — 기본값 사용 중. 사이드바에서 이번 달 LNG 정보를 입력하세요."
        )

    return {
        "date":          target_date,
        "smp_series":    smp_result["smp"],
        "lng_price":     fixed_vars["lng_price"],
        "lng_heat":      fixed_vars["lng_heat"],
        "is_spot":       fixed_vars["is_spot"],
        "exchange_rate": rate_result["exchange_rate"],
        "smp_updated":   smp_result["updated"],
        "rate_updated":  rate_result["updated"],
        "warnings":      warnings,
    }
