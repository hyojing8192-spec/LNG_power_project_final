"""
epower_automation.py
====================
ePower 마켓(KMOS) UI 자동화 — SMP 엑셀 자동 다운로드.

[동작 흐름]
  1. ePower 마켓 창 활성화 (없으면 바로가기로 실행)
  2. 계통한계가격 메뉴 클릭
  3. 날짜 필드 클릭 → 전체선택 → 익일 날짜 입력
  4. 조회 버튼 클릭
  5. 엑셀 저장 아이콘 클릭
  6. 다운로드된 엑셀을 data/smp_excel/ 로 이동

[좌표 설정]
  처음 한번만 실행:
    python epower_automation.py --calibrate

[사용법]
  python epower_automation.py              # 익일 SMP 자동 다운로드
  python epower_automation.py --date 2026-04-08
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import time
from datetime import date, timedelta
from pathlib import Path

import pyautogui
import pygetwindow as gw

# ── 경로 ──────────────────────────────────────────────────────
PROJECT_ROOT  = Path(__file__).resolve().parent.parent   # 과제_최종/
DATA_DIR      = PROJECT_ROOT / "data"
SMP_EXCEL_DIR = DATA_DIR / "smp_excel"
CONFIG_PATH   = PROJECT_ROOT / "docs" / "epower_coords.json"
DOWNLOAD_DIR  = Path.home() / "Downloads"

EPOWER_LNK = r"C:\Users\user\Desktop\ePower 마켓.lnk"

# ── 로깅 ──────────────────────────────────────────────────────
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "epower_automation.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.3


# ── 좌표 관리 ─────────────────────────────────────────────────

DEFAULT_COORDS = {
    "date_field":        [660, 155],
    "search_button":     [1005, 155],
    "excel_save_button": [1775, 98],
    "menu_smp":          [115, 635],
}


def load_coords() -> dict:
    if CONFIG_PATH.is_file():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            saved = json.load(f)
        coords = DEFAULT_COORDS.copy()
        coords.update(saved)
        return coords
    return DEFAULT_COORDS.copy()


def save_coords(coords: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(coords, f, ensure_ascii=False, indent=2)


# ── 캘리브레이션 ──────────────────────────────────────────────

def calibrate():
    coords = load_coords()

    print("\n" + "=" * 55)
    print("  ePower 마켓 좌표 설정")
    print("  ePower 마켓을 열어놓은 상태에서 진행하세요.")
    print("=" * 55)

    items = [
        ("date_field",        "날짜 입력 필드 (날짜 텍스트 위)"),
        ("search_button",     "조회 버튼"),
        ("excel_save_button", "엑셀 저장 아이콘 (우측 상단)"),
        ("menu_smp",          "좌측 메뉴 '계통한계가격'"),
    ]

    for key, label in items:
        print(f"\n  [{label}]  현재: {coords.get(key)}")
        answer = input("  마우스를 위치에 올리고 Enter (s=건너뛰기): ").strip()
        if answer.lower() == "s":
            continue

        print("  3초 후 좌표 기록...")
        for i in range(3, 0, -1):
            print(f"  {i}...", end=" ", flush=True)
            time.sleep(1)
        x, y = pyautogui.position()
        coords[key] = [x, y]
        print(f"\n  -> ({x}, {y})")

    save_coords(coords)
    print("\n  설정 완료!")
    for key, label in items:
        print(f"    {label}: {coords[key]}")


# ── KMOS 창 제어 ──────────────────────────────────────────────

def _find_kmos():
    for w in gw.getAllWindows():
        if "KMOS" in w.title:
            return w
    return None


def _activate_kmos() -> bool:
    win = _find_kmos()

    if win is None:
        logger.info("[ePower] KMOS 미실행 → 실행...")
        if Path(EPOWER_LNK).exists():
            os.startfile(EPOWER_LNK)
        else:
            logger.error("[ePower] 바로가기 없음")
            return False

        for _ in range(40):
            time.sleep(1)
            win = _find_kmos()
            if win:
                break

    if win is None:
        logger.error("[ePower] KMOS 찾을 수 없음")
        return False

    try:
        if win.isMinimized:
            win.restore()
        win.activate()
        time.sleep(1)
        logger.info(f"[ePower] KMOS 활성화 완료")
        return True
    except Exception as e:
        logger.error(f"[ePower] 활성화 실패: {e}")
        return False


# ── 엑셀 다운로드 감지 ────────────────────────────────────────

def _snapshot_excel_files() -> set:
    """현재 다운로드 폴더 + smp_excel 폴더의 엑셀 파일 목록."""
    files = set()
    for d in [DOWNLOAD_DIR, SMP_EXCEL_DIR, Path.home() / "Documents"]:
        if d.is_dir():
            files |= set(d.glob("*.xlsx"))
            files |= set(d.glob("*.xls"))
    return files


def _wait_for_new_excel(before: set, timeout: int = 20) -> Path | None:
    """새 엑셀 파일이 나타나길 대기."""
    for _ in range(timeout):
        time.sleep(1)
        after = _snapshot_excel_files()
        new = after - before
        if new:
            newest = max(new, key=lambda f: f.stat().st_mtime)
            # 다운로드 중인 파일(.tmp, .crdownload) 건너뛰기
            if newest.suffix in (".xlsx", ".xls"):
                logger.info(f"[ePower] 새 엑셀: {newest}")
                return newest
    return None


# ── 메인 자동화 ───────────────────────────────────────────────

def download_smp_excel(target_date: date) -> Path | None:
    """
    ePower 마켓에서 SMP 엑셀 자동 다운로드.

    Returns:
        data/smp_excel/ 에 저장된 엑셀 경로, 실패 시 None
    """
    coords = load_coords()
    date_str = target_date.strftime("%Y-%m-%d")

    # 1. KMOS 활성화
    if not _activate_kmos():
        return None
    time.sleep(1)

    # 2. 계통한계가격 메뉴 (이미 열려있을 수도 있으므로 클릭)
    pos = coords["menu_smp"]
    pyautogui.click(pos[0], pos[1])
    logger.info(f"[ePower] 계통한계가격 메뉴 클릭")
    time.sleep(2)

    # 3. 날짜 입력: 필드 클릭 → Ctrl+A → 날짜 타이핑
    pos = coords["date_field"]
    pyautogui.click(pos[0], pos[1])
    time.sleep(0.3)
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.1)
    pyautogui.typewrite(target_date.strftime("%Y-%m-%d"), interval=0.03)
    logger.info(f"[ePower] 날짜 입력: {date_str}")
    time.sleep(0.3)

    # 4. 조회 클릭
    pos = coords["search_button"]
    pyautogui.click(pos[0], pos[1])
    logger.info(f"[ePower] 조회 클릭")
    time.sleep(3)

    # 5. 엑셀 다운로드
    before = _snapshot_excel_files()

    pos = coords["excel_save_button"]
    pyautogui.click(pos[0], pos[1])
    logger.info(f"[ePower] 엑셀 저장 클릭")
    time.sleep(2)

    # 저장 다이얼로그 대응: Enter 또는 "저장" 클릭
    pyautogui.press("enter")
    time.sleep(1)

    # 6. 새 엑셀 파일 감지
    excel_path = _wait_for_new_excel(before)
    if excel_path is None:
        logger.warning("[ePower] 엑셀 감지 실패")
        return None

    # 7. data/smp_excel/ 로 이동
    SMP_EXCEL_DIR.mkdir(parents=True, exist_ok=True)
    dest = SMP_EXCEL_DIR / f"계통한계가격_{target_date.strftime('%Y%m%d')}_육지.xlsx"
    if dest.exists():
        dest.unlink()
    shutil.move(str(excel_path), str(dest))
    logger.info(f"[ePower] 저장 완료: {dest}")

    return dest


# ── CLI ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ePower 마켓 SMP 엑셀 자동 다운로드")
    parser.add_argument("--calibrate", action="store_true", help="좌표 설정")
    parser.add_argument("--date", type=str, default=None, help="대상 날짜 (기본: 내일)")
    args = parser.parse_args()

    if args.calibrate:
        calibrate()
        return

    target = date.today() + timedelta(days=1)
    if args.date:
        target = date.fromisoformat(args.date)

    logger.info(f"ePower SMP 다운로드 시작 — 대상: {target}")

    result = download_smp_excel(target)
    if result:
        print(f"\n  OK: {result}")
    else:
        print(f"\n  FAIL — python epower_automation.py --calibrate 로 좌표 확인")


if __name__ == "__main__":
    main()
