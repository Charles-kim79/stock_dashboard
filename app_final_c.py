import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import re
import time

try:
    import yfinance as yf
    YF_AVAILABLE = True
except ImportError:
    YF_AVAILABLE = False

st.set_page_config(page_title="AI 투자 전략 엔진", layout="wide")

st.markdown("""
<style>
    /* 전체 폰트 축소 */
    html, body, [class*="css"] {
        font-size: 13px !important;
    }
    /* 데이터프레임 셀 축소 */
    .stDataFrame, .stDataFrame [data-testid="stTable"] {
        font-size: 12px !important;
    }
    .stDataFrame td, .stDataFrame th {
        padding: 4px 6px !important;
    }
    /* 메트릭 박스 축소 */
    [data-testid="stMetricValue"] {
        font-size: 18px !important;
    }
    [data-testid="stMetricLabel"] {
        font-size: 11px !important;
    }
    /* 제목 약간 축소 */
    h1 { font-size: 24px !important; }
    h2 { font-size: 20px !important; }
    h3 { font-size: 16px !important; }
    /* 메인 컨테이너 패딩 축소 */
    .main .block-container {
        padding-top: 1rem !important;
        padding-left: 1rem !important;
        padding-right: 1rem !important;
        max-width: 100% !important;
    }
    /* 탭 폰트 축소 */
    .stTabs [data-baseweb="tab"] {
        font-size: 13px !important;
    }
</style>
""", unsafe_allow_html=True)

# ============ 클라우드 전용: 비밀번호 보호 ============
PASSWORD = st.secrets.get("APP_PASSWORD", "")

if "auth" not in st.session_state:
    st.session_state.auth = False

if not st.session_state.auth:
    st.markdown("## 🔒 AI 투자 전략 엔진")
    st.caption("Designed by Charles Kim · v1.0")
    pw = st.text_input("비밀번호", type="password")
    if st.button("로그인"):
        if pw == PASSWORD and PASSWORD != "":
            st.session_state.auth = True
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")
    st.stop()
# =====================================================

# 기술적 분석 임계값 상수
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
BB_PROXIMITY = 1.03  # 볼린저 하단 +3% 이내
STOCH_LOW = 30

# =========================
# 평가 분류 기준 (신호등)
# =========================
# 펀더멘털
def classify_gap(pct):       return '💚' if pct >= 30 else '💛' if pct >= 15 else '❤️'
def classify_per(v):         return '❤️' if v > 20 else '💛' if v > 10 else '💚'
def classify_pbr(v):         return '❤️' if v > 3 else '💛' if v > 1.5 else '💚'
def classify_opmargin(pct):  return '💚' if pct > 15 else '💛' if pct >= 5 else '❤️'

# 기술
def classify_rsi(v):         return '❤️' if v > 70 else '💚' if v < 30 else '💛'
def classify_bb_position(close, upper, lower):
    if upper == lower: return '💛'
    pct = (close - lower) / (upper - lower)
    return '💚' if pct < 0.3 else '❤️' if pct > 0.7 else '💛'
def classify_trend(close, ma20, ma20_5ago):
    slope = (ma20 - ma20_5ago) / ma20_5ago if ma20_5ago else 0
    if close > ma20 and slope > 0.01: return '💚'
    if close < ma20 and slope < -0.01: return '❤️'
    return '💛'

# 종합 시그널
def composite(trend, bb, rsi):
    """종합 시그널 + 근거 텍스트 반환"""
    # 매수 케이스
    if trend == '💚' and bb == '💚':
        return '💚 매수 고려', '추세 상승 + 볼린저 하단'
    
    # 보류 케이스
    if trend == '❤️':
        return '❤️ 진입 보류', '추세 하락'
    if rsi == '❤️' and bb == '❤️':
        return '❤️ 진입 보류', 'RSI 과매수 + 볼린저 상단'
    
    # 관망 케이스 (근거를 좀 더 구체적으로)
    reasons = []
    if trend == '💛': reasons.append('추세 횡보')
    if bb == '💛': reasons.append('볼린저 중심')
    if rsi == '💛': reasons.append('RSI 중립')
    reason_text = ' + '.join(reasons) if reasons else '시그널 혼재'
    return '💛 관망', reason_text

# =========================
# 데이터 수집 함수
# =========================
@st.cache_data(ttl=3600)
def get_kr_market_prices():
    """네이버 모바일 API로 KOSPI/KOSDAQ 시세 수집 (각 시장 200종목)"""
    results = []
    headers = {
        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) '
                      'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 '
                      'Mobile/15E148 Safari/604.1',
        'Accept': 'application/json',
        'Referer': 'https://m.stock.naver.com/'
    }
    errors = []

    for mkt in ["KOSPI", "KOSDAQ"]:
        for page in range(1, 11):
            url = f"https://m.stock.naver.com/api/stocks/marketValue/{mkt}?page={page}&pageSize=100"
            try:
                res = requests.get(url, headers=headers, timeout=10)

                if res.status_code != 200:
                    errors.append(f"{mkt} p{page}: HTTP {res.status_code}")
                    continue
                if not res.text.strip().startswith('{'):
                    errors.append(f"{mkt} p{page}: JSON 아님 (앞 100자: {res.text[:100]})")
                    continue

                data = res.json()
                stocks = data.get('stocks', [])
                
                # 진단 모드용: 첫 번째 응답 구조 저장 (전역 상태나 별도 변수 활용을 위해 errors에 함께 추가하거나 별도 반환)
                if page == 1 and mkt == "KOSPI" and stocks:
                    results.append({"__DIAG_JSON__": stocks[0]})

                for s in stocks:
                    close_price_str = str(s.get('closePrice', '0')).replace(',', '')
                    try:
                        close_price = int(close_price_str)
                    except ValueError:
                        close_price = 0

                    results.append({
                        '종목코드': s.get('itemCode'),
                        '현재가': close_price,
                        '종목명': s.get('stockName'),
                        '시장': mkt,
                        '시가총액': s.get('marketValueHangeul') or s.get('marketValue'),
                        '등락률': s.get('fluctuationsRatio')
                    })

                time.sleep(0.4)

            except Exception as e:
                errors.append(f"{mkt} p{page}: {type(e).__name__} - {e}")
                continue

    # 진단용 JSON 추출 및 일반 데이터 분리
    diag_json = next((r["__DIAG_JSON__"] for r in results if "__DIAG_JSON__" in r), None)
    filtered_results = [r for r in results if "__DIAG_JSON__" not in r]
    
    df = pd.DataFrame(filtered_results).drop_duplicates(subset=['종목코드']).reset_index(drop=True)
    return df, errors, diag_json


@st.cache_data(ttl=3600)
def get_stock_details(code):
    """핫픽 종목의 재무 지표 보강 (API 활용)"""
    url = f"https://m.stock.naver.com/api/stock/{code}/finance/annual"
    headers = {
        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15'
    }
    details = {'매출액': '-', '영업이익': '-', 'PER': None, 'PBR': None, '영업이익률': None}
    
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            tr_list = data.get('financeInfo', {}).get('trTitleList', [])
            recent_key = None
            for tr in reversed(tr_list):
                if tr.get('isConsensus') == 'N':
                    recent_key = tr.get('key')
                    break
            
            if recent_key:
                row_list = data.get('financeInfo', {}).get('rowList', [])
                for row in row_list:
                    title = row.get('title')
                    val_str = row.get('columns', {}).get(recent_key, {}).get('value', '-')
                    if title == '매출액':
                        details['매출액'] = val_str
                    elif title == '영업이익':
                        details['영업이익'] = val_str
                    elif title == 'PER':
                        try: details['PER'] = float(val_str.replace(',', ''))
                        except: pass
                    elif title == 'PBR':
                        try: details['PBR'] = float(val_str.replace(',', ''))
                        except: pass
                    elif title == '영업이익률':
                        try: details['영업이익률'] = float(val_str.replace(',', ''))
                        except: pass
        time.sleep(0.3)
    except Exception as e:
        pass
    
    return details


def generate_perspective(per, pbr, op_margin, discrepancy):
    """직관적 코멘트 생성"""
    comments = []
    
    if pd.notnull(per):
        if per < 10: comments.append(f"PER {per}배(저평가)")
        elif per <= 20: comments.append(f"PER {per}배(적정)")
        else: comments.append(f"PER {per}배(부담)")
            
    if pd.notnull(pbr):
        if pbr < 1: comments.append(f"PBR {pbr}배(저PBR)")
        elif pbr > 3: comments.append(f"PBR {pbr}배(고PBR)")
            
    if pd.notnull(op_margin):
        if op_margin > 15: comments.append(f"영익률 {op_margin}%(우수)")
        elif op_margin >= 5: comments.append(f"영익률 {op_margin}%(양호)")
        else: comments.append(f"영익률 {op_margin}%(주의)")
            
    if pd.notnull(discrepancy):
        comments.append(f"괴리율 {discrepancy}%")
        
    return " · ".join(comments)


@st.cache_data(ttl=3600)
def get_hankyung_research(days=7):
    """한경 컨센서스 최근 n일 기업 리포트 파싱"""
    now = datetime.now()
    sd = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    ed = now.strftime("%Y-%m-%d")

    data = []
    errors = []
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    for page_num in range(1, 6):
        url = (f"https://consensus.hankyung.com/analysis/list"
               f"?sdate={sd}&edate={ed}&now_page={page_num}&report_type=CO")
        
        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code != 200:
                errors.append(f"한경 p{page_num}: HTTP {res.status_code}")
                continue

            soup = BeautifulSoup(res.text, 'html.parser')
            table = soup.find('table')

            if not table:
                errors.append(f"한경 p{page_num}: 테이블 없음 (응답 앞 300자: {res.text[:300]})")
                continue

            rows = table.find_all('tr')[1:]
            if not rows or len(rows) == 0:
                break

            for row in rows:
                cols = row.find_all('td')
                if len(cols) < 6:
                    continue

                link = cols[1].find('a')
                title_text = link.text.strip() if link else cols[1].text.strip()
                title_text = title_text.split('\n')[0].strip()

                code_match = re.search(r'\((\d{6})\)', title_text)
                item_code = code_match.group(1) if code_match else ""

                name_match = re.match(r'^([^(]+)', title_text)
                stock_name = name_match.group(1).strip() if name_match else title_text[:20]

                title_after = re.sub(r'^[^)]+\)\s*', '', title_text).strip()

                pdf_href = link.get('href') if link else ''
                pdf_url = f"https://consensus.hankyung.com{pdf_href}" if pdf_href else ''

                price_str = cols[2].text.replace(',', '').strip()
                target_price = pd.to_numeric(price_str, errors='coerce')

                opinion = cols[3].text.strip()
                broker = cols[5].text.strip()

                data.append({
                    '작성일': cols[0].text.strip(),
                    '종목명': stock_name,
                    '종목코드': item_code,
                    '리포트제목': title_after if title_after else title_text,
                    '적정가격': target_price,
                    '투자의견': opinion,
                    '작성기관': broker,
                    'PDF링크': pdf_url
                })
            
            time.sleep(0.3)
            
        except Exception as e:
            errors.append(f"한경 p{page_num}: {type(e).__name__} - {e}")
            break

    df = pd.DataFrame(data)

    if df.empty:
        return df, errors

    total = len(df)
    no_code = (df['종목코드'] == "").sum()
    no_price = df['적정가격'].isna().sum()
    zero_price = (df['적정가격'] == 0).sum()

    df = df[df['종목코드'] != ""]
    df = df.dropna(subset=['적정가격'])
    df = df[df['적정가격'] > 0]

    errors.append(
        f"한경 파싱: 총 {total}건 → 유효 {len(df)}건 "
        f"(종목코드 없음: {no_code}, 적정가 NaN: {no_price}, 적정가 0: {zero_price})"
    )

    return df.reset_index(drop=True), errors


@st.cache_data(ttl=3600)
def get_technical_signals_batch(unique_items):
    """일봉 6개월 받아서 기술적 시그널 딕셔너리 반환"""
    signals = {}
    diag_sample = None
    
    if not YF_AVAILABLE:
        for item in unique_items:
            signals[item['종목코드']] = {
                'RSI': '(미설치)', '볼린저 위치': '(미설치)', '추세': '(미설치)', '종합 시그널': '(미설치)', '근거': '-'
            }
        return signals, diag_sample
        
    for item in unique_items:
        code, mkt = item['종목코드'], item['시장']
        suffix = '.KS' if mkt == 'KOSPI' else '.KQ'
        ticker = f"{code}{suffix}"
        
        try:
            df = yf.download(ticker, period='6mo', interval='1d', progress=False, auto_adjust=True)
            if df.empty or len(df) < 30:
                signals[code] = {
                    'RSI': '(부족)', '볼린저 위치': '(부족)', '추세': '(부족)', '종합 시그널': '(부족)', '근거': '-'
                }
                continue
                
            # yfinance 1.3.0 의 MultiIndex 컬럼 처리
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
                
            if diag_sample is None:
                diag_sample = df.head(5)
                
            # 지표 계산
            # 20일선
            df['MA20'] = df['Close'].rolling(20).mean()
            
            # 볼린저밴드(20, 2)
            std20 = df['Close'].rolling(20).std()
            df['BB_Upper'] = df['MA20'] + 2 * std20
            df['BB_Lower'] = df['MA20'] - 2 * std20
            
            # RSI(14)
            delta = df['Close'].diff()
            gain = delta.where(delta > 0, 0).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / loss
            df['RSI'] = 100 - (100 / (1 + rs))
            
            # 스토캐스틱(14, 3)
            low14 = df['Low'].rolling(14).min()
            high14 = df['High'].rolling(14).max()
            df['STOCH_K'] = 100 * (df['Close'] - low14) / (high14 - low14)
            df['STOCH_D'] = df['STOCH_K'].rolling(3).mean()
            
            last = df.iloc[-1]
            prev = df.iloc[-2]
            
            sigs = []
            close = last['Close']
            ma20 = last['MA20']
            upper = last['BB_Upper']
            lower = last['BB_Lower']
            rsi_val = last['RSI']
            
            # 이전 5일 전 MA20 (추세 기울기용)
            prev5 = df.iloc[-6] if len(df) >= 6 else df.iloc[-1]
            ma20_5ago = prev5['MA20']
            
            # Classification
            trend_sig = classify_trend(close, ma20, ma20_5ago)
            bb_sig = classify_bb_position(close, upper, lower)
            rsi_sig = classify_rsi(rsi_val) if pd.notnull(rsi_val) else '💛'
            
            comp_sig, comp_reason = composite(trend_sig, bb_sig, rsi_sig)
            
            # 문자열 포맷팅
            rsi_str = f"{rsi_val:.1f} {rsi_sig}" if pd.notnull(rsi_val) else f"- 💛"
            
            if upper == lower:
                bb_str = "- 💛"
            else:
                pct = (close - lower) / (upper - lower)
                pos_text = "하단" if pct < 0.3 else "상단" if pct > 0.7 else "중심"
                bb_str = f"{pos_text} {bb_sig}"
                
            slope = (ma20 - ma20_5ago) / ma20_5ago if ma20_5ago else 0
            trend_text = "상승" if close > ma20 and slope > 0.01 else "하락" if close < ma20 and slope < -0.01 else "횡보"
            trend_str = f"{trend_text} {trend_sig}"
            
            signals[code] = {
                'RSI': rsi_str,
                '볼린저 위치': bb_str,
                '추세': trend_str,
                '종합 시그널': comp_sig,
                '근거': comp_reason
            }
        except Exception as e:
            signals[code] = {
                'RSI': f"(오류)", '볼린저 위치': f"(오류)", '추세': f"(오류)", '종합 시그널': f"(오류)", '근거': '-'
            }
            
    return signals, diag_sample

# =========================
# UI
# =========================
st.title("🎯 AI 투자 전략 엔진")
st.markdown("##### `v1.0` · Investment Strategy Dashboard")

st.info("""
**📌 목적** — 한경 컨센서스 신규 리포트 중 시장가 대비 저평가된 종목을 추출하여 일일 매수 후보군 도출  
**📌 사용법** — ① 기간 선택 → ② 괴리율 하한선 설정 → ③ 핫픽 추출 → ④ 펀더멘털·기술 탭 비교 분석  
**📌 활용** — 본 도구는 의사결정 보조용이며, 모든 매매 책임은 사용자에게 있음
""")
st.markdown("---")

st.sidebar.markdown("### ⚙️ 설정")
period_option = st.sidebar.selectbox(
    "📅 리포트 기간",
    ["최근 1주일", "최근 2주일", "최근 1개월"],
    index=0
)
PERIOD_DAYS = {"최근 1주일": 7, "최근 2주일": 14, "최근 1개월": 30}
selected_days = PERIOD_DAYS[period_option]

st.sidebar.markdown("---")
st.sidebar.markdown("**🎯 투자성향 필터** (Coming Soon)")
st.sidebar.selectbox(
    "성향 선택",
    ["전체", "보수적", "공격적"],
    disabled=True,
    help="향후 업데이트 예정"
)

threshold = st.sidebar.slider(
    '괴리율 하한선 (%)', 0, 50, 15,
    help="목표가 대비 현재가가 이 수치 이상 저평가된 종목만 표시"
)

show_debug = st.sidebar.checkbox("진단 모드", value=False,
                            help="데이터 수집 단계의 상세 정보 표시")

if st.button('🚀 오늘의 핫픽 추출', type="primary"):
    progress = st.progress(0)
    status = st.empty()

    status.text("1/3 · 시세 수집 중 (KOSPI + KOSDAQ)...")
    price_df, price_errors, diag_json = get_kr_market_prices()
    progress.progress(33)

    status.text(f"2/3 · 한경 컨센서스 리포트 수집 중 ({period_option})...")
    research_df, research_errors = get_hankyung_research(days=selected_days)
    progress.progress(66)

    if show_debug:
        with st.expander("🔍 진단 정보", expanded=True):
            st.write(f"**시세 데이터**: {len(price_df)}건")
            if price_errors:
                st.error("시세 수집 메시지:")
                for e in price_errors:
                    st.text(f"  - {e}")
            st.write(f"**리서치 데이터**: {len(research_df)}건")
            if research_errors:
                st.info("리서치 수집 메시지:")
                for e in research_errors:
                    st.text(f"  - {e}")
            if not price_df.empty:
                st.write("시세 샘플 (앞 3건):")
                st.dataframe(price_df.head(3))
            if not research_df.empty:
                st.write("리서치 샘플 (앞 3건):")
                st.dataframe(research_df.head(3))

    status.text("3/3 · 종목 매칭 및 괴리율 계산 중...")

    if price_df.empty:
        progress.empty(); status.empty()
        st.error("❌ 시세 데이터를 가져오지 못했습니다.")
    elif research_df.empty:
        progress.empty(); status.empty()
        st.error("❌ 리서치 데이터를 가져오지 못했습니다.")
    else:
        merged = pd.merge(research_df, price_df, on='종목코드', how='left')
        matched = merged.dropna(subset=['현재가']).copy()
        matched['괴리율(%)'] = ((matched['적정가격'] - matched['현재가'])
                              / matched['현재가'] * 100).round(2)

        # 보완 2: 종목별 그룹 정렬 (종목평균괴리율 기준)
        matched['종목평균괴리율'] = matched.groupby('종목코드')['괴리율(%)'].transform('mean')
        
        hot_picks = matched[matched['괴리율(%)'] >= threshold].copy()
        hot_picks = hot_picks.sort_values(
            by=['종목평균괴리율', '종목코드', '괴리율(%)'],
            ascending=[False, True, False]
        )

        progress.progress(100)
        time.sleep(0.3)
        progress.empty(); status.empty()

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("리서치 리포트", f"{len(research_df)}건")
        c2.metric("시세 매칭", f"{len(matched)}건")
        c3.metric(f"괴리율 ≥ {threshold}%", f"{len(hot_picks)}건")
        c4.metric("최고 괴리율",
                  f"{hot_picks['괴리율(%)'].max():.1f}%" if not hot_picks.empty else "-")

        st.markdown("---")

        if not hot_picks.empty:
            if not YF_AVAILABLE:
                st.warning("⚠️ `yfinance` 패키지가 설치되지 않아 기술적 분석이 생략되었습니다. 터미널에서 `pip install yfinance`를 실행해주세요.")
                
            status.text("4/4 · 보강 데이터 및 기술적 시그널 수집 중...")
            
            # 고유 종목코드 추출 (Stage 3 기술적 분석용)
            unique_items = hot_picks[['종목코드', '시장']].drop_duplicates().to_dict('records')
            tech_signals, diag_sample = get_technical_signals_batch(unique_items)
            
            for idx, row in hot_picks.iterrows():
                code = row['종목코드']
                
                # 재무 보강
                details = get_stock_details(code)
                hot_picks.at[idx, '매출액'] = details.get('매출액', '-')
                hot_picks.at[idx, '영업이익'] = details.get('영업이익', '-')
                
                # 펀더멘털 평가 추가
                hot_picks.at[idx, '괴리율'] = f"{row['괴리율(%)']}% {classify_gap(row['괴리율(%)'])}"
                
                per = details.get('PER')
                hot_picks.at[idx, 'PER'] = f"{per} {classify_per(per)}" if pd.notnull(per) else "-"
                
                pbr = details.get('PBR')
                hot_picks.at[idx, 'PBR'] = f"{pbr} {classify_pbr(pbr)}" if pd.notnull(pbr) else "-"
                
                opm = details.get('영업이익률')
                hot_picks.at[idx, '영익률'] = f"{opm}% {classify_opmargin(opm)}" if pd.notnull(opm) else "-"
                
                # 기술적 시그널 반영
                tech = tech_signals.get(code, {})
                hot_picks.at[idx, 'RSI'] = tech.get('RSI', '-')
                hot_picks.at[idx, '볼린저 위치'] = tech.get('볼린저 위치', '-')
                hot_picks.at[idx, '추세'] = tech.get('추세', '-')
                hot_picks.at[idx, '종합 시그널'] = tech.get('종합 시그널', '-')
                hot_picks.at[idx, '근거'] = tech.get('근거', '-')

            if show_debug and diag_sample is not None:
                with st.expander("🔍 기술적 분석(yfinance) 첫 종목 응답(5행)"):
                    st.dataframe(diag_sample)

            st.success(f"✅ 오늘의 핫픽 {len(hot_picks)}개 추출 완료")

            with st.expander("📖 **지표 해석 가이드** — 클릭해서 펼쳐보기", expanded=False):
                st.markdown("""
                ### 🚦 신호등 시스템
                > **💚 매수에 유리** | **💛 중립** | **❤️ 매수에 불리**
                
                ---
                
                ### 📊 펀더멘털 지표
                
                | 지표 | 💚 좋음 | 💛 보통 | ❤️ 주의 | 의미 |
                |---|---|---|---|---|
                | **괴리율** | ≥30% | 15~30% | <15% | 목표가까지 상승 여력 |
                | **PER** | <10배 | 10~20배 | >20배 | 이익 대비 주가 (저PER = 저평가) |
                | **PBR** | <1.5배 | 1.5~3배 | >3배 | 자산 대비 주가 (저PBR = 저평가) |
                | **영업이익률** | >15% | 5~15% | <5% | 사업 수익성 |
                
                ### 📈 기술적 지표
                
                | 지표 | 💚 좋음 | 💛 보통 | ❤️ 주의 | 의미 |
                |---|---|---|---|---|
                | **RSI(14)** | <30 (과매도) | 30~70 | >70 (과매수) | 단기 과열도 |
                | **볼린저 위치** | 하단(0~30%) | 중심 | 상단(70~100%) | 가격대 위치 |
                | **추세** | 상승 (종가>20일선 + 5일↑) | 횡보 | 하락 (종가<20일선 + 5일↓) | 단기 방향성 |
                
                ### 🎯 종합 시그널 판정 룰
                
                - **💚 매수 고려** = 추세 상승 + 볼린저 하단 (이상적 진입점)
                - **❤️ 진입 보류** = 추세 하락 OR (RSI 과매수 + 볼린저 상단)
                - **💛 관망** = 명확한 시그널 없음 (다른 지표·뉴스 확인 필요)
                
                ---
                
                ### 💡 활용 팁
                - **보수적 투자**: 펀더멘털 4지표 모두 💚 + 추세 💚인 종목 우선
                - **공격적 투자**: 괴리율 💚 + RSI 💚 + 볼린저 💚 (저점 매수 기회)
                - **💚 많은 행 = 안전한 매수 후보**
                """)

            # 탭 분리
            tab1, tab2 = st.tabs(["📊 펀더멘털 분석", "📈 기술적 분석"])
            
            with tab1:
                display_cols1 = ['작성일', '종목명_x', '시장', '현재가', '등락률', '적정가격',
                                '괴리율', '시가총액', 'PER', 'PBR', '영익률', '매출액', '영업이익', '투자의견', '작성기관', '리포트제목', 'PDF링크']
                df1 = hot_picks[[c for c in display_cols1 if c in hot_picks.columns]].rename(columns={'종목명_x': '종목명'})
                
                df1['현재가'] = df1['현재가'].apply(lambda x: f"{int(x):,}")
                df1['적정가격'] = df1['적정가격'].apply(lambda x: f"{int(x):,}")
                
                st.dataframe(
                    df1,
                    column_config={
                        "PDF링크": st.column_config.LinkColumn("리포트", display_text="📄")
                    },
                    hide_index=True,
                    width=1400
                )
                
            with tab2:
                display_cols2 = ['종목명_x', '시장', '현재가', 'RSI', '볼린저 위치', '추세', '종합 시그널', '근거']
                df2 = hot_picks[[c for c in display_cols2 if c in hot_picks.columns]].rename(columns={'종목명_x': '종목명'})
                
                df2['현재가'] = df2['현재가'].apply(lambda x: f"{int(x):,}")
                
                def color_signal_bg(val):
                    s = str(val)
                    if '💚' in s: return 'background-color: #d4edda; color: black;'
                    if '💛' in s: return 'background-color: #fff3cd; color: black;'
                    if '❤️' in s: return 'background-color: #f8d7da; color: black;'
                    return ''
                
                styled = df2.style.map(color_signal_bg, subset=['종합 시그널'])
                st.dataframe(styled, hide_index=True, width=1400)

            st.session_state['hot_picks'] = hot_picks

            csv_cols = ['작성일', '종목명_x', '시장', '현재가', '등락률', '적정가격', '괴리율', '시가총액', 'PER', 'PBR', '영익률', '매출액', '영업이익', 'RSI', '볼린저 위치', '추세', '종합 시그널', '근거', '투자의견', '작성기관', '리포트제목', 'PDF링크']
            csv_df = hot_picks[[c for c in csv_cols if c in hot_picks.columns]].rename(columns={'종목명_x': '종목명'})
            
            csv = csv_df.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                "📥 CSV 다운로드", csv,
                file_name=f"hotpicks_{datetime.now().strftime('%Y%m%d')}.csv",
                mime='text/csv'
            )
        else:
            st.warning(
                f"괴리율 {threshold}% 이상 종목이 없습니다. "
                f"슬라이더를 낮춰 보세요. (시세 매칭 성공: {len(matched)}건)"
            )

st.markdown("---")
col_a, col_b = st.columns([2, 1])
with col_a:
    st.caption("📊 데이터 출처: 한경 컨센서스 + 네이버 금융 · 모든 투자 책임은 본인에게 있습니다.")
with col_b:
    st.caption("**Designed by Charles Kim** · v1.0 · 2026")
