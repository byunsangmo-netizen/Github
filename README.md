# Kappy Investment OS V15.1

정식 모듈형 아키텍처 + Dashboard Sidebar 버전입니다.

## 실행
```bash
pip install -r requirements.txt
streamlit run app.py
```

## V15.1 변경사항
- 왼쪽 Dashboard Sidebar 추가
- 보유종목 요약, 총 매수금액 표시
- 보유종목 클릭 시 차트 티커 자동 변경
- 관심종목 추가/삭제 및 클릭 선택
- AI 빠른 실행 안내 버튼 추가
- `.gitignore` 추가로 캐시/DB/pycache 업로드 방지

## GitHub 업로드 필수 파일/폴더
- app.py
- requirements.txt
- README.md
- .gitignore
- broker/
- core/
- market/
- storage/
- ui/

`__pycache__`, `cache_prices`, `*.pkl`, `*.db`는 올리지 마세요.
