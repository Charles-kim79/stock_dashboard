# 🎯 AI 투자 전략 엔진

매일 아침 10분, 한경 컨센서스 신규 리포트 중 시장가 대비 저평가된 종목을 추출하는 개인용 투자 비서.

**Designed by Charles Kim · v1.0**

## 기능
- 한경 컨센서스 최근 리포트 자동 수집 (1주/2주/1개월)
- 네이버 금융 API 기반 시세/PER/PBR/매출/영업이익 수집
- 펀더멘털 분석 (괴리율 / PER / PBR / 영업이익률)
- 기술적 분석 (RSI / 볼린저밴드 / 추세 / 종합 시그널)
- 신호등 시스템 (💚 매수 유리 / 💛 중립 / ❤️ 매수 불리)

- 🎯 매수 판정 (단기·스윙 / 중장기 분할매수) + 📒 매수 계획·일지

## Supabase 연결 (매수 계획 영구 저장)
연결하지 않아도 앱은 동작하지만, 계획이 새로고침하면 사라집니다.

1. [supabase.com](https://supabase.com) 무료 프로젝트 생성 → **SQL Editor**에서 아래 실행

```sql
create table plans (
  id bigint generated always as identity primary key,
  created_at timestamptz default now(),
  code text, name text, market text, mode text,
  entry_price numeric, target_price numeric, stop_price numeric,
  reason text, score text, plan jsonb,
  exception boolean default false,
  status text default 'open',
  closed_at timestamptz, close_price numeric, outcome text, close_note text
);
alter table plans enable row level security;  -- 정책 없음 = 외부 직접 접근 차단
```

2. **Project Settings → API**에서 `Project URL`과 `service_role` 키 복사
3. Streamlit Cloud → 앱 → **Settings → Secrets**에 추가
```toml
SUPABASE_URL = "https://xxxx.supabase.co"
SUPABASE_KEY = "service_role 키"
```
⚠️ 키는 코드/레포에 절대 넣지 말고 Secrets에만 둡니다 (이 레포는 공개 상태).

## 데이터 출처
- 한경 컨센서스 (consensus.hankyung.com)
- 네이버 금융 (m.stock.naver.com)
- Yahoo Finance (yfinance)

## 활용
본 도구는 의사결정 보조용이며, 모든 매매 책임은 사용자에게 있습니다.
