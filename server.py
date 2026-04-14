"""
server.py — FastAPI 백엔드
LNG발전 최적 가이던스 REST API
"""
from __future__ import annotations

import math
import sys
import platform
import subprocess
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

# ── 모듈 경로 설정 ────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent
_MODULES = _ROOT / "modules"
for p in [str(_ROOT), str(_MODULES)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from config import (
    MODES, MODE_LABELS, DEFAULT_LNG_PRICE, DEFAULT_LNG_HEAT,
    FALLBACK_EXCHANGE_RATE, SMP_HIGH_THRESHOLD, LEGAL_HOLIDAYS,
)
from economics_engine import get_elec_price, build_hourly_table
from anomaly_detector import (
    calc_smp_thresholds, detect_smp_anomalies,
    detect_econ_change,
)
from ml_predictor import load_data, load_models, predict_day, retrain
from smp_collector import load_cached_smp, list_cached_dates
from guidance_generator import generate_full_guidance

# ── FastAPI 앱 ────────────────────────────────────────────────
app = FastAPI(title="LNG-OPT API", version="2.0")

# ── 전역 캐시 ─────────────────────────────────────────────────
_models = None
_metrics = None
_raw_df: Optional[pd.DataFrame] = None
_data_loaded = False
_lng_heat_default = DEFAULT_LNG_HEAT
_exchange_rate_default = float(FALLBACK_EXCHANGE_RATE)


def _ensure_models():
    global _models, _metrics, _raw_df, _data_loaded
    global _lng_heat_default, _exchange_rate_default
    if _data_loaded:
        return
    try:
        df = load_data()
        _models, _metrics = load_models(df)
        _raw_df = df
        _data_loaded = True
        # 자동 산출
        if "lng_heat" in df.columns:
            _lng_heat_default = round(float(df["lng_heat"].mean()), 4)
        if "exchange_rate" in df.columns and "datetime" in df.columns:
            df_tmp = df.copy()
            df_tmp["_date"] = df_tmp["datetime"].dt.date
            last_d = df_tmp["_date"].max()
            prev = df_tmp[df_tmp["_date"] < last_d]
            if not prev.empty:
                prev_d = prev["_date"].max()
                _exchange_rate_default = round(
                    float(prev[prev["_date"] == prev_d]["exchange_rate"].mean()), 2
                )
    except Exception as e:
        print(f"[WARN] 모델 로드 실패: {e}")


# ── JSON 직렬화 헬퍼 ──────────────────────────────────────────
def _safe(val):
    """numpy/nan → JSON-safe Python 타입 변환."""
    if val is None:
        return None
    if isinstance(val, pd.Timestamp):
        return str(val)
    if isinstance(val, float):
        return None if (math.isnan(val) or math.isinf(val)) else val
    if isinstance(val, (np.integer,)):
        return int(val)
    if isinstance(val, (np.floating,)):
        v = float(val)
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(val, np.ndarray):
        return [_safe(x) for x in val.tolist()]
    if isinstance(val, (np.bool_,)):
        return bool(val)
    if isinstance(val, (pd.NaT.__class__,)):
        return None
    return val


def _safe_list(lst):
    return [_safe(v) for v in lst]


# ── 날짜 유틸 ─────────────────────────────────────────────────
_WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]


def _is_holiday(d: date) -> bool:
    return d in LEGAL_HOLIDAYS or d.weekday() >= 5


def _prev_workday(d: date) -> date:
    d -= timedelta(days=1)
    while _is_holiday(d):
        d -= timedelta(days=1)
    return d


def _get_display_dates(base: date) -> list[date]:
    dates = [base]
    nxt = base + timedelta(days=1)
    dates.append(nxt)
    if _is_holiday(nxt):
        d = nxt
        while True:
            d += timedelta(days=1)
            dates.append(d)
            if not _is_holiday(d):
                break
    return sorted(set(dates))


def _default_date() -> date:
    import glob as _glob
    today = date.today()
    d = today if not _is_holiday(today) else _prev_workday(today)
    csv_files = sorted(_glob.glob(str(_ROOT / "data" / "경제성분석_*.csv")))
    if csv_files:
        try:
            stem = Path(csv_files[-1]).stem
            parsed = date.fromisoformat(stem.replace("경제성분석_", ""))
            if parsed <= today and not _is_holiday(parsed):
                d = parsed
        except Exception:
            pass
    return d


def _load_smp_for_date(d: date):
    """(smp_list, source, has_real) 반환."""
    import math as _m
    # 1) 캐시
    cached = load_cached_smp(d)
    if cached and len(cached.get("smp", [])) == 24:
        vals = cached["smp"]
        if any(isinstance(v, (int, float)) and not _m.isnan(v) and v > 0 for v in vals):
            return vals, f"캐시({cached.get('source', '')})", True
    # 2) ePower 엑셀
    try:
        from smp_collector import _scan_epower_excel
        vals = _scan_epower_excel(d)
        if vals and len(vals) == 24:
            return vals, "ePower 엑셀", True
    except Exception:
        pass
    # 3) 학습 데이터
    if _raw_df is not None and "smp" in _raw_df.columns and "datetime" in _raw_df.columns:
        df_day = _raw_df[_raw_df["datetime"].dt.date == d]
        if len(df_day) >= 24:
            return df_day["smp"].head(24).tolist(), "학습데이터", True
    return [float("nan")] * 24, "미공시", False


# ── 스케줄러 상태 ─────────────────────────────────────────────
def _scheduler_status() -> str:
    if platform.system() != "Windows":
        return "stopped"
    try:
        r = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'", "get", "CommandLine"],
            capture_output=True, text=True, timeout=5,
        )
        if "run_scheduler" not in r.stdout:
            return "stopped"
    except Exception:
        return "stopped"
    try:
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq XPlatform.exe"],
            capture_output=True, text=True, timeout=5,
        )
        if "XPlatform.exe" in r.stdout:
            return "fetching"
    except Exception:
        pass
    return "running"


# ── Pydantic 모델 ─────────────────────────────────────────────
class AnalysisRequest(BaseModel):
    target_date: str         # "YYYY-MM-DD"
    lng_price: float = DEFAULT_LNG_PRICE
    is_spot: bool = False


# ══════════════════════════════════════════════════════════════
# API 엔드포인트
# ══════════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup_event():
    """앱 시작 시 모델 로드 (백그라운드 없이 동기 처리 — 첫 요청 지연 방지)."""
    _ensure_models()


@app.get("/api/init")
def api_init():
    """초기 설정값 반환."""
    _ensure_models()
    today = date.today()
    d = _default_date()
    cached = list_cached_dates()
    return {
        "default_date": d.isoformat(),
        "today": today.isoformat(),
        "lng_heat": _lng_heat_default,
        "exchange_rate": _exchange_rate_default,
        "default_lng_price": DEFAULT_LNG_PRICE,
        "cached_dates": [str(x) for x in cached],
        "data_loaded": _data_loaded,
    }


@app.get("/api/scheduler-status")
def api_scheduler_status():
    return {"status": _scheduler_status()}


@app.post("/api/analysis")
def api_analysis(req: AnalysisRequest):
    """전체 경제성 분석 수행."""
    _ensure_models()

    try:
        target_date = date.fromisoformat(req.target_date)
    except ValueError:
        raise HTTPException(400, "날짜 형식 오류 (YYYY-MM-DD)")

    # 날짜 보정
    if _is_holiday(target_date):
        target_date = _prev_workday(target_date)

    display_dates = _get_display_dates(target_date)

    # SMP 로드
    all_smp = {d: _load_smp_for_date(d) for d in display_dates}
    smp_series, smp_source, has_smp = all_smp[target_date]

    # 날짜 상태
    smp_status = []
    for d in display_dates:
        _, src, ok = all_smp[d]
        smp_status.append({
            "date": d.isoformat(),
            "label": f"{d.month}/{d.day}({_WEEKDAY_KR[d.weekday()]})",
            "source": src,
            "available": ok,
        })

    if not _data_loaded:
        return JSONResponse({
            "has_smp": False,
            "error": "데이터 로드 실패",
            "smp_status": smp_status,
        })

    if not has_smp:
        return JSONResponse({
            "has_smp": False,
            "target_date": target_date.isoformat(),
            "target_label": f"{target_date.month}월{target_date.day}일({_WEEKDAY_KR[target_date.weekday()]})",
            "smp_source": smp_source,
            "smp_status": smp_status,
        })

    # 임계값 / 경제성 테이블 / 가이던스
    thresholds = calc_smp_thresholds(
        req.lng_price, _lng_heat_default, _exchange_rate_default, is_spot=req.is_spot
    )
    pred_results = predict_day(
        _models, target_date, smp_series,
        req.lng_price, _lng_heat_default, _exchange_rate_default,
        elec_price_fn=get_elec_price,
    )
    hourly_df = build_hourly_table(
        target_date=target_date,
        smp_series=smp_series,
        lng_price=req.lng_price,
        lng_heat=_lng_heat_default,
        exchange_rate=_exchange_rate_default,
        pred_results=pred_results,
        is_spot=req.is_spot,
        smp_high_threshold=thresholds["smp_high"],
    )
    guidance = generate_full_guidance(
        target_date=target_date,
        hourly_df=hourly_df,
        smp_series=smp_series,
        thresholds=thresholds,
        lng_price=req.lng_price,
        exchange_rate=_exchange_rate_default,
        lng_heat=_lng_heat_default,
        is_spot=req.is_spot,
    )

    # KPI
    avg_smp = float(np.nanmean(smp_series))
    best_modes = hourly_df["최적모드"].value_counts()
    top_mode_map = {"2기": "2기 full", "2기저부하": "2기 저부하", "1기": "1기 full", "정지": "정지"}
    top_mode_raw = best_modes.index[0] if len(best_modes) else "-"
    top_mode = top_mode_map.get(top_mode_raw, top_mode_raw)

    # ── 종합 차트 데이터 ──────────────────────────────────────
    date_pairs = []
    for i in range(len(display_dates) - 1):
        date_pairs.append((display_dates[i], display_dates[i + 1]))

    chart_labels, chart_smp, chart_bep = [], [], []
    for pair_idx, (d_from, d_to) in enumerate(date_pairs):
        smp_from, _, ok_from = all_smp[d_from]
        smp_to, _, ok_to = all_smp[d_to]
        if pair_idx == 0:
            for h in [22, 23]:
                chart_labels.append(f"{d_from.month}/{d_from.day} {h:02d}시")
                chart_smp.append(_safe(smp_from[h]) if ok_from else None)
                if ok_from:
                    mode = hourly_df["최적모드"].iloc[h]
                    bep_col = f"BEP_{mode}($/MMBtu)"
                    chart_bep.append(_safe(hourly_df[bep_col].iloc[h]) if bep_col in hourly_df.columns else None)
                else:
                    chart_bep.append(None)
        for h in range(0, 22):
            chart_labels.append(f"{d_to.month}/{d_to.day} {h:02d}시")
            chart_smp.append(_safe(smp_to[h]) if ok_to else None)
            if ok_to:
                mode = hourly_df["최적모드"].iloc[h]
                bep_col = f"BEP_{mode}($/MMBtu)"
                chart_bep.append(_safe(hourly_df[bep_col].iloc[h]) if bep_col in hourly_df.columns else None)
            else:
                chart_bep.append(None)

    # ── 야간/주간 테이블 ──────────────────────────────────────
    MODE_DISPLAY = {
        "2기": "2기 full", "2기저부하": "2기 저부하",
        "1기": "1기 full", "정지": "정지",
    }
    NIGHT_HOURS = list(range(22, 24)) + list(range(0, 8))
    DAY_HOURS = list(range(8, 22))
    plan = guidance["hourly_plan"]

    def _build_table(hours, smp_src, plan_src, hourly_src, d_label):
        cols = [f"{h:02d}시" for h in hours]
        rows = {
            "최적운전모드": [], "SMP": [], "수전단가": [],
            "대체단가": [], "BEP": [], "경제성(억원)": [],
        }
        for h in hours:
            smp_val = smp_src[h] if smp_src else None
            if smp_val is None or (isinstance(smp_val, float) and math.isnan(smp_val)):
                for k in rows:
                    rows[k].append(None)
                continue
            p = plan_src[h]
            mode = p["best_mode"]
            rows["최적운전모드"].append(MODE_DISPLAY.get(mode, mode))
            rows["SMP"].append(round(_safe(smp_val) or 0, 1))
            ep = _safe(hourly_src["수전단가(원/kWh)"].iloc[h])
            rows["수전단가"].append(round(ep or 0, 1))
            rows["BEP"].append(round(_safe(p["bep"]) or 0, 2) if p["bep"] else None)
            rows["경제성(억원)"].append(round(_safe(p["econ_bil"]) or 0, 3) if p["econ_bil"] else None)
            # 대체단가
            if mode == "2기":
                rows["대체단가"].append(round((smp_val * 0.7 + (ep or 0) * 0.3), 1))
            elif mode in ("2기저부하", "1기"):
                rows["대체단가"].append(round(ep or 0, 1))
            else:
                rows["대체단가"].append(round((smp_val * 0.7 + (ep or 0) * 0.3), 1))
        return {"columns": cols, "rows": rows}

    result_pairs = []
    for d_night, d_day in date_pairs:
        wk_n = _WEEKDAY_KR[d_night.weekday()]
        wk_d = _WEEKDAY_KR[d_day.weekday()]
        smp_n, _, ok_n = all_smp[d_night]
        smp_d, _, ok_d = all_smp[d_day]

        # 야간 테이블: D일 22~23시 + D+1일 00~07시
        night_table = None
        if ok_n:
            night_smp = [smp_n[h] if h >= 22 else (smp_d[h] if ok_d else float("nan"))
                         for h in NIGHT_HOURS]
            # D일 기준 가이던스 재사용 (야간 시간대)
            night_plan_g = generate_full_guidance(
                target_date=d_night, hourly_df=hourly_df,
                smp_series=smp_n, thresholds=thresholds,
                lng_price=req.lng_price, exchange_rate=_exchange_rate_default,
                lng_heat=_lng_heat_default, is_spot=req.is_spot,
            )
            night_table = _build_table(
                NIGHT_HOURS,
                {h: (smp_n[h] if h >= 22 else (smp_d[h] if ok_d else float("nan"))) for h in NIGHT_HOURS},
                {h: night_plan_g["hourly_plan"][h] for h in NIGHT_HOURS},
                hourly_df,
                f"{d_night.month}/{d_night.day}",
            )

        # 주간 테이블: D+1일 08~21시
        day_table = None
        if ok_d:
            pred_d = predict_day(
                _models, d_day, smp_d,
                req.lng_price, _lng_heat_default, _exchange_rate_default,
                elec_price_fn=get_elec_price,
            )
            hourly_d = build_hourly_table(
                target_date=d_day, smp_series=smp_d,
                lng_price=req.lng_price, lng_heat=_lng_heat_default,
                exchange_rate=_exchange_rate_default, pred_results=pred_d,
                is_spot=req.is_spot, smp_high_threshold=thresholds["smp_high"],
            )
            day_plan_g = generate_full_guidance(
                target_date=d_day, hourly_df=hourly_d,
                smp_series=smp_d, thresholds=thresholds,
                lng_price=req.lng_price, exchange_rate=_exchange_rate_default,
                lng_heat=_lng_heat_default, is_spot=req.is_spot,
            )
            day_table = _build_table(
                DAY_HOURS,
                {h: smp_d[h] for h in DAY_HOURS},
                {h: day_plan_g["hourly_plan"][h] for h in DAY_HOURS},
                hourly_d,
                f"{d_day.month}/{d_day.day}",
            )

        result_pairs.append({
            "night_header": f"야간  {d_night.month}월{d_night.day}일({wk_n}) 22시 ~ {d_day.month}월{d_day.day}일({wk_d}) 08시",
            "day_header": f"주간  {d_day.month}월{d_day.day}일({wk_d}) 08시 ~ 22시",
            "night_available": ok_n,
            "day_available": ok_d,
            "night_table": night_table,
            "day_table": day_table,
        })

    # ── 가이던스 요약 ─────────────────────────────────────────
    summary = guidance["daily_summary"]
    n_anomaly = sum(len(v) for v in summary["anomaly_hours"].values())

    # D+1 SMP 가용 여부 (종합화면과 동일한 로직)
    _next_d = display_dates[1] if len(display_dates) > 1 else None
    _ok_next = bool(_next_d and all_smp.get(_next_d, (None, None, False))[2])
    _next_label = f"{_next_d.month}/{_next_d.day}" if _next_d else "익일"

    guidance_out = {
        "avg_smp": _safe(summary["smp_avg"]),
        "best_overall": summary["best_overall"],
        "total_econ": _safe(summary["total_econ_best"]),
        "anomaly_count": n_anomaly,
        "recommendation": summary["recommendation"],
        "mode_dist": {k: int(v) for k, v in summary["mode_dist"].items()},
        "econ_totals": {k: _safe(v) for k, v in summary["econ_totals"].items()},
        "kakao_message": guidance.get("kakao_message", ""),
        "text_report": guidance.get("text_report", ""),
        # 주간(08~22시): D+1 SMP 가용 시에만 포함
        "day_plan": [_plan_item(p) for p in guidance["hourly_plan"] if 8 <= p["hour"] < 22] if _ok_next else [],
        # 야간: 22~23시(D일, 항상) + 00~07시(D+1, 가용 시에만)
        "night_plan": sorted(
            [_plan_item(p) for p in guidance["hourly_plan"] if p["hour"] >= 22] +
            ([_plan_item(p) for p in guidance["hourly_plan"] if p["hour"] < 8] if _ok_next else []),
            key=lambda x: (0 if x["hour"] >= 22 else 1, x["hour"])
        ),
        "day_available": _ok_next,
        "next_date_label": _next_label,
    }

    # ── 이상구간 탐지 ─────────────────────────────────────────
    anomaly_out = {
        "counts": {"zero": 0, "low": 0, "high": 0},
        "details": [],
        "chart": {"smp_series": [], "anomaly_points": [], "threshold_low": 0, "threshold_high": 0},
    }
    if _raw_df is not None and "smp" in _raw_df.columns and "datetime" in _raw_df.columns:
        anomalies = detect_smp_anomalies(
            _raw_df,
            smp_low=thresholds["smp_low"],
            smp_high=thresholds["smp_high"],
        )
        if not anomalies.empty:
            n_zero = len(anomalies[anomalies["anomaly_type"] == "SMP 제로"])
            n_low  = len(anomalies[anomalies["anomaly_type"] == "SMP 경제성 한계"])
            n_high = len(anomalies[anomalies["anomaly_type"] == "SMP 과대"])
            anomaly_out["counts"] = {"zero": int(n_zero), "low": int(n_low), "high": int(n_high)}
            anomaly_out["details"] = [
                {k: _safe(v) for k, v in row.items()}
                for row in anomalies.head(50).to_dict(orient="records")
            ]
            # 이상구간 마커 (최근 500개만 전송)
            anomaly_out["chart"]["anomaly_points"] = [
                {
                    "x": pd.Timestamp(row["datetime"]).isoformat(),
                    "y": round(float(row["smp"]), 1),
                    "type": row["anomaly_type"],
                }
                for _, row in anomalies.tail(500).iterrows()
            ]

        # SMP 시계열 (최근 90일 × 24h — 약 2160점) — ISO 8601 포맷
        df_chart = _raw_df[["datetime", "smp"]].dropna(subset=["smp"]).copy()
        df_chart["datetime"] = pd.to_datetime(df_chart["datetime"])
        cutoff = df_chart["datetime"].max() - pd.Timedelta(days=90)
        df_chart = df_chart[df_chart["datetime"] >= cutoff].sort_values("datetime")
        anomaly_out["chart"]["smp_series"] = [
            {"x": pd.Timestamp(r["datetime"]).isoformat(), "y": round(float(r["smp"]), 1)}
            for _, r in df_chart.iterrows()
        ]
        anomaly_out["chart"]["threshold_low"]  = round(float(thresholds["smp_low"]),  1)
        anomaly_out["chart"]["threshold_high"] = round(float(thresholds["smp_high"]), 1)

    return JSONResponse({
        "has_smp": True,
        "target_date": target_date.isoformat(),
        "target_label": f"{target_date.month}월{target_date.day}일({_WEEKDAY_KR[target_date.weekday()]})",
        "smp_status": smp_status,
        "kpis": {
            "lng_price": req.lng_price,
            "price_type": "Spot" if req.is_spot else "사용단가",
            "exchange_rate": _exchange_rate_default,
            "avg_smp": round(avg_smp, 1),
            "top_mode": top_mode,
            "lng_heat": _lng_heat_default,
        },
        "thresholds": {
            "smp_low": round(_safe(thresholds["smp_low"]) or 0, 1),
            "smp_high": round(_safe(thresholds["smp_high"]) or 0, 1),
        },
        "chart": {
            "labels": chart_labels,
            "smp": chart_smp,
            "bep": chart_bep,
            "lng_price_line": [req.lng_price] * len(chart_labels),
        },
        "date_pairs": result_pairs,
        "guidance": guidance_out,
        "anomaly": anomaly_out,
    })


def _plan_item(p: dict) -> dict:
    MODE_DISPLAY = {
        "2기": "2기 full", "2기저부하": "2기 저부하",
        "1기": "1기 full", "정지": "정지",
    }
    return {
        "hour": p["hour"],
        "time_str": p["time_str"],
        "smp": _safe(p["smp"]),
        "best_mode": MODE_DISPLAY.get(p["best_mode"], p["best_mode"]),
        "action": p["action"],
        "bep": _safe(p["bep"]),
        "econ_bil": _safe(p["econ_bil"]),
        "note": p.get("note", ""),
    }


@app.get("/api/anomaly-chart")
def api_anomaly_chart():
    """이상구간 탐지 차트 전용 엔드포인트 (분석과 독립적으로 호출 가능)."""
    _ensure_models()

    result = {
        "smp_series": [],
        "anomaly_points": [],
        "threshold_low": 0,
        "threshold_high": 0,
        "counts": {"zero": 0, "low": 0, "high": 0},
        "details": [],
        "error": None,
    }

    if _raw_df is None or "smp" not in _raw_df.columns or "datetime" not in _raw_df.columns:
        result["error"] = "원시 데이터 미로드"
        print("[anomaly-chart] _raw_df 없음 또는 컬럼 누락")
        return JSONResponse(result)

    try:
        thresholds = calc_smp_thresholds(
            DEFAULT_LNG_PRICE, _lng_heat_default, _exchange_rate_default, is_spot=False
        )

        # 이상구간 탐지
        anomalies = detect_smp_anomalies(
            _raw_df,
            smp_low=thresholds["smp_low"],
            smp_high=thresholds["smp_high"],
        )
        if not anomalies.empty:
            n_zero = len(anomalies[anomalies["anomaly_type"] == "SMP 제로"])
            n_low  = len(anomalies[anomalies["anomaly_type"] == "SMP 경제성 한계"])
            n_high = len(anomalies[anomalies["anomaly_type"] == "SMP 과대"])
            result["counts"] = {"zero": int(n_zero), "low": int(n_low), "high": int(n_high)}
            result["details"] = [
                {k: _safe(v) for k, v in row.items()}
                for row in anomalies.head(50).to_dict(orient="records")
            ]
            result["anomaly_points"] = [
                {
                    "x": pd.Timestamp(row["datetime"]).isoformat(),
                    "y": round(float(row["smp"]), 1),
                    "type": row["anomaly_type"],
                }
                for _, row in anomalies.tail(500).iterrows()
            ]

        # SMP 시계열 (최근 90일)
        df_chart = _raw_df[["datetime", "smp"]].dropna(subset=["smp"]).copy()
        df_chart["datetime"] = pd.to_datetime(df_chart["datetime"])
        if not df_chart.empty:
            cutoff = df_chart["datetime"].max() - pd.Timedelta(days=90)
            df_chart = df_chart[df_chart["datetime"] >= cutoff].sort_values("datetime")
            result["smp_series"] = [
                {"x": pd.Timestamp(r["datetime"]).isoformat(), "y": round(float(r["smp"]), 1)}
                for _, r in df_chart.iterrows()
            ]
        result["threshold_low"]  = round(float(thresholds["smp_low"]),  1)
        result["threshold_high"] = round(float(thresholds["smp_high"]), 1)
        print(f"[anomaly-chart] smp_series={len(result['smp_series'])} 개, threshold={result['threshold_low']}~{result['threshold_high']}")
    except Exception as e:
        result["error"] = str(e)
        print(f"[anomaly-chart] 오류: {e}")

    return JSONResponse(result)


@app.get("/api/ml-metrics")
def api_ml_metrics():
    _ensure_models()
    if not _data_loaded or not _metrics:
        return {"data_loaded": False}
    split = _metrics.get("_split", {})
    rows = []
    for mode in MODES:
        mm = _metrics.get(mode, {})
        for target, m in mm.items():
            rows.append({
                "mode": MODE_LABELS.get(mode, mode),
                "target": target,
                "mae": _safe(m.get("mae")),
                "r2": _safe(m.get("r2")),
                "r2_cv": _safe(m.get("r2_cv")),
                "mae_test": _safe(m.get("mae_test")),
                "r2_test": _safe(m.get("r2_test")),
                "n_train": _safe(m.get("n_samples")),
                "n_test": _safe(m.get("n_samples_test")),
            })
    return {
        "data_loaded": True,
        "split": {k: _safe(v) for k, v in split.items()},
        "metrics": rows,
    }


@app.post("/api/retrain")
def api_retrain():
    global _models, _metrics, _raw_df, _data_loaded
    try:
        new_metrics = retrain()
        _data_loaded = False
        _ensure_models()
        return {"success": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/rawdata")
def api_rawdata():
    _ensure_models()
    if _raw_df is None or _raw_df.empty:
        return {"available": False}
    df = _raw_df
    num_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    stats = df[num_cols].describe().T.round(3)
    preview = df.head(100).copy()
    if "datetime" in preview.columns:
        preview["datetime"] = preview["datetime"].astype(str)
    # SMP 히스토그램
    smp_hist = []
    if "smp" in df.columns:
        vals = df["smp"].dropna().values
        counts, bins = np.histogram(vals, bins=40)
        smp_hist = [
            {"x": round(float((bins[i] + bins[i + 1]) / 2), 1), "y": int(counts[i])}
            for i in range(len(counts))
        ]
    return {
        "available": True,
        "n_rows": len(df),
        "n_cols": len(df.columns),
        "stats": stats.reset_index().rename(columns={"index": "column"}).to_dict(orient="records"),
        "preview_cols": list(preview.columns),
        "preview_rows": preview.to_dict(orient="records"),
        "smp_histogram": smp_hist,
    }


# ── 정적 파일 서빙 ────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=str(_ROOT / "frontend")), name="static")


@app.get("/")
def root():
    return FileResponse(str(_ROOT / "frontend" / "index.html"))


# ── 직접 실행 ────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
