"""
kakao_sender.py  (F6 - 카카오톡 전파)
======================================
카카오톡 REST API를 통한 가동계획 메시지 자동 발송.

설정 순서:
  1. https://developers.kakao.com 에서 앱 생성
  2. [앱 설정] > [플랫폼] > Web > 사이트 도메인: http://localhost
  3. [제품 설정] > [카카오 로그인] > 활성화 ON
  4. [제품 설정] > [카카오 로그인] > [동의항목] > '카카오톡 메시지 전송' 선택 동의
  5. [제품 설정] > [카카오 로그인] > Redirect URI: http://localhost/oauth
  6. .env에 KAKAO_REST_API_KEY 입력
  7. python kakao_sender.py --auth  (최초 1회, 브라우저 인증)
  8. python kakao_sender.py --test  (테스트 발송)

사용법:
  python kakao_sender.py --auth         # 카카오 인증 (최초 1회)
  python kakao_sender.py --test         # 테스트 메시지 발송
  python kakao_sender.py --send "메시지" # 직접 메시지 발송
"""

from __future__ import annotations

import json
import logging
import webbrowser
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests

logger = logging.getLogger("kakao_sender")

# ── 경로 ──────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOKEN_FILE = PROJECT_ROOT / "data" / "kakao_token.json"

# ── 카카오 API 설정 (.env에서 로드) ──────────────────────────
try:
    from config import KAKAO_REST_API_KEY, KAKAO_CLIENT_SECRET
except ImportError:
    import os
    KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY", "")
    KAKAO_CLIENT_SECRET = os.getenv("KAKAO_CLIENT_SECRET", "")

REDIRECT_URI = "http://localhost/oauth"
AUTH_URL = "https://kauth.kakao.com/oauth/authorize"
TOKEN_URL = "https://kauth.kakao.com/oauth/token"
SEND_ME_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
SEND_FRIENDS_URL = "https://kapi.kakao.com/v1/api/talk/friends/message/default/send"
FRIENDS_URL = "https://kapi.kakao.com/v1/api/talk/friends"


# ──────────────────────────────────────────────────────────────
# 토큰 관리
# ──────────────────────────────────────────────────────────────

def _save_token(token_data: dict):
    """토큰을 JSON 파일에 저장."""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    token_data["saved_at"] = datetime.now().isoformat()
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(token_data, f, ensure_ascii=False, indent=2)
    logger.info(f"토큰 저장: {TOKEN_FILE}")


def _load_token() -> dict | None:
    """저장된 토큰 로드."""
    if not TOKEN_FILE.exists():
        return None
    with open(TOKEN_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _refresh_token(refresh_token: str) -> dict | None:
    """리프레시 토큰으로 액세스 토큰 갱신."""
    data = {
        "grant_type": "refresh_token",
        "client_id": KAKAO_REST_API_KEY,
        "refresh_token": refresh_token,
    }
    if KAKAO_CLIENT_SECRET:
        data["client_secret"] = KAKAO_CLIENT_SECRET
    resp = requests.post(TOKEN_URL, data=data)
    if resp.status_code == 200:
        new_data = resp.json()
        # 기존 토큰 데이터에 병합 (refresh_token은 갱신되지 않을 수 있음)
        old = _load_token() or {}
        old.update(new_data)
        _save_token(old)
        logger.info("토큰 갱신 성공")
        return old
    else:
        logger.error(f"토큰 갱신 실패: {resp.text}")
        return None


def get_access_token() -> str | None:
    """유효한 액세스 토큰 반환. 만료 시 자동 갱신."""
    token = _load_token()
    if not token:
        logger.warning("토큰 없음. --auth로 인증하세요.")
        return None

    access_token = token.get("access_token")
    refresh_token = token.get("refresh_token")

    # 토큰 유효성 테스트
    resp = requests.get(
        "https://kapi.kakao.com/v1/user/access_token_info",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    if resp.status_code == 200:
        return access_token

    # 만료 -> 갱신 시도
    if refresh_token:
        logger.info("액세스 토큰 만료, 갱신 중...")
        new_token = _refresh_token(refresh_token)
        if new_token:
            return new_token.get("access_token")

    logger.error("토큰 갱신 실패. --auth로 재인증하세요.")
    return None


# ──────────────────────────────────────────────────────────────
# 인증 (최초 1회)
# ──────────────────────────────────────────────────────────────

def authorize():
    """
    브라우저를 열어 카카오 인증 후 토큰 발급.
    로컬 서버로 redirect URI의 code를 캡처.
    """
    if not KAKAO_REST_API_KEY:
        print("[!] KAKAO_REST_API_KEY가 설정되지 않았습니다.")
        print("    .env에 KAKAO_REST_API_KEY=your_rest_api_key 를 추가하세요.")
        print("    https://developers.kakao.com 에서 앱 생성 후 REST API 키 확인")
        return

    # 인증 URL 생성
    auth_url = (
        f"{AUTH_URL}?client_id={KAKAO_REST_API_KEY}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=talk_message,friends"
    )

    print("=" * 60)
    print("  카카오 인증")
    print("=" * 60)
    print(f"\n  브라우저에서 카카오 로그인 후 동의해주세요.")
    print(f"  리다이렉트 후 URL에서 code= 값을 복사하세요.\n")

    webbrowser.open(auth_url)

    print("  브라우저 주소창의 URL을 복사하여 붙여넣기하세요.")
    print("  (예: http://localhost/oauth?code=XXXXXX)\n")
    redirect_url = input("  URL 입력: ").strip()

    # code 추출
    parsed = urlparse(redirect_url)
    params = parse_qs(parsed.query)
    code = params.get("code", [None])[0]

    if not code:
        print("[!] code를 찾을 수 없습니다. URL을 다시 확인하세요.")
        return

    # code -> token 교환
    token_data_req = {
        "grant_type": "authorization_code",
        "client_id": KAKAO_REST_API_KEY,
        "redirect_uri": REDIRECT_URI,
        "code": code,
    }
    if KAKAO_CLIENT_SECRET:
        token_data_req["client_secret"] = KAKAO_CLIENT_SECRET
    resp = requests.post(TOKEN_URL, data=token_data_req)

    if resp.status_code == 200:
        token_data = resp.json()
        _save_token(token_data)
        print(f"\n  인증 성공! 토큰 저장: {TOKEN_FILE}")
        print(f"  access_token: {token_data['access_token'][:20]}...")
    else:
        print(f"\n  [!] 토큰 발급 실패: {resp.text}")


# ──────────────────────────────────────────────────────────────
# 메시지 발송
# ──────────────────────────────────────────────────────────────

def send_to_me(message: str) -> bool:
    """나에게 카카오톡 메시지 보내기."""
    token = get_access_token()
    if not token:
        return False

    template = {
        "object_type": "text",
        "text": message,
        "link": {
            "web_url": "https://developers.kakao.com",
            "mobile_web_url": "https://developers.kakao.com",
        },
    }

    resp = requests.post(
        SEND_ME_URL,
        headers={"Authorization": f"Bearer {token}"},
        data={"template_object": json.dumps(template, ensure_ascii=False)},
    )

    if resp.status_code == 200:
        logger.info("카카오톡 (나에게) 발송 성공")
        return True
    else:
        logger.error(f"카카오톡 (나에게) 발송 실패: {resp.status_code} {resp.text}")
        return False


def get_friends() -> list[dict]:
    """카카오톡 친구 목록 조회 (메시지 동의한 친구만)."""
    token = get_access_token()
    if not token:
        return []

    resp = requests.get(
        FRIENDS_URL,
        headers={"Authorization": f"Bearer {token}"},
    )

    if resp.status_code == 200:
        data = resp.json()
        friends = data.get("elements", [])
        return [{"uuid": f["uuid"], "name": f.get("profile_nickname", "?")} for f in friends]
    else:
        logger.error(f"친구 목록 조회 실패: {resp.text}")
        return []


def send_to_friends(message: str, friend_uuids: list[str] | None = None) -> bool:
    """
    카카오톡 친구에게 메시지 보내기.

    friend_uuids가 None이면 모든 동의 친구에게 발송.
    """
    token = get_access_token()
    if not token:
        return False

    if not friend_uuids:
        friends = get_friends()
        if not friends:
            logger.warning("메시지 수신 동의한 친구가 없습니다.")
            return False
        friend_uuids = [f["uuid"] for f in friends]
        logger.info(f"  수신 대상: {[f['name'] for f in friends]}")

    template = {
        "object_type": "text",
        "text": message,
        "link": {
            "web_url": "https://developers.kakao.com",
            "mobile_web_url": "https://developers.kakao.com",
        },
    }

    resp = requests.post(
        SEND_FRIENDS_URL,
        headers={"Authorization": f"Bearer {token}"},
        data={
            "receiver_uuids": json.dumps(friend_uuids),
            "template_object": json.dumps(template, ensure_ascii=False),
        },
    )

    if resp.status_code == 200:
        result = resp.json()
        success = result.get("successful_receiver_uuids", [])
        logger.info(f"카카오톡 (친구) 발송 성공: {len(success)}명")
        return True
    else:
        logger.error(f"카카오톡 (친구) 발송 실패: {resp.status_code} {resp.text}")
        return False


def send_kakao_guidance(kakao_message: str) -> bool:
    """
    가동계획 카카오톡 발송 (나에게 + 친구).

    Returns:
        True: 1건 이상 발송 성공
    """
    if not KAKAO_REST_API_KEY:
        logger.info("카카오 API 미설정 - 발송 생략")
        return False

    token = get_access_token()
    if not token:
        logger.warning("카카오 토큰 없음 - 발송 생략")
        return False

    sent_any = False

    # 나에게 보내기
    if send_to_me(kakao_message):
        sent_any = True

    # 친구에게 보내기
    try:
        if send_to_friends(kakao_message):
            sent_any = True
    except Exception as e:
        logger.warning(f"친구 발송 실패 (나에게는 발송됨): {e}")

    return sent_any


# ──────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import sys

    sys.path.insert(0, str(PROJECT_ROOT / "modules"))
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="카카오톡 메시지 발송")
    parser.add_argument("--auth", action="store_true", help="카카오 인증 (최초 1회)")
    parser.add_argument("--test", action="store_true", help="테스트 메시지 발송")
    parser.add_argument("--send", type=str, help="직접 메시지 발송")
    parser.add_argument("--friends", action="store_true", help="친구 목록 확인")
    args = parser.parse_args()

    if args.auth:
        authorize()
    elif args.test:
        ok = send_to_me("LNG 발전 경제성 시스템 - 카카오톡 테스트 메시지입니다.")
        print("발송 성공!" if ok else "발송 실패. --auth로 인증하세요.")
    elif args.send:
        ok = send_to_me(args.send)
        print("발송 성공!" if ok else "발송 실패.")
    elif args.friends:
        friends = get_friends()
        if friends:
            print(f"\n메시지 수신 동의 친구 ({len(friends)}명):")
            for f in friends:
                print(f"  - {f['name']} ({f['uuid'][:8]}...)")
        else:
            print("메시지 수신 동의한 친구가 없습니다.")
    else:
        parser.print_help()
