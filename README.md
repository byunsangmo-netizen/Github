# Kappy Investment OS V14.4

보유종목 저장과 NH 나무증권 잔고 불러오기를 안정화한 버전입니다.

## 실행
```bash
pip install -r requirements.txt
streamlit run app.py
```

## 주요 변경
- 보유종목 SQLite 저장 및 재접속 시 복원
- 보유종목 JSON 백업 다운로드/복원
- NH 나무증권 종합잔고 `.xls` HTML 파일 업로드 지원
- `pandas.read_html`/`lxml` 의존 제거
- BeautifulSoup 기반 NH Import 엔진 적용
- 실제투자 체크 종목은 관심그룹 `매수 종목`에 자동 등록
- 데이터 요청은 버튼을 눌렀을 때만 실행하여 yfinance rate limit 최소화
- 오늘 브리핑, 보유종목, 포트폴리오, 매도 엔진 흐름 유지

## GitHub/Streamlit 배포
아래 3개 파일만 덮어쓴 뒤 Commit → Push 하면 됩니다.

```text
app.py
requirements.txt
README.md
```
