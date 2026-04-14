# Streamlit 대시보드 UI 리디자인 명세서

> **목표**: 현재 Streamlit 기본 스타일의 대시보드를 "Mooney" 컨셉의 **글래스모피즘(Glassmorphism)** 기반 모던 파이낸셜 대시보드로 재설계한다.

---

## 1. 디자인 시스템 (Design System)

### 1.1 컬러 팔레트

| 역할 | 값 | 설명 |
|------|----|------|
| Primary | `#4F46E5` (Indigo-600) | 주요 CTA, 활성 카드 배경 |
| Primary Light | `#6366F1` (Indigo-500) | 호버, 강조 |
| Accent Pink | `#F472B6` | 차트 보조 색상 (지출/Expense) |
| Background | `#F0F0F8` ~ `#E8E8F5` | 연보라 그라데이션 배경 |
| Surface (Glass) | `rgba(255,255,255,0.65)` | 카드 배경 (frosted glass) |
| Surface Border | `rgba(255,255,255,0.8)` | 카드 테두리 |
| Text Primary | `#1E1B4B` | 제목, 주요 숫자 |
| Text Secondary | `#6B7280` | 서브텍스트, 레이블 |
| Positive | `#10B981` | 수입/양수 값 |
| Negative | `#EF4444` | 지출/음수 값 |

### 1.2 배경

```css
background: linear-gradient(135deg, #ddd6fe 0%, #e0e7ff 40%, #fce7f3 100%);
```

- 전체 페이지 배경은 연보라~핑크 메시 그라데이션
- 배경에 대형 blur circle을 겹쳐 depth 표현

### 1.3 타이포그래피

| 용도 | 폰트 | 크기 |
|------|------|------|
| 로고 / 브랜드 | `Sora` Bold | 20px |
| 페이지 제목 | `Sora` SemiBold | 28px |
| 카드 수치 (KPI) | `Sora` Bold | 28–32px |
| 본문 / 레이블 | `DM Sans` Regular | 13–15px |
| 서브텍스트 | `DM Sans` Regular | 12px, opacity 0.6 |

> Google Fonts CDN으로 `Sora`, `DM Sans` 임포트

### 1.4 글래스모피즘 카드 스타일

```css
.glass-card {
  background: rgba(255, 255, 255, 0.65);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border: 1px solid rgba(255, 255, 255, 0.8);
  border-radius: 20px;
  box-shadow: 0 8px 32px rgba(99, 102, 241, 0.08);
}
```

---

## 2. 레이아웃 구조

```
┌─────────────────────────────────────────────────────────────┐
│  Sidebar (220px)  │         Main Content Area               │
│                   │                                         │
│  [Logo]           │  [Topbar: Search | Bell | Menu]         │
│  ─────────        │  ─────────────────────────────          │
│  Dashboard  ●     │  Dashboard                              │
│  Wallet           │                                         │
│  Transaction      │  [Balance Card]  [Exchange Rate Chart]  │
│  Profile          │                                         │
│  Payment          │  [History Chart] [Efficiency Donut]     │
│                   │                                         │
│  [Annual Report   │                   [User Profile Panel]  │
│   Download Card]  │                   [Quick Actions]       │
│                   │                   [Recent Transactions] │
└─────────────────────────────────────────────────────────────┘
```

### 2.1 3단 컬럼 구성

- **Left**: 사이드바 (고정 220px) — 네비게이션 + 하단 리포트 카드
- **Center**: 메인 콘텐츠 (flex-grow) — KPI 카드 + 차트 영역
- **Right**: 유저 패널 (고정 280px) — 프로필 + 퀵액션 + 최근 거래

---

## 3. 컴포넌트별 상세 명세

### 3.1 사이드바 (Sidebar)

- 배경: `rgba(255,255,255,0.5)` + blur
- 로고: 아이콘 + "mooney" 텍스트 (Sora Bold)
- 네비게이션 항목: 아이콘 + 텍스트, 활성 항목은 왼쪽에 `4px indigo` 세로 바 + 텍스트 `#4F46E5`
- 항목 목록: Dashboard, Wallet, Transaction, Profile, Payment
- 하단 Annual Report 카드:
  - 글래스 카드 스타일
  - 일러스트 이미지 (차트 아이콘)
  - "Annual Report" 제목 + "Annually detailed report" 서브텍스트
  - Indigo `Download` 버튼 (border-radius: 12px)

### 3.2 상단바 (Topbar)

- 검색창: `rounded-full`, 아이콘 포함, 배경 `rgba(255,255,255,0.7)`
- 우측: 알림 벨 아이콘 (뱃지 포함), 더보기(⋮) 버튼

### 3.3 Balance 카드 (Primary Card)

- 배경: Indigo gradient (`#4F46E5` → `#6366F1`)
- 텍스트 전체 흰색
- 구성 요소:
  - 좌상단: "Balance" 레이블
  - 우상단: "CARD 05"
  - 중앙: `$ 53,250` (Bold 32px)
  - 카드 번호: `•••• •••• •••• 6252`
  - 하단 좌: "VALID THRU 02/25"
  - 하단 우: "CARD HOLDER Jonas"
- border-radius: 20px

### 3.4 Exchange Rate 카드

- 글래스 카드
- 헤더: "Exchange rates" + USD ⇌ IDR 토글/레이블
- Recharts `LineChart` (부드러운 곡선, `#4F46E5` 색상, dot 없음)
- X축: APR MAY JUN JUL JUL JUL

### 3.5 History 차트 카드

- 글래스 카드
- 헤더: "History" + `···` 메뉴 버튼
- Recharts `BarChart`: Income(Indigo) / Expense(Pink) 그룹 바
- Y축: $0 / $500 / $2,000 / $3,000
- X축: APR MAY JUN JUL AUG
- 하단 범례: ● Income ● Expense

### 3.6 Efficiency 도넛 카드

- 글래스 카드
- 헤더: "Efficiency" + `···` 메뉴 버튼
- Recharts `PieChart` (도넛형): Income(Indigo) / Expense(Pink)
- 중앙 텍스트: `$ 1,700` + `↑ 55%` (초록색)
- 하단 범례: ─ Income ─ Expense

### 3.7 우측 유저 패널

**프로필 섹션**
- 유저 아바타 (원형, 80px)
- 이름: "Jonas Kanwald" (Sora SemiBold 18px)

**퀵 액션 버튼 (4개)**

| 아이콘 | 레이블 |
|--------|--------|
| 💳 | Top Up |
| 🖨️ | Pay |
| ✈️ | Send |
| ⬇️ | Request |

- 각 버튼: 원형 글래스 아이콘 + 하단 텍스트 레이블
- hover 시 Indigo 배경으로 전환

**최근 거래 섹션**

- 섹션 구분: "TODAY" / "YESTERDAY" (Small caps, Gray)
- 각 행: 아바타 | 이름 + "Payment received/sent" | 금액
  - 수입: `+$250` (초록)
  - 지출: `-$250` (빨강)
- 구분선으로 TODAY/YESTERDAY 분리

---

## 4. Streamlit 구현 전략

### 4.1 Custom CSS 주입 방법

```python
st.markdown("""
<style>
  /* 전체 앱 스타일 오버라이드 */
  .stApp {
    background: linear-gradient(135deg, #ddd6fe 0%, #e0e7ff 40%, #fce7f3 100%);
    font-family: 'DM Sans', sans-serif;
  }
  /* Streamlit 기본 UI 숨기기 */
  #MainMenu, footer, header { visibility: hidden; }
  .block-container { padding: 0 !important; max-width: 100% !important; }
</style>
""", unsafe_allow_html=True)
```

### 4.2 레이아웃 구현

```python
# 3컬럼 레이아웃
sidebar_col, main_col, right_col = st.columns([2.2, 5, 2.8])
```

### 4.3 차트 라이브러리

- `plotly.express` 또는 `plotly.graph_objects` 사용
- `transparent` 배경, 커스텀 색상 적용
- `st.plotly_chart(fig, use_container_width=True)`

### 4.4 HTML 컴포넌트 사용

복잡한 카드(Balance Card, 거래 목록 등)는 `st.markdown(html, unsafe_allow_html=True)`로 구현

---

## 5. 파일 구조 (예시)

```
project/
├── app.py                  # 메인 Streamlit 앱
├── components/
│   ├── sidebar.py          # 사이드바 컴포넌트
│   ├── balance_card.py     # Balance 카드
│   ├── charts.py           # 차트 (Exchange, History, Efficiency)
│   └── right_panel.py      # 유저 패널 + 거래 목록
├── styles/
│   └── main.css            # 전체 CSS (st.markdown으로 주입)
└── assets/
    └── avatar.png          # 유저 아바타 이미지
```

---

## 6. 작업 순서 (Claude CLI Task Checklist)

- [ ] **Step 1**: `styles/main.css` 작성 — 글래스모피즘 기반 전체 디자인 시스템 CSS 변수 및 공통 클래스 정의
- [ ] **Step 2**: `components/sidebar.py` 구현 — 네비게이션 + Annual Report 카드
- [ ] **Step 3**: `components/balance_card.py` 구현 — Indigo gradient 카드 (HTML)
- [ ] **Step 4**: `components/charts.py` 구현 — Exchange Rate(Line), History(Bar), Efficiency(Donut) 3종 Plotly 차트
- [ ] **Step 5**: `components/right_panel.py` 구현 — 유저 프로필, 퀵액션 버튼, 최근 거래 목록
- [ ] **Step 6**: `app.py` 통합 — 3컬럼 레이아웃으로 모든 컴포넌트 조립
- [ ] **Step 7**: 반응형 검토 및 hover/transition 애니메이션 CSS 추가
- [ ] **Step 8**: 기존 데이터/로직 연결 — 실제 데이터소스로 더미 데이터 대체

---

## 7. 참고 디자인 원칙

1. **글래스모피즘 일관성**: 모든 카드는 `backdrop-filter: blur(20px)` + 반투명 흰 배경 유지
2. **컬러 절제**: Primary Indigo와 Accent Pink 두 색만 사용, 나머지는 무채색
3. **여백 관대하게**: 카드 내부 padding 최소 `24px`, 카드 간격 `16px`
4. **수치는 크게**: KPI 숫자는 충분히 크게(28px+), 시선이 바로 가도록
5. **그림자 섬세하게**: `box-shadow`에 Indigo tint를 섞어 떠있는 느낌 부여
