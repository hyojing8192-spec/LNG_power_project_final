"""
SMP 다운로드 과정 화면 녹화 스크립트.

사용법:
  python scripts/record_smp_download.py
"""

import sys
import time
import threading
from pathlib import Path
from datetime import datetime

import cv2
import numpy as np
import pyautogui

# 프로젝트 루트
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "modules"))

# ── 녹화 설정 ──
OUTPUT_PATH = str(ROOT / f"SMP_Download_Recording_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4")
FPS = 10
SCREEN_SIZE = pyautogui.size()


def record_screen(stop_event: threading.Event, output_path: str):
    """별도 스레드에서 화면을 녹화."""
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(output_path, fourcc, FPS, SCREEN_SIZE)

    print(f"[녹화] 시작: {output_path}")
    print(f"  해상도: {SCREEN_SIZE[0]}x{SCREEN_SIZE[1]}, FPS: {FPS}")

    interval = 1.0 / FPS
    while not stop_event.is_set():
        t0 = time.time()
        screenshot = pyautogui.screenshot()
        frame = np.array(screenshot)
        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        writer.write(frame)
        elapsed = time.time() - t0
        if elapsed < interval:
            time.sleep(interval - elapsed)

    writer.release()
    print(f"[녹화] 완료: {output_path}")


def main():
    from kmos_smp_download import download_smp_from_kmos

    today_str = datetime.now().strftime("%Y%m%d")
    filename = f"계통한계가격_{today_str}_육지"

    # 기존 4/10 캐시 삭제 (재다운로드 시연용)
    cache = ROOT / "data" / "smp_cache" / f"smp_{datetime.now().strftime('%Y-%m-%d')}.json"
    if cache.exists():
        cache.unlink()
        print(f"[준비] 캐시 삭제: {cache.name}")

    # 녹화 시작
    stop_event = threading.Event()
    recorder = threading.Thread(target=record_screen, args=(stop_event, OUTPUT_PATH))
    recorder.start()

    time.sleep(1)  # 녹화 안정화

    try:
        # KMOS SMP 다운로드 실행
        print(f"\n[실행] KMOS SMP 다운로드: {filename}")
        download_smp_from_kmos(filename=filename, skip_date=True)
    finally:
        # 녹화 종료
        time.sleep(2)
        stop_event.set()
        recorder.join()

    print(f"\n영상 저장 완료: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
