# Stock Agent Pro V14.3

Kappy Investment OS V14.3

## 주요 변경
- NH 나무증권 종합잔고 `.xls` HTML 파일 업로드 지원
- NH 잔고에서 보유 종목, 매수가, 수량 자동 인식
- 매수가는 사용자가 입력하고, 매수금액은 `매수가 × 수량`으로 자동 계산
- 실제투자 체크 종목은 관심그룹 `매수 종목`에 자동 등록
- 보유종목 중심 오늘 브리핑 유지
- 현 상황 분석 버튼을 눌렀을 때만 가격 데이터 요청
- AI Conviction Score 주식전망요약 탭 추가
  - 최근 6시간 뉴스 점수
  - 기관 목표주가 변화/여력
  - 기술적 추세
  - AI 밸류체인 내 위치
  - 자금 유입 강도

## 실행
```bash
pip install -r requirements.txt
streamlit run app.py
```

## GitHub/Streamlit 배포
`app.py`, `requirements.txt`, `README.md`만 덮어쓰기 후 Commit → Push → Streamlit 재배포하면 됩니다.
