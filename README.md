<<<<<<< HEAD
# Kappy Investment OS V15

첫 번째 정식 아키텍처 버전입니다.

## 실행
=======
# Kappy Investment OS V15.2

정식 아키텍처 + Sidebar + NH 나무증권 Import 보정판입니다.

## 실행

>>>>>>> 4ce4681d25c00875230976a699b34fa86efc2a39
```bash
pip install -r requirements.txt
streamlit run app.py
```

<<<<<<< HEAD
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
=======
## V15.2 수정

- NH 나무증권 종합잔고에서 예수금/RP/외화예수금 제외
- NH 해외주식 ISIN → 미국 티커 자동 변환
- NH 파일의 원화 매입금액을 환율로 나누어 미국 주식 매수가(USD)로 자동 환산
- 통화 컬럼 추가: USD/KRW 분리
- Sidebar 총 매수금액을 USD/KRW로 분리 표시
- 가격 데이터 실패 시 빈 차트 대신 안내 메시지 표시
- `.gitignore` 유지
>>>>>>> 4ce4681d25c00875230976a699b34fa86efc2a39
