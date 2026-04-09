# LNG 발전 가동경제성 자동 판단 시스템

> SMP 기반 LNG 발전소 운전모드 최적화 · ML 예측 · 이상구간 탐지 · 가이던스 생성 · 자동 전파

## 개요

에너지 수급담당자의 **SMP(계통한계가격) 수작업 확인 및 LNG 발전 가동경제성 수동 계산** 프로세스를 자동화하는 시스템입니다.

- SMP 자동 수집 (전력거래소 크롤링, 17~19시)
- ePower 마켓(KMOS) SMP 엑셀 자동 다운로드 (PyAutoGUI 기반 GUI 자동화)
- XGBoost ML 모델 기반 운전모드별 역송량·수전량·효율 예측
- LNG가격·환율·열량 기반 BEP 자동 계산 및 경제성 판단
- 동적 임계값 역산을 통한 SMP 이상구간 3단계 탐지
- 시간별 가동계획표 및 일간 요약 리포트 자동 생성 (F5)
- Gmail SMTP 정기/긴급 메일 발송 및 카카오톡 메시지 전파 (F6)
- APScheduler 기반 17:30~19:30 통합 스케줄러 (F7)
- Streamlit 대시보드를 통한 시각화 및 분석

## 주요 기능

| 기능 | 모듈 | 파일 | 설명 |
|------|------|------|------|
| F1 | 데이터 수집 | `modules/data_collector.py` | SMP 크롤링, 환율 API, 월간 고정변수 관리 |
| F1 | SMP 수집기 | `modules/smp_collector.py` | 전력거래소 SMP 자동 수집 및 캐시 |
| F1 | KMOS SMP 다운로드 | `modules/kmos_smp_download.py` | ePower 마켓 SMP 엑셀 자동 다운로드 (PyAutoGUI) |
| F1.4 | 데이터 전처리 | `docs/preprocess_데이터.py` | 학습 데이터 전처리 및 저부하 더미 보강 |
| F2 | ML 예측 | `modules/ml_predictor.py` | XGBoost 기반 운전모드별 설비특성 예측 |
| F3 | 경제성 계산 | `modules/economics_engine.py` | 대체단가·BEP 계산, 기력발전 BEP, 최적 운전모드 선정 |
| F4 | 이상치 탐지 | `modules/anomaly_detector.py` | SMP 동적 임계값 역산, 3단계 이상구간 분류 |
| F5 | 가이던스 생성 | `modules/guidance_generator.py` | 시간별 가동계획표, 일간 요약, 이상구간 경고 메시지 |
| F6 | 메일 발송 | `modules/mail_sender.py` | Gmail SMTP 정기 메일 + 긴급 알림 (HTML 테이블) |
| F6 | 카카오톡 전파 | `modules/kakao_sender.py` | 카카오톡 REST API 기반 가동계획 메시지 발송 |
| F7 | 통합 스케줄러 | `scripts/run_scheduler.py` | SMP 수집 → 분석 → 가이던스 → 메일 통합 자동화 |
| - | 설정 | `modules/config.py` | 전역 상수 및 파라미터 |
| - | 대시보드 | `scripts/LNG_project_final.py` | Streamlit 기반 경제성 분석 대시보드 |
| - | 일일 분석 | `scripts/run_daily_analysis.py` | 일일 자동 분석 스크립트 |
| - | ePower 자동화 | `scripts/epower_automation.py` | ePower 스케줄 다운로드 자동화 |

## 운전모드

| 모드 | 발전량 범위 | 설명 |
|------|------------|------|
| 1기 가동 | ~285,000 kW | 주로 수전대체 |
| 2기 저부하 | 380,000~410,000 kW | 수전·역송 Zero화 운전 |
| 2기 가동 | 530,000~595,000 kW | 수전대체 + 역송대체 |

## SMP 이상구간 분류

| 유형 | 조건 | 권고 |
|------|------|------|
| SMP 제로 | SMP <= 0 | LNG 발전 즉시 감발/정지 검토 |
| SMP 경제성 한계 | 0 < SMP < smp_low | 1기 또는 저부하 운전 전환 검토 |
| SMP 과대 | SMP >= smp_high | 기력발전 LNG 점화 추가 검토 |

> smp_low / smp_high는 당월 LNG가격·환율·열량 기반 동적 역산값 (고정값 폴백 포함)

## 프로젝트 구조

```
├── modules/
│   ├── config.py                  # 전역 설정 상수
│   ├── data_collector.py          # F1: 데이터 수집 (SMP, 환율, 고정변수)
│   ├── smp_collector.py           # F1: SMP 자동 수집기
│   ├── kmos_smp_download.py       # F1: ePower KMOS SMP 엑셀 다운로드
│   ├── kmos_smp_download_ver2.py  # F1: KMOS 다운로드 개선 버전
│   ├── ml_predictor.py            # F2: XGBoost ML 예측
│   ├── economics_engine.py        # F3: 경제성 계산 엔진
│   ├── anomaly_detector.py        # F4: 이상치 탐지
│   ├── guidance_generator.py      # F5: 가이던스 생성
│   ├── mail_sender.py             # F6: Gmail 메일 발송
│   └── kakao_sender.py            # F6: 카카오톡 메시지 전파
├── scripts/
│   ├── LNG_project_final.py       # Streamlit 대시보드
│   ├── run_daily_analysis.py      # 일일 자동 분석
│   ├── run_scheduler.py           # F7: 통합 스케줄러
│   ├── epower_automation.py       # ePower 스케줄 다운로드 자동화
│   └── run_smp_collector.bat      # SMP 수집 배치 파일
├── data/
│   ├── 데이터.csv                  # 학습 데이터
│   ├── 경제성분석_*.csv            # 일별 경제성 분석 결과
│   ├── models/                    # XGBoost 학습 모델 (.pkl)
│   ├── smp_cache/                 # 일별 SMP 캐시 (JSON)
│   └── smp_excel/                 # KMOS SMP 엑셀 파일
├── docs/
│   ├── context.txt                # 프로젝트 배경 및 업무 흐름
│   ├── PROJECT_STATUS.md          # 프로젝트 현황
│   ├── requirements.txt           # Python 패키지 의존성
│   ├── preprocess_데이터.py       # 학습 데이터 전처리 스크립트
│   └── epower_coords.json        # ePower GUI 자동화 좌표
└── logs/                          # 실행 로그
```

## 설치 및 실행

### 요구사항

- Python 3.10+

### 설치

```bash
pip install -r docs/requirements.txt
```

### 대시보드 실행

```bash
streamlit run scripts/LNG_project_final.py
```

### SMP 일일 수집

```bash
python modules/smp_collector.py
```

### KMOS SMP 다운로드 (ePower 마켓)

```bash
# 좌표 캡처 (최초 1회)
python modules/kmos_smp_download.py --calibrate

# SMP 다운로드
python modules/kmos_smp_download.py
```

### 통합 스케줄러 실행

```bash
# 스케줄러 시작 (백그라운드 상주, 17:30~19:30 자동 실행)
python scripts/run_scheduler.py

# 즉시 1회 실행 (테스트용)
python scripts/run_scheduler.py --now
```

### 메일 발송 테스트

```bash
python modules/mail_sender.py --test
```

### 카카오톡 설정 및 테스트

```bash
# 카카오 인증 (최초 1회)
python modules/kakao_sender.py --auth

# 테스트 발송
python modules/kakao_sender.py --test
```

## 기술 스택

- **ML**: XGBoost, scikit-learn (월별 Stratified CV)
- **시각화**: Streamlit, Plotly
- **데이터**: pandas, numpy, openpyxl
- **수집**: requests, BeautifulSoup4 (전력거래소 크롤링)
- **GUI 자동화**: PyAutoGUI (ePower KMOS 다운로드)
- **메일**: smtplib, Gmail SMTP
- **메시징**: 카카오톡 REST API
- **스케줄링**: APScheduler

## BEP 계산식

```
BEP = 대체단가(원/kWh) / 효율(Mcal/kWh) × 열량(Mcal/Nm³) × 1293(Nm³/ton) / 52(MMBtu/ton) / 환율(원/$) - 제세금($/MMBtu)
```

- **사용단가**: 제세금 = 0 (이미 도입된 계약 LNG)
- **Spot LNG**: 제세금 = 0.8 $/MMBtu (현물 구매 시 세금 포함)

## 데모

프로젝트 루트의 `E_Power_Data_Automatin.mp4` 파일에서 ePower 데이터 자동화 시연 영상을 확인할 수 있습니다.
