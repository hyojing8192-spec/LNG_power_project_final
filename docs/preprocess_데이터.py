"""
data/데이터.csv 전처리
====================
- 셀 값 `-`(단독)·`#DIV/0!`·빈 값 → 숫자 열에서는 0
- 결측치(NaN) → 0 (시간 열 `구분`은 빈 문자열만 정리)
- 2기 저부하 관련 열: 실측이 거의 없거나 0인 구간은, 같은 행의 LNG발전 2기(정격 부하) 열과의 비율로 더미 보강
  (학습 데이터 부족 완화; `config.MODE_THRESHOLDS`의 1기~저부하 구간 사용)

실행 (프로젝트 루트): `python modules/preprocess_데이터.py` 또는 `python -m modules.preprocess_데이터`
기본 출력: `data/데이터_preprocessed.csv` (원본은 덮어쓰지 않음)
"""

from __future__ import annotations

import argparse
import sys
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd

# 프로젝트 루트 (이 파일이 modules/ 안에 있음)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config import MODE_THRESHOLDS  # noqa: E402

ENC = "utf-8-sig"
META_LINES = 3

# 2기 저부하 열 → 참조할 LNG발전 2기(동종) 열 (엑셀 수식 구조와 대응)
LOW2GI_REFERENCE_PAIRS: tuple[tuple[str, str], ...] = (
    (
        "LNG발전 2기 저부하\n대체단가(원/kwh)",
        "LNG발전 2기\n대체단가(원/kWh)",
    ),
    (
        "LNG발전 2기 저부하\nBEP($/MMBtu)",
        "LNG발전 2기\nBEP($/MMBtu)",
    ),
    ("LNG발전 2기 저부하", "LNG발전 2기"),
    ("LNG발전 2기 저부하.1", "LNG발전 2기.1"),
)

# 저부하 구간에서 (저부하>0 & 참조>0)인 행이 이 값 미만이면 비율 추정이 불안정 → 고정 비율 사용
MIN_ROWS_FOR_RATIO = 5
# 저부하 발전량 밴드: 1기 상한 ~ 저부하 상한 (kW)
LOW2GI_GEN_MIN = float(MODE_THRESHOLDS["1gi_max"])
LOW2GI_GEN_MAX = float(MODE_THRESHOLDS["low2gi_max"])
# 비율을 못 구할 때: 저부하가 정격 2기보다 약간 낮다는 가정의 보수적 스케일
FALLBACK_RATIO = 0.98


def _series_to_numeric_zero(s: pd.Series) -> pd.Series:
    """쉼표·단독 `-`·#DIV/0! 등을 제거한 뒤 숫자로 변환, 불가하면 0."""
    t = s.astype(str).str.replace(",", "", regex=False).str.strip()
    t = t.replace("#DIV/0!", "0", regex=False)
    # 전각/하이픈 변형
    for dash in ("–", "—"):
        t = t.str.replace(dash, "-", regex=False)
    # 셀 전체가 하이픈 하나뿐인 경우(결측)만 0 — 음수(-2.1 등)는 유지
    lone = t.str.match(r"^-\s*$")
    t = t.where(~lone, "0")
    t = t.replace({"-": "0", "nan": "0", "None": "0", "": "0"})
    out = pd.to_numeric(t, errors="coerce")
    return out.fillna(0.0)


def _load_csv_with_meta(path: Path) -> tuple[str, pd.DataFrame]:
    raw = path.read_text(encoding=ENC)
    lines = raw.splitlines()
    if len(lines) < META_LINES + 1:
        raise ValueError(f"파일이 너무 짧습니다(메타 {META_LINES}행 + 본문 필요): {path}")
    meta = "\n".join(lines[:META_LINES]) + "\n"
    rest = "\n".join(lines[META_LINES:])
    df = pd.read_csv(StringIO(rest), encoding=ENC, on_bad_lines="skip")
    return meta, df


def _basic_numeric_fill(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    time_col = "구분"
    for col in out.columns:
        if col == time_col:
            out[col] = out[col].astype(str).replace({"nan": ""}).fillna("")
            continue
        out[col] = _series_to_numeric_zero(out[col])
    return out


def _impute_low2gi_from_reference(df: pd.DataFrame) -> pd.DataFrame:
    """
    LNG발전량이 2기 저부하 밴드일 때, 저부하 열이 0이고 참조(2기 정격) 열이 양수면
    학습된 중앙 비율(또는 FALLBACK_RATIO)로 저부하 값을 채움.
    """
    out = df.copy()
    gen_col = "LNG발전량(kW)"
    if gen_col not in out.columns:
        return out

    g = out[gen_col].astype(float)
    band = (g >= LOW2GI_GEN_MIN) & (g < LOW2GI_GEN_MAX)

    for low_col, ref_col in LOW2GI_REFERENCE_PAIRS:
        if low_col not in out.columns or ref_col not in out.columns:
            continue
        low = out[low_col].astype(float)
        ref = out[ref_col].astype(float)

        valid_ratio = band & (low > 0) & (ref > 0)
        if int(valid_ratio.sum()) >= MIN_ROWS_FOR_RATIO:
            rvec = low[valid_ratio] / ref[valid_ratio]
            rvec = rvec.replace([np.inf, -np.inf], np.nan).dropna()
            ratio = float(rvec.median()) if len(rvec) else FALLBACK_RATIO
            if not np.isfinite(ratio) or ratio <= 0:
                ratio = FALLBACK_RATIO
        else:
            ratio = FALLBACK_RATIO

        need = band & (low <= 0) & (ref > 0)
        if need.any():
            out.loc[need, low_col] = ref[need] * ratio

    return out


def preprocess_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    df = _basic_numeric_fill(df)
    df = _impute_low2gi_from_reference(df)
    return df


def run(
    input_path: Path,
    output_path: Path,
) -> None:
    meta, df = _load_csv_with_meta(input_path)
    n_before = len(df)
    df = preprocess_dataframe(df)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding=ENC, newline="") as f:
        f.write(meta)
        df.to_csv(f, index=False, lineterminator="\n")
    print(f"저장: {output_path} (행 {n_before}건)")


def main() -> None:
    ap = argparse.ArgumentParser(description="데이터.csv 전처리 ( -, #DIV/0!, 결측→0, 저부하 더미 보강 )")
    ap.add_argument(
        "-i",
        "--input",
        type=Path,
        default=_PROJECT_ROOT / "data" / "데이터.csv",
        help="입력 CSV",
    )
    ap.add_argument(
        "-o",
        "--output",
        type=Path,
        default=_PROJECT_ROOT / "data" / "데이터_preprocessed.csv",
        help="출력 CSV",
    )
    args = ap.parse_args()
    if not args.input.is_file():
        raise SystemExit(f"입력 파일 없음: {args.input}")
    run(args.input, args.output)


if __name__ == "__main__":
    main()
