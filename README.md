# Kappy Investment OS V15.2

정식 아키텍처 + Sidebar + NH 나무증권 Import 보정판입니다.

## 실행

```bash
pip install -r requirements.txt
streamlit run app.py
```

## V15.2 수정

- NH 나무증권 종합잔고에서 예수금/RP/외화예수금 제외
- NH 해외주식 ISIN → 미국 티커 자동 변환
- NH 파일의 원화 매입금액을 환율로 나누어 미국 주식 매수가(USD)로 자동 환산
- 통화 컬럼 추가: USD/KRW 분리
- Sidebar 총 매수금액을 USD/KRW로 분리 표시
- 가격 데이터 실패 시 빈 차트 대신 안내 메시지 표시
- `.gitignore` 유지
