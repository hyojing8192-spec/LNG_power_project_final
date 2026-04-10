"""
스케줄러 상태 API 서버 (Streamlit 실시간 인디케이터용).
포트 8599에서 JSON 응답.

Streamlit과 함께 자동 실행됨.
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import subprocess
import json
import threading


def check_status() -> str:
    """running | fetching | stopped"""
    try:
        r = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'",
             "get", "CommandLine"],
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


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        status = check_status()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps({"status": status}).encode())

    def log_message(self, *args):
        pass  # suppress logs


def start_server():
    try:
        server = HTTPServer(("127.0.0.1", 8599), Handler)
        server.serve_forever()
    except OSError:
        pass  # port already in use


def start_in_background():
    t = threading.Thread(target=start_server, daemon=True)
    t.start()


if __name__ == "__main__":
    print("Scheduler status server on http://127.0.0.1:8599")
    start_server()
