import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import re
import time
import hmac

APP_VERSION = "v1.9"   # 화면에 표시되는 버전은 모두 이 값 하나를 사용

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
    /* 메뉴(탭): 크게, 박스형. 선택된 탭은 파랑 (빨강은 '추출' 버튼 전용) */
    .stTabs [data-baseweb="tab-list"] { gap: 8px; border-bottom: none; }
    .stTabs [data-baseweb="tab"] {
        padding: 12px 22px !important; border: 2px solid rgba(128,128,128,0.45);
        border-radius: 10px; background: rgba(128,128,128,0.10);
    }
    .stTabs [data-baseweb="tab"] p { font-size: 17px !important; font-weight: 700 !important; }
    .stTabs [aria-selected="true"] { background: #1f6feb !important; border-color: #1f6feb !important; }
    .stTabs [aria-selected="true"] p { color: #ffffff !important; }
    .stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] { display: none; }
    /* 버튼: 기본 강조는 파랑, 오늘의 핫픽 '추출' 버튼만 빨강 */
    .stButton button[kind="primary"], .stFormSubmitButton button[kind="primary"] {
        background: #1f6feb; border-color: #1f6feb; color: #fff;
        font-size: 15px !important; font-weight: 700 !important; padding: 0.55rem 1.3rem !important;
    }
    .st-key-extract_btn button[kind="primary"] { background: #ff4b4b; border-color: #ff4b4b; }
    .stButton button { font-weight: 600 !important; }
</style>
""", unsafe_allow_html=True)

# ============ 클라우드 전용: 비밀번호 보호 ============
PASSWORD = str(st.secrets.get("APP_PASSWORD", "")).strip()
MAX_LOGIN_TRIES = 5

if "auth" not in st.session_state:
    st.session_state.auth = False
if "login_fail" not in st.session_state:
    st.session_state.login_fail = 0

if not st.session_state.auth:
    st.markdown("## 🔒 AI 투자 전략 엔진")
    st.caption(f"Designed by Charles Kim · {APP_VERSION}")
    if st.session_state.login_fail >= MAX_LOGIN_TRIES:
        st.error("시도 횟수를 초과했습니다. 페이지를 새로고침한 뒤 다시 시도하세요.")
        st.stop()
    pw = st.text_input("비밀번호", type="password")
    if st.button("로그인"):
        # 모바일 키보드가 붙이는 앞뒤 공백 제거 + 타이밍 공격 방지 비교
        if PASSWORD and hmac.compare_digest(pw.strip().encode(), PASSWORD.encode()):
            st.session_state.auth = True
            st.session_state.login_fail = 0
            st.rerun()
        else:
            st.session_state.login_fail += 1
            st.error("비밀번호가 틀렸습니다. (대소문자·공백 확인)")
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


def _f(s):
    """'9.73배', '22,292원', '45%', '-3.2', 'N/A' → float 또는 None"""
    if s is None:
        return None
    t = re.sub(r'[^\d.\-]', '', str(s))
    if t in ('', '-', '.', '-.'):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def parse_mcap_eok(s):
    """'1,268조 6,425억' → 억 단위 숫자 (정렬용)"""
    s = str(s)
    jo = re.search(r'([\d,]+)\s*조', s)
    eok = re.search(r'([\d,]+)\s*억', s)
    if not jo and not eok:
        v = _f(s)
        return v
    total = int(jo.group(1).replace(',', '')) * 10000 if jo else 0
    total += int(eok.group(1).replace(',', '')) if eok else 0
    return float(total)


NAVER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15'
}


@st.cache_data(ttl=3600)
def get_stock_details(code):
    """종목 재무·전망 지표 (네이버 금융 API): 확정치 + 컨센서스(추정) + 업종 동료 코드"""
    details = {
        '매출액': '-', '영업이익': '-', 'PER': None, 'PBR': None, '영업이익률': None,
        'ROE': None, '부채비율': None, '영업이익_직전': None, '영업이익_추정': None,
        '추정PER': None, '추정EPS': None, 'EPS': None, '의견평균': None, 'peers': [],
        '목표주가': None, '리포트수': 0, '리포트목록': [],
        '외인5일': None, '기관5일': None, '수급일수': 0, 'IR일정': None, '외인5일금액': None, '기관5일금액': None,
    }

    # 1) 연간 재무: 직전 확정연도 + 컨센서스(추정) 연도
    try:
        res = requests.get(f"https://m.stock.naver.com/api/stock/{code}/finance/annual",
                           headers=NAVER_HEADERS, timeout=10)
        if res.status_code == 200:
            fin = res.json().get('financeInfo', {})
            tr_list = fin.get('trTitleList', [])
            recent_key = next((tr.get('key') for tr in reversed(tr_list) if tr.get('isConsensus') == 'N'), None)
            cons_key = next((tr.get('key') for tr in tr_list if tr.get('isConsensus') == 'Y'), None)
            for row in fin.get('rowList', []):
                title = row.get('title')
                cols = row.get('columns', {})
                val = cols.get(recent_key, {}).get('value', '-') if recent_key else '-'
                fwd = cols.get(cons_key, {}).get('value') if cons_key else None
                if title == '매출액':
                    details['매출액'] = val
                elif title == '영업이익':
                    details['영업이익'] = val
                    details['영업이익_직전'] = _f(val)
                    details['영업이익_추정'] = _f(fwd)
                elif title == 'PER':
                    details['PER'] = _f(val)
                elif title == 'PBR':
                    details['PBR'] = _f(val)
                elif title == '영업이익률':
                    details['영업이익률'] = _f(val)
                elif title == 'ROE':
                    details['ROE'] = _f(val)
                elif title == '부채비율':
                    details['부채비율'] = _f(val)
        time.sleep(0.1)
    except Exception:
        pass

    # 2) 종합: 추정PER(포워드), 추정EPS, 현재 PBR, 투자의견 평균, 동일업종 종목
    try:
        res = requests.get(f"https://m.stock.naver.com/api/stock/{code}/integration",
                           headers=NAVER_HEADERS, timeout=10)
        if res.status_code == 200:
            data = res.json()
            for t in data.get('totalInfos', []):
                c, v = t.get('code'), _f(t.get('value'))
                if c == 'cnsPer':
                    details['추정PER'] = v
                elif c == 'cnsEps':
                    details['추정EPS'] = v
                elif c == 'eps':
                    details['EPS'] = v
                elif c == 'pbr' and v is not None:
                    details['PBR'] = v          # 현재 시점 PBR 우선
                elif c == 'per' and v is not None:
                    details['PER'] = v
            ci = data.get('consensusInfo') or {}
            details['의견평균'] = _f(ci.get('recommMean'))
            details['목표주가'] = _f(ci.get('priceTargetMean'))
            rs = data.get('researches') or []
            details['리포트수'] = len(rs)
            details['리포트목록'] = [{'제목': r.get('tit'), '증권사': r.get('bnm'), '일자': r.get('wdt'), 'id': r.get('id')}
                                  for r in rs[:5]]
            # 수급: 최근 5거래일 외국인·기관 순매수 수량 합 (최신순 목록)
            deals = (data.get('dealTrendInfos') or [])[:5]
            if deals:
                fo = [_f(x.get('foreignerPureBuyQuant')) for x in deals]
                og = [_f(x.get('organPureBuyQuant')) for x in deals]
                if any(v is not None for v in fo) and any(v is not None for v in og):
                    details['외인5일'] = sum(v for v in fo if v is not None)
                    details['기관5일'] = sum(v for v in og if v is not None)
                    details['수급일수'] = len(deals)
                    px = [_f(x.get('closePrice')) for x in deals]        # 일별 순매수 수량 × 종가 = 순매수 금액
                    details['외인5일금액'] = sum(q * c for q, c in zip(fo, px) if q is not None and c is not None)
                    details['기관5일금액'] = sum(q * c for q, c in zip(og, px) if q is not None and c is not None)
            # 다음 IR/실적 발표 일정
            ir = data.get('irScheduleInfo')
            if isinstance(ir, list):
                ir = ir[0] if ir else None
            if isinstance(ir, dict) and ir.get('irScheduleDate'):
                details['IR일정'] = {'title': ir.get('title'), 'date': ir.get('irScheduleDate'),
                                   'dday': _f(ir.get('irScheduleDday'))}
            peers = data.get('industryCompareInfo') or []
            if isinstance(peers, list):
                details['peers'] = [p.get('itemCode') for p in peers
                                    if p.get('itemCode') and p.get('itemCode') != code][:4]
        time.sleep(0.1)
    except Exception:
        pass

    return details


@st.cache_data(ttl=21600)
def get_peer_fper(peers):
    """동일업종 비교 종목들의 추정PER 중앙값 (peers: 종목코드 tuple). 2개 미만이면 None"""
    vals = []
    for pc in peers:
        try:
            res = requests.get(f"https://m.stock.naver.com/api/stock/{pc}/integration",
                               headers=NAVER_HEADERS, timeout=10)
            if res.status_code == 200:
                for t in res.json().get('totalInfos', []):
                    if t.get('code') == 'cnsPer':
                        v = _f(t.get('value'))
                        if v is not None and 0 < v < 100:
                            vals.append(v)
            time.sleep(0.1)
        except Exception:
            continue
    if len(vals) < 2:
        return None
    s = sorted(vals)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


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
            
            low20 = df['Low'].tail(20).min()
            bb_pct = (close - lower) / (upper - lower) if upper != lower else None
            signals[code] = {
                '_num': {
                    'close': float(close), 'ma20': float(ma20), 'bb_lower': float(lower),
                    'low20': float(low20), 'slope': float(slope),
                    'high6m': float(df['Close'].max()),
                    'stoch_k': float(last['STOCH_K']) if pd.notnull(last['STOCH_K']) else None,
                    'rsi': float(rsi_val) if pd.notnull(rsi_val) else None,
                    'bb_pct': float(bb_pct) if bb_pct is not None else None,
                },
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
# 매수 판정 엔진 v2 — 100점 가중 점수 (밸류 25 · 성장전망 35 · 체력 20 · 타이밍 20)
# =========================
VERDICT_BUY = '💚 매수 적정'
VERDICT_WAIT = '💛 대기'
VERDICT_SKIP = '⚪ 제외'
MODE_SWING = '단기·스윙'
MODE_LONG = '중장기 분할매수'
CATS = [('밸류에이션', 25), ('성장·전망', 35), ('수익성·체력', 20), ('타이밍', 20)]


@st.cache_data(ttl=3600)
def get_index_drawdowns():
    """코스피/코스닥 지수의 최근 6개월 고점 대비 낙폭(%, 음수). 실패 시 빈 dict."""
    out = {}
    if not YF_AVAILABLE:
        return out
    for mkt, tk in (('KOSPI', '^KS11'), ('KOSDAQ', '^KQ11')):
        try:
            df = yf.download(tk, period='6mo', interval='1d', progress=False, auto_adjust=True)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if df.empty:
                continue
            out[mkt] = float((df['Close'].iloc[-1] / df['Close'].max() - 1) * 100)
        except Exception:
            continue
    return out


def _item(cat, label, pts, mx, detail):
    return {'cat': cat, 'label': label, 'pts': pts, 'max': mx, 'detail': detail}


def score_stock(cur, target, n_reports, d, num, mkt_dd, mode):
    """항목별 점수표 생성. 반환: (items, ctx) — ctx는 판정에 쓰는 파생값"""
    d = d or {}
    num = num or {}
    items = []
    gap = (target - cur) / cur * 100 if cur else None

    # ── 밸류에이션 (25) ─────────────────────────────
    fper, peer = d.get('추정PER'), d.get('업종PER')
    if fper is None or fper <= 0:
        items.append(_item('밸류에이션', '추정PER(포워드)', 0, 15, '추정치 없음 또는 적자 전망'))
    elif peer:
        ratio = fper / peer
        pts = 15 if ratio <= 0.7 else 11 if ratio <= 0.9 else 7 if ratio <= 1.1 else 3 if ratio <= 1.3 else 0
        items.append(_item('밸류에이션', '추정PER vs 동일업종', pts, 15,
                           f"추정PER {fper:.1f}배 / 업종 {peer:.1f}배 ({ratio * 100:.0f}%)"))
    else:
        pts = 15 if fper <= 8 else 11 if fper <= 12 else 7 if fper <= 18 else 3 if fper <= 25 else 0
        items.append(_item('밸류에이션', '추정PER(포워드)', pts, 15, f"{fper:.1f}배 (업종 비교 데이터 없음)"))

    pbr = d.get('PBR')
    if pbr is None or pbr <= 0:
        items.append(_item('밸류에이션', 'PBR', 0, 10, '데이터 없음'))
    else:
        pts = 10 if pbr <= 1 else 7 if pbr <= 1.5 else 4 if pbr <= 2.5 else 1 if pbr <= 4 else 0
        items.append(_item('밸류에이션', 'PBR', pts, 10, f"{pbr:.2f}배"))

    # ── 성장·전망 (35) — 과거가 아닌 컨센서스(미래) 중심 ──────
    op_prev, op_fwd = d.get('영업이익_직전'), d.get('영업이익_추정')
    if op_prev is None or op_fwd is None:
        items.append(_item('성장·전망', '영업이익 성장(추정)', 0, 12, '데이터 없음'))
    elif op_fwd <= 0:
        items.append(_item('성장·전망', '영업이익 성장(추정)', 0, 12, '적자 전망'))
    elif op_prev <= 0:
        items.append(_item('성장·전망', '영업이익 성장(추정)', 8, 12, '흑자전환 전망'))
    else:
        g = (op_fwd / op_prev - 1) * 100
        pts = 12 if g >= 30 else 9 if g >= 15 else 6 if g >= 5 else 3 if g >= 0 else 0
        items.append(_item('성장·전망', '영업이익 성장(추정)', pts, 12, f"{g:+.0f}% (직전 {op_prev:,.0f}억 → 추정 {op_fwd:,.0f}억)"))

    eps, ceps = d.get('EPS'), d.get('추정EPS')
    if eps is None or ceps is None:
        items.append(_item('성장·전망', 'EPS 성장(추정)', 0, 8, '데이터 없음'))
    elif eps <= 0 < ceps:
        items.append(_item('성장·전망', 'EPS 성장(추정)', 5, 8, '흑자전환 전망'))
    elif eps <= 0:
        items.append(_item('성장·전망', 'EPS 성장(추정)', 0, 8, '적자'))
    else:
        g = (ceps / eps - 1) * 100
        pts = 8 if g >= 30 else 5 if g >= 10 else 2 if g >= 0 else 0
        items.append(_item('성장·전망', 'EPS 성장(추정)', pts, 8, f"{g:+.0f}% ({eps:,.0f} → {ceps:,.0f}원)"))

    if gap is None:
        items.append(_item('성장·전망', '목표가 괴리율', 0, 10, '-'))
    else:
        pts = 10 if gap >= 40 else 8 if gap >= 25 else 5 if gap >= 15 else 2 if gap >= 8 else 0
        items.append(_item('성장·전망', '목표가 괴리율', pts, 10, f"{gap:.1f}% (리포트 평균 목표가)"))

    rec = d.get('의견평균')
    pts = (3 if rec and rec >= 4.0 else 2 if rec and rec >= 3.5 else 0) + (2 if n_reports >= 2 else 0)
    items.append(_item('성장·전망', '애널리스트 컨센서스', pts, 5,
                       f"의견 {rec if rec else '-'}/5 · 리포트 {n_reports}건"))

    # ── 수익성·체력 (20) — 대기업을 감안해 영익률 8%/ROE 10%를 '양호' 기준으로 ──
    opm, roe = d.get('영업이익률'), d.get('ROE')
    hi = (opm is not None and opm >= 15) or (roe is not None and roe >= 15)
    mid = (opm is not None and opm >= 8) or (roe is not None and roe >= 10)
    low = (opm is not None and opm >= 5) or (roe is not None and roe >= 6)
    pts = 10 if hi else 7 if mid else 4 if low else 1 if (opm is not None and opm > 0) else 0
    items.append(_item('수익성·체력', '수익성(영익률·ROE)', pts, 10,
                       f"영익률 {opm if opm is not None else '-'}% · ROE {roe if roe is not None else '-'}%"))

    debt = d.get('부채비율')
    if debt is None:
        items.append(_item('수익성·체력', '부채비율', 5, 10, '데이터 없음(중립 5점)'))
    else:
        pts = 10 if debt <= 100 else 6 if debt <= 200 else 2 if debt <= 300 else 0
        items.append(_item('수익성·체력', '부채비율', pts, 10, f"{debt:.0f}%"))

    # ── 타이밍 (20) ──────────────────────────────
    rsi, k, bb_pct = num.get('rsi'), num.get('stoch_k'), num.get('bb_pct')
    trend_up = bool(num) and num['close'] > num['ma20'] and num['slope'] > 0.01
    trend_down = bool(num) and num['close'] < num['ma20'] and num['slope'] < -0.01
    high = num.get('high6m')
    s_depth = (1 - cur / high) * 100 if high else None
    m_depth = -mkt_dd if mkt_dd is not None else None
    cause = None
    if s_depth is not None:
        if s_depth < 10:
            cause = 'none'
        elif m_depth is None:
            cause = None
        elif s_depth <= max(m_depth * 1.5, m_depth + 5):
            cause = 'market'
        else:
            cause = 'stock'

    if rsi is not None and k is not None:
        if rsi <= 30 and k <= 20:
            dbl, dtxt = 1.0, f"RSI {rsi:.0f}(30↓ 과매도)·스토캐스틱 {k:.0f}(20↓ 과매도) 동시 과매도 → 이중 확인"
        elif rsi <= 40 and k <= 30:
            dbl, dtxt = 0.6, f"RSI {rsi:.0f}·스토캐스틱 {k:.0f} — 과매도 근접"
        elif rsi >= 70 or k >= 80:
            dbl, dtxt = 0.0, f"RSI {rsi:.0f}·스토캐스틱 {k:.0f} — 과매수 (RSI 70↑ 또는 스토캐스틱 80↑)"
        else:
            dbl, dtxt = 0.2, f"RSI {rsi:.0f}·스토캐스틱 {k:.0f} — 중립"
    else:
        dbl, dtxt = 0.0, '데이터 없음'

    # 수급: 최근 5거래일 외국인·기관 순매수 합 (둘 다 사면 만점, 둘 다 팔면 0점)
    fo, og = d.get('외인5일'), d.get('기관5일')
    if fo is None or og is None:
        sup, stxt = 0.5, '수급 데이터 없음 (중립)'
    else:
        if fo > 0 and og > 0:
            sup = 1.0
        elif fo + og > 0:
            sup = 0.67
        elif fo > 0 or og > 0:
            sup = 0.33
        else:
            sup = 0.0
        fa, oa = d.get('외인5일금액'), d.get('기관5일금액')
        if fa is not None and oa is not None:
            stxt = f"최근 {d.get('수급일수', 5)}일 순매수 — 외국인 {fa / 1e8:+,.0f}억원 · 기관 {oa / 1e8:+,.0f}억원"
        else:
            stxt = f"최근 {d.get('수급일수', 5)}일 외국인 {fo / 10000:+,.1f}만주 · 기관 {og / 10000:+,.1f}만주"

    if mode == MODE_LONG:
        if s_depth is None:
            items.append(_item('타이밍', '고점 대비 눌림', 0, 6, '데이터 없음'))
        else:
            pts = 6 if s_depth >= 25 else 4 if s_depth >= 15 else 2 if s_depth >= 10 else 0
            items.append(_item('타이밍', '6개월 고점 대비 눌림', pts, 6, f"-{s_depth:.0f}%"))
        if cause == 'market':
            items.append(_item('타이밍', '하락 원인', 5, 5, f"종목 -{s_depth:.0f}% vs 지수 -{m_depth:.0f}% → 시장 요인 중심"))
        elif cause == 'stock':
            items.append(_item('타이밍', '하락 원인', 0, 5, f"종목 -{s_depth:.0f}% vs 지수 -{m_depth:.0f}% → 개별 악재 가능성"))
        else:
            items.append(_item('타이밍', '하락 원인', 2, 5, '조정 미미 또는 지수 데이터 없음 (중립 2점)'))
        items.append(_item('타이밍', 'RSI+스토캐스틱 이중 확인', round(4 * dbl), 4, dtxt))
        items.append(_item('타이밍', '수급(외국인·기관)', round(5 * sup), 5, stxt))
    else:
        tpts = 5 if trend_up else 2 if (num and not trend_down) else 0
        items.append(_item('타이밍', '추세(20일선)', tpts, 5,
                           '상승' if trend_up else '하락' if trend_down else '횡보' if num else '데이터 없음'))
        if bb_pct is None:
            items.append(_item('타이밍', '볼린저 위치', 0, 4, '데이터 없음'))
        else:
            pts = 4 if bb_pct < 0.3 else 3 if bb_pct < 0.5 else 1 if bb_pct < 0.7 else 0
            items.append(_item('타이밍', '볼린저 위치', pts, 4, f"밴드 내 {bb_pct * 100:.0f}% (0%=하단선·100%=상단선" + (", 하단 이탈" if bb_pct <= 0 else "") + ")"))
        items.append(_item('타이밍', 'RSI+스토캐스틱 이중 확인', round(5 * dbl), 5, dtxt))
        items.append(_item('타이밍', '수급(외국인·기관)', round(6 * sup), 6, stxt))

    ir = d.get('IR일정') or {}
    earn_dday = ir.get('dday') if '실적' in str(ir.get('title', '')) else None
    ctx = {'gap': gap, 'trend_down': trend_down, 'rsi': rsi, 'bb_pct': bb_pct, 'cause': cause,
           's_depth': s_depth, 'high': high, 'op_fwd': op_fwd, 'earn_dday': earn_dday}
    return items, ctx


def level_of(pct):
    """득점률(%) → 상/중/하 + 신호등"""
    return ('🟢 상', '상') if pct >= 70 else ('🟡 중', '중') if pct >= 40 else ('🔴 하', '하')


def evaluate(mode, cur, target, n_reports, d, num, mkt_dd, params, trend=None):
    """점수 + 가격 계획 + 판정(매수 적정/대기/제외).
    trend: 목표가 추이(target_trend 결과)"""
    items, ctx = score_stock(cur, target, n_reports, d, num, mkt_dd, mode)
    cats = {c: (sum(i['pts'] for i in items if i['cat'] == c), mx) for c, mx in CATS}
    score = sum(p for p, _ in cats.values())
    out = {'items': items, 'cats': cats, 'score': score, 'total': 100, 'target': target, 'cur': cur,
           'cat_pct': {c: round(p / mx * 100) for c, (p, mx) in cats.items()},
           'stop': None, 'rr': None, 'loss_pct': None, 'wait_price': None, 'mode': mode, 'plan': None,
           'target_eff': target, 'upside_pct': (target / cur - 1) * 100 if cur else None}
    num = num or {}

    if mode == MODE_LONG:
        n, step = params['n_split'], params['split_step']
        prices = [cur * (1 - step / 100 * i) for i in range(n)]
        avg = sum(prices) / n
        out['plan'] = {
            'tranches': [(p, params['max_weight'] / n) for p in prices], 'avg': avg,
            'upside': (target / avg - 1) * 100 if avg else None,
            'review_below': prices[-1] * (1 - step / 100),
        }
    elif num:
        # 스윙은 1주~1달 보유 기준 → 증권사 목표가(보통 12개월)를 '목표 수익률 상한'으로 현실화
        t_eff = min(target, cur * (1 + params.get('swing_cap', 20) / 100))
        out['target_eff'] = t_eff
        out['upside_pct'] = (t_eff / cur - 1) * 100
        stop = min(num['low20'] * 0.98, cur * 0.97)
        risk = cur - stop
        out['stop'] = stop
        out['loss_pct'] = risk / cur * 100
        out['rr'] = (t_eff - cur) / risk if risk > 0 else None
        p_rr = (t_eff + params['min_rr'] * stop) / (1 + params['min_rr'])
        p_loss = stop / (1 - params['max_loss'] / 100)
        wp = min(p_rr, p_loss, cur)
        out['wait_price'] = wp if wp > stop else None

    # 하드 게이트: 적자 전망은 점수와 무관하게 제외
    if ctx['op_fwd'] is not None and ctx['op_fwd'] <= 0:
        out['verdict'], out['reason'] = VERDICT_SKIP, '영업이익 적자 전망 — 점수와 무관하게 제외'
        return out
    if mode == MODE_LONG and ctx['cause'] == 'stock':
        out['verdict'], out['reason'] = VERDICT_SKIP, '개별 악재 가능성 — 하락 원인(실적·공시) 확인 전엔 매수하지 않음'
        return out
    if score < params['min_score']:
        out['verdict'], out['reason'] = VERDICT_SKIP, f"종합 {score}점 (기준 {params['min_score']}점 미만)"
        return out

    # 이벤트 게이트: 점수가 좋아도 지금 사지 않는 이유 (실적 발표 직전, 목표가 연속 하향)
    event_reasons = []
    ew = params.get('earn_wait', 0)
    if ew and ctx['earn_dday'] is not None and 0 <= ctx['earn_dday'] <= ew:
        event_reasons.append(f"실적 발표 D-{int(ctx['earn_dday'])} — 발표 후 재검토")
    if trend and trend.get('dir') == 'down':
        event_reasons.append(f"목표가 연속 하향({trend['text']}) — 가설 재점검")
    if event_reasons:
        out['verdict'], out['reason'] = VERDICT_WAIT, ' · '.join(event_reasons)
        return out

    if mode == MODE_LONG:
        if ctx['s_depth'] is None or ctx['s_depth'] < 15:
            out['verdict'] = VERDICT_WAIT
            out['wait_price'] = ctx['high'] * 0.85 if ctx['high'] else None
            out['reason'] = '아직 충분히 눌리지 않음 — 6개월 고점 -15% 구간에서 분할 시작'
        else:
            out['verdict'] = VERDICT_BUY
            out['reason'] = (f"{score}점 · 분할매수 시작 · 평단 {int(out['plan']['avg']):,}원 · "
                             f"목표 상승여력 {out['plan']['upside']:.0f}%")
        return out

    reasons = []
    if not num:
        reasons.append('기술 데이터 부족')
    else:
        if ctx['trend_down']:
            reasons.append('추세 하락 — 회복 확인 후 재검토')
        if ctx['rsi'] is not None and ctx['bb_pct'] is not None and ctx['rsi'] > 70 and ctx['bb_pct'] > 0.7:
            reasons.append('과열(RSI>70·볼린저 상단) — 눌림 대기')
        if out['rr'] is None:
            reasons.append('손익비 계산 불가')
        elif out['rr'] < params['min_rr']:
            reasons.append(f"기대수익 +{out['upside_pct']:.0f}% 대비 위험 -{out['loss_pct']:.1f}% → 손익비 {out['rr']:.1f} (기준 {params['min_rr']} 미만)")
        if out['loss_pct'] is not None and out['loss_pct'] > params['max_loss']:
            reasons.append(f"손절폭 {out['loss_pct']:.1f}% > 허용 {params['max_loss']}%")
    if reasons:
        out['verdict'], out['reason'] = VERDICT_WAIT, ' · '.join(reasons)
    else:
        out['verdict'] = VERDICT_BUY
        out['reason'] = (f"{score}점 · 기대수익 +{out['upside_pct']:.0f}% vs 위험 -{out['loss_pct']:.1f}% "
                         f"(손익비 {out['rr']:.1f})")
    return out

# =========================
# 시장 신호 (매크로): 지수 추세 + 환율·유가·금리·VIX → 오늘 매수해도 되는 장인가
# =========================
MACRO_TICKERS = {
    'KOSPI': '^KS11', 'KOSDAQ': '^KQ11', 'S&P500': '^GSPC',
    '환율(원/$)': 'KRW=X', '유가(WTI)': 'CL=F', '미국10년금리': '^TNX', 'VIX': '^VIX',
}


@st.cache_data(ttl=1800)
def get_macro():
    """주요 지수·환율·유가·금리의 최근 흐름 (yfinance 일봉 6개월). 실패 항목은 빠짐"""
    out = {}
    if not YF_AVAILABLE:
        return out
    for name, tk in MACRO_TICKERS.items():
        try:
            df = yf.download(tk, period='6mo', interval='1d', progress=False, auto_adjust=True)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            s = df['Close'].dropna()
            if len(s) < 25:
                continue
            ma20 = s.rolling(20).mean()
            ma60 = s.rolling(60).mean() if len(s) >= 60 else None
            out[name] = {
                'close': float(s.iloc[-1]),
                'chg1': float(s.iloc[-1] / s.iloc[-2] - 1) * 100,
                'chg5': float(s.iloc[-1] / s.iloc[-6] - 1) * 100,
                'chg20': float(s.iloc[-1] / s.iloc[-21] - 1) * 100,
                'abs5': float(s.iloc[-1] - s.iloc[-6]),
                'ma20': float(ma20.iloc[-1]),
                'ma20_5ago': float(ma20.iloc[-6]),
                'ma60': float(ma60.iloc[-1]) if ma60 is not None else None,
                'low20': float(s.tail(20).min()),
                'high20': float(s.tail(20).max()),
                'below_ma20_recent': bool((s.tail(4).iloc[:-1] < ma20.tail(4).iloc[:-1]).any()),
            }
        except Exception:
            continue
    return out


def index_regime(m):
    """지수 1개의 추세 상태 → (신호등, 라벨, 조언)"""
    if not m:
        return ('⚪', '데이터 없음', '판단 보류')
    c, ma20, ma60 = m['close'], m['ma20'], m['ma60']
    slope = (ma20 / m['ma20_5ago'] - 1) if m['ma20_5ago'] else 0
    bounce = (c / m['low20'] - 1) * 100            # 최근 20일 저점 대비 반등폭
    breakout = c >= m['high20'] and c > ma20       # 20일 고점 돌파
    above = c > ma20
    if above and slope > 0 and (ma60 is None or c > ma60):
        return ('🟢', '상승 추세' + (' · 고점 돌파' if breakout else ''), '정상적으로 매수 후보를 볼 수 있는 장')
    if above and m['below_ma20_recent']:
        return ('🟡', '추세 전환 시도 (20일선 상향 돌파)', '확인 중 — 소액·분할로만 접근')
    if not above and bounce >= 5 and m['chg5'] >= 2:
        return ('🟡', '반등 강함 (아직 20일선 아래)', '선별 매수 가능 — 비중은 평소의 절반')
    if not above and slope < 0:
        if bounce < 3:
            return ('🔴', '하락 지속', '오늘은 신규 매수 보류 — 반등 확인 후')
        return ('🟡', '반등 약함', '관망 — 20일선 회복 전까지 대기')
    return ('🟡', '횡보', '선별 매수 (종목 판정 우선)')


def macro_impacts(mac):
    """환율·유가·금리·VIX 변화의 영향 (규칙 기반 설명). [(아이콘, 제목, 설명)]"""
    notes = []
    fx = mac.get('환율(원/$)')
    if fx:
        if fx['chg5'] >= 1.5:
            notes.append(('🔺', f"원화 약세 (환율 {fx['close']:,.0f}원, 5일 {fx['chg5']:+.1f}%)",
                          "외국인 매도 압력·수입물가 부담 → 내수·항공·정유 불리 / 반도체·자동차 등 수출주 상대 유리"))
        elif fx['chg5'] <= -1.5:
            notes.append(('🔻', f"원화 강세 (환율 {fx['close']:,.0f}원, 5일 {fx['chg5']:+.1f}%)",
                          "외국인 수급 우호 → 증시에는 긍정 / 수출주 마진엔 부담"))
    oil = mac.get('유가(WTI)')
    if oil:
        if oil['chg5'] >= 5:
            notes.append(('🔺', f"유가 급등 (WTI {oil['close']:.1f}$, 5일 {oil['chg5']:+.1f}%)",
                          "정유·에너지 유리 / 항공·화학·운송은 비용 부담 / 물가·금리 압력으로 성장주에 부정적"))
        elif oil['chg5'] <= -5:
            notes.append(('🔻', f"유가 급락 (WTI {oil['close']:.1f}$, 5일 {oil['chg5']:+.1f}%)",
                          "항공·화학 비용 완화 / 정유·에너지 부담 / 경기 둔화 우려 신호일 수 있음"))
    rate = mac.get('미국10년금리')
    if rate:
        if rate['abs5'] >= 0.15:
            notes.append(('🔺', f"미 금리 상승 (10년물 {rate['close']:.2f}%, 5일 {rate['abs5']:+.2f}%p)",
                          "성장주·바이오 밸류에이션 부담 / 은행·보험 상대 유리"))
        elif rate['abs5'] <= -0.15:
            notes.append(('🔻', f"미 금리 하락 (10년물 {rate['close']:.2f}%, 5일 {rate['abs5']:+.2f}%p)",
                          "성장주·바이오에 우호적"))
    vix = mac.get('VIX')
    if vix:
        if vix['close'] >= 25:
            notes.append(('⚠️', f"변동성 확대 (VIX {vix['close']:.1f})", "공포 구간 — 신규 매수는 분할·소액으로"))
        elif vix['close'] <= 15:
            notes.append(('✅', f"변동성 안정 (VIX {vix['close']:.1f})", "위험 선호 우호적"))
    return notes


def render_market_banner():
    """화면 맨 위 '오늘의 시장 신호' — 결론(한 줄) → 지수 신호등 → 매크로 영향"""
    mac = get_macro()
    st.markdown("### 🌐 오늘의 시장 신호")
    st.caption("참고용 지표입니다 (종목 판정에는 반영되지 않음). 시장을 확인했다면 '🔥 종목 스크리닝' 탭으로 이동하세요.")
    if not mac:
        st.info("시장 데이터를 가져오지 못했습니다 (yfinance). 잠시 후 새로고침해 보세요.")
        return None
    regs = {k: index_regime(mac.get(k)) for k in ('KOSPI', 'KOSDAQ')}
    order = {'🔴': 0, '🟡': 1, '🟢': 2, '⚪': 1}
    worst = min(regs.values(), key=lambda r: order[r[0]])
    box = {'🔴': st.error, '🟡': st.warning, '🟢': st.success, '⚪': st.info}[worst[0]]
    box(f"**{worst[0]} {worst[1]}** — {worst[2]}")

    cols = st.columns(4)
    for col, k in zip(cols[:2], ('KOSPI', 'KOSDAQ')):
        m = mac.get(k)
        if m:
            col.metric(f"{regs[k][0]} {k}", f"{m['close']:,.0f}", f"5일 {m['chg5']:+.1f}%")
            col.caption(regs[k][1])
    for col, k in zip(cols[2:], ('환율(원/$)', '유가(WTI)')):
        m = mac.get(k)
        if m:
            col.metric(k, f"{m['close']:,.1f}", f"5일 {m['chg5']:+.1f}%", delta_color="off")

    notes = macro_impacts(mac)
    if notes:
        with st.expander(f"📌 매크로 변화가 주는 영향 ({len(notes)}건)", expanded=True):
            for icon, title, desc in notes:
                st.markdown(f"{icon} **{title}**  \n{desc}")
    else:
        st.caption("환율·유가·금리에 특이한 급변은 없습니다.")
    return regs


# =========================
# 내 포트폴리오 저장소 (Supabase 연결 시 영구 저장, 미연결 시 세션 메모리)
#   status: 'watch'(관심·Pick) → 'open'(매수 확정) → 'closed'(청산)
# =========================
BUCKETS = ['국내 단기·중기', '국내 장기(배당)', '해외 성장']


def _sb():
    """Supabase 접속 정보(Secrets: SUPABASE_URL, SUPABASE_KEY) — 없으면 None"""
    try:
        url = str(st.secrets.get("SUPABASE_URL", "")).strip().rstrip('/')
        key = str(st.secrets.get("SUPABASE_KEY", "")).strip()
    except Exception:
        return None
    return (url, key) if url and key else None


def _sb_req(method, path, prefer='return=representation', **kw):
    url, key = _sb()
    headers = {'apikey': key, 'Authorization': f'Bearer {key}',
               'Content-Type': 'application/json', 'Prefer': prefer}
    r = requests.request(method, f"{url}/rest/v1/{path}", headers=headers, timeout=10, **kw)
    r.raise_for_status()
    return r.json() if r.text else []


# ---- 목표가 스냅샷: 날마다 컨센서스 목표가를 저장해 '연속 하향'을 감지 (consensus_snap 테이블, 선택 기능) ----
def snapshot_targets(items):
    """items: [{'code','target','n_reports','recomm'}] → 오늘자로 upsert. 실패해도 앱은 계속 동작"""
    rows = [{'code': i['code'], 'snap_date': datetime.now().strftime('%Y-%m-%d'), 'target': i['target'],
             'n_reports': i.get('n_reports'), 'recomm': i.get('recomm')}
            for i in items if i.get('target')]
    if not rows or not _sb():
        return False
    try:
        _sb_req('POST', 'consensus_snap?on_conflict=code,snap_date',
                prefer='resolution=merge-duplicates,return=minimal', json=rows)
        return True
    except Exception:
        st.session_state['snap_error'] = True
        return False


def get_target_hist(codes):
    """{종목코드: [(날짜, 목표가), ...] 오래된 순} — 최근 90일. 테이블 없거나 미연결이면 {}"""
    if not codes or not _sb():
        return {}
    try:
        since = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
        lst = ','.join(sorted(set(codes)))
        rows = _sb_req('GET', f"consensus_snap?select=code,snap_date,target&code=in.({lst})"
                              f"&snap_date=gte.{since}&order=snap_date.asc&limit=2000")
    except Exception:
        st.session_state['snap_error'] = True
        return {}
    out = {}
    for r in rows:
        if r.get('target') is not None:
            out.setdefault(r['code'], []).append((str(r['snap_date'])[:10], float(r['target'])))
    return out


def target_trend(hist):
    """목표가 추이 판정: 최근 3개 스냅샷이 계속 낮아지면 'down'(연속 하향), 계속 오르면 'up'"""
    if not hist or len(hist) < 3:
        return {'dir': 'insufficient', 'text': '이력 부족(날마다 추출하면 쌓입니다)', 'points': hist or []}
    a, b, c = hist[-3][1], hist[-2][1], hist[-1][1]
    chg = (c / hist[0][1] - 1) * 100 if hist[0][1] else 0
    text = f"{hist[-3][1]:,.0f}→{hist[-2][1]:,.0f}→{c:,.0f}원"
    if a > b > c:
        return {'dir': 'down', 'text': text, 'points': hist, 'chg': chg}
    if a < b < c:
        return {'dir': 'up', 'text': text, 'points': hist, 'chg': chg}
    return {'dir': 'flat', 'text': text, 'points': hist, 'chg': chg}


def get_plans(force=False):
    """저장된 항목 목록(최신순). session_state에 캐시, 쓰기 후 force=True로 갱신"""
    if force or '_plans' not in st.session_state:
        if _sb():
            try:
                st.session_state['_plans'] = _sb_req('GET', 'plans?select=*&order=created_at.desc&limit=500')
            except Exception as e:
                st.error(f"포트폴리오 불러오기 실패: {type(e).__name__} - {e}")
                st.session_state['_plans'] = st.session_state.get('_plans', [])
        else:
            st.session_state['_plans'] = list(st.session_state.get('_plans_mem', []))
    return st.session_state['_plans']


def save_plan(rec):
    """새 항목 저장 → (성공여부, 메시지)"""
    try:
        if _sb():
            _sb_req('POST', 'plans', json=rec)
        else:
            mem = st.session_state.setdefault('_plans_mem', [])
            new_id = max([p['id'] for p in mem], default=0) + 1
            mem.insert(0, {**rec, 'id': new_id, 'created_at': datetime.now().isoformat()})
        get_plans(force=True)
        return True, '저장했습니다.'
    except Exception as e:
        return False, f"저장 실패: {type(e).__name__} - {e}"


def update_plan(plan_id, fields):
    try:
        if _sb():
            _sb_req('PATCH', f'plans?id=eq.{int(plan_id)}', json=fields)
        else:
            for p in st.session_state.get('_plans_mem', []):
                if p['id'] == plan_id:
                    p.update(fields)
        get_plans(force=True)
        return True, '기록했습니다.'
    except Exception as e:
        return False, f"기록 실패: {type(e).__name__} - {e}"


def delete_plan(plan_id):
    try:
        if _sb():
            _sb_req('DELETE', f'plans?id=eq.{int(plan_id)}')
        else:
            st.session_state['_plans_mem'] = [p for p in st.session_state.get('_plans_mem', [])
                                              if p['id'] != plan_id]
        get_plans(force=True)
        return True, '삭제했습니다.'
    except Exception as e:
        return False, f"삭제 실패: {type(e).__name__} - {e}"




def build_pick_record(code, ev, bucket, thesis='', theme='', note=''):
    """판정 결과를 관심(Pick) 레코드로. 부가정보(구분·관점·테마·메모)는 plan(jsonb)에 담아 DB 스키마 변경을 피함"""
    plan = ev.get('plan')
    meta = {'bucket': bucket, 'thesis': thesis, 'theme': theme, 'note': note, 'ccy': 'KRW',
            'verdict': ev['verdict'], 'score': ev['score'], 'judged_mode': ev['mode']}
    if plan:
        meta.update({'tranches': [{'price': round(p), 'weight_pct': round(w, 2)} for p, w in plan['tranches']],
                     'avg': round(plan['avg']), 'review_below': round(plan['review_below'])})
    return {
        'code': code, 'name': ev['name'], 'market': ev['market'], 'mode': ev['mode'],
        'entry_price': round(ev['cur']),
        'target_price': round(ev['target'] if ev.get('plan') else ev.get('target_eff', ev['target'])),   # 스윙은 현실화한 목표가를 기본값으로
        'stop_price': round(ev['stop']) if ev.get('stop') else None,
        'reason': '', 'score': f"{ev['score']}/100", 'plan': meta,
        'exception': False, 'status': 'watch',
    }


@st.cache_data(ttl=600)
def get_last_price(code, market):
    if not YF_AVAILABLE:
        return None
    try:
        tk = code if market == 'US' else f"{code}{'.KS' if market == 'KOSPI' else '.KQ'}"   # 해외는 티커 그대로
        df = yf.download(tk, period='5d', interval='1d', progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return float(df['Close'].dropna().iloc[-1]) if not df.empty else None
    except Exception:
        return None


def plan_status(p, price):
    """저장된 약속 대비 현재 상태 문구"""
    if price is None:
        return '현재가 확인 불가'
    ret = (price / p['entry_price'] - 1) * 100
    tgt, stop = p.get('target_price'), p.get('stop_price')
    review = (p.get('plan') or {}).get('review_below')
    if tgt and price >= tgt:
        return f"🎯 목표 도달 — 계획대로 매도 ({ret:+.1f}%)"
    if stop and price <= stop:
        return f"🛑 손절선 이탈 — 계획대로 정리 ({ret:+.1f}%)"
    if review and price <= review:
        return f"⚠️ 재검토 조건 도달 — 추가매수 중단·가설 점검 ({ret:+.1f}%)"
    return f"보유 중 ({ret:+.1f}%)"



STATUS_LABEL = {'watch': '관심', 'open': '보유', 'closed': '종료'}
LABEL_STATUS = {v: k for k, v in STATUS_LABEL.items()}


def _norm(v):
    """표 편집 전후 비교용: None/NaN → 빈 문자열"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ''
    if isinstance(v, float) and v.is_integer():
        v = int(v)                                   # 5.0 과 5 를 같은 값으로 취급
    return str(v).strip()


SELL_REASONS = ['목표 달성', '손절', '임의 매도']
EDIT_STATUS = ['관심', '보유']          # '종료'는 아래 '매도 기록'에서만 (매도가·매도일을 반드시 남기기 위해)


def _ccy_of(p):
    return (p.get('plan') or {}).get('ccy') or ('USD' if p.get('market') == 'US' else 'KRW')


def _px(v, ccy):
    """통화별 가격 저장 형식: 원화는 정수, 달러는 소수 2자리"""
    return int(round(float(v))) if ccy == 'KRW' else round(float(v), 2)


def _money(v, ccy):
    return f"{v:,.0f}원" if ccy == 'KRW' else f"${v:,.2f}"


def render_portfolio():
    """📂 내 포트폴리오: 국내(원)·해외(달러) 표 + 직접 추가 + 추가 매수 평균가 + 매도 기록 + 결과 기록"""
    st.subheader("📂 내 포트폴리오")
    st.caption("표의 칸(상태·구분·컨셉·메모·매수가·수량·목표가·손절가)을 눌러 고친 뒤 **💾 변경 저장**. "
               "삭제는 행 선택 → 🗑 → 저장. '보유'로 바꾸려면 메모에 매수 이유(10자 이상)가 필요합니다. "
               "매도는 아래 '🏁 매도 기록'에서 기록하면 결과 데이터로 쌓입니다.")
    if not _sb():
        st.warning("⚠️ 저장소 미연결 — 지금은 이 브라우저 세션에만 저장돼 새로고침하면 사라집니다. "
                   "README의 'Supabase 연결'을 따라 Secrets에 키를 넣으세요.")

    def meta_of(p):
        return p.get('plan') or {}

    plans = [p for p in get_plans() if p.get('status') in STATUS_LABEL]
    active = [p for p in plans if p['status'] in ('watch', 'open')]
    closed = [p for p in plans if p['status'] == 'closed']
    ver = st.session_state.get('pf_ver', 0)
    mac = get_macro()
    fx = (mac.get('환율(원/$)') or {}).get('close')

    # ── 요약: 보유 종목 평가 (수량이 있는 것만) ──
    tot = {'KRW': [0.0, 0.0], 'USD': [0.0, 0.0]}        # [평가금액, 매수금액]
    for p in active:
        q = meta_of(p).get('qty')
        if p['status'] == 'open' and q:
            pr = get_last_price(p['code'], p['market'])
            if pr:
                c = _ccy_of(p)
                tot[c][0] += pr * q
                tot[c][1] += p['entry_price'] * q
    if tot['KRW'][1] or tot['USD'][1]:
        m1, m2, m3 = st.columns(3)
        if tot['KRW'][1]:
            m1.metric("🇰🇷 국내 보유 평가", f"{tot['KRW'][0]:,.0f}원",
                      f"{(tot['KRW'][0] / tot['KRW'][1] - 1) * 100:+.1f}%", delta_color="off")
        if tot['USD'][1]:
            m2.metric("🌎 해외 보유 평가", f"${tot['USD'][0]:,.0f}",
                      f"{(tot['USD'][0] / tot['USD'][1] - 1) * 100:+.1f}%", delta_color="off")
            if fx:
                m3.metric("해외 원화 환산", f"{tot['USD'][0] * fx:,.0f}원", f"환율 {fx:,.0f}원/$", delta_color="off")

    # ── 필터 + 표 (국내/해외 분리) ──
    themes = sorted({meta_of(p).get('theme') or '(미지정)' for p in active})
    f1, f2 = st.columns([3, 2])
    f_status = f1.radio("상태", ['전체'] + EDIT_STATUS, horizontal=True, key="pf_f_status")
    f_theme = f2.selectbox("컨셉", ['전체'] + themes, key="pf_f_theme")

    tables = []        # [{'ccy', 'rows', 'by_id', 'edited'}]
    all_rows = []
    if not active:
        st.info("아직 담은 종목이 없습니다. 🔥 종목 스크리닝에서 담거나 아래 '➕ 종목 직접 추가'를 이용하세요.")
    for ccy, title in (('KRW', '🇰🇷 국내 (원)'), ('USD', '🌎 해외 (달러)')):
        rows, by_id = [], {}
        for p in active:
            if _ccy_of(p) != ccy:
                continue
            m = meta_of(p)
            label = STATUS_LABEL[p['status']]
            theme = m.get('theme') or '(미지정)'
            if (f_status != '전체' and label != f_status) or (f_theme != '전체' and theme != f_theme):
                continue
            price = get_last_price(p['code'], p['market'])
            by_id[p['id']] = p
            row = {
                'ID': p['id'], '종목': p['name'], '상태': label,
                '구분': m.get('bucket', BUCKETS[0]), '컨셉': m.get('theme') or '',
                '메모': m.get('note') or p.get('reason') or '',
                '매수가': p['entry_price'], '수량': m.get('qty'),
                '현재가': price,
                '수익률(%)': round((price / p['entry_price'] - 1) * 100, 1) if price and p['entry_price'] else None,
                '목표가': p['target_price'], '손절가': p.get('stop_price'),
                '알림': plan_status(p, price),
            }
            rows.append(row)
            all_rows.append({**row, '통화': ccy, '점수': m.get('score')})
        if not rows:
            continue
        st.markdown(f"**{title}**")
        df = pd.DataFrame(rows)
        keep_cols = [c for c in df.columns if df[c].notna().any() or c in ('수량', '목표가', '손절가')]
        df = df[keep_cols]
        editable = ('상태', '구분', '컨셉', '메모', '매수가', '수량', '목표가', '손절가')
        pf = "%,d" if ccy == 'KRW' else "%,.2f"
        step = 100 if ccy == 'KRW' else 0.01
        edited = st.data_editor(
            df, hide_index=True, width=1400, num_rows="dynamic",
            disabled=[c for c in df.columns if c not in editable],
            key=f"pf_editor_{ccy}_{ver}_{f_status}_{f_theme}",
            column_config={
                '상태': st.column_config.SelectboxColumn('상태', options=EDIT_STATUS, required=True),
                '구분': st.column_config.SelectboxColumn('구분', options=BUCKETS, required=True),
                '컨셉': st.column_config.TextColumn('컨셉', help="섹터·테마 (예: AI반도체, 배당주)"),
                '메모': st.column_config.TextColumn('메모', help="왜 담았나 / 매수 이유", width='large'),
                '매수가': st.column_config.NumberColumn('매수가(평균)', format=pf, min_value=0, step=step,
                                                    help="입력한 값이 그대로 기준. 여러 번 샀다면 '➕ 추가 매수'로 평균가 갱신"),
                '수량': st.column_config.NumberColumn('수량', format="%,d", min_value=0, step=1),
                '현재가': st.column_config.NumberColumn('현재가', format=pf),
                '수익률(%)': st.column_config.NumberColumn('수익률(%)', format="%.1f"),
                '목표가': st.column_config.NumberColumn('목표가', format=pf, min_value=0, step=step,
                                                    help="직접 입력한 목표가가 기준이 됩니다 (이력 관리)"),
                '손절가': st.column_config.NumberColumn('손절가', format=pf, min_value=0, step=step),
            })
        tables.append({'ccy': ccy, 'rows': rows, 'by_id': by_id, 'edited': edited, 'editable': editable})

    if tables and st.button("💾 변경 저장", type="primary", key="pf_save"):
        done, errors = 0, []
        for t in tables:
            ccy, orig, by_id, edited = t['ccy'], {r['ID']: r for r in t['rows']}, t['by_id'], t['edited']
            kept = {int(i) for i in edited['ID'].dropna()}
            for pid in set(orig) - kept:                                  # 표에서 삭제한 행
                ok_, msg = delete_plan(pid)
                done += ok_
                if not ok_:
                    errors.append(f"{orig[pid]['종목']}: {msg}")
            for _, r in edited.iterrows():
                if pd.isna(r['ID']):                                      # 새로 추가된 빈 행은 무시
                    continue
                pid = int(r['ID'])
                o, p = orig[pid], by_id[pid]
                if all(_norm(r[k]) == _norm(o[k]) for k in t['editable'] if k in r.index and k in o):
                    continue
                new_status = LABEL_STATUS[r['상태']]
                theme, note = _norm(r['컨셉']), _norm(r['메모'])
                if not theme:
                    errors.append(f"{r['종목']}: 컨셉을 입력하세요")
                    continue
                if new_status == 'open' and p['status'] != 'open' and len(note) < 10:
                    errors.append(f"{r['종목']}: '보유'로 바꾸려면 메모에 매수 이유를 10자 이상 적으세요")
                    continue
                meta = dict(meta_of(p))
                meta.update({'bucket': r['구분'], 'theme': theme, 'note': note, 'ccy': ccy})
                if '수량' in r.index and pd.notna(r['수량']):
                    meta['qty'] = int(r['수량'])
                fields = {'plan': meta, 'status': new_status}
                if '매수가' in r.index and pd.notna(r['매수가']) and r['매수가'] > 0:
                    fields['entry_price'] = _px(r['매수가'], ccy)
                if '목표가' in r.index:
                    fields['target_price'] = _px(r['목표가'], ccy) if pd.notna(r['목표가']) and r['목표가'] > 0 else None
                if '손절가' in r.index:
                    fields['stop_price'] = _px(r['손절가'], ccy) if pd.notna(r['손절가']) and r['손절가'] > 0 else None
                if new_status == 'open' and p['status'] != 'open':
                    fields['reason'] = note
                    fields['exception'] = meta.get('verdict') not in (VERDICT_BUY, '-')   # 규칙 미통과 종목을 산 경우 표시
                ok_, msg = update_plan(pid, fields)
                done += ok_
                if not ok_:
                    errors.append(f"{r['종목']}: {msg}")
        for e in errors:
            st.error(e)
        if done and not errors:
            st.session_state['pf_ver'] = ver + 1
            st.rerun()
        elif not done and not errors:
            st.info("변경된 내용이 없습니다.")

    # ── ➕ 종목 직접 추가 (국내/해외) ──
    with st.expander("➕ 종목 직접 추가 (해외 종목은 여기서)"):
        mk = st.radio("시장", ['국내', '해외'], horizontal=True, key="add_mk")
        code = name = market = None
        if mk == '국내':
            ccy = 'KRW'
            q = st.text_input("종목명 또는 6자리 코드", key="add_q_kr")
            if q.strip():
                cands = search_stocks(q)
                if cands:
                    lab = {f"{c['name']} ({c['code']}·{c['market']})": c for c in cands}
                    c = lab[st.selectbox("검색 결과", list(lab.keys()), key="add_pick_kr")]
                    code, name, market = c['code'], c['name'], c['market']
                else:
                    st.caption("검색 결과가 없습니다.")
        else:
            ccy = 'USD'
            code = st.text_input("티커 (예: SMR, JOBY, IONQ)", key="add_tk").strip().upper() or None
            name = st.text_input("종목명 (선택 — 비우면 티커로 표시)", key="add_nm").strip() or code
            market = 'US'
        a1, a2, a3 = st.columns(3)
        st_label = a1.selectbox("상태", EDIT_STATUS, key="add_st")
        bucket = a2.selectbox("구분", BUCKETS, index=2 if mk == '해외' else 0, key="add_bk")
        theme = a3.text_input("컨셉·섹터 (필수)", key="add_th")
        nf = "%.0f" if ccy == 'KRW' else "%.2f"
        stp = 100.0 if ccy == 'KRW' else 0.01
        b1, b2, b3, b4 = st.columns(4)
        buy = b1.number_input("매수가 (관심이면 비워도 됨)", min_value=0.0, step=stp, format=nf, key="add_buy")
        qty = b2.number_input("수량", min_value=0, step=1, key="add_qty")
        target = b3.number_input("목표가 (필수)", min_value=0.0, step=stp, format=nf, key="add_tg")
        stop = b4.number_input("손절가 (선택)", min_value=0.0, step=stp, format=nf, key="add_sl")
        note = st.text_input("메모 (보유라면 매수 이유 10자 이상)", key="add_note")
        if st.button("➕ 추가", key="add_go"):
            err = None
            if not code or not name:
                err = "종목을 선택(국내)하거나 티커를 입력(해외)하세요."
            elif not theme.strip():
                err = "컨셉·섹터를 입력하세요."
            elif target <= 0:
                err = "목표가를 입력하세요. (직접 정한 목표가가 이력 관리 기준입니다)"
            elif st_label == '보유' and (buy <= 0 or len(note.strip()) < 10):
                err = "보유로 추가하려면 매수가와 매수 이유(메모 10자 이상)가 필요합니다."
            elif code in {p['code'] for p in active}:
                err = "이미 포트폴리오에 있는 종목입니다."
            price_now = get_last_price(code, market) if code and not err else None
            if not err and price_now is None and buy <= 0:
                err = "현재가를 가져오지 못했습니다. 티커를 확인하거나 매수가를 직접 입력하세요."
            if err:
                st.error(err)
            else:
                entry = buy if buy > 0 else price_now
                meta = {'bucket': bucket, 'theme': theme.strip(), 'note': note.strip(), 'ccy': ccy,
                        'verdict': '-', 'manual': True}
                if qty > 0:
                    meta['qty'] = int(qty)
                ok_, msg = save_plan({
                    'code': code, 'name': name, 'market': market, 'mode': '-',
                    'entry_price': _px(entry, ccy), 'target_price': _px(target, ccy),
                    'stop_price': _px(stop, ccy) if stop > 0 else None,
                    'reason': note.strip() if st_label == '보유' else '', 'score': None, 'plan': meta,
                    'exception': False, 'status': LABEL_STATUS[st_label]})
                if ok_:
                    st.session_state['pf_ver'] = ver + 1
                    st.rerun()
                else:
                    st.error(msg)

    # ── ➕ 추가 매수: 평균 매수가·수량 자동 계산 ──
    with st.expander("➕ 추가 매수 (평균 매수가 자동 계산)"):
        cands = [p for p in active]
        if not cands:
            st.caption("관심/보유 종목이 있어야 사용할 수 있습니다.")
        else:
            lab = {f"{p['name']} · 매수가 {_money(p['entry_price'], _ccy_of(p))} · 수량 {int(meta_of(p).get('qty') or 0):,}": p
                   for p in cands}
            p = lab[st.selectbox("종목", list(lab.keys()), key="addbuy_pick")]
            ccy = _ccy_of(p)
            old_q = int(meta_of(p).get('qty') or 0)
            g1, g2 = st.columns(2)
            add_p = g1.number_input("이번 매수가", min_value=0.0, step=100.0 if ccy == 'KRW' else 0.01,
                                    format="%.0f" if ccy == 'KRW' else "%.2f", key="addbuy_p")
            add_q = g2.number_input("이번 매수 수량", min_value=0, step=1, key="addbuy_q")
            if add_p > 0 and add_q > 0:
                if old_q <= 0:
                    st.warning("기존 수량이 없어 평균을 낼 수 없습니다. 위 표의 '수량' 칸에 기존 보유 수량을 먼저 입력·저장하세요.")
                else:
                    new_q = old_q + int(add_q)
                    new_avg = (p['entry_price'] * old_q + add_p * add_q) / new_q
                    st.success(f"새 평균 매수가 **{_money(new_avg, ccy)}** · 총 수량 **{new_q:,}**  "
                               f"(기존 {_money(p['entry_price'], ccy)}×{old_q:,} + 추가 {_money(add_p, ccy)}×{int(add_q):,})")
                    if st.button("이 값으로 적용", key="addbuy_apply"):
                        meta = dict(meta_of(p))
                        meta['qty'] = new_q
                        ok_, msg = update_plan(p['id'], {'plan': meta, 'entry_price': _px(new_avg, ccy)})
                        if ok_:
                            st.session_state['pf_ver'] = ver + 1
                            st.rerun()
                        else:
                            st.error(msg)

    # ── 🏁 매도 기록 (종료 처리) ──
    with st.expander("🏁 매도 기록 (종료 처리 → 결과 데이터로 저장)"):
        opens = [p for p in active if p['status'] == 'open']
        if not opens:
            st.caption("보유 중인 종목이 없습니다. 위 표에서 상태를 '보유'로 바꾼 종목만 매도 기록을 남길 수 있습니다.")
        else:
            lab = {f"{p['name']} · 매수가 {_money(p['entry_price'], _ccy_of(p))}": p for p in opens}
            p = lab[st.selectbox("종목", list(lab.keys()), key="sell_pick")]
            ccy = _ccy_of(p)
            s1, s2, s3 = st.columns(3)
            sell_p = s1.number_input("매도가 (필수)", min_value=0.0, step=100.0 if ccy == 'KRW' else 0.01,
                                     format="%.0f" if ccy == 'KRW' else "%.2f", key="sell_p")
            sell_d = s2.date_input("매도일", value=datetime.now().date(), key="sell_d")
            reason = s3.selectbox("매도 이유", SELL_REASONS, key="sell_r")
            note = st.text_input("한 줄 복기 (선택)", key="sell_n")
            if st.button("종료 처리", key="sell_go"):
                if sell_p <= 0:
                    st.error("매도가를 입력하세요.")
                else:
                    ok_, msg = update_plan(p['id'], {
                        'status': 'closed', 'closed_at': sell_d.isoformat(), 'close_price': _px(sell_p, ccy),
                        'outcome': reason, 'close_note': note.strip()})
                    if ok_:
                        st.session_state['pf_ver'] = ver + 1
                        st.rerun()
                    else:
                        st.error(msg)

    # ── 컨셉별 요약 ──
    if all_rows:
        sdf = pd.DataFrame(all_rows)
        sdf['컨셉'] = sdf['컨셉'].replace('', '(미지정)')
        sdf['수익률(%)'] = pd.to_numeric(sdf['수익률(%)'], errors='coerce')
        g = sdf.groupby('컨셉').agg(종목수=('종목', 'size'), 평균수익률=('수익률(%)', 'mean')).round(1).reset_index()
        with st.expander("🧭 컨셉별 요약 (평균 수익률)"):
            st.dataframe(g.sort_values('종목수', ascending=False), hide_index=True, width=500,
                         column_config={"평균수익률": st.column_config.NumberColumn("평균수익률(%)", format="%.1f")})

    # ── 📈 결과 기록 (종료된 종목) ──
    st.markdown("### 📈 결과 기록 (매도 완료)")
    if not closed:
        st.caption("아직 매도 기록이 없습니다. 위 '🏁 매도 기록'에서 종료 처리하면 여기에 결과가 쌓입니다.")
    else:
        crow = []
        for p in closed:
            m = meta_of(p)
            buy_d, sell_d_ = str(p.get('created_at'))[:10], str(p.get('closed_at') or '')[:10]
            try:
                held = (datetime.strptime(sell_d_, '%Y-%m-%d') - datetime.strptime(buy_d, '%Y-%m-%d')).days
            except Exception:
                held = None
            ret = (p['close_price'] / p['entry_price'] - 1) * 100 if p.get('close_price') and p['entry_price'] else None
            crow.append({'종목': p['name'], '구분': m.get('bucket'), '컨셉': m.get('theme') or '',
                         '매수가': p['entry_price'], '매도가': p.get('close_price'),
                         '수익률(%)': round(ret, 1) if ret is not None else None,
                         '목표가': p.get('target_price'), '매수일': buy_d, '매도일': sell_d_, '보유일': held,
                         '매도 이유': p.get('outcome'), '매수 이유': p.get('reason'), '복기': p.get('close_note'),
                         '통화': _ccy_of(p)})
        cdf = pd.DataFrame(crow)
        rets = cdf['수익률(%)'].dropna()
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("종료 건수", f"{len(cdf)}건")
        k2.metric("승률", f"{(rets > 0).mean() * 100:.0f}%" if len(rets) else "-")
        k3.metric("평균 수익률", f"{rets.mean():+.1f}%" if len(rets) else "-")
        k4.metric("평균 보유일", f"{cdf['보유일'].dropna().mean():.0f}일" if cdf['보유일'].notna().any() else "-")
        if cdf['매도 이유'].notna().any():
            st.caption("매도 이유별: " + " · ".join(f"{k} {v}건" for k, v in cdf['매도 이유'].value_counts().items()))
        st.dataframe(cdf.dropna(axis=1, how='all'), hide_index=True, width=1400,
                     column_config={"수익률(%)": st.column_config.NumberColumn("수익률(%)", format="%.1f")})


def fmt_num(v, nd=1, suf=''):
    return f"{v:,.{nd}f}{suf}" if v is not None and pd.notnull(v) else '-'


def render_pick_form(evs, key):
    """선택 종목들을 포트폴리오(관심)에 담기: 구분·관점·테마(필수)·메모를 함께 저장"""
    themes = []
    for p in get_plans():
        t = (p.get('plan') or {}).get('theme')
        if t and t not in themes:
            themes.append(t)
    thesis = ''   # 관점은 컨셉과 중복돼 제거 (기존 저장 데이터는 그대로 유지)
    c1, c2, c3 = st.columns([2, 2, 3])
    bucket = c1.selectbox("포트폴리오 구분", BUCKETS, index=1 if mode == MODE_LONG else 0, key=f"pf_b_{key}")
    theme = c2.text_input("컨셉·섹터 (필수)", key=f"pf_th_{key}", placeholder="예: AI반도체, 방산, 배당주")
    note = c3.text_input("한 줄 메모 (선택)", key=f"pf_n_{key}")
    if themes:
        st.caption("자주 쓴 테마: " + ", ".join(themes[:10]))
    if st.button(f"📂 {len(evs)}종목 포트폴리오에 담기", type="primary", key=f"pf_btn_{key}"):
        if not theme.strip():
            st.error("섹터·테마를 입력하세요. 컨셉 없이 담으면 나중에 옥석을 가릴 수 없습니다.")
            return
        existing = {p['code'] for p in get_plans() if p.get('status') in ('watch', 'open')}
        added, skipped, failed = [], [], []
        for c, ev in evs.items():
            if c in existing:
                skipped.append(ev['name'])
                continue
            ok_, msg = save_plan(build_pick_record(c, ev, bucket, thesis, theme.strip(), note.strip()))
            if ok_:
                added.append(ev['name'])
            else:
                failed.append(msg)
        if added:
            st.success(f"담았습니다: {', '.join(added)} — '📂 내 관심종목' 탭에서 확인하세요.")
        if skipped:
            st.info(f"이미 포트폴리오에 있어 건너뜀: {', '.join(skipped)}")
        if failed:
            st.error(' / '.join(failed))


def render_stock_report(code, ev, d, tech, reports=None):
    """종목 1개 종합 리포트 — ①결론 ②4분류 상/중/하 ③가격계획 ④강점·우려 ⑤세부 점수표 ⑥수급·일정·목표가추이"""
    d = d or {}
    tech = tech or {}

    # ① 결론
    box = {VERDICT_BUY: st.success, VERDICT_WAIT: st.warning, VERDICT_SKIP: st.info}.get(ev['verdict'], st.info)
    box(f"**{ev['verdict']}** — {ev['name']} · {ev['market']} · 종합 **{ev['score']}점**  \n{ev['reason']}")
    if ev.get('no_target'):
        st.warning("증권사 목표가가 없어 기대수익·손익비 해석에 주의가 필요합니다(목표가=현재가로 계산).")

    # ② 4분류 상/중/하
    cols = st.columns(4)
    for col, (cat, _) in zip(cols, CATS):
        pct = ev['cat_pct'][cat]
        col.metric(cat, f"{pct}% · {level_of(pct)[0]}")
    st.caption("점수는 '종목의 질', 판정은 '점수 + 가격 조건 + 시장 상황'까지 본 결론입니다. 점수가 높아도 가격·시장이 안 맞으면 💛 대기입니다.")

    # ③ 가격 계획
    if ev.get('plan'):
        plan = ev['plan']
        a1, a2, a3, a4 = st.columns(4)
        a1.metric("현재가", f"{int(ev['cur']):,}원")
        a2.metric("증권사 목표가", f"{int(ev['target']):,}원", f"{ev['upside_pct']:+.0f}%", delta_color="off")
        a3.metric("분할 완료 시 평단", f"{int(plan['avg']):,}원")
        a4.metric("평단 기준 상승여력", f"{plan['upside']:+.0f}%" if plan['upside'] is not None else "-")
        if ev['verdict'] == VERDICT_BUY:
            st.markdown("**📐 분할 매수 계획** — 직전 매수가보다 간격만큼 하락할 때만 다음 매수")
            st.dataframe(pd.DataFrame([
                {'회차': f"{i}차", '매수가': int(p), '투입 비중(%)': round(w, 2)}
                for i, (p, w) in enumerate(plan['tranches'], 1)
            ]), hide_index=True)
            st.info(f"🛑 **가격 손절 대신 재검토 조건**: ① {int(plan['review_below']):,}원 이하로 추가 하락 "
                    "② 적자 전환/영업이익 급감 ③ 증권사 목표가 연속 하향 — 하나라도 해당하면 추가 매수를 멈추고 가설을 다시 점검")
        elif ev['verdict'] == VERDICT_WAIT and ev['wait_price']:
            st.warning(f"⏳ **{int(ev['wait_price']):,}원 이하**(6개월 고점 -15%)로 내려오면 분할 매수를 시작할 수 있습니다.")
    else:
        a1, a2, a3, a4 = st.columns(4)
        a1.metric("현재가", f"{int(ev['cur']):,}원")
        a2.metric("기대수익", f"{ev['upside_pct']:+.0f}%" if ev.get('upside_pct') is not None else "-",
                  f"목표 {int(ev['target_eff']):,}원", delta_color="off")
        a3.metric("감수할 위험", f"-{ev['loss_pct']:.1f}%" if ev['loss_pct'] else "-",
                  f"손절 {int(ev['stop']):,}원" if ev['stop'] else None, delta_color="off")
        a4.metric("손익비", f"{ev['rr']:.1f}" if ev['rr'] is not None else "-",
                  "기대수익 ÷ 위험", delta_color="off")
        if ev['verdict'] == VERDICT_WAIT and ev['wait_price']:
            st.warning(f"⏳ **{int(ev['wait_price']):,}원 이하**로 내려오면 손익비·손절폭 조건을 충족해 매수할 수 있습니다.")
        gap = (ev['target'] - ev['cur']) / ev['cur'] * 100 if ev['cur'] else 0
        st.caption(f"증권사 목표가 {int(ev['target']):,}원 (괴리율 {gap:+.0f}%, 보통 12개월 기준) → 스윙은 목표 수익률 상한을 적용해 "
                   f"{int(ev['target_eff']):,}원으로 계산했습니다.")

    # 증권사 목표가: 핫픽은 한경 리포트별 목표가·의견·PDF, 검색 종목은 네이버 컨센서스 평균
    st.markdown("**🎯 증권사 목표가**")
    if reports:
        rdf = pd.DataFrame(reports)
        tg = pd.to_numeric(rdf['목표가'], errors='coerce')
        t1, t2, t3, t4 = st.columns(4)
        t1.metric("평균 목표가", f"{tg.mean():,.0f}원", f"{(tg.mean() / ev['cur'] - 1) * 100:+.0f}%", delta_color="off")
        t2.metric("최고", f"{tg.max():,.0f}원", f"{(tg.max() / ev['cur'] - 1) * 100:+.0f}%", delta_color="off")
        t3.metric("최저", f"{tg.min():,.0f}원", f"{(tg.min() / ev['cur'] - 1) * 100:+.0f}%", delta_color="off")
        t4.metric("리포트 수", f"{len(rdf)}건")
        rdf['괴리율(%)'] = ((tg / ev['cur'] - 1) * 100).round(0)
        st.dataframe(rdf[['일자', '증권사', '목표가', '괴리율(%)', '의견', '제목', '리포트']], hide_index=True, width=1400,
                     column_config={
                         '목표가': st.column_config.NumberColumn('목표가', format="%,d"),
                         '괴리율(%)': st.column_config.NumberColumn('괴리율(%)', format="%d"),
                         '리포트': st.column_config.LinkColumn('리포트', display_text='📄 PDF'),
                     })
    else:
        gap_c = (ev['target'] - ev['cur']) / ev['cur'] * 100 if ev['cur'] else 0
        rec = d.get('의견평균')
        st.markdown(f"컨센서스 평균 목표가 **{int(ev['target']):,}원** ({gap_c:+.0f}%)"
                    + (f" · 투자의견 평균 {rec:.1f}/5" if rec else "")
                    + f" · 리포트 {int(d.get('리포트수') or 0)}건")
        st.caption("증권사별 목표가·PDF는 한경 컨센서스 리포트가 있는 '🚀 오늘의 핫픽' 종목에서 표로 제공됩니다.")

    # ④ 강점 / 우려
    ranked = sorted([i for i in ev['items'] if i['max']], key=lambda i: i['pts'] / i['max'], reverse=True)
    good = [i for i in ranked if i['pts'] / i['max'] >= 0.7][:3]
    bad = [i for i in reversed(ranked) if i['pts'] / i['max'] <= 0.3][:3]
    s1, s2 = st.columns(2)
    with s1:
        st.markdown("**👍 강점**")
        for i in good:
            st.markdown(f"- {i['label']}: {i['detail']}")
        if not good:
            st.markdown("- 뚜렷한 강점 항목 없음")
    with s2:
        st.markdown("**⚠️ 우려**")
        for i in bad:
            st.markdown(f"- {i['label']}: {i['detail']}")
        if not bad:
            st.markdown("- 뚜렷한 우려 항목 없음")

    # ⑤ 타이밍 지표: 일반 지침 기준으로 과매도/과매수 구간 표시
    tn = tech.get('_num') or {}
    if tn:
        st.markdown("**⏱ 타이밍 지표 — 일반 지침 기준**")

        def zone(v, lo, hi):
            return '🟢 과매도 (매수 관심)' if v <= lo else '🔴 과매수 (추격 주의)' if v >= hi else '⚪ 중립'

        rsi_v, k_v, bb_v = tn.get('rsi'), tn.get('stoch_k'), tn.get('bb_pct')
        rows_t = []
        if rsi_v is not None:
            rows_t.append({'지표': 'RSI(14)', '현재값': f"{rsi_v:.0f} / 100",
                           '일반 기준': '30 이하 과매도 · 70 이상 과매수', '상태': zone(rsi_v, 30, 70)})
        if k_v is not None:
            rows_t.append({'지표': '스토캐스틱 %K(14,3)', '현재값': f"{k_v:.0f} / 100",
                           '일반 기준': '20 이하 과매도 · 80 이상 과매수', '상태': zone(k_v, 20, 80)})
        if bb_v is not None:
            rows_t.append({'지표': '볼린저밴드(20,2) 위치', '현재값': f"{bb_v * 100:.0f}%  (0%=하단선 · 100%=상단선)",
                           '일반 기준': '10% 이하 하단권 · 90% 이상 상단권', '상태': zone(bb_v * 100, 10, 90)})
        n_low = sum(1 for r in rows_t if r['상태'].startswith('🟢'))
        n_high = sum(1 for r in rows_t if r['상태'].startswith('🔴'))
        gap20 = (tn['close'] / tn['ma20'] - 1) * 100
        up = tn['close'] > tn['ma20'] and tn['slope'] > 0.01
        down = tn['close'] < tn['ma20'] and tn['slope'] < -0.01
        rows_t.append({'지표': '추세(20일선)', '현재값': f"20일선 대비 {gap20:+.1f}%",
                       '일반 기준': '20일선 위+우상향 상승 · 아래+우하향 하락',
                       '상태': '🟢 상승' if up else '🔴 하락' if down else '⚪ 횡보'})
        if tn.get('high6m'):
            dd6 = (1 - ev['cur'] / tn['high6m']) * 100
            rows_t.append({'지표': '6개월 고점 대비', '현재값': f"-{dd6:.0f}%",
                           '일반 기준': '-15% 이상 눌리면 조정 구간', '상태': '🟢 조정 구간' if dd6 >= 15 else '⚪ 고점 부근'})
        st.dataframe(pd.DataFrame(rows_t), hide_index=True, width=1400)
        if n_low >= 2:
            st.success(f"과매도 신호 {n_low}/3 — 단기 반등을 노린 매수 관심 구간입니다"
                       + (" (추세가 하락이라 반등 확인 후 진입 권장)" if down else ""))
        elif n_high >= 1:
            st.warning(f"과매수 신호 {n_high}/3 — 지금 추격 매수는 불리합니다")
        else:
            st.caption(f"과매도 신호 {n_low}/3 — 아직 뚜렷한 과매도 구간은 아닙니다")

    # ⑥ 세부 점수표 (분류별로 정렬, 신호등 평가)
    st.markdown("**📋 세부 근거**")
    cat_order = [c for c, _ in CATS]
    st.dataframe(pd.DataFrame([{
        '분류': i['cat'], '항목': i['label'], '값': i['detail'],
        '평가': '🟢' if i['pts'] / i['max'] >= 0.7 else '🟡' if i['pts'] / i['max'] >= 0.4 else '🔴',
        '점수': f"{i['pts']}/{i['max']}",
    } for i in sorted(ev['items'], key=lambda i: cat_order.index(i['cat']))]), hide_index=True, width=1400)

    # ⑥ 수급 · 일정 · 목표가 추이
    e1, e2, e3 = st.columns(3)
    fo, og = d.get('외인5일'), d.get('기관5일')
    with e1:
        st.markdown("**🧾 수급 (5거래일)**")
        fa, oa = d.get('외인5일금액'), d.get('기관5일금액')
        if fo is None or og is None:
            st.markdown("데이터 없음")
        elif fa is not None and oa is not None:
            st.markdown(f"외국인 {fa / 1e8:+,.0f}억원  \n기관 {oa / 1e8:+,.0f}억원")
        else:
            st.markdown(f"외국인 {fo / 10000:+,.1f}만주  \n기관 {og / 10000:+,.1f}만주")
    with e2:
        st.markdown("**📅 다음 일정**")
        ir = d.get('IR일정')
        if ir:
            dd_txt = f" (D-{int(ir['dday'])})" if ir.get('dday') is not None else ''
            st.markdown(f"{ir.get('title', '')}  \n{ir.get('date', '')}{dd_txt}")
        else:
            st.markdown("예정된 IR 일정 없음")
    with e3:
        st.markdown("**🎯 목표가 추이**")
        tr = ev.get('trend')
        if tr and tr['dir'] in ('down', 'up', 'flat'):
            icon = {'down': '🔻 연속 하향', 'up': '🔺 연속 상향', 'flat': '➖ 혼조'}[tr['dir']]
            st.markdown(f"{icon}  \n{tr['text']}")
        elif not _sb():
            st.markdown("저장소 미연결 — 추적 불가")
        elif st.session_state.get('snap_error'):
            st.markdown("추적용 테이블이 없습니다 (README 참고)")
        else:
            st.markdown("이력 쌓는 중  \n(서로 다른 날 3회 추출 후 판정)")

    reports = d.get('리포트목록') or []
    if reports:
        with st.expander("📰 최근 증권사 리포트"):
            for r in reports:
                link = (f"https://finance.naver.com/research/company_read.naver?nid={r['id']}" if r.get('id') else None)
                title = f"[{r.get('제목', '')}]({link})" if link else r.get('제목', '')
                st.markdown(f"- {r.get('일자', '')} · **{r.get('증권사', '')}** — {title}")
    st.markdown(f"[📊 네이버 증권 종합](https://m.stock.naver.com/domestic/stock/{code}/total) · "
                f"[📈 차트](https://m.stock.naver.com/fchart/domestic/stock/{code})")

@st.cache_data(ttl=600)
def search_stocks(query):
    """종목명/코드 검색 (네이버 자동완성) — 코스피·코스닥 일반 종목만"""
    out = []
    try:
        r = requests.get("https://m.stock.naver.com/front-api/search/autoComplete",
                         params={'query': query.strip(), 'target': 'stock'}, headers=NAVER_HEADERS, timeout=10)
        if r.status_code == 200:
            for it in (r.json().get('result') or {}).get('items', []):
                if (it.get('category') == 'stock' and not it.get('isEtf')
                        and it.get('typeCode') in ('KOSPI', 'KOSDAQ')):
                    out.append({'code': it['code'], 'name': it['name'], 'market': it['typeCode']})
    except Exception:
        pass
    return out[:10]


@st.cache_data(ttl=300)
def get_basic(code):
    try:
        r = requests.get(f"https://m.stock.naver.com/api/stock/{code}/basic", headers=NAVER_HEADERS, timeout=10)
        if r.status_code == 200:
            j = r.json()
            return {'name': j.get('stockName'), 'cur': _f(j.get('closePrice')),
                    'market': (j.get('stockExchangeType') or {}).get('name')}
    except Exception:
        pass
    return {}


def analyze_code(code, name=None, market=None):
    """검색한 종목 1개를 현재 모드·규칙으로 평가 → (결과 dict, 오류 메시지)"""
    basic = get_basic(code)
    if not basic or not basic.get('cur'):
        return None, '시세를 가져오지 못했습니다. 코드/종목명을 확인하세요.'
    market = market or basic.get('market') or 'KOSPI'
    if market not in ('KOSPI', 'KOSDAQ'):
        return None, '코스피/코스닥 종목만 지원합니다.'
    d = dict(get_stock_details(code))
    d['업종PER'] = get_peer_fper(tuple(d.get('peers') or ()))
    tech_all, _ = get_technical_signals_batch([{'종목코드': code, '시장': market}])
    tech = tech_all.get(code, {})
    target = d.get('목표주가')
    snapshot_targets([{'code': code, 'target': target, 'n_reports': d.get('리포트수'), 'recomm': d.get('의견평균')}])
    trend = target_trend(get_target_hist([code]).get(code))
    idx_dd = get_index_drawdowns() if mode == MODE_LONG else {}
    ev = evaluate(mode, basic['cur'], target if target else basic['cur'], int(d.get('리포트수') or 0),
                  d, tech.get('_num'), idx_dd.get(market), RULES, trend=trend)
    ev['trend'] = trend
    ev['name'] = name or basic.get('name') or code
    ev['market'] = market
    ev['no_target'] = not target
    return {'code': code, 'ev': ev, 'd': d, 'tech': tech, 'mode': mode}, None


def render_stock_search():
    """🔍 종목 분석: 종목명/코드 검색 → 한 화면 종합 리포트 → 포트폴리오 담기"""
    st.subheader("🔍 종목 분석")
    st.caption("핫픽에 없는 종목도 검색해서 **판정·펀더멘털·기술 분석을 한 화면**에서 볼 수 있습니다. (코스피·코스닥)")
    q = st.text_input("종목명 또는 6자리 코드", key=f"q_stock_{st.session_state.get('reset_n', 0)}",
                      placeholder="예: 삼성전자, 에코프로, 005930")
    if not q.strip():
        return
    cands = search_stocks(q)
    if not cands and re.fullmatch(r'\d{6}', q.strip()):
        b = get_basic(q.strip())
        if b.get('name'):
            cands = [{'code': q.strip(), 'name': b['name'], 'market': b.get('market') or 'KOSPI'}]
    if not cands:
        st.info("검색 결과가 없습니다. (ETF·해외 종목은 지원하지 않습니다)")
        return
    labels = {f"{c['name']} ({c['code']}·{c['market']})": c for c in cands}
    pick = st.selectbox("검색 결과", list(labels.keys()), key=f"sr_pick_{st.session_state.get('reset_n', 0)}")
    c = labels[pick]
    if st.button("📋 종합 분석", type="primary", key="sr_go"):
        with st.spinner("재무·컨센서스·기술 지표를 모으는 중..."):
            rep, err = analyze_code(c['code'], c['name'], c['market'])
        if err:
            st.error(err)
        else:
            st.session_state['search_res'] = rep

    rep = st.session_state.get('search_res')
    if rep and rep['code'] == c['code']:
        if rep.get('mode') != mode:
            st.info(f"이 결과는 '{rep['mode']}' 모드로 계산됐습니다. 현재 모드({mode})로 보려면 다시 '종합 분석'을 누르세요.")
        render_stock_report(rep['code'], rep['ev'], rep['d'], rep['tech'])
        st.markdown("---")
        st.markdown("**📂 이 종목을 포트폴리오에 담기**")
        render_pick_form({rep['code']: rep['ev']}, f"search_{rep['code']}")


# =========================
# UI
# =========================
rn = st.session_state.get('reset_n', 0)   # '처음으로'/'선택 해제' 시 증가 → 표 선택 상태 초기화용 키

st.title("🎯 AI 투자 전략 엔진")
st.markdown(f"##### `{APP_VERSION}` · Investment Strategy Dashboard")


st.sidebar.markdown("### ⚙️ 설정")
period_option = st.sidebar.selectbox(
    "📅 리포트 기간",
    ["최근 1주일", "최근 2주일", "최근 1개월"],
    index=0
)
PERIOD_DAYS = {"최근 1주일": 7, "최근 2주일": 14, "최근 1개월": 30}
selected_days = PERIOD_DAYS[period_option]

threshold = st.sidebar.slider(
    '괴리율 하한선 (%)', 0, 50, 15,
    help="목표가 대비 현재가가 이 수치 이상 저평가된 종목만 표시"
)

excl_spac_pref = st.sidebar.checkbox("스팩·우선주 제외", value=True,
                                      help="데이터가 부실하거나 분석 의미가 낮은 종목을 후보에서 뺍니다")
min_mcap = st.sidebar.slider("최소 시가총액 (억원)", 0, 5000, 1000, 100,
                             help="너무 작은 종목은 데이터·유동성이 불안정해 후보에서 제외 (0=제한 없음)")

show_debug = st.sidebar.checkbox("진단 모드", value=False,
                            help="데이터 수집 단계의 상세 정보 표시")

st.sidebar.markdown("---")
st.sidebar.markdown("**🧭 내 매수 규칙** (판정 기준)")
mode = st.sidebar.radio(
    "판정 모드", [MODE_SWING, MODE_LONG],
    help="단기·스윙: 추세 하락이면 대기 / 중장기: 시장 요인 하락 + 체력 양호 시 분할매수")
min_score = st.sidebar.slider("최소 종합 점수 (100점)", 30, 90, 60,
                              help="이 점수 미만이면 '제외'. 오늘 후보 점수 분포를 보고 조정하세요.")
earn_wait = st.sidebar.slider("실적 발표 N일 전엔 대기", 0, 10, 5,
                             help="실적 발표가 N일 이내면 점수가 높아도 '대기'로 판정 (0=사용 안 함)")
RULES = {'min_score': min_score, 'min_rr': 2.0, 'max_loss': 10,
         'n_split': 3, 'split_step': 7, 'max_weight': 5, 'earn_wait': earn_wait,
         'swing_cap': 20}
if mode == MODE_SWING:
    RULES['swing_cap'] = st.sidebar.slider("스윙 목표 수익률 상한 (%)", 5, 50, 20,
                                           help="증권사 목표가는 보통 12개월 기준이라, 1주~1달 스윙에는 이 수익률까지만 기대수익으로 계산")
    RULES['min_rr'] = st.sidebar.slider("최소 손익비", 1.0, 4.0, 2.0, 0.5,
                                        help="(목표가-현재가)/(현재가-손절가). 미달이면 '대기'")
    RULES['max_loss'] = st.sidebar.slider("최대 손절폭 (%)", 3, 20, 10,
                                          help="손절가까지 하락률이 이보다 크면 '대기'")
else:
    RULES['n_split'] = st.sidebar.slider("분할 횟수", 2, 5, 3)
    RULES['split_step'] = st.sidebar.slider("분할 간격 (%)", 3, 15, 7,
                                            help="직전 매수가보다 이만큼 더 내려가면 다음 분할")
    RULES['max_weight'] = st.sidebar.slider("종목당 최대 투입 비중 (%)", 1, 20, 5,
                                            help="전체 투자금 대비. 분할 횟수로 균등 배분")


page_tabs = st.tabs(["🌐 오늘의 시장 신호", "🔥 종목 스크리닝", "📂 내 포트폴리오"])

with page_tabs[0]:
    render_market_banner()

with page_tabs[1]:
    screen_tabs = st.tabs(["🚀 오늘의 핫픽", "🔍 종목 검색"])
    with screen_tabs[0]:
        # ---------- 1) 수집·계산: 버튼 클릭 시에만 실행, 결과는 session_state에 보관 ----------
        # (표 행 클릭 등으로 재실행돼도 결과가 사라지지 않도록 렌더링과 분리)
        bc1, bc2 = st.columns([3, 1])
        run_extract = bc1.button('🚀 오늘의 핫픽 추출', type="primary", key="extract_btn")
        if bc2.button("🔄 스크리닝 초기화", key="reset_screen",
                      help="추출 결과·체크한 종목·필터·검색 내용을 모두 지우고 처음 상태로"):
            st.session_state.pop('result', None)
            st.session_state.pop('search_res', None)
            st.session_state['reset_n'] = rn + 1
            st.rerun()
        if run_extract:
            progress = st.progress(0)
            status = st.empty()

            status.text("1/5 · 시세 수집 중 (KOSPI + KOSDAQ)...")
            price_df, price_errors, diag_json = get_kr_market_prices()
            progress.progress(20)

            status.text(f"2/5 · 한경 컨센서스 리포트 수집 중 ({period_option})...")
            research_df, research_errors = get_hankyung_research(days=selected_days)
            progress.progress(40)

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

            status.text("3/5 · 종목 매칭 및 괴리율 계산 중...")

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

                # 종목별 그룹 정렬 (종목평균괴리율 기준)
                matched['종목평균괴리율'] = matched.groupby('종목코드')['괴리율(%)'].transform('mean')

                hot_picks = matched[matched['괴리율(%)'] >= threshold].copy()
                n_before = hot_picks['종목코드'].nunique()
                if excl_spac_pref and not hot_picks.empty:
                    is_spac = hot_picks['종목명_x'].astype(str).str.contains('스팩')
                    is_pref = ~hot_picks['종목코드'].astype(str).str.endswith('0')      # 우선주는 코드 끝자리가 0이 아님
                    hot_picks = hot_picks[~(is_spac | is_pref)].copy()
                if min_mcap > 0 and not hot_picks.empty and '시가총액' in hot_picks.columns:
                    mcap = hot_picks['시가총액'].map(parse_mcap_eok)
                    hot_picks = hot_picks[mcap.isna() | (mcap >= min_mcap)].copy()
                n_filtered = n_before - hot_picks['종목코드'].nunique()
                hot_picks = hot_picks.sort_values(
                    by=['종목평균괴리율', '종목코드', '괴리율(%)'],
                    ascending=[False, True, False]
                )

                # 종목별 컨센서스(리포트 평균 목표가·건수) — 판정에 사용
                consensus = (matched.groupby('종목코드')['적정가격']
                             .agg(['mean', 'count']).to_dict('index'))

                tech_signals, diag_sample, details_map, hist_map, reports_map = {}, None, {}, {}, {}
                if not hot_picks.empty:
                    unique_items = hot_picks[['종목코드', '시장']].drop_duplicates().to_dict('records')

                    status.text("4/5 · 기술적 시그널 수집 중...")
                    tech_signals, diag_sample = get_technical_signals_batch(unique_items)
                    progress.progress(65)

                    status.text("5/5 · 재무·컨센서스·동일업종 데이터 수집 중... (처음엔 1~2분 걸릴 수 있음)")
                    for item in unique_items:
                        c = item['종목코드']
                        d = dict(get_stock_details(c))
                        d['업종PER'] = get_peer_fper(tuple(d.get('peers') or ()))
                        details_map[c] = d

                    # 목표가 스냅샷 저장(Supabase 연결 시) → 연속 하향 감지에 사용
                    snapshot_targets([{'code': c, 'target': dd.get('목표주가'), 'n_reports': dd.get('리포트수'),
                                       'recomm': dd.get('의견평균')} for c, dd in details_map.items()])
                    hist_map = get_target_hist(list(details_map.keys()))

                    # 종목별 증권사 리포트(목표가·의견·PDF) — 종합 리포트의 '증권사 목표가' 표에 사용
                    for item in unique_items:
                        c = item['종목코드']
                        sub = matched[matched['종목코드'] == c].sort_values('작성일', ascending=False)
                        reports_map[c] = [{'일자': r['작성일'], '증권사': r['작성기관'], '목표가': r['적정가격'],
                                           '의견': r['투자의견'], '제목': r['리포트제목'], '리포트': r['PDF링크']}
                                          for _, r in sub.iterrows()]

                    # 정렬이 정확하도록 숫자는 숫자 열로, 이모지는 별도 '신호' 열로 분리
                    num_cols = {k: [] for k in ('PER', '추정PER', 'PBR', '영익률(%)', 'ROE(%)', 'RSI', '볼린저(%)')}
                    str_cols = {k: [] for k in ('매출액', '영업이익', '신호', '추세', '종합 시그널', '근거', '차트')}
                    for _, row in hot_picks.iterrows():
                        code = row['종목코드']
                        d = details_map[code]
                        tech = tech_signals.get(code, {})
                        tn = tech.get('_num') or {}
                        per, pbr, opm = d.get('PER'), d.get('PBR'), d.get('영업이익률')

                        num_cols['PER'].append(per)
                        num_cols['추정PER'].append(d.get('추정PER'))
                        num_cols['PBR'].append(pbr)
                        num_cols['영익률(%)'].append(opm)
                        num_cols['ROE(%)'].append(d.get('ROE'))
                        num_cols['RSI'].append(tn.get('rsi'))
                        num_cols['볼린저(%)'].append(tn['bb_pct'] * 100 if tn.get('bb_pct') is not None else None)

                        sig = classify_gap(row['괴리율(%)'])
                        sig += classify_per(per) if (per is not None and per > 0) else '❤️'
                        sig += classify_pbr(pbr) if pbr else '➖'
                        sig += classify_opmargin(opm) if opm is not None else '➖'
                        str_cols['매출액'].append(d.get('매출액', '-'))
                        str_cols['영업이익'].append(d.get('영업이익', '-'))
                        str_cols['신호'].append(sig)
                        str_cols['추세'].append(tech.get('추세', '-'))
                        str_cols['종합 시그널'].append(tech.get('종합 시그널', '-'))
                        str_cols['근거'].append(tech.get('근거', '-'))
                        str_cols['차트'].append(f"https://m.stock.naver.com/fchart/domestic/stock/{code}")

                    for k, v in num_cols.items():
                        hot_picks[k] = pd.to_numeric(pd.Series(v, index=hot_picks.index), errors='coerce')
                    for k, v in str_cols.items():
                        hot_picks[k] = v

                if not hot_picks.empty:
                    hot_picks['등락률'] = pd.to_numeric(hot_picks['등락률'], errors='coerce')
                    if '시가총액' in hot_picks.columns:
                        hot_picks['시총(억)'] = hot_picks['시가총액'].map(parse_mcap_eok)

                progress.progress(100)
                time.sleep(0.3)
                progress.empty(); status.empty()

                st.session_state['result'] = {
                    'hot_picks': hot_picks, 'consensus': consensus,
                    'details_map': details_map, 'tech_signals': tech_signals,
                    'diag_sample': diag_sample, 'n_reports': len(research_df), 'hist_map': hist_map,
                    'n_filtered': n_filtered, 'reports_map': reports_map,
                    'n_matched': len(matched), 'threshold': threshold,
                    'period': period_option, 'at': datetime.now().strftime('%Y-%m-%d %H:%M'),
                }

        # ---------- 2) 렌더링: session_state의 결과를 매 실행마다 그림 ----------
        res = st.session_state.get('result')
        if res:
            hot_picks = res['hot_picks']
            consensus, details_map, tech_signals = res['consensus'], res['details_map'], res['tech_signals']
            diag_sample, matched_n = res['diag_sample'], res['n_matched']
            hist_map = res.get('hist_map', {})
            reports_map = res.get('reports_map', {})

            if res.get('n_filtered'):
                st.caption(f"🧹 스팩·우선주·소형주 필터로 {res['n_filtered']}종목을 후보에서 제외했습니다 (사이드바에서 조정)")
            st.caption(f"🕒 추출 시각 {res['at']} · 리포트 기간 {res['period']} · 괴리율 하한 {res['threshold']}% "
                       f"(판정 규칙은 사이드바에서 바꾸면 즉시 반영, 추출 조건은 다시 추출해야 반영)")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("리서치 리포트", f"{res['n_reports']}건")
            c2.metric("시세 매칭", f"{matched_n}건")
            c3.metric(f"괴리율 ≥ {res['threshold']}%", f"{len(hot_picks)}건")
            c4.metric("최고 괴리율",
                      f"{hot_picks['괴리율(%)'].max():.1f}%" if not hot_picks.empty else "-")

            st.markdown("---")

            if not hot_picks.empty:
                if not YF_AVAILABLE:
                    st.warning("⚠️ `yfinance` 패키지가 설치되지 않아 기술적 분석이 생략되었습니다.")

                # 종목별 판정 계산 (규칙은 현재 사이드바 값 기준)
                verdicts = {}
                idx_dd = get_index_drawdowns() if mode == MODE_LONG else {}
                for code in hot_picks['종목코드'].unique():
                    r0 = hot_picks[hot_picks['종목코드'] == code].iloc[0]
                    cons = consensus.get(code, {})
                    if not cons or not r0['현재가']:
                        continue
                    ev = evaluate(mode, float(r0['현재가']), float(cons['mean']), int(cons['count']),
                                  details_map.get(code, {}), tech_signals.get(code, {}).get('_num'),
                                  idx_dd.get(r0['시장']), RULES, trend=target_trend(hist_map.get(code)))
                    ev['name'] = r0['종목명_x']; ev['market'] = r0['시장']
                    ev['trend'] = target_trend(hist_map.get(code))
                    verdicts[code] = ev

                st.success(f"✅ 오늘의 핫픽 {len(hot_picks)}건 ({len(verdicts)}종목) 추출 완료")

                # 탭 분리: 판정(결론) → 펀더멘털 → 기술적
                tab0, tab1, tab2 = st.tabs(["🎯 매수 판정", "📊 펀더멘털 분석", "📈 기술적 분석"])

                with tab0:
                    if mode == MODE_LONG:
                        st.caption(
                            f"**중장기 분할매수 모드** — 점수 ≥ {RULES['min_score']} · {RULES['n_split']}회 분할(간격 {RULES['split_step']}%) · "
                            f"종목당 최대 {RULES['max_weight']}%. 하락이 시장 요인 중심이고 체력이 양호할 때만 나눠서 매수합니다."
                        )
                        if not idx_dd:
                            st.warning("지수 낙폭 데이터를 가져오지 못해 '시장 요인 vs 개별 악재' 구분이 제한됩니다.")
                        else:
                            st.caption("📉 지수 6개월 고점 대비: " + " · ".join(f"{k} {v:.1f}%" for k, v in idx_dd.items()))
                    else:
                        st.caption(
                            f"**단기·스윙 모드** — 점수 ≥ {RULES['min_score']} · 손익비 ≥ {RULES['min_rr']} · 손절폭 ≤ {RULES['max_loss']}% "
                            "(목표가=증권사 평균, 손절가=최근 20일 저가-2%)"
                        )

                    order = {VERDICT_BUY: 0, VERDICT_WAIT: 1, VERDICT_SKIP: 2}
                    rows = []
                    for code, ev in sorted(verdicts.items(), key=lambda kv: (order[kv[1]['verdict']], -kv[1]['score'])):
                        rows.append({
                            '종목코드': code, '종목명': ev['name'], '시장': ev['market'],
                            '판정': ev['verdict'], '종합점수': ev['score'],
                            '밸류': ev['cat_pct']['밸류에이션'], '성장': ev['cat_pct']['성장·전망'],
                            '체력': ev['cat_pct']['수익성·체력'], '타이밍': ev['cat_pct']['타이밍'],
                            '현재가': int(ev['cur']),
                            '매수 가능가': int(ev['wait_price']) if ev['verdict'] == VERDICT_WAIT and ev['wait_price'] else None,
                            '기대수익(%)': round(ev['upside_pct'], 0) if ev.get('upside_pct') is not None else None,
                            '위험(%)': round(-ev['loss_pct'], 1) if ev['loss_pct'] else None,
                            '손익비': round(ev['rr'], 1) if ev['rr'] is not None else None,
                            '평단(분할)': int(ev['plan']['avg']) if ev.get('plan') and ev['verdict'] == VERDICT_BUY else None,
                            '사유': ev['reason'],
                        })
                    vdf = pd.DataFrame(rows).dropna(axis=1, how='all')   # 모드에 안 맞는 빈 열 제거

                    n_buy = sum(1 for e in verdicts.values() if e['verdict'] == VERDICT_BUY)
                    n_wait = sum(1 for e in verdicts.values() if e['verdict'] == VERDICT_WAIT)
                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("💚 매수 적정", f"{n_buy}종목")
                    m2.metric("💛 대기", f"{n_wait}종목")
                    m3.metric("⚪ 제외", f"{len(verdicts) - n_buy - n_wait}종목")
                    scores = sorted(e['score'] for e in verdicts.values())
                    if scores:
                        m4.metric("점수 분포", f"최고 {scores[-1]}", f"중앙 {scores[len(scores) // 2]}", delta_color="off")
                    if n_buy == 0:
                        st.info("오늘은 규칙을 통과한 종목이 없습니다. **안 사는 것도 결정입니다.** "
                                "💛 대기 종목은 '매수 가능가'까지 내려오면 다시 보세요.")

                    # 필터: 같은 성격끼리 모아 보기
                    f1, f2, f3 = st.columns(3)
                    sel_v = f1.multiselect("판정 필터", [VERDICT_BUY, VERDICT_WAIT, VERDICT_SKIP],
                                           default=[VERDICT_BUY, VERDICT_WAIT, VERDICT_SKIP], key=f"flt_v_{rn}")
                    sel_m = f2.multiselect("시장 필터", ['KOSPI', 'KOSDAQ'], default=['KOSPI', 'KOSDAQ'], key=f"flt_m_{rn}")
                    min_view = f3.slider("표시 최소 종합점수", 0, 100, 0, key=f"flt_s_{rn}")
                    vdf_view = vdf[vdf['판정'].isin(sel_v) & vdf['시장'].isin(sel_m)
                                   & (vdf['종합점수'] >= min_view)].reset_index(drop=True)

                    st.caption("👆 왼쪽 체크박스로 **여러 종목**을 고를 수 있습니다. 열 제목을 누르면 정렬됩니다. "
                               "체크만으로는 저장되지 않고, 아래 '📂 포트폴리오에 담기'를 눌러야 저장됩니다.")
                    if vdf_view.empty:
                        st.info("필터 조건에 맞는 종목이 없습니다.")
                        event0 = None
                    else:
                        event0 = st.dataframe(
                            vdf_view, hide_index=True, width=1400,
                            column_config={
                                "종합점수": st.column_config.ProgressColumn("종합점수", min_value=0, max_value=100, format="%d"),
                                "밸류": st.column_config.NumberColumn("밸류(%)", format="%d%%", help="70%↑ 상 · 40~70% 중 · 40%↓ 하"),
                                "성장": st.column_config.NumberColumn("성장(%)", format="%d%%"),
                                "체력": st.column_config.NumberColumn("체력(%)", format="%d%%"),
                                "타이밍": st.column_config.NumberColumn("타이밍(%)", format="%d%%"),
                                "현재가": st.column_config.NumberColumn("현재가", format="%,d"),
                                "매수 가능가": st.column_config.NumberColumn("매수 가능가", format="%,d",
                                                                        help="💛 대기 종목이 이 가격 이하로 내려오면 조건 충족"),
                                "기대수익(%)": st.column_config.NumberColumn("기대수익(%)", format="%d",
                                                                        help="스윙은 목표 수익률 상한 적용, 중장기는 증권사 목표가 기준"),
                                "위험(%)": st.column_config.NumberColumn("위험(%)", format="%.1f",
                                                                      help="현재가에서 손절가까지 하락률"),
                                "손익비": st.column_config.NumberColumn("손익비", format="%.1f",
                                                                     help="기대수익 ÷ 위험"),
                                "평단(분할)": st.column_config.NumberColumn("평단(분할)", format="%,d",
                                                                        help="분할매수 완료 시 예상 평균단가"),
                            },
                            on_select="rerun", selection_mode="multi-row", key=f"verdict_table_{mode}_{rn}"
                        )

                    sel_rows = event0.selection.rows if event0 is not None else []
                    if sel_rows:
                        codes = [vdf_view.iloc[i]['종목코드'] for i in sel_rows]
                        h_a, h_b = st.columns([4, 1])
                        h_a.markdown(f"**✔ {len(codes)}종목 선택됨** — 아래에 종목별 종합 리포트가 열립니다.")
                        if h_b.button("↩️ 선택 해제", key=f"clr_btn_{rn}"):
                            st.session_state['reset_n'] = rn + 1
                            st.rerun()

                        for j, c in enumerate(codes):
                            ev = verdicts[c]
                            with st.expander(f"📌 {ev['name']} · {ev['market']} — {ev['verdict']} · {ev['score']}점",
                                             expanded=(j == 0)):
                                render_stock_report(c, ev, details_map.get(c), tech_signals.get(c), reports_map.get(c))

                        st.markdown("**📂 선택한 종목을 포트폴리오에 담기** (컨셉과 함께 저장)")
                        render_pick_form({c: verdicts[c] for c in codes}, f"picks_{rn}")

                with tab1:
                    st.caption("👆 숫자 열은 모두 숫자로 저장돼 **열 제목을 눌러 정확히 정렬**됩니다. '신호' = 괴리율·PER·PBR·영익률 순 신호등.")

                    display_cols1 = ['작성일', '종목명_x', '시장', '현재가', '등락률', '적정가격',
                                    '괴리율(%)', '시총(억)', 'PER', '추정PER', 'PBR', '영익률(%)', 'ROE(%)', '신호',
                                    '매출액', '영업이익', '투자의견', '작성기관', '리포트제목', 'PDF링크']
                    df1 = hot_picks[[c for c in display_cols1 if c in hot_picks.columns]].rename(
                        columns={'종목명_x': '종목명', '적정가격': '목표가'}).reset_index(drop=True)

                    # 카드 표시 영역 (선택 시 채워짐)
                    card1 = st.container()

                    event1 = st.dataframe(
                        df1,
                        column_config={
                            "현재가": st.column_config.NumberColumn("현재가", format="%,d"),
                            "목표가": st.column_config.NumberColumn("목표가", format="%,d"),
                            "등락률": st.column_config.NumberColumn("등락률(%)", format="%.2f"),
                            "괴리율(%)": st.column_config.NumberColumn("괴리율(%)", format="%.1f"),
                            "시총(억)": st.column_config.NumberColumn("시총(억)", format="%,d"),
                            "PER": st.column_config.NumberColumn("PER", format="%.1f"),
                            "추정PER": st.column_config.NumberColumn("추정PER", format="%.1f", help="컨센서스 기준 포워드 PER"),
                            "PBR": st.column_config.NumberColumn("PBR", format="%.2f"),
                            "영익률(%)": st.column_config.NumberColumn("영익률(%)", format="%.1f"),
                            "ROE(%)": st.column_config.NumberColumn("ROE(%)", format="%.1f"),
                            "PDF링크": st.column_config.LinkColumn("리포트", display_text="📄")
                        },
                        hide_index=True,
                        width=1400,
                        on_select="rerun",
                        selection_mode="single-row",
                        key=f"fund_table_{rn}"
                    )

                    if event1.selection.rows:
                        sel = df1.iloc[event1.selection.rows[0]]
                        with card1:
                            st.markdown(f"#### 📌 {sel.get('종목명', '-')}  ·  {sel.get('시장', '')}")
                            m1, m2, m3, m4 = st.columns(4)
                            cur = sel.get('현재가'); tgt = sel.get('목표가'); gp = sel.get('괴리율(%)')
                            m1.metric("현재가", f"{int(cur):,}원" if pd.notnull(cur) else "-")
                            m2.metric("목표가", f"{int(tgt):,}원" if pd.notnull(tgt) else "-")
                            m3.metric("괴리율", f"{gp:.1f}%" if pd.notnull(gp) else "-")
                            m4.metric("투자의견", str(sel.get('투자의견', '-')))

                            def fmt(v, suf=''):
                                return f"{v:.1f}{suf}" if pd.notnull(v) else '-'
                            n1, n2, n3, n4 = st.columns(4)
                            n1.markdown(f"**PER** {fmt(sel.get('PER'))} · **추정PER** {fmt(sel.get('추정PER'))}")
                            n2.markdown(f"**PBR** {fmt(sel.get('PBR'))}")
                            n3.markdown(f"**영익률** {fmt(sel.get('영익률(%)'), '%')} · **ROE** {fmt(sel.get('ROE(%)'), '%')}")
                            n4.markdown(f"**신호** {sel.get('신호', '-')}")
                            st.markdown(
                                f"**시총** {fmt(sel.get('시총(억)'))}억  ·  "
                                f"**매출액** {sel.get('매출액', '-')}  ·  "
                                f"**영업이익** {sel.get('영업이익', '-')}"
                            )
                            st.markdown(f"**리포트** {sel.get('리포트제목', '-')}  ·  _{sel.get('작성기관', '')}_")
                            st.markdown("---")

                with tab2:
                    st.caption("👆 RSI·볼린저(%)는 숫자 열이라 정렬이 정확합니다. 볼린저(%)는 밴드 안 위치(0=하단, 100=상단).")

                    display_cols2 = ['작성일', '종목명_x', '시장', '현재가', '적정가격', '괴리율(%)', 'RSI', '볼린저(%)',
                                     '추세', '종합 시그널', '근거', '차트']
                    df2 = hot_picks[[c for c in display_cols2 if c in hot_picks.columns]].rename(
                        columns={'종목명_x': '종목명', '적정가격': '목표가'}).reset_index(drop=True)

                    # 카드 표시 영역
                    card2 = st.container()

                    def color_signal_bg(val):
                        s = str(val)
                        if '💚' in s: return 'background-color: #d4edda; color: black;'
                        if '💛' in s: return 'background-color: #fff3cd; color: black;'
                        if '❤️' in s: return 'background-color: #f8d7da; color: black;'
                        return ''

                    styled = df2.style.map(color_signal_bg, subset=['종합 시그널'])
                    event2 = st.dataframe(
                        styled,
                        hide_index=True,
                        width=1400,
                        column_config={
                            "현재가": st.column_config.NumberColumn("현재가", format="%,d"),
                            "목표가": st.column_config.NumberColumn("목표가", format="%,d"),
                            "괴리율(%)": st.column_config.NumberColumn("괴리율(%)", format="%.1f"),
                            "RSI": st.column_config.NumberColumn("RSI", format="%.0f"),
                            "볼린저(%)": st.column_config.NumberColumn("볼린저(%)", format="%.0f"),
                            "차트": st.column_config.LinkColumn(
                                "차트",
                                display_text="📈 보기",
                                help="네이버 증권 차트로 이동"
                            )
                        },
                        on_select="rerun",
                        selection_mode="single-row",
                        key=f"tech_table_{rn}"
                    )

                    if event2.selection.rows:
                        sel = df2.iloc[event2.selection.rows[0]]
                        with card2:
                            st.markdown(f"#### 📌 {sel.get('종목명', '-')}  ·  {sel.get('시장', '')}")
                            m1, m2, m3 = st.columns(3)
                            cur = sel.get('현재가'); tgt = sel.get('목표가'); gp = sel.get('괴리율(%)')
                            m1.metric("현재가", f"{int(cur):,}원" if pd.notnull(cur) else "-")
                            m2.metric("목표가", f"{int(tgt):,}원" if pd.notnull(tgt) else "-")
                            m3.metric("괴리율", f"{gp:.1f}%" if pd.notnull(gp) else "-")
                            rsi_v, bb_v = sel.get('RSI'), sel.get('볼린저(%)')
                            st.markdown(
                                f"**RSI** {f'{rsi_v:.0f}' if pd.notnull(rsi_v) else '-'}  ·  "
                                f"**볼린저** {f'{bb_v:.0f}%' if pd.notnull(bb_v) else '-'}  ·  "
                                f"**추세** {sel.get('추세', '-')}"
                            )
                            st.markdown(f"### {sel.get('종합 시그널', '-')}")
                            st.caption(f"근거: {sel.get('근거', '-')}")
                            chart_url = sel.get('차트', '')
                            if chart_url:
                                st.markdown(f"[📈 네이버 차트 보기]({chart_url})")
                            st.markdown("---")

                with st.expander("📖 판정 기준 · 용어 설명 (맨 아래)", expanded=False):
                    st.markdown("""
                    ### 🎯 판정은 3단계로 정해집니다
                    1. **종합 점수(100점)** — 종목의 질. 기준(사이드바, 기본 60점) 미만이면 ⚪ 제외
                    2. **가격·시장 조건** — 점수가 통과해도 아래 중 하나라도 걸리면 💛 대기
                       - 스윙: 손익비 부족, 손절폭 과다, 추세 하락, 과열 / 중장기: 아직 충분히 안 눌림
                       - 실적 발표 N일 전, 목표가 3회 연속 하향
                    3. 모두 통과하면 💚 매수 적정

                    ### 📊 종합 점수 구성 (100점) — 표에서는 분류별 %와 상/중/하로 표시
                    | 분류 | 배점 | 보는 것 |
                    |---|---|---|
                    | **밸류에이션** | 25 | 추정PER(포워드)을 동일업종 중앙값과 비교, PBR |
                    | **성장·전망** | 35 | 영업이익·EPS 추정 성장, 목표가 괴리율, 애널리스트 의견 (미래 지표라 비중 최대) |
                    | **수익성·체력** | 20 | 영업이익률 또는 ROE(대기업 고려해 8%/10%를 양호로), 부채비율 |
                    | **타이밍** | 20 | 스윙: 추세·볼린저·RSI+스토캐스틱 이중확인·수급 / 중장기: 눌림·하락원인·이중확인·수급 |
                    - **상/중/하**: 득점률 70% 이상 🟢 상 · 40~70% 🟡 중 · 40% 미만 🔴 하
                    - **이중 확인**: RSI와 스토캐스틱이 *동시에* 과매도일 때만 만점
                    - **수급**: 최근 5거래일 외국인·기관 순매수 (둘 다 사면 만점)
                    - **하드 게이트**: 영업이익 적자 전망은 점수와 무관하게 ⚪ 제외 / 중장기는 '개별 악재' 의심 시 제외

                    ### 💡 용어
                    - **괴리율** = (증권사 목표가 − 현재가) ÷ 현재가. 목표가까지 *얼마나 남았나* (보통 12개월 기준)
                    - **기대수익** = 스윙 모드에서 괴리율에 **목표 수익률 상한(기본 20%)**을 씌운 값. 1주~1달 보유에 현실적인 목표
                    - **감수할 위험** = 현재가에서 손절가까지 하락률 (손절가 = 최근 20일 저가 −2%)
                    - **손익비** = 기대수익 ÷ 감수할 위험. 2.0이면 "1을 잃을 각오로 2를 노린다". 괴리율은 *얼마나 오를 수 있나*, 손익비는 *위험 대비 남는 장사인가*
                    - **매수 가능가** = 💛 대기 종목이 *이 가격 이하로 내려오면 조건을 충족*한다는 가격 (지금 사지 말고 기다릴 기준선)

                    ### 🚦 표의 '신호' 열 (펀더멘털 탭): 괴리율·PER·PBR·영익률 순 💚좋음 💛보통 ❤️주의 ➖없음
                    """)

                csv_cols = ['작성일', '종목명_x', '시장', '현재가', '등락률', '적정가격', '괴리율(%)', '시총(억)', 'PER', '추정PER', 'PBR', '영익률(%)', 'ROE(%)', '신호', '매출액', '영업이익', 'RSI', '볼린저(%)', '추세', '종합 시그널', '근거', '차트', '투자의견', '작성기관', '리포트제목', 'PDF링크']
                csv_df = hot_picks[[c for c in csv_cols if c in hot_picks.columns]].rename(columns={'종목명_x': '종목명'})

                csv = csv_df.to_csv(index=False).encode('utf-8-sig')
                st.download_button(
                    "📥 CSV 다운로드", csv,
                    file_name=f"hotpicks_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime='text/csv'
                )
            else:
                st.warning(
                    f"괴리율 {res['threshold']}% 이상 종목이 없습니다. "
                    f"슬라이더를 낮춰 보세요. (시세 매칭 성공: {matched_n}건)"
                )



    with screen_tabs[1]:
        render_stock_search()

with page_tabs[2]:
    render_portfolio()

st.markdown("---")
col_a, col_b = st.columns([2, 1])
with col_a:
    st.caption("📊 데이터 출처: 한경 컨센서스 + 네이버 금융 · 모든 투자 책임은 본인에게 있습니다.")
with col_b:
    st.caption(f"**Designed by Charles Kim** · {APP_VERSION} · 2026")
