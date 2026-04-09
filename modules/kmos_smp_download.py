"""
KMOS 계통한계가격(SMP) 자동 다운로드 스크립트
- ePower 마켓 실행부터 엑셀 저장까지 전체 자동화
- PyAutoGUI 기반 GUI 자동화

사용법:
  1) 좌표 캡처 (최초 1회):
       python kmos_smp_download.py --calibrate
     → 각 버튼 위치에 마우스를 올리고 Enter 누르면 좌표 저장

  2) SMP 다운로드:
       python kmos_smp_download.py
       python kmos_smp_download.py --skip-menu --skip-date

  3) 좌표 확인:
       python kmos_smp_download.py --show-coords
"""

import pyautogui
import subprocess
import time
import os
import json
import argparse
from datetime import datetime, timedelta
from pathlib import Path

# 안전장치: 마우스를 화면 좌측 상단 모서리로 옮기면 즉시 중단
pyautogui.FAILSAFE = True

# ── 경로 설정 ────────────────────────────────────────────────
KMOS_SHORTCUT = r"C:\Users\user\Desktop\ePower 마켓.lnk"

PROJECT_ROOT = Path(__file__).resolve().parent.parent   # 과제_최종/
DEFAULT_SAVE_DIR = str(PROJECT_ROOT / "data" / "smp_excel")
DEFAULT_FILENAME = None  # None이면 자동 생성: 계통한계가격_YYYYMMDD_육지

# 좌표 저장 파일 (캘리브레이션 결과)
COORDS_FILE = PROJECT_ROOT / "data" / "kmos_coords.json"

# 좌표 기본값 (1920x1080, 125% 배율, 싱글 모니터, 전체화면 기준)
# --calibrate 로 실제 좌표를 캡처하면 kmos_coords.json에 저장됨
COORDS_DEFAULT = {
    "계통한계가격_메뉴": (910, 390),
    "내일_날짜_선택": (485, 102),
    "조회_버튼": (635, 102),
    "엑셀_아이콘": (1500, 103),
    "저장하기_버튼": (1495, 123),
    "파일이름_입력": (870, 555),
    "저장_버튼": (1125, 613),
    "디렉토리_입력": (750, 262),
    "안전확인_팝업": (740, 458),
}


def load_coords() -> dict:
    """저장된 좌표 로드. 없으면 기본값 반환."""
    if COORDS_FILE.exists():
        with open(COORDS_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        # 기본값에 저장값 덮어쓰기 (새 키 추가 대응)
        merged = {**COORDS_DEFAULT, **saved}
        return merged
    return COORDS_DEFAULT.copy()


def save_coords(coords: dict):
    """좌표를 JSON 파일에 저장."""
    COORDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(COORDS_FILE, "w", encoding="utf-8") as f:
        json.dump(coords, f, ensure_ascii=False, indent=2)
    print(f"\n  좌표 저장 완료: {COORDS_FILE}")


COORDS = load_coords()


def is_kmos_running():
    """KMOS가 이미 실행 중인지 확인"""
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq XPlatform.exe"],
        capture_output=True, text=True
    )
    return "XPlatform.exe" in result.stdout


def clipboard_paste(text):
    """클립보드에 텍스트 복사 후 붙여넣기 (한글/특수문자 지원)"""
    # 큰따옴표로 감싸고, 내부 큰따옴표는 이스케이프
    escaped = text.replace('"', '`"')
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", f'Set-Clipboard -Value "{escaped}"'],
        capture_output=True
    )
    pyautogui.hotkey("ctrl", "v")


def download_smp_from_kmos(
    filename=DEFAULT_FILENAME,
    delay=1.0,
    skip_menu=False,
    skip_date=False,
):
    """
    KMOS에서 계통한계가격 엑셀 파일을 자동 다운로드합니다.

    Args:
        filename: 저장할 파일명 (확장자 제외)
        delay: 클릭 간 대기 시간 (초)
        skip_menu: True면 계통한계가격 메뉴 클릭 생략
        skip_date: True면 날짜 선택 생략
    """

    now = lambda: datetime.now().strftime("%H:%M:%S")

    if filename is None:
        tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y%m%d')
        filename = f"계통한계가격_{tomorrow}_육지"

    print(f"[{now()}] KMOS SMP 자동 다운로드")
    print(f"  파일명: {filename}")
    print()

    # ── Step 0: KMOS 실행 ──
    if is_kmos_running():
        print(f"[{now()}] Step 0: KMOS 이미 실행 중. 전면 전환...")
        # XPlatform 창을 전면으로 가져오기
        subprocess.run([
            "powershell.exe", "-NoProfile", "-Command",
            "$p = Get-Process XPlatform -ErrorAction SilentlyContinue | "
            "Where-Object {$_.MainWindowHandle -ne 0} | Select-Object -First 1; "
            "if($p){ Add-Type '[DllImport(\"user32.dll\")] public static extern bool "
            "SetForegroundWindow(IntPtr hWnd); [DllImport(\"user32.dll\")] public static extern bool "
            "ShowWindow(IntPtr hWnd, int nCmdShow);' -Name W -Namespace A; "
            "[A.W]::ShowWindow($p.MainWindowHandle, 9); "  # SW_RESTORE
            "[A.W]::SetForegroundWindow($p.MainWindowHandle) }"
        ], capture_output=True)
        time.sleep(2)
    else:
        print(f"[{now()}] Step 0: ePower 마켓 실행...")
        os.startfile(KMOS_SHORTCUT)
        time_sleep = 35
        print(f"  {time_sleep}초 대기 (로딩)...")
        time.sleep(time_sleep)

        if is_kmos_running():
            print(f"  실행 확인!")
        else:
            print(f"  실행 실패. 수동으로 켜주세요.")
            return

    # ── Step 0.5: KMOS 전체화면 ──
    print(f"[{now()}] Step 0.5: KMOS 전체화면...")
    pyautogui.hotkey("win", "up")
    time.sleep(2)

    # ── Step 1: 계통한계가격 메뉴 ──
    if not skip_menu:
        print(f"[{now()}] Step 1: 계통한계가격 메뉴 클릭...")
        pyautogui.click(*COORDS["계통한계가격_메뉴"])
        time.sleep(delay * 3)
    else:
        print(f"[{now()}] Step 1: 생략 (skip_menu)")

    # ── Step 2: 내일 날짜 선택 ──
    if not skip_date:
        print(f"[{now()}] Step 2: 내일 날짜 선택...")
        pyautogui.click(*COORDS["내일_날짜_선택"])
        time.sleep(delay * 2)
    else:
        print(f"[{now()}] Step 2: 생략 (skip_date)")

    # ── Step 3: 조회 ──
    print(f"[{now()}] Step 3: 조회 버튼 클릭...")
    pyautogui.click(*COORDS["조회_버튼"])
    time.sleep(delay * 3)

    # ── Step 4: 엑셀 아이콘 ──
    print(f"[{now()}] Step 4: 엑셀 아이콘 클릭...")
    pyautogui.click(*COORDS["엑셀_아이콘"])
    time.sleep(delay)

    # ── Step 5: 저장하기 버튼 ──
    print(f"[{now()}] Step 5: 저장하기 버튼 클릭...")
    pyautogui.click(*COORDS["저장하기_버튼"])
    time.sleep(delay * 2)

    # ── Step 6: 저장 디렉토리 이동 ──
    print(f"[{now()}] Step 6: 저장 디렉토리 이동: {DEFAULT_SAVE_DIR}")
    # 주소창 포커스: Alt+D (Windows 저장 대화상자 표준 단축키)
    pyautogui.hotkey("alt", "d")
    time.sleep(0.5)
    clipboard_paste(DEFAULT_SAVE_DIR)
    time.sleep(0.5)
    pyautogui.press("enter")  # 디렉토리 이동 (저장 아님)
    time.sleep(delay * 3)  # 디렉토리 갱신 대기

    # # ── Step 6.5: 파일이름 입력 ──
    # print(f"[{now()}] Step 6.5: 파일이름 입력: {filename}")
    # pyautogui.doubleClick(*COORDS["파일이름_입력"])  # 더블클릭으로 전체 선택
    # time.sleep(0.5)
    # clipboard_paste(filename)
    # time.sleep(delay)

    # ── Step 7: 저장 ──
    print(f"[{now()}] Step 7: 저장 버튼 클릭...")
    pyautogui.click(*COORDS["저장_버튼"])
    time.sleep(delay * 2)

    # ── Step 7.5: "안전하지 않은 파일" 확인 팝업 ──
    print(f"[{now()}] Step 7.5: 안전 확인 팝업 클릭...")
    pyautogui.click(*COORDS["안전확인_팝업"])
    time.sleep(delay)

    # ── Step 7.6: 파일 저장 대기 ──
    import shutil, glob

    save_path = os.path.join(DEFAULT_SAVE_DIR, f"{filename}.xlsx")
    print(f"[{now()}] Step 7.6: 파일 저장 대기...")

    search_dirs = [
        DEFAULT_SAVE_DIR,
        os.path.expanduser("~/Documents"),
        os.path.expanduser("~/Downloads"),
        os.path.expanduser("~/Desktop"),
    ]

    found = False
    for i in range(30):  # 최대 30초 대기
        # 1차: 예상 파일명 확인
        if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
            print(f"  파일 확인! ({os.path.getsize(save_path):,} bytes)")
            found = True
            break

        # 2차: 각 디렉토리에서 최근 생성된 xlsx 파일 검색
        for search_dir in search_dirs:
            if not os.path.isdir(search_dir):
                continue
            for f in glob.glob(os.path.join(search_dir, "*.xlsx")):
                # ~$ 임시 파일 무시
                if os.path.basename(f).startswith("~$"):
                    continue
                # 최근 60초 이내 생성된 파일
                if time.time() - os.path.getmtime(f) < 60 and os.path.getsize(f) > 0:
                    if f != save_path:
                        print(f"  발견: {f}")
                        os.makedirs(DEFAULT_SAVE_DIR, exist_ok=True)
                        dest = os.path.join(DEFAULT_SAVE_DIR, os.path.basename(f))
                        shutil.move(f, dest)
                        save_path = dest
                        print(f"  -> {dest} 로 이동 완료")
                    else:
                        print(f"  파일 확인! ({os.path.getsize(f):,} bytes)")
                    found = True
                    break
            if found:
                break

        if found:
            break
        time.sleep(1)

    if not found:
        print(f"  [!] 파일을 찾지 못했습니다. 수동 확인 필요.")

    # ── Step 8: KMOS 종료 ──
    print(f"[{now()}] Step 8: KMOS 종료...")
    subprocess.run(["taskkill", "/IM", "XPlatform.exe", "/F"], capture_output=True)
    time.sleep(1)

    if not is_kmos_running():
        print(f"  KMOS 종료 완료.")
    else:
        print(f"  종료 실패. 수동으로 꺼주세요.")

    print(f"\n[{now()}] 전체 완료!")
    print(f"  파일: {save_path}")


def calibrate_coords():
    """
    대화형 좌표 캡처 도구.

    각 버튼/영역 위에 마우스를 올린 뒤 Enter를 누르면 좌표를 기록합니다.
    모든 좌표를 캡처하면 kmos_coords.json에 저장합니다.
    """
    print("=" * 60)
    print("  KMOS 좌표 캡처 도구")
    print("  각 항목에 대해 마우스를 해당 위치에 올리고 Enter를 누르세요.")
    print("  건너뛰려면 's'를 입력하세요.")
    print("=" * 60)

    # ePower 마켓을 먼저 실행해야 좌표 캡처 가능
    print("\n  [!] ePower 마켓을 전체화면으로 열어두세요.")
    input("  준비되면 Enter를 누르세요...\n")

    coord_names = [
        ("계통한계가격_메뉴", "좌측 메뉴에서 '계통한계가격' 항목"),
        ("내일_날짜_선택", "'내일' 또는 날짜 선택 버튼"),
        ("조회_버튼", "'조회' 버튼"),
        ("엑셀_아이콘", "엑셀 다운로드 아이콘 (상단 우측)"),
        ("저장하기_버튼", "'저장하기' 드롭다운 메뉴 항목"),
        ("디렉토리_입력", "저장 대화상자의 경로 입력란"),
        ("파일이름_입력", "저장 대화상자의 파일이름 입력란"),
        ("저장_버튼", "저장 대화상자의 '저장' 버튼"),
        ("안전확인_팝업", "'안전하지 않은 파일' 확인 팝업의 확인 버튼"),
    ]

    new_coords = load_coords()

    for key, desc in coord_names:
        current = new_coords.get(key, (0, 0))
        print(f"  [{key}] {desc}")
        print(f"    현재값: {current}")
        resp = input(f"    마우스를 올리고 Enter (s=건너뛰기): ").strip().lower()

        if resp == "s":
            print(f"    -> 건너뜀 (기존값 유지)")
        else:
            pos = pyautogui.position()
            new_coords[key] = (pos.x, pos.y)
            print(f"    -> 캡처 완료: ({pos.x}, {pos.y})")
        print()

    save_coords(new_coords)

    # 전역 COORDS도 갱신
    global COORDS
    COORDS = new_coords

    print("\n  캘리브레이션 완료! 이제 다운로드를 실행할 수 있습니다.")
    print(f"  python kmos_smp_download.py")


def show_coords():
    """현재 저장된 좌표를 출력."""
    coords = load_coords()
    source = "kmos_coords.json" if COORDS_FILE.exists() else "기본값 (캘리브레이션 필요)"
    print(f"\n  현재 좌표 ({source}):")
    print(f"  {'=' * 50}")
    for key, val in coords.items():
        print(f"  {key:.<30} {val}")
    print(f"\n  KMOS 바로가기: {KMOS_SHORTCUT}")
    print(f"  저장 경로:     {DEFAULT_SAVE_DIR}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KMOS 계통한계가격 자동 다운로드")
    parser.add_argument("--filename", default=DEFAULT_FILENAME, help="저장 파일명")
    parser.add_argument("--delay", type=float, default=1.0, help="클릭 간 대기 시간 (초)")
    parser.add_argument("--skip-menu", action="store_true", help="계통한계가격 메뉴 생략")
    parser.add_argument("--skip-date", action="store_true", help="날짜 선택 생략")
    parser.add_argument("--calibrate", action="store_true", help="좌표 캡처 모드")
    parser.add_argument("--show-coords", action="store_true", help="현재 좌표 확인")

    args = parser.parse_args()

    if args.calibrate:
        calibrate_coords()
    elif args.show_coords:
        show_coords()
    else:
        if not COORDS_FILE.exists():
            print("[!] 좌표 캡처가 필요합니다. 먼저 아래 명령을 실행하세요:")
            print("    python kmos_smp_download.py --calibrate")
            print()
            resp = input("    기본 좌표로 진행할까요? (y/n): ").strip().lower()
            if resp != "y":
                exit(0)

        download_smp_from_kmos(
            filename=args.filename,
            delay=args.delay,
            skip_menu=args.skip_menu,
            skip_date=args.skip_date,
        )
