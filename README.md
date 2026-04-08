# LNG 발전 가동경제성 자동 판단 시스템

> SMP 기반 LNG 발전소 운전모드 최적화 · ML 예측 · 이상구간 탐지

## 개요

에너지 수급담당자의 **SMP(계통한계가격) 수작업 확인 및 LNG 발전 가동경제성 수동 계산** 프로세스를 자동화하는 시스템입니다.

- SMP 자동 수집 (전력거래소 크롤링, 17~19시)
- XGBoost ML 모델 기반 운전모드별 역송량·수전량·효율 예측
- LNG가격·환율·열량 기반 BEP 자동 계산 및 경제성 판단
- 동적 임계값 역산을 통한 SMP 이상구간 3단계 탐지
- Streamlit 대시보드를 통한 시각화 및 분석

## 주요 기능

| 모듈 | 파일 | 설명 |
|------|------|------|
| 데이터 수집 | `modules/data_collector.py` | SMP 크롤링, 환율 API, 월간 고정변수 관리 |
| SMP 수집기 | `modules/smp_collector.py` | 전력거래소 SMP 자동 수집 및 캐시 |
| ML 예측 | `modules/ml_predictor.py` | XGBoost 기반 운전모드별 설비특성 예측 |
| 경제성 계산 | `modules/economics_engine.py` | 대체단가·BEP 계산, 최적 운전모드 선정 |
| 이상치 탐지 | `modules/anomaly_detector.py` | SMP 동적 임계값 역산, 3단계 이상구간 분류 |
| 설정 | `modules/config.py` | 전역 상수 및 파라미터 |
| 대시보드 | `scripts/LNG_project_final.py` | Streamlit 기반 경제성 분석 대시보드 |

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

## 프로젝트 구조

```
├── modules/
│   ├── config.py              # 전역 설정 상수
│   ├── data_collector.py      # 데이터 수집 (SMP, 환율, 고정변수)
│   ├── smp_collector.py       # SMP 자동 수집기
│   ├── ml_predictor.py        # XGBoost ML 예측
│   ├── economics_engine.py    # 경제성 계산 엔진
│   └── anomaly_detector.py    # 이상치 탐지
├── scripts/
│   ├── LNG_project_final.py   # Streamlit 대시보드
│   ├── run_daily_analysis.py  # 일일 자동 분석 스크립트
│   ├── epower_automation.py   # 전력 자동화
│   └── run_smp_collector.bat  # SMP 수집 배치 파일
├── data/
│   ├── 데이터.csv              # 학습 데이터
│   ├── models/                # XGBoost 학습 모델 (.pkl)
│   └── smp_cache/             # 일별 SMP 캐시
├── docs/
│   ├── context.txt            # 프로젝트 배경 및 업무 흐름
│   ├── PROJECT_STATUS.md      # 프로젝트 현황
│   └── requirements.txt       # Python 패키지 의존성
└── logs/                      # 실행 로그
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

## 기술 스택

- **ML**: XGBoost, scikit-learn (월별 Stratified CV)
- **시각화**: Streamlit, Plotly
- **데이터**: pandas, numpy, openpyxl
- **수집**: requests, BeautifulSoup4 (전력거래소 크롤링)
- **스케줄링**: APScheduler

## BEP 계산식

```
BEP = 대체단가(원/kWh) / 효율(Mcal/kWh) × 열량(Mcal/Nm³) × 1293(Nm³/ton) / 52(MMBtu/ton) / 환율(원/$) - 제세금($/MMBtu)
```

- **사용단가**: 제세금 = 0 (이미 도입된 계약 LNG)
- **Spot LNG**: 제세금 = 0.8 $/MMBtu (현물 구매 시 세금 포함)
