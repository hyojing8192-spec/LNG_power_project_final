"""
model_comparison_test.py
========================
XGBoost vs RandomForest vs LightGBM 비교 테스트 (독립 실행용)

- 기존 시스템 파일을 수정하지 않습니다.
- 기존 데이터(data/데이터.csv)와 피처 엔지니어링을 재사용합니다.
- 동일한 8:2 월별 stratified 분할로 공정하게 비교합니다.
- 7:3 분할과도 비교합니다.

실행 방법:
    cd C:\\Users\\user\\Desktop\\과제_최종
    python scripts/model_comparison_test.py
"""

from __future__ import annotations

import sys
import os
import warnings

# 프로젝트 루트를 sys.path에 추가 (modules/ 임포트용)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "modules"))

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import KFold, cross_val_score
from xgboost import XGBRegressor

try:
    import lightgbm as lgb
    _HAS_LGB = True
except ImportError:
    _HAS_LGB = False
    print("[WARN] lightgbm 미설치. pip install lightgbm 으로 설치하세요.")

# 기존 모듈에서 데이터 로딩·피처 빌드 함수만 읽기 전용으로 가져옴
from ml_predictor import load_data, build_features, time_series_train_test_split
from config import MODEL_FEATURES, MODES, XGBOOST_PARAMS

FEATURE_COLS = MODEL_FEATURES
TARGET_COLS = ["export", "import"]

# ── 모델 정의 ─────────────────────────────────────────────────────────────────

def make_models() -> dict:
    models = {
        "XGBoost": XGBRegressor(
            n_estimators=300, max_depth=5, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, min_child_weight=10,
            random_state=42, n_jobs=-1,
        ),
        "RandomForest": RandomForestRegressor(
            n_estimators=300, max_depth=None, min_samples_leaf=5,
            random_state=42, n_jobs=-1,
        ),
    }
    if _HAS_LGB:
        models["LightGBM"] = lgb.LGBMRegressor(
            n_estimators=300, max_depth=5, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, min_child_weight=10,
            random_state=42, n_jobs=-1, verbose=-1,
        )
    return models


# ── 학습·평가 ─────────────────────────────────────────────────────────────────

def evaluate_split(df: pd.DataFrame, test_fraction: float) -> pd.DataFrame:
    """
    지정한 분할 비율로 모드별·타깃별·모델별 MAE/R² 비교표 반환.
    """
    df_feat = build_features(df)
    df_train, df_test = time_series_train_test_split(df_feat, test_fraction)

    rows = []
    for mode in MODES:
        subset_tr = df_train[df_train["mode"] == mode].dropna(subset=FEATURE_COLS)
        subset_te = df_test[df_test["mode"] == mode].dropna(subset=FEATURE_COLS)

        if len(subset_tr) < 30:
            print(f"  [SKIP] {mode} train 데이터 부족 ({len(subset_tr)}행)")
            continue

        X_tr = subset_tr[FEATURE_COLS]
        X_te = subset_te[FEATURE_COLS] if len(subset_te) > 0 else None

        for target in TARGET_COLS:
            if target not in subset_tr.columns:
                continue

            y_tr = subset_tr[target].fillna(0)
            y_te = subset_te[target].fillna(0) if len(subset_te) > 0 else None

            for model_name, base_model in make_models().items():
                import copy
                model = copy.deepcopy(base_model)

                # 학습
                model.fit(X_tr, y_tr)

                # Train 지표
                y_pred_tr = model.predict(X_tr)
                mae_tr = mean_absolute_error(y_tr, y_pred_tr)
                r2_tr  = r2_score(y_tr, y_pred_tr)

                # CV R² (5-Fold)
                n = len(X_tr)
                k = max(2, min(5, n // 20))
                cv = KFold(n_splits=k, shuffle=True, random_state=42)
                cv_scores = cross_val_score(model, X_tr, y_tr, cv=cv, scoring="r2", n_jobs=-1)
                r2_cv = float(np.mean(cv_scores[np.isfinite(cv_scores)]))

                # Test 지표
                if X_te is not None and y_te is not None and len(X_te) > 0:
                    y_pred_te = model.predict(X_te)
                    mae_te = mean_absolute_error(y_te, y_pred_te)
                    r2_te  = r2_score(y_te, y_pred_te)
                else:
                    mae_te = r2_te = None

                rows.append({
                    "모드":         mode,
                    "타깃":         target,
                    "모델":         model_name,
                    "Train MAE":   round(mae_tr, 2),
                    "Train R²":    round(r2_tr, 4),
                    "CV R² (5fold)": round(r2_cv, 4),
                    "Test MAE":    round(mae_te, 2) if mae_te is not None else "-",
                    "Test R²":     round(r2_te, 4) if r2_te is not None else "-",
                    "Train N":     len(subset_tr),
                    "Test N":      len(subset_te),
                })

    return pd.DataFrame(rows)


# ── 출력 ──────────────────────────────────────────────────────────────────────

def print_table(df: pd.DataFrame, title: str) -> None:
    print(f"\n{'='*80}")
    print(f"  {title}")
    print(f"{'='*80}")
    if df.empty:
        print("  (결과 없음)")
        return

    display_cols = ["모드", "타깃", "모델", "Train MAE", "Train R²", "CV R² (5fold)", "Test MAE", "Test R²", "Train N", "Test N"]
    print(df[display_cols].to_string(index=False))


def print_winner_summary(df82: pd.DataFrame, df73: pd.DataFrame) -> None:
    print(f"\n{'='*80}")
    print("  모델별 종합 순위 (Test R² 기준, 높을수록 좋음)")
    print(f"{'='*80}")

    for label, df in [("8:2 분할", df82), ("7:3 분할", df73)]:
        print(f"\n[{label}]")
        # Test R²가 숫자인 행만 집계
        numeric = df[df["Test R²"] != "-"].copy()
        numeric["Test R²"] = numeric["Test R²"].astype(float)
        if numeric.empty:
            print("  (비교 불가 — test 데이터 없음)")
            continue
        summary = (
            numeric.groupby("모델")["Test R²"]
            .agg(["mean", "min", "max", "count"])
            .rename(columns={"mean": "평균 R²", "min": "최소 R²", "max": "최대 R²", "count": "항목수"})
            .sort_values("평균 R²", ascending=False)
        )
        print(summary.to_string())


# ── 메인 ──────────────────────────────────────────────────────────────────────

def main():
    data_path = os.path.join(PROJECT_ROOT, "data", "데이터.csv")
    print(f"데이터 로딩: {data_path}")
    df = load_data(data_path)
    print(f"  → {len(df)}행 × {len(df.columns)}열 로드 완료")
    print(f"  → 기간: {df['datetime'].min().date()} ~ {df['datetime'].max().date()}")

    print("\n[1/2] 8:2 분할 평가 중 ...")
    df82 = evaluate_split(df, test_fraction=0.2)
    print_table(df82, "모델 비교 결과 [8:2 분할 - 현재 시스템과 동일]")

    print("\n[2/2] 7:3 분할 평가 중 ...")
    df73 = evaluate_split(df, test_fraction=0.3)
    print_table(df73, "모델 비교 결과 [7:3 분할 - 비교용]")

    print_winner_summary(df82, df73)

    # CSV 저장 (선택)
    out_dir = os.path.join(PROJECT_ROOT, "data")
    out_path = os.path.join(out_dir, "model_comparison_result.csv")
    combined = pd.concat(
        [df82.assign(분할="8:2"), df73.assign(분할="7:3")],
        ignore_index=True,
    )
    combined.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\n결과 저장: {out_path}")


if __name__ == "__main__":
    main()
