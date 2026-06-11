# Stock Agent Pro v8 실전개선판

실행:

```bash
pip install -r requirements.txt
streamlit run app.py
```

## 추가 반영
- 거래량 폭증 점수: Volume MA20, Volume Ratio, OBV
- 재무/실적 점수: 매출 성장, EPS 성장, 순이익률, Forward P/E, 목표가 대비 여력
- 옵션/수급 대체 점수: 최근 만기 옵션 콜/풋 흐름
- 뉴스 점수 고도화: 목표가 상향/하향, 가이던스, 실적 서프라이즈 키워드 가중치
- 후보 스캐너 score에 거래량·재무·옵션/수급·터틀 점수 반영
- RSI 아래 거래량 참고 그래프 추가

주의: 이 앱은 정보 분석용이며 투자 권유가 아닙니다.
