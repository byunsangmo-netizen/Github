# Kappy Investment OS V15

첫 번째 정식 아키텍처 버전입니다.

## 실행
```bash
pip install -r requirements.txt
streamlit run app.py
```

## 구조
- `core/`: 지표, 점수, 매도 엔진, Conviction Score
- `market/`: 가격 데이터, 종목명, 섹터/유니버스
- `broker/`: NH 나무증권 HTML `.xls` 잔고 Import
- `storage/`: SQLite 저장/복원
- `ui/`: 차트 렌더링

## 핵심 기능
- 보유종목 SQLite 저장 및 자동 복원
- NH 나무증권 종합잔고 HTML `.xls` Import
- 버튼 클릭 시에만 yfinance 데이터 요청
- 오늘 브리핑
- 보유종목 AI 매도 타이밍 분석
- AI Conviction Score
- 후보 스캐너, 섹터 로테이션, 간단 백테스트
