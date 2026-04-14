# PRD v2 — LNG 발전소 경제성 자동화 분석 시스템

> **버전**: 2.0  
> **작성일**: 2026-04-14  
> **기준 커밋**: `5b97df4` (대시보드 리팩터링: app.py/server.py/components 추가)  
> **이전 버전 대비**: 신규 모듈형 UI 아키텍처, FastAPI 백엔드, Glassmorphism 디자인 시스템 반영

---

## 1. 프로젝트 개요

### 1.1 목적

LNG 복합화력 발전소의 **일별 경제성 판단 자동화**. 전력 시장가격(SMP)을 수집하고, ML로 운영 파라미터를 예측하여, LNG 발전 vs. 기력 발전 간 손익분기점(BEP)을 산출한다. 결과를 **시간별 운전 가이던스**로 생성해 담당자에게 이메일·카카오톡으로 발송하며, 실시간 대시보드로 시각화한다.

### 1.2 핵심 가치

| 항목 | 내용 |
|------|------|
| 의사결정 자동화 | 매일 17:30~19:30 SMP 확정 후 자동 판단, 수동 분석 제거 |
| 재무 영향 | 발전 모드(LNG/기력/저부하) 선택은 시간당 수억 원 규모 손익에 직결 |
| 이상치 조기 경보 | SMP 급등·급락 구간 자동 감지 → 즉시 담당자 알림 |
| 이중 UI 지원 | 레거시 Streamlit 모노리스 + 신규 Glassmorphism 컴포넌트 UI 병행 운영 |

---

## 2. 시스템 아키텍처

### 2.1 전체 데이터 흐름

```
[데이터 수집]
  KPX 크롤러 / ePower KMOS Excel
        ↓
  SMP JSON 캐시 (data/smp_cache/)
        ↓
[ML 예측]  ml_predictor.py (XGBoost)
  ├─ 역송량(export) 예측
  ├─ 수전량(import) 예측
  └─ 효율(efficiency) 예측
        ↓
[경제성 엔진]  economics_engine.py
  ├─ LNG 발전 BEP (열효율 1.57~1.68 Mcal/kWh)
  └─ 기력 발전 BEP (열효율 2.3 Mcal/kWh)
        ↓
[이상치 탐지]  anomaly_detector.py
  ├─ smp_low  (LNG 발전 BEP 기반 하한 임계값)
  ├─ smp_high (기력 발전 BEP 기반 상한 임계값)
  └─ 3단계 이상 분류 (정상 / 주의 / 위험)
        ↓
[가이던스 생성]  guidance_generator.py
  └─ 시간별 운전 계획표 + 요약 마크다운
        ↓
[발송]  mail_sender.py + kakao_sender.py
  ├─ Gmail SMTP (HTML 리포트)
  └─ KakaoTalk REST API (요약 메시지)
        ↓
[대시보드]  LNG_project_final.py (레거시)
           app.py + server.py (신규)
```

### 2.2 코드 구조

```
과제_최종/
├── app.py                      # 신규 진입점 (Glassmorphism UI, 3-column 레이아웃)
├── server.py                   # FastAPI REST API 백엔드
├── scripts/
│   └── LNG_project_final.py   # 레거시 Streamlit 모노리스 (1,470줄)
├── modules/                    # 핵심 비즈니스 로직 (13개 모듈, ~7,869줄)
│   ├── config.py              # 전역 상수 (모드, 전기요금, LNG 파라미터, 공휴일)
│   ├── smp_collector.py       # SMP 수집 (3단계 폴백: KPX → Excel → 히스토리)
│   ├── ml_predictor.py        # XGBoost 예측 모델
│   ├── economics_engine.py    # BEP 계산 엔진
│   ├── anomaly_detector.py    # 동적 임계값 이상치 탐지
│   ├── guidance_generator.py  # 시간별 가이던스 생성
│   ├── mail_sender.py         # Gmail SMTP 발송
│   ├── kakao_sender.py        # 카카오톡 발송
│   ├── run_scheduler.py       # APScheduler 오케스트레이션
│   ├── date_utils.py          # 날짜 로직 단일 소스 (v1.5 통합)
│   └── ...
├── components/                 # 신규 UI 컴포넌트 (모듈형)
│   ├── sidebar.py             # 좌측 네비게이션 + 연간 리포트 카드
│   ├── right_panel.py         # 우측 설정 패널 (날짜/LNG가격/Spot)
│   └── pages/
│       ├── dashboard.py       # 메인 대시보드
│       ├── wallet.py          # 손익 분석
│       ├── transaction.py     # 거래 내역
│       ├── anomaly.py         # 이상 구간 시각화
│       ├── ml_model.py        # ML 예측 결과
│       └── rawdata.py         # 원시 데이터 조회
├── styles/
│   └── main.css               # Glassmorphism 디자인 시스템
├── frontend/
│   ├── index.html
│   ├── css/style.css
│   └── js/main.js
├── data/
│   ├── smp_cache/             # 날짜별 SMP JSON (smp_YYYY-MM-DD.json)
│   ├── smp_excel/             # 원본 계통한계가격 Excel 파일
│   └── ml_models/             # 학습된 XGBoost 모델 파일
└── docs/
    ├── DASHBOARD_REFACTOR.md  # 리팩터링 계획 및 진행 상황
    └── PROJECT_STATUS.md      # 프로젝트 현황
```

---

## 3. 기능 요구사항

### F1. SMP 데이터 수집

| ID | 요구사항 | 우선순위 |
|----|----------|----------|
| F1-1 | KPX 전력거래소 크롤링으로 당일 시간별 SMP 자동 수집 | P0 |
| F1-2 | ePower KMOS Excel 파일 파싱으로 폴백 수집 | P0 |
| F1-3 | 수집 실패 시 최근 히스토리 데이터 대체 (3단계 폴백) | P1 |
| F1-4 | `data/smp_cache/smp_YYYY-MM-DD.json` 형식으로 캐싱 | P0 |
| F1-5 | 평일/공휴일 구분하여 수집 스케줄 관리 | P1 |

### F2. ML 예측

| ID | 요구사항 | 우선순위 |
|----|----------|----------|
| F2-1 | XGBoost 모델로 역송량(export), 수전량(import), 효율(efficiency) 예측 | P0 |
| F2-2 | 운전 모드별 모델 분기: 1기/2기/저부하 | P0 |
| F2-3 | 예측 결과를 `hourly_df` DataFrame 형태로 출력 | P0 |
| F2-4 | 모델 파일 `data/ml_models/`에서 로드, 학습 재실행 없이 추론만 | P1 |

### F3. 경제성 분석 (BEP 계산)

| ID | 요구사항 | 우선순위 |
|----|----------|----------|
| F3-1 | LNG 발전 손익분기 SMP 계산 (열효율 1.57~1.68 Mcal/kWh 범위 적용) | P0 |
| F3-2 | 기력 발전 손익분기 SMP 계산 (열효율 2.3 Mcal/kWh 고정) | P0 |
| F3-3 | 시간별 발전 모드 추천 (LNG 발전 / 기력 발전 / 정지) 산출 | P0 |
| F3-4 | Spot 계약 여부에 따른 계산 로직 분기 | P1 |
| F3-5 | LNG 현물가격 수동 입력 반영 (대시보드 사이드바 파라미터) | P1 |

### F4. 이상치 탐지

| ID | 요구사항 | 우선순위 |
|----|----------|----------|
| F4-1 | 동적 임계값 역산: BEP 기반 `smp_low` / `smp_high` 자동 산출 | P0 |
| F4-2 | 3단계 이상 분류: 정상 / 주의(단일 임계 초과) / 위험(양측 임계 초과) | P0 |
| F4-3 | 이상 구간 시간 범위 및 SMP 값 목록 출력 | P0 |
| F4-4 | 이상 구간 시각화 (차트에 하이라이트 표시) | P1 |

### F5. 가이던스 생성

| ID | 요구사항 | 우선순위 |
|----|----------|----------|
| F5-1 | 24시간 시간별 운전 계획표 생성 (마크다운 테이블) | P0 |
| F5-2 | 요약 메시지 생성 (카카오톡 전송용, 500자 이내) | P0 |
| F5-3 | HTML 형식 상세 리포트 생성 (이메일 전송용) | P1 |

### F6. 알림 발송

| ID | 요구사항 | 우선순위 |
|----|----------|----------|
| F6-1 | Gmail SMTP로 HTML 리포트 이메일 자동 발송 | P0 |
| F6-2 | KakaoTalk REST API로 요약 메시지 발송 | P0 |
| F6-3 | 발송 실패 시 30분 간격 재시도 (최대 3회) | P1 |
| F6-4 | 발송 결과 로그 기록 | P1 |

### F7. 스케줄러

| ID | 요구사항 | 우선순위 |
|----|----------|----------|
| F7-1 | APScheduler로 매일 17:30 자동 실행 (F1-F6 파이프라인 순차 실행) | P0 |
| F7-2 | 실행 윈도우: 17:30~19:30 (SMP 확정 대기) | P0 |
| F7-3 | 10분 주기 상태 체크 (date_utils.py 단일 소스 기반) | P1 |
| F7-4 | 스케줄러 상태 배지를 대시보드에 표시 (로컬 전용, Cloud 환경 비표시) | P2 |

### F8. 대시보드 — 레거시 (LNG_project_final.py)

| ID | 요구사항 | 우선순위 |
|----|----------|----------|
| F8-1 | Streamlit 기반 단일 파일 대시보드 | P0 |
| F8-2 | 사이드바: 날짜 선택, LNG 현물가격 입력, Spot 계약 토글 | P0 |
| F8-3 | 경제성 분석, 가이던스, 이상 구간, ML 결과, 원시 데이터 6개 탭(Expander) | P0 |
| F8-4 | 60초 자동 새로고침 | P1 |
| F8-5 | 달력 UI로 날짜 선택 + 다중 날짜 시계열 차트 확장 | P1 |

### F9. 대시보드 — 신규 (app.py + server.py)

| ID | 요구사항 | 우선순위 |
|----|----------|----------|
| F9-1 | Glassmorphism 디자인 시스템 적용 (styles/main.css) | P0 |
| F9-2 | 3-column 레이아웃: 좌(네비게이션) / 중(메인 콘텐츠) / 우(설정 패널) | P0 |
| F9-3 | 페이지 라우팅: Dashboard / Wallet / Transaction / Anomaly / ML Model / Raw Data | P0 |
| F9-4 | FastAPI REST 백엔드 (`server.py`)로 데이터 분리 | P1 |
| F9-5 | 연간 리포트 카드 (사이드바 내 요약 위젯) | P2 |

---

## 4. 비기능 요구사항

### 4.1 성능

| 항목 | 목표값 |
|------|--------|
| 전체 파이프라인 실행 시간 (F1-F6) | 10분 이내 |
| 대시보드 초기 로드 | 5초 이내 |
| SMP 캐시 히트 시 응답 | 1초 이내 |
| ML 추론 (24시간 예측) | 3초 이내 |

### 4.2 안정성

- SMP 수집 3단계 폴백으로 단일 장애 지점 제거
- 스케줄러 실패 시 30분 재시도 (최대 2시간 윈도우 내)
- 이메일/카카오 발송 독립 실행 (한쪽 실패해도 다른 쪽 계속)

### 4.3 유지보수성

- `config.py`를 유일한 상수 소스로 관리 (Magic Number 금지)
- `date_utils.py`를 날짜 로직 단일 소스로 통합 (v1.5 리팩터링 완료)
- 컴포넌트 분리 (`components/pages/`) — 페이지별 독립 수정 가능

### 4.4 환경 호환성

| 환경 | 지원 여부 |
|------|----------|
| Windows 로컬 (APScheduler + 스케줄러 배지) | 지원 |
| Streamlit Cloud (Linux, 스케줄러 배지 숨김) | 지원 |
| Docker 컨테이너 | 미정 |

---

## 5. 주요 파라미터 및 설정

### 5.1 LNG 발전 경제성 파라미터

| 파라미터 | 값 | 비고 |
|----------|-----|------|
| LNG 열효율 범위 | 1.57 ~ 1.68 Mcal/kWh | 운전 모드·부하에 따라 변동 |
| 기력 발전 열효율 | 2.3 Mcal/kWh | 고정값 |
| LNG 현물가격 | 사용자 입력 (기본값: config.py) | 사이드바 슬라이더 |
| Spot 계약 여부 | Boolean (토글) | 계산 로직 분기 |

### 5.2 스케줄러 설정

| 파라미터 | 값 |
|----------|-----|
| 실행 시작 | 17:30 (KST) |
| 실행 윈도우 종료 | 19:30 (KST) |
| 상태 체크 주기 | 10분 |
| 재시도 간격 | 30분 |

### 5.3 이상치 탐지 임계값

| 임계값 | 기준 |
|--------|------|
| `smp_low` | LNG 발전 BEP 역산 (하한) |
| `smp_high` | 기력 발전 BEP 역산 (상한) |
| 분류 | 정상 / 주의 (단일 초과) / 위험 (양측 초과) |

---

## 6. API 설계 (server.py — FastAPI)

> 현재 구현 중. 아래는 목표 API 명세.

| Method | Endpoint | 설명 |
|--------|----------|------|
| GET | `/api/smp/{date}` | 특정 날짜 SMP 데이터 조회 |
| GET | `/api/predict/{date}` | ML 예측 결과 조회 |
| GET | `/api/economics/{date}` | BEP 계산 결과 조회 |
| GET | `/api/anomalies/{date}` | 이상치 탐지 결과 조회 |
| GET | `/api/guidance/{date}` | 시간별 가이던스 조회 |
| POST | `/api/send-report` | 이메일/카카오 리포트 수동 발송 트리거 |
| GET | `/api/scheduler/status` | 스케줄러 상태 조회 |

---

## 7. 현재 진행 상황 및 미결 과제

### 7.1 완료된 기능 (v1 → v2 기준)

| 항목 | 상태 |
|------|------|
| SMP 3단계 폴백 수집 | 완료 |
| XGBoost ML 예측 | 완료 |
| LNG + 기력 발전 BEP 계산 | 완료 |
| 동적 임계값 이상치 탐지 | 완료 |
| Gmail SMTP + 카카오톡 통합 발송 | 완료 |
| APScheduler 10분 주기 | 완료 |
| date_utils.py 날짜 로직 통합 | 완료 |
| Streamlit 1분 자동 새로고침 | 완료 |
| 달력 날짜 선택 + 다중 날짜 시계열 | 완료 |
| 이상 구간 시각화 (차트 하이라이트) | 완료 |
| Glassmorphism UI (app.py) 기본 뼈대 | 완료 |
| components/pages/* 기본 구조 | 완료 |

### 7.2 미결 과제 (v2 목표)

| ID | 항목 | 우선순위 |
|----|------|----------|
| TODO-1 | server.py FastAPI 엔드포인트 전체 구현 완료 | P0 |
| TODO-2 | 두 UI 아키텍처 단일화 결정 (레거시 유지 vs. 신규 완전 전환) | P0 |
| TODO-3 | components/pages/ 모든 페이지 데이터 연동 완료 | P1 |
| TODO-4 | 공유 데이터 레이어 구현 (두 UI가 동일 캐시 참조) | P1 |
| TODO-5 | Docker 컨테이너화 (Streamlit Cloud 이외 배포 지원) | P2 |
| TODO-6 | REST API 인증 (JWT 또는 API Key) | P2 |
| TODO-7 | 이상치 메일 발송 트리거 자동화 (현재 수동 포함) | P1 |
| TODO-8 | 연간 리포트 카드 위젯 구현 (사이드바) | P2 |

---

## 8. 용어 정의

| 용어 | 설명 |
|------|------|
| SMP | System Marginal Price (계통한계가격) — 전력 시장 시간별 정산 단가 (원/kWh) |
| BEP | Break-Even Point (손익분기점) — 발전 비용과 SMP 수익이 같아지는 SMP 수준 |
| LNG 발전 | LNG 연료 기반 가스터빈 복합 사이클 발전 |
| 기력 발전 | 증기터빈 기반 화력 발전 (열효율 2.3 Mcal/kWh) |
| 역송량 (export) | 발전소에서 계통으로 보내는 전력량 |
| 수전량 (import) | 계통에서 발전소로 받는 전력량 |
| 저부하 | 최소 출력 운전 모드 |
| 가이던스 | 시간별 운전 모드 추천 계획표 |
| Glassmorphism | 반투명 유리 효과 기반 UI 디자인 시스템 |

---

## 9. 변경 이력

| 버전 | 날짜 | 주요 변경사항 |
|------|------|---------------|
| v1.0 | 초기 | Streamlit 모노리스, SMP 수집, ML 예측, 이메일 발송 |
| v1.2 | — | 달력 날짜 선택, 시계열 다중 날짜 차트 |
| v1.3 | — | Streamlit Cloud Linux 감지, 스케줄러 배지 조건부 표시 |
| v1.4 | — | 1분 자동 새로고침, 최신 CSV 날짜 자동 표시 |
| v1.5 | — | 이메일/카카오 통합 발송, 이상구간 시각화, date_utils.py 통합 |
| v2.0 | 2026-04-14 | app.py Glassmorphism UI, server.py FastAPI, components/ 모듈화, SMP 캐시 4/7~4/14 업데이트 |
