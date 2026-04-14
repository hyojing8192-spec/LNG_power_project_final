# 대시보드 리팩터링 계획

> 대상: `scripts/LNG_project_final.py` (1,446줄)
> 컨셉: `dashboard_concept.jpg` 참고 — 카드 그리드 레이아웃
> 폰트: Pretendard

---

## 1. 현재 구조

```
[타이틀 + 사이드바]
  ├── KPI 4개 (st.columns 4)
  ├── 종합 차트 (풀폭)
  ├── 종합 테이블 야간/주간 (풀폭, 날짜쌍 루프)
  ├── SMP 미공시 시 산출불가 카드
  │
  ├── [expander] 경제성 분석 (상세)
  │     ├── KPI 4개
  │     ├── 24시간 테이블
  │     └── 경제성 차트
  ├── [expander] 가동 가이던스 (상세)
  │     ├── 주간 테이블 + 차트
  │     ├── 야간 테이블 + 차트
  │     ├── 모드별 경제성 요약
  │     └── CSV 다운로드
  ├── [expander] 이상구간 탐지
  │     ├── SMP 차트 + 임계선
  │     └── 경제성 급변 차트
  ├── [expander] ML 모델 성능
  │     ├── R2 스코어
  │     ├── 재학습 버튼
  │     └── SMP 분포 히스토그램
  └── [expander] 원시 데이터
```

### 문제점
- expander 5개가 세로로 나열 → 스크롤 길고 탐색 어려움
- 종합 화면과 상세 분석이 섞여 있음
- KPI 카드가 st.metric 기본 스타일로 밋밋함

---

## 2. 리팩터링 목표

### 레이아웃 변경 (컨셉 참고)

```
[타이틀 + 사이드바]
  │
  ├── 상단 KPI 카드 (2x2 그리드 또는 4컬럼)
  │     ├── 평균 SMP        ├── BEP 임계값
  │     ├── 최적 운전모드    ├── 일일 경제성
  │
  ├── 메인 영역 (2컬럼)
  │     ├── [좌] 종합 차트 (SMP vs BEP vs LNG가격)
  │     └── [우] 종합 테이블 (야간/주간 탭)
  │
  ├── 가이던스 영역 (탭: 주간 | 야간)
  │     ├── 가동계획표 + 차트 (각 탭 내)
  │     └── 모드별 경제성 요약
  │
  └── 하단 상세 (탭: 이상구간 | ML모델 | 원시데이터)
        ├── 이상구간 차트/테이블
        ├── ML R2 스코어 + 재학습
        └── 원시 데이터 미리보기
```

### 핵심 변경 사항

| 영역 | 현재 | 변경 |
|------|------|------|
| KPI 카드 | `st.metric` 기본 | CSS 카드 (배경색, 아이콘, 큰 숫자) |
| 종합 차트+테이블 | 풀폭 세로 나열 | **2컬럼** (차트 좌 / 테이블 우) |
| 야간/주간 테이블 | 세로 나열 | **st.tabs** (야간 \| 주간) |
| 가이던스 상세 | expander | **st.tabs** (주간 \| 야간) |
| 하단 분석 | expander 4개 | **st.tabs** (이상구간 \| ML \| 데이터) |
| 폰트 | 맑은 고딕 기본 | **Pretendard** (Google Fonts CDN) |

---

## 3. 스타일 가이드

### 폰트
```css
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css');
html, body, [class*="st-"] {
    font-family: 'Pretendard', -apple-system, sans-serif;
}
```

### 색상 팔레트 (기존 유지 + 정리)
| 용도 | 색상 |
|------|------|
| 주요 (SMP 라인) | `#2F5597` |
| 보조 (BEP 막대) | `#B4C7E7` |
| 강조 (LNG가격) | `#ED7D31` |
| 가동 | `#28a745` |
| 감발전환 | `#f39c12` |
| 정지 | `#dc3545` |
| 기력점화 | `#3498db` |

### KPI 카드 CSS (예시)
```css
.kpi-card {
    background: #f8f9fc;
    border-radius: 12px;
    padding: 20px;
    border-left: 4px solid #2F5597;
}
.kpi-card .value {
    font-size: 2em;
    font-weight: 700;
}
.kpi-card .label {
    font-size: 0.85em;
    color: #666;
}
```

---

## 4. 작업 체크리스트

- [ ] Pretendard 폰트 적용 (CSS inject)
- [ ] KPI 카드 4개 리디자인 (HTML+CSS)
- [ ] 종합 차트 + 테이블 → 2컬럼 배치
- [ ] 야간/주간 종합 테이블 → st.tabs 전환
- [ ] 가이던스 섹션 → st.tabs (expander 제거)
- [ ] 하단 상세 → st.tabs (expander 3개 → 탭 3개)
- [ ] 차트 색상/스타일 통일
- [ ] 전체 테스트 + 반응형 확인
- [ ] 불필요 CSS/마크업 정리

---

## 5. 주의사항

- 비즈니스 로직(분석, 데이터 처리) 변경 없음
- `generate_full_guidance`, `build_hourly_table` 등 기존 함수 호출 유지
- 사이드바 구조 유지 (날짜 선택, LNG가격, 열량 등)
- Streamlit Cloud 배포 호환성 유지
- 변경 후 `streamlit run scripts/LNG_project_final.py` 정상 실행 확인

---

## 6. 예상 소요 시간

| 작업 | 소요 |
|------|------|
| 폰트 + CSS 기반 | 15분 |
| KPI 카드 리디자인 | 20분 |
| 2컬럼 + 탭 레이아웃 | 40분 |
| 가이던스 탭 전환 | 20분 |
| 하단 탭 전환 | 15분 |
| 테스트 + 미세 조정 | 20분 |
| **합계** | **~2시간** |
