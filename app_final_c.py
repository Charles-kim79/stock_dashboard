import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import re
import time
import hmac

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
PASSWORD = str(st.secrets.get("APP_PASSWORD", "")).strip()
MAX_LOGIN_TRIES = 5

if "auth" not in st.session_state:
    st.session_state.auth = False
if "login_fail" not in st.session_state:
    st.session_state.login_fail = 0

if not st.session_state.auth:
    st.markdown("## 🔒 AI 투자 전략 엔진")
    st.caption("Designed by Charles Kim · v1.1")
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
            
            low20 = df['Low'].tail(20).min()
            bb_pct = (close - lower) / (upper - lower) if upper != lower else None
            signals[code] = {
                '_num': {
                    'close': float(close), 'ma20': float(ma20), 'bb_lower': float(lower),
                    'low20': float(low20), 'slope': float(slope),
                    'high6m': float(df['Close'].max()),
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
# 매수 판정 엔진 (확신 점수 + 사전 약속 가격)
# =========================
VERDICT_BUY = '💚 매수 적정'
VERDICT_WAIT = '💛 대기'
VERDICT_SKIP = '⚪ 제외'
MODE_SWING = '단기·스윙'
MODE_LONG = '중장기 분할매수'


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


def evaluate_stock_long(cur, target, n_reports, per, pbr, opm, num, mkt_dd, params):
    """중장기 분할매수 판정: 하락 원인(시장 vs 개별) + 체력 확인 + 분할 계획

    mkt_dd: 해당 시장 지수의 6개월 고점 대비 낙폭(%, 음수) 또는 None
    """
    gap = (target - cur) / cur * 100 if cur else None
    high = num.get('high6m') if num else None
    stock_dd = (cur / high - 1) * 100 if high else None          # 음수
    s_depth = -stock_dd if stock_dd is not None else None        # 양수 낙폭
    m_depth = -mkt_dd if mkt_dd is not None else None

    dipped = s_depth is not None and s_depth >= 15
    if s_depth is None:
        cause, cause_txt = None, '데이터 없음'
    elif s_depth < 10:
        cause, cause_txt = 'none', f'낙폭 {s_depth:.0f}% (조정 미미)'
    elif m_depth is None:
        cause, cause_txt = None, f'종목 낙폭 {s_depth:.0f}% (지수 데이터 없음)'
    elif s_depth <= max(m_depth * 1.5, m_depth + 5):
        cause, cause_txt = 'market', f'종목 -{s_depth:.0f}% vs 지수 -{m_depth:.0f}% → 시장 요인 중심'
    else:
        cause, cause_txt = 'stock', f'종목 -{s_depth:.0f}% vs 지수 -{m_depth:.0f}% → 개별 악재 가능성'

    def has(v):
        return v is not None

    checks = [
        ('고점 대비 -15% 이상 눌림', dipped if has(s_depth) else None,
         f"-{s_depth:.0f}%" if has(s_depth) else '데이터 없음'),
        ('하락이 시장 요인 중심(개별 악재 아님)', cause in ('market', 'none') if cause else None, cause_txt),
        ('괴리율 30% 이상', gap is not None and gap >= 30, f"{gap:.1f}%" if gap is not None else '-'),
        ('리포트 2건 이상(복수 확인)', n_reports >= 2, f"{n_reports}건"),
        ('흑자(PER>0)', (per > 0) if has(per) else None, f"{per}" if has(per) else '데이터 없음'),
        ('PBR 1.5배 이하', (pbr <= 1.5) if has(pbr) else None, f"{pbr}" if has(pbr) else '데이터 없음'),
        ('영업이익률 5% 이상', (opm >= 5) if has(opm) else None, f"{opm}%" if has(opm) else '데이터 없음'),
    ]
    checks = [(l, (None if p is None else bool(p)), d) for l, p, d in checks]
    score = sum(1 for _, p, _ in checks if p)
    total = len(checks)

    # 분할 계획: 현재가부터 step%씩 내려가며 균등 분할
    n, step = params['n_split'], params['split_step']
    prices = [cur * (1 - step / 100 * i) for i in range(n)]
    avg = sum(prices) / n
    per_tranche = params['max_weight'] / n
    plan = {
        'tranches': [(p, per_tranche) for p in prices], 'avg': avg,
        'upside': (target / avg - 1) * 100 if avg else None,
        'last': prices[-1],
        'review_below': prices[-1] * (1 - step / 100),
    }

    out = {'checks': checks, 'score': score, 'total': total, 'target': target, 'cur': cur,
           'stop': None, 'rr': None, 'loss_pct': None, 'wait_price': None,
           'plan': plan, 'cause_txt': cause_txt, 'mode': MODE_LONG}

    if cause == 'stock':
        out['verdict'] = VERDICT_SKIP
        out['reason'] = f"개별 악재 가능성 — {cause_txt}. 하락 원인(실적·공시)을 확인하기 전엔 매수하지 않음"
    elif score < params['min_score_long']:
        out['verdict'] = VERDICT_SKIP
        out['reason'] = f"확신 점수 {score}/{total} (기준 {params['min_score_long']} 미만)"
    elif not dipped:
        out['verdict'] = VERDICT_WAIT
        out['wait_price'] = high * 0.85 if high else None
        out['reason'] = '아직 충분히 눌리지 않음 — 고점 대비 -15% 구간에서 분할 시작'
    else:
        out['verdict'] = VERDICT_BUY
        out['reason'] = (f"분할매수 시작 · {cause_txt} · 평단 {int(avg):,}원 · "
                         f"목표 상승여력 {plan['upside']:.0f}%")
    return out


def evaluate_stock(cur, target, n_reports, per, pbr, opm, num, params):
    """확신 점수 체크리스트 + 진입/목표/손절/손익비 → 판정(매수 적정/대기/제외)

    cur: 현재가, target: 증권사 목표가 평균, n_reports: 리포트 건수
    num: 기술지표 수치 dict(_num) 또는 None, params: 사이드바 규칙값
    """
    gap = (target - cur) / cur * 100 if cur else None
    num = num or {}
    trend_up = bool(num) and num['close'] > num['ma20'] and num['slope'] > 0.01
    trend_down = bool(num) and num['close'] < num['ma20'] and num['slope'] < -0.01
    bb_pct, rsi = num.get('bb_pct'), num.get('rsi')

    def ok(cond, has_data=True):
        return bool(cond) if has_data else None

    checks = [
        ('괴리율 15% 이상', ok(gap is not None and gap >= 15), f"{gap:.1f}%" if gap is not None else '-'),
        ('괴리율 30% 이상', ok(gap is not None and gap >= 30), f"{gap:.1f}%" if gap is not None else '-'),
        ('리포트 2건 이상(복수 확인)', ok(n_reports >= 2), f"{n_reports}건"),
        ('PER 0~15배(흑자·저평가)', ok(per is not None and 0 < per <= 15, per is not None), f"{per}" if per is not None else '데이터 없음'),
        ('PBR 1.5배 이하', ok(pbr is not None and pbr <= 1.5, pbr is not None), f"{pbr}" if pbr is not None else '데이터 없음'),
        ('영업이익률 10% 이상', ok(opm is not None and opm >= 10, opm is not None), f"{opm}%" if opm is not None else '데이터 없음'),
        ('추세 상승(20일선 위·기울기+)', ok(trend_up, bool(num)), '상승' if trend_up else '하락' if trend_down else '횡보' if num else '데이터 없음'),
        ('볼린저 상단 아님(<70%)', ok(bb_pct is not None and bb_pct < 0.7, bb_pct is not None), f"{bb_pct*100:.0f}%" if bb_pct is not None else '데이터 없음'),
        ('RSI 과매수 아님(<70)', ok(rsi is not None and rsi < 70, rsi is not None), f"{rsi:.0f}" if rsi is not None else '데이터 없음'),
    ]
    score = sum(1 for _, passed, _ in checks if passed)
    total = len(checks)

    out = {'checks': checks, 'score': score, 'total': total, 'target': target, 'cur': cur,
           'stop': None, 'rr': None, 'loss_pct': None, 'wait_price': None}

    # 가격 계획: 손절 = 최근 20일 저가 -2% (이탈 시 가설 훼손), 현재가 -3%보다는 가까이 두지 않음
    if num:
        stop = min(num['low20'] * 0.98, cur * 0.97)
        risk = cur - stop
        out['stop'] = stop
        out['loss_pct'] = risk / cur * 100
        out['rr'] = (target - cur) / risk if risk > 0 else None
        # 손익비 기준을 만족하는 가격 / 최대 손실 허용을 만족하는 가격 중 낮은 쪽
        p_rr = (target + params['min_rr'] * stop) / (1 + params['min_rr'])
        p_loss = stop / (1 - params['max_loss'] / 100)
        wait_price = min(p_rr, p_loss, cur)
        out['wait_price'] = wait_price if wait_price > stop else None

    if score < params['min_score']:
        out['verdict'] = VERDICT_SKIP
        out['reason'] = f"확신 점수 {score}/{total} (기준 {params['min_score']} 미만)"
        return out

    reasons = []
    if not num:
        reasons.append('기술 데이터 부족')
    else:
        if trend_down:
            reasons.append('추세 하락 — 회복 확인 후 재검토')
        if rsi is not None and bb_pct is not None and rsi > 70 and bb_pct > 0.7:
            reasons.append('과열(RSI>70, 볼린저 상단) — 눌림 대기')
        if out['rr'] is None or out['rr'] < params['min_rr']:
            reasons.append(f"손익비 {out['rr']:.1f} < {params['min_rr']}" if out['rr'] is not None else '손익비 계산 불가')
        if out['loss_pct'] is not None and out['loss_pct'] > params['max_loss']:
            reasons.append(f"손절폭 {out['loss_pct']:.1f}% > 허용 {params['max_loss']}%")

    if reasons:
        out['verdict'] = VERDICT_WAIT
        out['reason'] = ' · '.join(reasons)
    else:
        out['verdict'] = VERDICT_BUY
        out['reason'] = f"점수 {score}/{total} · 손익비 {out['rr']:.1f} · 손절폭 {out['loss_pct']:.1f}%"
    return out


# =========================
# 매수 계획 저장소 (Supabase 연결 시 영구 저장, 미연결 시 세션 메모리)
# =========================
def _sb():
    """Supabase 접속 정보(Secrets: SUPABASE_URL, SUPABASE_KEY) — 없으면 None"""
    try:
        url = str(st.secrets.get("SUPABASE_URL", "")).strip().rstrip('/')
        key = str(st.secrets.get("SUPABASE_KEY", "")).strip()
    except Exception:
        return None
    return (url, key) if url and key else None


def _sb_req(method, path, **kw):
    url, key = _sb()
    headers = {'apikey': key, 'Authorization': f'Bearer {key}',
               'Content-Type': 'application/json', 'Prefer': 'return=representation'}
    r = requests.request(method, f"{url}/rest/v1/{path}", headers=headers, timeout=10, **kw)
    r.raise_for_status()
    return r.json() if r.text else []


def get_plans(force=False):
    """저장된 계획 목록(최신순). session_state에 캐시, 쓰기 후 force=True로 갱신"""
    if force or '_plans' not in st.session_state:
        if _sb():
            try:
                st.session_state['_plans'] = _sb_req('GET', 'plans?select=*&order=created_at.desc&limit=300')
            except Exception as e:
                st.error(f"계획 불러오기 실패: {type(e).__name__} - {e}")
                st.session_state['_plans'] = st.session_state.get('_plans', [])
        else:
            st.session_state['_plans'] = st.session_state.get('_plans_mem', [])
    return st.session_state['_plans']


def save_plan(rec):
    """새 계획 저장 → (성공여부, 메시지)"""
    try:
        if _sb():
            _sb_req('POST', 'plans', json=rec)
        else:
            mem = st.session_state.setdefault('_plans_mem', [])
            mem.insert(0, {**rec, 'id': len(mem) + 1, 'created_at': datetime.now().isoformat()})
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


def build_plan_record(code, ev, reason, exception):
    plan = ev.get('plan')
    plan_json = None
    if plan:
        plan_json = {'tranches': [{'price': round(p), 'weight_pct': round(w, 2)} for p, w in plan['tranches']],
                     'avg': round(plan['avg']), 'review_below': round(plan['review_below'])}
    return {
        'code': code, 'name': ev['name'], 'market': ev['market'],
        'mode': ev.get('mode', MODE_SWING),
        'entry_price': round(ev['cur']), 'target_price': round(ev['target']),
        'stop_price': round(ev['stop']) if ev.get('stop') else None,
        'reason': reason.strip(), 'score': f"{ev['score']}/{ev['total']}",
        'plan': plan_json, 'exception': bool(exception), 'status': 'open',
    }


@st.cache_data(ttl=600)
def get_last_price(code, market):
    if not YF_AVAILABLE:
        return None
    try:
        df = yf.download(f"{code}{'.KS' if market == 'KOSPI' else '.KQ'}", period='5d',
                         interval='1d', progress=False, auto_adjust=True)
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


OUTCOMES = ['목표 달성 후 매도', '손절·재검토 후 정리', '계획 없이 임의 청산']


def render_plans():
    """내 매수 계획·일지 섹션 (진행 중 계획 + 종료 기록 + 규율 통계)"""
    st.markdown("---")
    st.subheader("📒 내 매수 계획 · 일지")
    if not _sb():
        st.warning("⚠️ 저장소 미연결 — 지금은 이 브라우저 세션에만 저장돼 새로고침하면 사라집니다. "
                   "영구 저장은 README의 'Supabase 연결' 3단계를 따라 Secrets에 키를 넣으세요.")
    plans = get_plans()
    open_p = [p for p in plans if p.get('status') == 'open']
    closed_p = [p for p in plans if p.get('status') == 'closed']

    if not open_p:
        st.caption("진행 중인 계획이 없습니다. 🎯 매수 판정 탭에서 종목을 선택해 계획을 저장하세요.")
    else:
        rows = []
        for p in open_p:
            price = get_last_price(p['code'], p['market'])
            held = (datetime.now().date() - datetime.strptime(str(p['created_at'])[:10], '%Y-%m-%d').date()).days
            rows.append({
                'ID': p['id'], '종목': p['name'], '모드': p['mode'], '보유일': held,
                '진입가': p['entry_price'], '현재가': int(price) if price else None,
                '목표가': p['target_price'], '손절가': p.get('stop_price'),
                '상태': plan_status(p, price), '매수 이유': p['reason'],
                '예외': '⚠️' if p.get('exception') else '',
            })
        st.dataframe(pd.DataFrame(rows).dropna(axis=1, how='all'), hide_index=True, width=1400,
                     column_config={c: st.column_config.NumberColumn(c, format="%,d")
                                    for c in ('진입가', '현재가', '목표가', '손절가')})

        with st.form("close_plan_form", clear_on_submit=True):
            st.markdown("**✅ 청산 기록**")
            labels = {f"#{p['id']} {p['name']}": p for p in open_p}
            pick = st.selectbox("종목", list(labels.keys()))
            close_price = st.number_input("청산가(원)", min_value=0, step=100)
            outcome = st.selectbox("어떻게 끝났나?", OUTCOMES)
            note = st.text_input("한 줄 복기 (선택)")
            if st.form_submit_button("기록"):
                p = labels[pick]
                if close_price <= 0:
                    st.error("청산가를 입력하세요.")
                else:
                    ok_, msg = update_plan(p['id'], {
                        'status': 'closed', 'closed_at': datetime.now().isoformat(),
                        'close_price': int(close_price), 'outcome': outcome, 'close_note': note.strip()})
                    (st.success if ok_ else st.error)(msg)
                    if ok_:
                        st.rerun()

    if closed_p:
        rets = [(p['close_price'] / p['entry_price'] - 1) * 100 for p in closed_p]
        followed = sum(1 for p in closed_p if p.get('outcome') != OUTCOMES[2])
        exc_rets = [r for r, p in zip(rets, closed_p) if p.get('exception')]
        s1, s2, s3 = st.columns(3)
        s1.metric("종료 건수", f"{len(closed_p)}건")
        s2.metric("계획 준수율", f"{followed / len(closed_p) * 100:.0f}%",
                  help="목표 달성·손절 규칙대로 끝낸 비율 (임의 청산 제외)")
        s3.metric("평균 수익률", f"{sum(rets) / len(rets):+.1f}%",
                  f"예외 매수 평균 {sum(exc_rets) / len(exc_rets):+.1f}%" if exc_rets else None, delta_color="off")
        with st.expander("📜 종료된 기록 보기"):
            st.dataframe(pd.DataFrame([{
                '종목': p['name'], '진입가': p['entry_price'], '청산가': p['close_price'],
                '수익률(%)': round((p['close_price'] / p['entry_price'] - 1) * 100, 1),
                '결과': p.get('outcome'), '매수 이유': p['reason'], '복기': p.get('close_note', ''),
                '예외': '⚠️' if p.get('exception') else '',
            } for p in closed_p]), hide_index=True, width=1400)


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


st.sidebar.markdown("---")
st.sidebar.markdown("**🧭 내 매수 규칙** (판정 기준)")
mode = st.sidebar.radio(
    "판정 모드", [MODE_SWING, MODE_LONG],
    help="단기·스윙: 추세 하락이면 대기 / 중장기: 시장 요인 하락 + 체력 양호 시 분할매수")
if mode == MODE_SWING:
    min_score = st.sidebar.slider("최소 확신 점수 (9점 만점)", 3, 9, 6,
                                  help="이 점수 미만이면 '제외'")
    min_rr = st.sidebar.slider("최소 손익비", 1.0, 4.0, 2.0, 0.5,
                               help="(목표가-현재가)/(현재가-손절가). 미달이면 '대기'")
    max_loss = st.sidebar.slider("최대 손절폭 (%)", 3, 20, 10,
                                 help="손절가까지 하락률이 이보다 크면 '대기'")
else:
    min_score, min_rr, max_loss = 6, 2.0, 10
RULES = {'min_score': min_score, 'min_rr': min_rr, 'max_loss': max_loss}
if mode == MODE_LONG:
    RULES['min_score_long'] = st.sidebar.slider("최소 확신 점수 (7점 만점)", 3, 7, 5,
                                                help="이 점수 미만이면 '제외'")
    RULES['n_split'] = st.sidebar.slider("분할 횟수", 2, 5, 3)
    RULES['split_step'] = st.sidebar.slider("분할 간격 (%)", 3, 15, 7,
                                            help="직전 매수가보다 이만큼 더 내려가면 다음 분할")
    RULES['max_weight'] = st.sidebar.slider("종목당 최대 투입 비중 (%)", 1, 20, 5,
                                            help="전체 투자금 대비. 분할 횟수로 균등 배분")


# ---------- 1) 수집·계산: 버튼 클릭 시에만 실행, 결과는 session_state에 보관 ----------
# (표 행 클릭 등으로 재실행돼도 결과가 사라지지 않도록 렌더링과 분리)
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

        # 종목별 그룹 정렬 (종목평균괴리율 기준)
        matched['종목평균괴리율'] = matched.groupby('종목코드')['괴리율(%)'].transform('mean')

        hot_picks = matched[matched['괴리율(%)'] >= threshold].copy()
        hot_picks = hot_picks.sort_values(
            by=['종목평균괴리율', '종목코드', '괴리율(%)'],
            ascending=[False, True, False]
        )

        # 종목별 컨센서스(리포트 평균 목표가·건수) — 판정에 사용
        consensus = (matched.groupby('종목코드')['적정가격']
                     .agg(['mean', 'count']).to_dict('index'))

        tech_signals, diag_sample, details_map = {}, None, {}
        if not hot_picks.empty:
            status.text("4/4 · 보강 데이터 및 기술적 시그널 수집 중...")
            unique_items = hot_picks[['종목코드', '시장']].drop_duplicates().to_dict('records')
            tech_signals, diag_sample = get_technical_signals_batch(unique_items)

            for item in unique_items:
                details_map[item['종목코드']] = get_stock_details(item['종목코드'])

            for idx, row in hot_picks.iterrows():
                code = row['종목코드']
                details = details_map[code]
                hot_picks.at[idx, '매출액'] = details.get('매출액', '-')
                hot_picks.at[idx, '영업이익'] = details.get('영업이익', '-')
                hot_picks.at[idx, '괴리율'] = f"{row['괴리율(%)']}% {classify_gap(row['괴리율(%)'])}"

                per = details.get('PER')
                hot_picks.at[idx, 'PER'] = f"{per} {classify_per(per)}" if pd.notnull(per) else "-"
                pbr = details.get('PBR')
                hot_picks.at[idx, 'PBR'] = f"{pbr} {classify_pbr(pbr)}" if pd.notnull(pbr) else "-"
                opm = details.get('영업이익률')
                hot_picks.at[idx, '영익률'] = f"{opm}% {classify_opmargin(opm)}" if pd.notnull(opm) else "-"

                tech = tech_signals.get(code, {})
                hot_picks.at[idx, 'RSI'] = tech.get('RSI', '-')
                hot_picks.at[idx, '볼린저 위치'] = tech.get('볼린저 위치', '-')
                hot_picks.at[idx, '추세'] = tech.get('추세', '-')
                hot_picks.at[idx, '종합 시그널'] = tech.get('종합 시그널', '-')
                hot_picks.at[idx, '근거'] = tech.get('근거', '-')
                hot_picks.at[idx, '차트'] = f"https://m.stock.naver.com/fchart/domestic/stock/{code}"

        progress.progress(100)
        time.sleep(0.3)
        progress.empty(); status.empty()

        st.session_state['result'] = {
            'hot_picks': hot_picks, 'consensus': consensus,
            'details_map': details_map, 'tech_signals': tech_signals,
            'diag_sample': diag_sample, 'n_reports': len(research_df),
            'n_matched': len(matched), 'threshold': threshold,
            'period': period_option, 'at': datetime.now().strftime('%Y-%m-%d %H:%M'),
        }

# ---------- 2) 렌더링: session_state의 결과를 매 실행마다 그림 ----------
res = st.session_state.get('result')
if res:
    hot_picks = res['hot_picks']
    consensus, details_map, tech_signals = res['consensus'], res['details_map'], res['tech_signals']
    diag_sample, matched_n = res['diag_sample'], res['n_matched']

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
            d = details_map.get(code, {})
            if not cons or not r0['현재가']:
                continue
            common = dict(
                cur=float(r0['현재가']), target=float(cons['mean']), n_reports=int(cons['count']),
                per=d.get('PER'), pbr=d.get('PBR'), opm=d.get('영업이익률'),
                num=tech_signals.get(code, {}).get('_num'))
            if mode == MODE_LONG:
                ev = evaluate_stock_long(**common, mkt_dd=idx_dd.get(r0['시장']), params=RULES)
            else:
                ev = evaluate_stock(**common, params=RULES)
            ev['name'] = r0['종목명_x']; ev['market'] = r0['시장']
            verdicts[code] = ev

        st.success(f"✅ 오늘의 핫픽 {len(hot_picks)}건 ({len(verdicts)}종목) 추출 완료")

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
            
            ### 🎯 매수 판정 (확신 점수 9점 만점)
            - 체크 9개: 괴리율 15%/30%, 복수 리포트, PER, PBR, 영업이익률, 추세 상승, 볼린저 상단 아님, RSI 과매수 아님
            - **⚪ 제외** = 점수 미달 · **💛 대기** = 점수 통과했으나 손익비/손절폭/추세/과열 미충족 · **💚 매수 적정** = 모두 통과
            - 손절가 = 최근 20일 저가 -2%, 목표가 = 증권사 평균 목표가. 기준값은 사이드바에서 조정

            ### 🧭 중장기 분할매수 모드 (7점 만점)
            - 체크 7개: 6개월 고점 대비 -15% 눌림, 시장 요인 중심 하락, 괴리율 30%, 복수 리포트, 흑자, PBR, 영업이익률
            - **하락 원인 진단**: 종목 낙폭을 지수 낙폭과 비교 (지수의 1.5배 또는 +5%p 이내면 시장 요인, 초과하면 개별 악재 → ⚪ 제외)
            - 통과 시 N회 분할 계획(간격·종목당 비중 기준)과 가설 재검토 조건을 제시

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
        tab0, tab1, tab2 = st.tabs(["🎯 매수 판정", "📊 펀더멘털 분석", "📈 기술적 분석"])

        with tab0:
            if mode == MODE_LONG:
                st.caption(
                    f"**중장기 분할매수 모드** — 점수 ≥ {RULES['min_score_long']}/7 · {RULES['n_split']}회 분할(간격 {RULES['split_step']}%) · "
                    f"종목당 최대 {RULES['max_weight']}%. 하락이 시장 요인 중심이고 체력이 양호할 때만 나눠서 매수합니다. "
                    "손절 대신 **가설 무효화 조건**(적자 전환, 목표가 연속 하향 등)이 생기면 재검토합니다."
                )
                if not idx_dd:
                    st.warning("지수 낙폭 데이터를 가져오지 못해 '시장 요인 vs 개별 악재' 구분이 제한됩니다.")
                else:
                    st.caption("📉 지수 6개월 고점 대비: " + " · ".join(f"{k} {v:.1f}%" for k, v in idx_dd.items()))
            else:
                st.caption(
                    f"내 규칙: 확신 점수 ≥ {RULES['min_score']}/9 · 손익비 ≥ {RULES['min_rr']} · 손절폭 ≤ {RULES['max_loss']}% "
                    "— 매수 전에 **진입가·목표가·손절가를 먼저 정하고**, 매도는 이 약속대로 합니다. "
                    "(목표가=증권사 평균, 손절가=최근 20일 저가-2%)"
                )
            order = {VERDICT_BUY: 0, VERDICT_WAIT: 1, VERDICT_SKIP: 2}
            rows = []
            for code, ev in sorted(verdicts.items(), key=lambda kv: (order[kv[1]['verdict']], -kv[1]['score'])):
                rows.append({
                    '종목코드': code, '종목명': ev['name'], '시장': ev['market'],
                    '판정': ev['verdict'], '점수': f"{ev['score']}/{ev['total']}",
                    '현재가': int(ev['cur']),
                    '재검토가': int(ev['wait_price']) if ev['verdict'] == VERDICT_WAIT and ev['wait_price'] else None,
                    '목표가': int(ev['target']),
                    '손절가': int(ev['stop']) if ev['stop'] else None,
                    '손익비': round(ev['rr'], 1) if ev['rr'] is not None else None,
                    '평단(분할)': int(ev['plan']['avg']) if ev.get('plan') and ev['verdict'] == VERDICT_BUY else None,
                    '사유': ev['reason'],
                })
            vdf = pd.DataFrame(rows).dropna(axis=1, how='all')   # 모드에 안 맞는 빈 열 제거

            n_buy = sum(1 for e in verdicts.values() if e['verdict'] == VERDICT_BUY)
            n_wait = sum(1 for e in verdicts.values() if e['verdict'] == VERDICT_WAIT)
            m1, m2, m3 = st.columns(3)
            m1.metric("💚 매수 적정", f"{n_buy}종목")
            m2.metric("💛 대기", f"{n_wait}종목")
            m3.metric("⚪ 제외", f"{len(verdicts) - n_buy - n_wait}종목")
            if n_buy == 0:
                st.info("오늘은 규칙을 통과한 종목이 없습니다. **안 사는 것도 결정입니다.** "
                        "💛 대기 종목은 '재검토가'까지 내려오면 다시 보세요.")

            card0 = st.container()
            event0 = st.dataframe(
                vdf, hide_index=True, width=1400,
                column_config={
                    "현재가": st.column_config.NumberColumn("현재가", format="%,d"),
                    "재검토가": st.column_config.NumberColumn("재검토가", format="%,d",
                                                          help="이 가격 이하에서 손익비·손절폭 규칙을 충족"),
                    "목표가": st.column_config.NumberColumn("목표가", format="%,d"),
                    "손절가": st.column_config.NumberColumn("손절가", format="%,d"),
                    "평단(분할)": st.column_config.NumberColumn("평단(분할)", format="%,d",
                                                            help="분할매수 완료 시 예상 평균단가"),
                },
                on_select="rerun", selection_mode="single-row", key=f"verdict_table_{mode}"
            )

            if event0.selection.rows:
                sel0 = vdf.iloc[event0.selection.rows[0]]
                ev = verdicts[sel0['종목코드']]
                with card0:
                    st.markdown(f"#### 📌 {ev['name']} · {ev['market']} — {ev['verdict']}")
                    st.caption(ev['reason'])
                    if ev.get('plan'):
                        plan = ev['plan']
                        p1, p2, p3 = st.columns(3)
                        p1.metric("현재가", f"{int(ev['cur']):,}원")
                        p2.metric("목표가(리포트 평균)", f"{int(ev['target']):,}원")
                        p3.metric("분할 완료 시 평단", f"{int(plan['avg']):,}원",
                                  f"상승여력 {plan['upside']:.0f}%" if plan['upside'] is not None else None,
                                  delta_color="off")
                        if ev['verdict'] == VERDICT_BUY:
                            st.markdown("**📐 분할 매수 계획** (직전 매수가보다 간격만큼 하락할 때만 다음 매수)")
                            st.dataframe(pd.DataFrame([
                                {'회차': f"{i}차", '매수가': int(p), '투입 비중(%)': round(w, 2)}
                                for i, (p, w) in enumerate(plan['tranches'], 1)
                            ]), hide_index=True)
                            st.info(f"🛑 **가격 손절 대신 재검토 조건**: ① {int(plan['review_below']):,}원 이하로 추가 하락 ② 적자 전환/영업이익 급감 "
                                    "③ 증권사 목표가 연속 하향 — 하나라도 해당하면 추가 매수를 멈추고 가설을 다시 점검합니다.")
                        elif ev['verdict'] == VERDICT_WAIT and ev['wait_price']:
                            st.warning(f"💛 **{int(ev['wait_price']):,}원 부근**(6개월 고점 -15%)까지 내려오면 분할을 시작합니다. 그 전엔 사지 않습니다.")
                    else:
                        p1, p2, p3, p4 = st.columns(4)
                        p1.metric("현재가", f"{int(ev['cur']):,}원")
                        p2.metric("목표가(리포트 평균)", f"{int(ev['target']):,}원")
                        p3.metric("손절가", f"{int(ev['stop']):,}원" if ev['stop'] else "-",
                                  f"-{ev['loss_pct']:.1f}%" if ev['loss_pct'] else None, delta_color="off")
                        p4.metric("손익비", f"{ev['rr']:.1f}" if ev['rr'] is not None else "-")
                        if ev['verdict'] == VERDICT_WAIT and ev['wait_price']:
                            st.warning(f"💛 **{int(ev['wait_price']):,}원 이하**로 내려오면 손익비·손절폭 규칙을 충족합니다. 그 전엔 사지 않습니다.")
                    is_exc = ev['verdict'] != VERDICT_BUY
                    st.markdown("**📝 매수 계획 저장**")
                    with st.form(f"plan_form_{sel0['종목코드']}_{mode}", clear_on_submit=True):
                        if is_exc:
                            st.caption("⚠️ 규칙을 통과하지 못한 종목입니다. 그래도 매수한다면 '예외 매수'로 기록되고, 예외 성과는 따로 집계됩니다.")
                        reason = st.text_input("왜 사는가? (필수, 10자 이상)",
                                               placeholder="예: 시장 급락으로 저평가, 실적·배당 안정적")
                        submitted = st.form_submit_button("⚠️ 예외 매수로 저장" if is_exc else "💾 이 계획으로 저장")
                    if submitted:
                        if len(reason.strip()) < 10:
                            st.error("매수 이유를 10자 이상 적어야 저장됩니다. 이유가 안 써지면 사지 않는 게 맞을 수 있습니다.")
                        else:
                            ok_, msg = save_plan(build_plan_record(sel0['종목코드'], ev, reason, is_exc))
                            (st.success if ok_ else st.error)(msg + (" 아래 '📒 내 매수 계획'에서 확인하세요." if ok_ else ""))
                    st.markdown("**확신 체크리스트**")
                    for label, passed, detail in ev['checks']:
                        mark = '✅' if passed else ('➖' if passed is None else '❌')
                        st.markdown(f"{mark} {label} — _{detail}_")
                    st.markdown("---")

        with tab1:
            st.caption("👆 표에서 종목을 클릭하면 해당 종목 정보가 위에 카드로 표시됩니다.")

            display_cols1 = ['작성일', '종목명_x', '시장', '현재가', '등락률', '적정가격',
                            '괴리율', '시가총액', 'PER', 'PBR', '영익률', '매출액', '영업이익', '투자의견', '작성기관', '리포트제목', 'PDF링크']
            df1 = hot_picks[[c for c in display_cols1 if c in hot_picks.columns]].rename(columns={'종목명_x': '종목명'})

            # 카드 표시 영역 (선택 시 채워짐)
            card1 = st.container()

            event1 = st.dataframe(
                df1,
                column_config={
                    "현재가": st.column_config.NumberColumn("현재가", format="%,d"),
                    "적정가격": st.column_config.NumberColumn("적정가격", format="%,d"),
                    "PDF링크": st.column_config.LinkColumn("리포트", display_text="📄")
                },
                hide_index=True,
                width=1400,
                on_select="rerun",
                selection_mode="single-row",
                key="fund_table"
            )

            if event1.selection.rows:
                sel = df1.iloc[event1.selection.rows[0]]
                with card1:
                    st.markdown(f"#### 📌 {sel.get('종목명', '-')}  ·  {sel.get('시장', '')}")
                    m1, m2, m3, m4 = st.columns(4)
                    cur = sel.get('현재가'); tgt = sel.get('적정가격')
                    m1.metric("현재가", f"{int(cur):,}원" if pd.notnull(cur) else "-")
                    m2.metric("목표가", f"{int(tgt):,}원" if pd.notnull(tgt) else "-")
                    m3.metric("괴리율", str(sel.get('괴리율', '-')))
                    m4.metric("투자의견", str(sel.get('투자의견', '-')))
                    n1, n2, n3 = st.columns(3)
                    n1.markdown(f"**PER** {sel.get('PER', '-')}")
                    n2.markdown(f"**PBR** {sel.get('PBR', '-')}")
                    n3.markdown(f"**영익률** {sel.get('영익률', '-')}")
                    st.markdown(
                        f"**시가총액** {sel.get('시가총액', '-')}  ·  "
                        f"**매출액** {sel.get('매출액', '-')}  ·  "
                        f"**영업이익** {sel.get('영업이익', '-')}"
                    )
                    st.markdown(f"**리포트** {sel.get('리포트제목', '-')}  ·  _{sel.get('작성기관', '')}_")
                    st.markdown("---")
            
        with tab2:
            st.caption("👆 표에서 종목을 클릭하면 해당 종목 정보가 위에 카드로 표시됩니다.")

            display_cols2 = ['작성일', '종목명_x', '시장', '현재가', '적정가격', '괴리율', 'RSI', '볼린저 위치', '추세', '종합 시그널', '근거', '차트']
            df2 = hot_picks[[c for c in display_cols2 if c in hot_picks.columns]].rename(columns={'종목명_x': '종목명', '적정가격': '목표가'})

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
                    "차트": st.column_config.LinkColumn(
                        "차트",
                        display_text="📈 보기",
                        help="네이버 증권 차트로 이동"
                    )
                },
                on_select="rerun",
                selection_mode="single-row",
                key="tech_table"
            )

            if event2.selection.rows:
                sel = df2.iloc[event2.selection.rows[0]]
                with card2:
                    st.markdown(f"#### 📌 {sel.get('종목명', '-')}  ·  {sel.get('시장', '')}")
                    m1, m2, m3 = st.columns(3)
                    cur = sel.get('현재가'); tgt = sel.get('목표가')
                    m1.metric("현재가", f"{int(cur):,}원" if pd.notnull(cur) else "-")
                    m2.metric("목표가", f"{int(tgt):,}원" if pd.notnull(tgt) else "-")
                    m3.metric("괴리율", str(sel.get('괴리율', '-')))
                    st.markdown(
                        f"**RSI** {sel.get('RSI', '-')}  ·  "
                        f"**볼린저** {sel.get('볼린저 위치', '-')}  ·  "
                        f"**추세** {sel.get('추세', '-')}"
                    )
                    st.markdown(f"### {sel.get('종합 시그널', '-')}")
                    st.caption(f"근거: {sel.get('근거', '-')}")
                    chart_url = sel.get('차트', '')
                    if chart_url:
                        st.markdown(f"[📈 네이버 차트 보기]({chart_url})")
                    st.markdown("---")

        st.session_state['hot_picks'] = hot_picks

        csv_cols = ['작성일', '종목명_x', '시장', '현재가', '등락률', '적정가격', '괴리율', '시가총액', 'PER', 'PBR', '영익률', '매출액', '영업이익', 'RSI', '볼린저 위치', '추세', '종합 시그널', '근거', '차트', '투자의견', '작성기관', '리포트제목', 'PDF링크']
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


render_plans()

st.markdown("---")
col_a, col_b = st.columns([2, 1])
with col_a:
    st.caption("📊 데이터 출처: 한경 컨센서스 + 네이버 금융 · 모든 투자 책임은 본인에게 있습니다.")
with col_b:
    st.caption("**Designed by Charles Kim** · v1.1 · 2026")