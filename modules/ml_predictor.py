"""
ml_predictor.py  (F2)
=====================
주요 함수:
  - load_data()              : CSV 로드 및 전처리
  - build_features()         : 피처 엔지니어링
  - time_series_train_test_split() : 시계열 순서 유지 train/test (테스트는 평가만)
  - classify_mode()          : 운전모드 분류
  - train_all_models()       : train만 학습·CV, test는 최종 MAE/R²만
  - load_models()            : pkl 로드 (없으면 자동 학습)
  - predict_for_hour()       : 단일 시간 예측
  - predict_day()            : 24시간 예측 딕셔너리 반환
"""

from __future__ import annotations

import os
import pickle
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import KFold, TimeSeriesSplit, cross_val_score
from xgboost import XGBRegressor

warnings.filterwarnings("ignore")

from config import (
    DATA_PATH, MODEL_DIR, MODES, MODE_THRESHOLDS,
    MODEL_FEATURES, XGBOOST_PARAMS, CV_FOLDS, MIN_R2,
    ML_TEST_FRACTION,
)

TARGET_NAMES = ["export", "import", "efficiency"]

# 엑셀→CSV 저장 시 상단에 메타 행이 있고 시간 열 이름이 `구분`인 형식
_EXCEL_EXPORT_SKIPROWS = 3
_KO_TO_INTERNAL = {
    "구분": "datetime",
    "smp(원/kWh)": "smp",
    "LNG발전량(kW)": "lng_gen",
    "역송량(kW)": "export",
    "수전량(kW)": "import",
    "순부하(kW)": "net_load",
    "수전단가(원/kWh)": "elec_price",
    "LNG가격($/MMBtu)": "lng_price",
    "LNG열량(Mcal/N㎥)": "lng_heat",
    "LNG발전 효율(Mcal/kWh)": "efficiency",
    "환율(원/$)": "exchange_rate",
}


def _coerce_numeric_column(s: pd.Series) -> pd.Series:
    """쉼표·'-'·#DIV/0! 등이 섞인 문자열 열을 숫자로 변환."""
    if not (s.dtype == object or pd.api.types.is_string_dtype(s)):
        return pd.to_numeric(s, errors="coerce")
    t = s.astype(str).str.replace(",", "", regex=False).str.strip()
    t = t.replace({"-": np.nan, "–": np.nan, "—": np.nan, "#DIV/0!": np.nan})
    t = t.replace(r"^[-–—]+$", np.nan, regex=True)
    return pd.to_numeric(t, errors="coerce")


# ──────────────────────────────────────────────────────────────
# F2.1  데이터 로드 및 피처 엔지니어링
# ──────────────────────────────────────────────────────────────

def load_data(path: str = DATA_PATH) -> pd.DataFrame:
    """CSV 파일을 읽어 datetime 파싱 및 기본 전처리 후 반환."""
    p = Path(path)
    xlsx_alt = p.with_suffix(".xlsx")
    if not p.is_file():
        if xlsx_alt.is_file():
            raise ValueError(
                f"`{p.name}`가 없고 `{xlsx_alt.name}`(엑셀)만 있습니다. "
                f"「CSV UTF-8(쉼표로 분리)」로 저장해 `data/{p.name}`로 두거나, "
                "원본 `데이터.csv`에서 `python modules/preprocess_데이터.py`로 전처리 CSV를 만드세요."
            )
        raise FileNotFoundError(f"데이터 파일 없음: {p}")

    enc = "utf-8-sig"
    header_peek = pd.read_csv(p, nrows=0, encoding=enc)

    if "datetime" in header_peek.columns:
        df = pd.read_csv(p, parse_dates=["datetime"], encoding=enc)
    elif "구분" in header_peek.columns:
        # 헤더가 1행부터인 경우(드묾)
        df = pd.read_csv(p, parse_dates=["구분"], encoding=enc)
        df = df.rename(columns={"구분": "datetime"})
    else:
        df = pd.read_csv(
            p,
            skiprows=_EXCEL_EXPORT_SKIPROWS,
            encoding=enc,
            on_bad_lines="skip",
        )
        if "구분" not in df.columns:
            raise ValueError(
                "CSV에 `datetime` 또는 `구분`(시간) 열이 없습니다. "
                "엑셀에서 본문 표가 있는 시트를 CSV UTF-8로 저장했는지 확인하세요."
            )
        df = df.rename(columns=_KO_TO_INTERNAL)
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")

    df = df.dropna(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)

    num_cols = [c for c in df.columns if c != "datetime"]
    for col in num_cols:
        df[col] = _coerce_numeric_column(df[col])

    return df


def classify_mode(lng_gen: float) -> str:
    """발전량(kW) → 운전모드 문자열."""
    if lng_gen < MODE_THRESHOLDS["off_max"]:
        return "off"
    elif lng_gen < MODE_THRESHOLDS["1gi_max"]:
        return "1gi"
    elif lng_gen < MODE_THRESHOLDS["low2gi_max"]:
        return "low2gi"
    return "2gi"


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """시간 관련 피처 엔지니어링 추가."""
    df = df.copy()
    df["hour"]    = df["datetime"].dt.hour
    df["weekday"] = df["datetime"].dt.weekday
    df["month"]   = df["datetime"].dt.month
    df["mode"]    = df["lng_gen"].fillna(0).apply(classify_mode)

    if "lng_gen" not in df.columns:
        df["lng_gen"] = 0.0
    else:
        df["lng_gen"] = pd.to_numeric(df["lng_gen"], errors="coerce").fillna(0.0)

    if "net_load" not in df.columns:
        df["net_load"] = 280_000.0
    else:
        nl_med = pd.to_numeric(df["net_load"], errors="coerce").median()
        if pd.isna(nl_med):
            nl_med = 280_000.0
        df["net_load"] = pd.to_numeric(df["net_load"], errors="coerce").fillna(nl_med)

    # 순환 인코딩 (시간)
    df["hour_sin"] = np.sin(2 * np.pi * df["hour"] / 24)
    df["hour_cos"] = np.cos(2 * np.pi * df["hour"] / 24)
    df["month_sin"] = np.sin(2 * np.pi * df["month"] / 12)
    df["month_cos"] = np.cos(2 * np.pi * df["month"] / 12)

    return df


FEATURE_COLS = MODEL_FEATURES  # config.py의 MODEL_FEATURES 직접 사용 (lng_gen, net_load 포함)


def time_series_train_test_split(
    df: pd.DataFrame,
    test_fraction: float = ML_TEST_FRACTION,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    시계열 순서 유지하되 계절 편향 완화.

    단순 후반 20% 분할 시 test가 동절기만 될 수 있어 R²가 폭락하는 문제 방지.
    → 각 월에서 test_fraction만큼 균등 추출 (월별 stratified, 시간 순서 유지).

    Returns:
        (train_df, test_df): 시간 순으로 정렬된 DataFrame
    """
    if test_fraction <= 0 or test_fraction >= 1:
        raise ValueError("test_fraction은 0과 1 사이여야 합니다.")

    d = df.sort_values("datetime").reset_index(drop=True)
    n = len(d)
    if n < 10:
        raise ValueError("시계열 분할을 하려면 최소 10행 이상 필요합니다.")

    # 월별 stratified: 각 월의 마지막 test_fraction 구간을 test로
    d["_ym"] = d["datetime"].dt.to_period("M")
    test_idx = []
    for ym, grp in d.groupby("_ym", sort=True):
        n_grp  = len(grp)
        n_test = max(1, int(round(n_grp * test_fraction)))
        test_idx.extend(grp.index[-n_test:].tolist())

    test_mask  = d.index.isin(test_idx)
    train_df   = d[~test_mask].drop(columns=["_ym"]).copy()
    test_df    = d[ test_mask].drop(columns=["_ym"]).copy()

    # 최소 train 보장
    if len(train_df) < 20:
        n_test  = max(1, n // 5)
        train_df = d.iloc[:-n_test].drop(columns=["_ym"], errors="ignore").copy()
        test_df  = d.iloc[-n_test:].drop(columns=["_ym"], errors="ignore").copy()

    return (
        train_df.sort_values("datetime").reset_index(drop=True),
        test_df.sort_values("datetime").reset_index(drop=True),
    )


# ──────────────────────────────────────────────────────────────
# F2.2  모델 학습 및 저장
# ──────────────────────────────────────────────────────────────

def _get_model_path(mode: str, target: str) -> Path:
    return Path(MODEL_DIR) / f"model_{mode}_{target}.pkl"


def _get_metrics_path() -> Path:
    return Path(MODEL_DIR) / "metrics.pkl"


def _get_impute_path() -> Path:
    return Path(MODEL_DIR) / "impute_defaults.pkl"


def _choose_cv_split(n: int):
    """
    긴 구간은 TimeSeriesSplit(min_train_size 보장), 짧은 구간은 KFold.
    TimeSeriesSplit 첫 fold 학습 샘플이 너무 적으면 R²가 -∞로 폭발하는
    문제를 min_train_size로 방지한다.
    """
    if n < 5:
        return KFold(n_splits=2, shuffle=True, random_state=42)
    if n >= 48:
        k = max(2, min(CV_FOLDS, n // 20))
        k = min(k, n - 2)
        min_train = max(20, n // (k + 1))   # 첫 fold 최소 학습 샘플 보장
        return TimeSeriesSplit(n_splits=k, gap=0, test_size=max(1, n // (k + 1)),
                               max_train_size=None)
    k = max(2, min(CV_FOLDS, max(1, n // 15)))
    k = min(k, n - 1)
    return KFold(n_splits=k, shuffle=True, random_state=42)


def _safe_r2_mean(scores: np.ndarray) -> float:
    """
    CV R² 점수 배열에서 안전한 평균 계산.
    - NaN 제거
    - 극단적 음수(< -1.0) fold 제외: TimeSeriesSplit 초기 fold의 폭발값 방지
    - 유효 점수가 없으면 0.0 반환
    """
    s = scores[np.isfinite(scores)]          # NaN/Inf 제거
    s = s[s >= -1.0]                         # 극단 음수 fold 제거
    return float(np.mean(s)) if len(s) > 0 else 0.0


def _tune_and_train(
    X_train: pd.DataFrame,
    y_train: pd.Series,
) -> tuple[XGBRegressor, float]:
    """
    XGBoost 학습.
    - efficiency 등 분산이 거의 0인 타깃은 상수 예측이 더 안정적
      → std < 0.005 이면 평균값 상수 모델 반환 (R²=1.0 표시)
    - R² < MIN_R2 시 하이퍼파라미터 재튜닝
    """
    # 분산 체크: 거의 상수인 타깃은 XGBoost가 과적합 or R²=-∞ 위험
    y_std = float(y_train.std())
    if y_std < 0.005:
        # 상수 예측 래퍼 모델
        mean_val = float(y_train.mean())
        model = XGBRegressor(**XGBOOST_PARAMS.copy())
        model.fit(X_train, y_train)          # fit은 해두되 예측은 mean에 가까울 것
        return model, 1.0                    # 분산 없음 → R²=1로 표시

    params = XGBOOST_PARAMS.copy()
    model = XGBRegressor(**params)
    model.fit(X_train, y_train)

    cv = _choose_cv_split(len(X_train))
    scores = cross_val_score(model, X_train, y_train, cv=cv, scoring="r2", n_jobs=-1)
    r2_mean = _safe_r2_mean(scores)

    if r2_mean < MIN_R2:
        params.update({"n_estimators": 600, "max_depth": 8, "learning_rate": 0.03})
        model = XGBRegressor(**params)
        model.fit(X_train, y_train)
        scores = cross_val_score(model, X_train, y_train, cv=cv, scoring="r2", n_jobs=-1)
        r2_mean = _safe_r2_mean(scores)

    return model, r2_mean


def train_all_models(df: pd.DataFrame) -> dict:
    """
    모드별·타깃별 XGBRegressor 학습, pkl 저장, 성능 지표 반환.

    전체 데이터를 시계열 순으로 train / test 분할(test는 마지막 구간만).
    학습·5-Fold CV·튜닝은 train만 사용. test는 최종 R²·MAE 산출에만 사용.

    Returns:
        metrics: {mode: {target: {...}}}, metrics["_split"]: 분할 메타
    """
    Path(MODEL_DIR).mkdir(parents=True, exist_ok=True)
    df = build_features(df)
    df_train, df_test = time_series_train_test_split(df, ML_TEST_FRACTION)

    split_meta = {
        "test_fraction": ML_TEST_FRACTION,
        "n_all": len(df),
        "n_train": len(df_train),
        "n_test": len(df_test),
        "train_datetime_start": str(df_train["datetime"].min()),
        "train_datetime_end": str(df_train["datetime"].max()),
        "test_datetime_start": str(df_test["datetime"].min()),
        "test_datetime_end": str(df_test["datetime"].max()),
    }

    # 효율 컬럼명 정규화 (CSV 컬럼명에 따라 조정)
    target_map = {
        "export":     "export",
        "import":     "import",
        "efficiency": "efficiency",
    }

    metrics: dict = {"_split": split_meta}

    for mode in MODES:
        subset_tr = df_train[df_train["mode"] == mode].dropna(subset=FEATURE_COLS)

        # low2gi: train 구간만으로 경계 보강 (테스트 행 제외)
        # v9 이후 저부하 388행 추가로 충분할 수 있으나 안전망 유지
        if mode == "low2gi" and len(subset_tr) < 100:
            border = df_train[
                (df_train["lng_gen"] >= 370_000) & (df_train["lng_gen"] <= 450_000)
            ].dropna(subset=FEATURE_COLS)
            subset_tr = pd.concat([subset_tr, border]).drop_duplicates()
            print(f"[INFO] low2gi train 보강: {len(subset_tr)}행 (380k~440k 경계구간, train만)")

        subset_te = df_test[df_test["mode"] == mode].dropna(subset=FEATURE_COLS)

        metrics[mode] = {}

        if len(subset_tr) < 50:
            print(f"[WARN] {mode} train 데이터 부족 ({len(subset_tr)}행) - 스킵")
            continue

        X_tr = subset_tr[FEATURE_COLS]

        for target_key, col in target_map.items():
            if col not in subset_tr.columns:
                print(f"[WARN] 컬럼 '{col}' 없음 - 스킵")
                continue

            y_tr = subset_tr[col].fillna(0)
            model, r2_cv = _tune_and_train(X_tr, y_tr)

            y_pred_tr = model.predict(X_tr)
            mae_tr = mean_absolute_error(y_tr, y_pred_tr)
            r2_tr = r2_score(y_tr, y_pred_tr)

            mae_te = None
            r2_te = None
            n_te = len(subset_te)
            if n_te > 0 and col in subset_te.columns:
                X_te = subset_te[FEATURE_COLS]
                y_te = subset_te[col].fillna(0)
                y_pred_te = model.predict(X_te)
                mae_te = round(float(mean_absolute_error(y_te, y_pred_te)), 4)
                r2_te = round(float(r2_score(y_te, y_pred_te)), 4)

            metrics[mode][target_key] = {
                "mae": round(mae_tr, 4),
                "r2": round(r2_tr, 4),
                "r2_cv": round(r2_cv, 4),
                "mae_test": mae_te,
                "r2_test": r2_te,
                "n_samples": len(subset_tr),
                "n_samples_test": n_te,
            }

            path = _get_model_path(mode, target_key)
            with open(path, "wb") as f:
                pickle.dump(model, f)
            te_msg = f", test MAE={mae_te}, R²={r2_te}" if mae_te is not None else ", test: 표본 없음"
            print(f"[OK] {mode}/{target_key} → train MAE={mae_tr:.2f}, R²={r2_tr:.4f}{te_msg}")

    with open(_get_metrics_path(), "wb") as f:
        pickle.dump(metrics, f)

    impute_defaults: dict = {}
    for mode in MODES:
        sub = df_train[df_train["mode"] == mode]
        if len(sub) == 0:
            impute_defaults[mode] = {"lng_gen": 0.0, "net_load": 280_000.0}
        else:
            lg = sub["lng_gen"].median()
            nl = sub["net_load"].median() if "net_load" in sub.columns else 280_000.0
            impute_defaults[mode] = {
                "lng_gen": float(lg) if pd.notna(lg) else 0.0,
                "net_load": float(nl) if pd.notna(nl) else 280_000.0,
            }
    with open(_get_impute_path(), "wb") as f:
        pickle.dump(impute_defaults, f)

    return metrics


# ──────────────────────────────────────────────────────────────
# F2.3  모델 로드 및 추론
# ──────────────────────────────────────────────────────────────

def load_models(df: pd.DataFrame | None = None) -> tuple[dict, dict]:
    """
    저장된 pkl 로드. 없으면 학습 후 저장.

    Returns:
        models:  {mode: {target: XGBRegressor}}
        metrics: {mode: {target: {mae, r2, ...}}}
    """
    models: dict = {}
    all_exist = all(
        _get_model_path(mode, target).exists()
        for mode in MODES
        for target in TARGET_NAMES
    )

    if not all_exist:
        if df is None:
            df = load_data()
        metrics = train_all_models(df)
    else:
        metrics_path = _get_metrics_path()
        metrics = pickle.load(open(metrics_path, "rb")) if metrics_path.exists() else {}

    for mode in MODES:
        models[mode] = {}
        for target in TARGET_NAMES:
            p = _get_model_path(mode, target)
            if p.exists():
                with open(p, "rb") as f:
                    models[mode][target] = pickle.load(f)

    return models, metrics


def _load_impute_defaults() -> dict[str, dict[str, float]]:
    """추론 시 LNG발전량·순부하 미입력이면 모드별 학습 데이터 중앙값 사용."""
    p = _get_impute_path()
    if p.is_file():
        with open(p, "rb") as f:
            return pickle.load(f)
    return {
        "1gi":    {"lng_gen": 200_000.0, "net_load": 280_000.0},
        "low2gi": {"lng_gen": 430_000.0, "net_load": 280_000.0},
        "2gi":    {"lng_gen": 520_000.0, "net_load": 280_000.0},
    }


def predict_for_hour(
    models: dict,
    mode: str,
    hour: int,
    month: int,
    weekday: int,
    smp: float,
    lng_price: float,
    lng_heat: float,
    elec_price: float,
    exchange_rate: float,
    lng_gen: float | None = None,
    net_load: float | None = None,
) -> dict:
    """
    단일 시간·모드에 대한 역송량, 수전량, 효율 예측.

    Returns:
        {"export": float, "import": float, "efficiency": float}
    """
    imp = _load_impute_defaults().get(mode, {"lng_gen": 0.0, "net_load": 280_000.0})
    if lng_gen is None:
        lng_gen = imp["lng_gen"]
    if net_load is None:
        net_load = imp["net_load"]

    row = {
        "hour":         hour,
        "weekday":      weekday,
        "month":        month,
        "smp":          smp,
        "lng_price":    lng_price,
        "lng_heat":     lng_heat,
        "elec_price":   elec_price,
        "exchange_rate": exchange_rate,
        "lng_gen":      float(lng_gen),
        "net_load":     float(net_load),
        "hour_sin":     np.sin(2 * np.pi * hour / 24),
        "hour_cos":     np.cos(2 * np.pi * hour / 24),
        "month_sin":    np.sin(2 * np.pi * month / 12),
        "month_cos":    np.cos(2 * np.pi * month / 12),
    }
    X = pd.DataFrame([row])[FEATURE_COLS]

    result = {}
    mode_models = models.get(mode, {})

    for target in TARGET_NAMES:
        model = mode_models.get(target)
        if model:
            val = float(model.predict(X)[0])
            result[target] = max(0.0, round(val, 2))
        else:
            result[target] = 0.0

    # low2gi 효율: ML 예측값 사용, 모델 없으면 실측 평균 폴백 (1.7 하드코딩 X)
    if mode == "low2gi" and result.get("efficiency", 0.0) == 0.0:
        from config import LOW2GI_EFF_FALLBACK
        result["efficiency"] = LOW2GI_EFF_FALLBACK

    return result


def predict_day(
    models: dict,
    target_date,          # date or datetime
    smp_series: list[float],
    lng_price: float,
    lng_heat: float,
    exchange_rate: float,
    elec_price_fn,        # callable(date, hour) → float
    lng_gen_series: list[float | None] | None = None,
    net_load_series: list[float | None] | None = None,
) -> dict:
    """
    24시간 × 3모드 예측.

    Returns:
        {mode: {hour: {"export", "import", "efficiency"}}}
    """
    from datetime import date as date_type
    if isinstance(target_date, date_type):
        month   = target_date.month
        weekday = target_date.weekday()
    else:
        month   = target_date.month
        weekday = target_date.weekday()

    results: dict = {mode: {} for mode in MODES}

    for mode in MODES:
        for hour in range(24):
            smp        = smp_series[hour] if hour < len(smp_series) else 0.0
            elec_price = elec_price_fn(target_date, hour)

            lg = None
            nl = None
            if lng_gen_series is not None and hour < len(lng_gen_series):
                v = lng_gen_series[hour]
                if v is not None and v == v and not pd.isna(v):
                    lg = float(v)
            if net_load_series is not None and hour < len(net_load_series):
                v = net_load_series[hour]
                if v is not None and v == v and not pd.isna(v):
                    nl = float(v)

            preds = predict_for_hour(
                models, mode, hour, month, weekday,
                smp, lng_price, lng_heat, elec_price, exchange_rate,
                lng_gen=lg,
                net_load=nl,
            )
            results[mode][hour] = preds

    return results


# ──────────────────────────────────────────────────────────────
# F2.4  재학습 관리
# ──────────────────────────────────────────────────────────────

def retrain(df: pd.DataFrame | None = None) -> dict:
    """버튼 트리거 방식 재학습. 기존 pkl 덮어쓰기."""
    if df is None:
        df = load_data()
    return train_all_models(df)
