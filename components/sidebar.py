"""sidebar.py — 네비게이션 사이드바 (글래스모피즘 흰색 카드)"""
from __future__ import annotations

import streamlit as st

NAV_ITEMS = [
    ("🏠", "Dashboard"),
    ("💰", "경제성 분석"),
    ("🔍", "이상구간 탐지"),
    ("🤖", "ML 모델"),
    ("📁", "원시 데이터"),
]


def render_sidebar() -> None:
    """사이드바 렌더링. 클릭 시 st.session_state.active_page 변경."""
    if "active_page" not in st.session_state:
        st.session_state.active_page = "Dashboard"

    active = st.session_state.active_page

    # ── 흰색 글래스 카드 전체 래퍼 ──────────────────────────────
    st.markdown(
        """
        <style>
        /* 사이드바 카드 내부 버튼 기본 스타일 초기화 */
        div[data-testid="column"]:first-child .stButton > button {
            background: transparent !important;
            color: #6B7280 !important;
            border: none !important;
            border-radius: 12px !important;
            font-family: 'DM Sans', sans-serif !important;
            font-size: 14px !important;
            font-weight: 400 !important;
            text-align: left !important;
            padding: 10px 14px !important;
            box-shadow: none !important;
            transition: all 0.2s ease !important;
            width: 100% !important;
        }
        div[data-testid="column"]:first-child .stButton > button:hover {
            background: rgba(79,70,229,0.08) !important;
            color: #4F46E5 !important;
            box-shadow: none !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # ── 로고 영역 ────────────────────────────────────────────────
    st.markdown(
        """
        <div style="
            text-align: center;
            padding: 8px 0 20px 0;
            border-bottom: 1px solid rgba(99,102,241,0.12);
            margin-bottom: 16px;
        ">
          <div style="
              font-family:'Sora',sans-serif;
              font-size: 24px; font-weight: 700;
              background: linear-gradient(135deg, #4F46E5, #6366F1);
              -webkit-background-clip: text;
              -webkit-text-fill-color: transparent;
              margin-bottom: 4px;
          ">⚡ LNG-OPT</div>
          <div style="
              font-size: 11px; color: #9CA3AF;
              font-family: 'DM Sans', sans-serif;
          ">최적 가이던스 시스템</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── MAIN MENU 섹션 레이블 ────────────────────────────────────
    st.markdown(
        """
        <div style="
            font-family:'DM Sans',sans-serif;
            font-size: 10px; font-weight: 600;
            color: #9CA3AF; letter-spacing: 1px;
            margin-bottom: 6px; padding-left: 4px;
        ">MAIN MENU</div>
        """,
        unsafe_allow_html=True,
    )

    # ── 네비게이션 버튼 ──────────────────────────────────────────
    for icon, label in NAV_ITEMS:
        is_active = (active == label)

        # 활성 상태 스타일 개별 주입
        btn_key = f"nav_{label}"
        if is_active:
            st.markdown(
                f"""
                <style>
                div[data-testid="column"]:first-child
                div[data-testid="stButton"]:has(button[aria-label="{btn_key}"]) button {{
                    background: rgba(79,70,229,0.12) !important;
                    color: #4F46E5 !important;
                    font-weight: 600 !important;
                    border-left: 3px solid #4F46E5 !important;
                }}
                </style>
                """,
                unsafe_allow_html=True,
            )

        if st.button(
            f"{icon}  {label}",
            key=btn_key,
            use_container_width=True,
            help=None,
        ):
            st.session_state.active_page = label
            st.rerun()

    # ── 구분선 ───────────────────────────────────────────────────
    st.markdown(
        "<hr style='border:none;border-top:1px solid rgba(99,102,241,0.12);margin:16px 0;'>",
        unsafe_allow_html=True,
    )

    # ── 일일 리포트 안내 카드 ────────────────────────────────────
    st.markdown(
        """
        <div style="
            background: linear-gradient(135deg,
                rgba(79,70,229,0.10), rgba(99,102,241,0.07));
            border: 1px solid rgba(79,70,229,0.18);
            border-radius: 16px;
            padding: 16px;
            text-align: center;
        ">
          <div style="font-size: 28px; margin-bottom: 8px;">📊</div>
          <div style="
              font-family:'Sora',sans-serif;
              font-size: 13px; font-weight: 600;
              color: #1E1B4B;
          ">일일 리포트</div>
          <div style="
              font-size: 11px; color: #6B7280;
              margin-top: 4px;
              font-family: 'DM Sans', sans-serif;
              line-height: 1.5;
          ">가이던스 탭에서<br>다운로드 가능</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
