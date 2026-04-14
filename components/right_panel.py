"""right_panel.py — 우측 설정/상태 패널"""
from __future__ import annotations

import platform
import subprocess

import streamlit as st


def render_right_panel(config: dict) -> dict:
    """
    우측 패널 렌더링.

    Parameters
    ----------
    config: dict with keys
        - default_date: date
        - default_lng_price: float
        - lng_heat: float
        - exchange_rate: float
        - smp_status: list of (date, src, ok)

    Returns
    -------
    dict with keys: target_date, lng_price, is_spot
    """
    # ── 스케줄러 상태 ──────────────────────────────────────────
    status = _check_scheduler_status()
    STATUS_CFG = {
        "running":  ("#10B981", "🟢 스케줄러 가동중"),
        "fetching": ("#F59E0B", "🟡 SMP 수집중"),
        "stopped":  ("#EF4444", "🔴 스케줄러 미실행"),
    }
    color, label = STATUS_CFG.get(status, STATUS_CFG["stopped"])

    st.markdown(
        f"""
        <div class="glass-card" style="padding:16px; margin-bottom:12px;">
          <div style="font-family:'Sora',sans-serif; font-size:13px; font-weight:600;
                      color:#1E1B4B; margin-bottom:8px;">시스템 상태</div>
          <span class="sched-badge" style="background:rgba(0,0,0,0.04); color:{color};">
            <span class="sched-dot" style="background:{color};"></span>
            {label}
          </span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── 설정 입력 ──────────────────────────────────────────────
    st.markdown(
        """
        <div style="font-family:'Sora',sans-serif; font-size:13px; font-weight:600;
                    color:#1E1B4B; margin-bottom:8px;">⚙️ 분석 설정</div>
        """,
        unsafe_allow_html=True,
    )

    # 날짜: 세션 상태 없으면 기본값으로 초기화
    if "user_target_date" not in st.session_state:
        st.session_state.user_target_date = config["default_date"]

    target_date = st.date_input(
        "분석 기준일",
        value=st.session_state.user_target_date,
        key="user_target_date",
    )

    # LNG 가격: 최초 1회만 기본값 설정, 이후엔 key로 session_state 자동 유지
    if "user_lng_price" not in st.session_state:
        st.session_state["user_lng_price"] = float(config["default_lng_price"])

    lng_price = st.number_input(
        "LNG 가격 ($/MMBtu)",
        min_value=0.0,
        step=0.5,
        format="%.2f",
        key="user_lng_price",
    )

    if "user_is_spot" not in st.session_state:
        st.session_state["user_is_spot"] = False

    is_spot = st.checkbox("Spot LNG (제세금 +0.8$/MMBtu)", key="user_is_spot")

    # 자동 산출값 표시
    st.markdown(
        f"""
        <div class="glass-card" style="padding:12px 16px; margin-top:8px; margin-bottom:8px;">
          <div style="font-size:11px; color:#6B7280; font-family:'DM Sans',sans-serif; line-height:1.8;">
            <b>LNG 열량</b>: {config['lng_heat']:.4f} Mcal/Nm³<br>
            <b>환율</b>: {config['exchange_rate']:,.0f} 원/$
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── SMP 수집 현황 ──────────────────────────────────────────
    st.markdown(
        """
        <div style="font-family:'Sora',sans-serif; font-size:13px; font-weight:600;
                    color:#1E1B4B; margin-bottom:8px; margin-top:8px;">📡 SMP 수집 현황</div>
        """,
        unsafe_allow_html=True,
    )

    for d, src, ok in config.get("smp_status", []):
        icon = "🟢" if ok else "🔴"
        st.caption(f"{icon} {d.month}/{d.day}: {src}")

    return {
        "target_date": target_date,
        "lng_price": lng_price,
        "is_spot": is_spot,
    }


def _check_scheduler_status() -> str:
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
