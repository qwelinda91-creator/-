import os
import urllib.parse
from dotenv import load_dotenv
load_dotenv()
import streamlit as st
import pandas as pd
import altair as alt
import requests as req
import base64
from supabase import create_client
from typing import List, Tuple
import re
import pickle
import random
from datetime import date
from pathlib import Path

def _secret(section: str, key: str) -> str:
    """st.secrets 우선, 없으면 환경변수(SECTION_KEY) 사용 — Render 배포 지원."""
    try:
        return st.secrets[section][key]
    except Exception:
        env_key = f"{section.upper()}_{key.upper()}"
        val = os.environ.get(env_key)
        if val is None:
            raise RuntimeError(f"Missing secret: [{section}] {key}  (env: {env_key})")
        return val

# ─── Supabase client ──────────────────────────────────────────────────────────

@st.cache_resource
def get_supabase():
    return create_client(
        _secret("supabase", "url"),
        _secret("supabase", "service_key"),
    )

# ─── Google OAuth ─────────────────────────────────────────────────────────────

def google_auth_url() -> str:
    params = {
        "client_id": _secret("google", "client_id"),
        "redirect_uri": _secret("google", "redirect_uri"),
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"

def google_exchange_code(code: str) -> str:
    resp = req.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": _secret("google", "client_id"),
            "client_secret": _secret("google", "client_secret"),
            "redirect_uri": _secret("google", "redirect_uri"),
            "grant_type": "authorization_code",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]

def google_get_user(token: str) -> dict:
    resp = req.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    return {"id": data["id"], "name": data.get("name", "사용자"), "email": data.get("email", "")}

# ─── Supabase data helpers ────────────────────────────────────────────────────

TX_COLS = ["날짜", "구분", "카테고리", "금액", "메모"]
RC_COLS = ["메뉴", "추정금액", "카테고리", "OCR원문"]

def load_transactions(user_id: str) -> pd.DataFrame:
    resp = get_supabase().table("transactions").select("id,tx_date,tx_type,category,amount,memo").eq("user_id", user_id).execute()
    if not resp.data:
        return pd.DataFrame(columns=["_id"] + TX_COLS)
    rename = {"tx_date": "날짜", "tx_type": "구분", "category": "카테고리", "amount": "금액", "memo": "메모"}
    df = pd.DataFrame(resp.data).rename(columns=rename)
    df["_id"] = df["id"]
    return df[["_id"] + TX_COLS]

def delete_transaction(record_id):
    get_supabase().table("transactions").delete().eq("id", record_id).execute()

def add_transaction(user_id: str, row: dict):
    get_supabase().table("transactions").insert({
        "user_id": user_id,
        "tx_date": str(row["날짜"])[:10],
        "tx_type": row["구분"],
        "category": row["카테고리"],
        "amount": int(row["금액"]),
        "memo": row["메모"],
    }).execute()

def clear_transactions(user_id: str):
    get_supabase().table("transactions").delete().eq("user_id", user_id).execute()

def load_receipts(user_id: str) -> pd.DataFrame:
    resp = get_supabase().table("receipt_records").select("id,merchant,estimated_amount,category,ocr_text").eq("user_id", user_id).execute()
    if not resp.data:
        return pd.DataFrame(columns=["_id"] + RC_COLS)
    rename = {"merchant": "메뉴", "estimated_amount": "추정금액", "category": "카테고리", "ocr_text": "OCR원문"}
    df = pd.DataFrame(resp.data).rename(columns=rename)
    df["_id"] = df["id"]
    return df[["_id"] + RC_COLS]

def delete_receipt(record_id):
    get_supabase().table("receipt_records").delete().eq("id", record_id).execute()

def add_receipt(user_id: str, row: dict):
    get_supabase().table("receipt_records").insert({
        "user_id": user_id,
        "merchant": row["메뉴"],
        "estimated_amount": int(row["추정금액"]),
        "category": row["카테고리"],
        "ocr_text": row["OCR원문"][:4000],
    }).execute()

def clear_receipts(user_id: str):
    get_supabase().table("receipt_records").delete().eq("user_id", user_id).execute()

def load_budgets(user_id: str) -> dict:
    resp = get_supabase().table("user_settings").select("*").eq("user_id", user_id).execute()
    if not resp.data:
        return {}
    row = resp.data[0]
    return {
        "food": row.get("budget_food", 300000),
        "transport": row.get("budget_transport", 100000),
        "life": row.get("budget_life", 150000),
        "sub": row.get("budget_sub", 50000),
        "save_goal": row.get("save_goal", 300000),
    }

def save_budgets(user_id: str, data: dict):
    get_supabase().table("user_settings").upsert({
        "user_id": user_id,
        "budget_food": data["food"],
        "budget_transport": data["transport"],
        "budget_life": data["life"],
        "budget_sub": data["sub"],
        "save_goal": data["save_goal"],
    }).execute()

def normalize_ocr_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    cleaned: List[str] = []
    for line in lines:
        line = re.sub(r"[|]+", " ", line)
        line = re.sub(r"\s+", " ", line)
        line = re.sub(r"(?<=\d)[Oo](?=\d)", "0", line)
        line = re.sub(r"(?<=\d)[lI](?=\d)", "1", line)
        cleaned.append(line)
    return "\n".join(cleaned)

def fix_korean_common_errors(text: str) -> str:
    replacements = {
        "가거": "가게",
        "친은": "친환경",
        "달 배": "배달",
        "노크x": "노크X",
        "벨x": "벨X",
    }
    for wrong, correct in replacements.items():
        text = text.replace(wrong, correct)
    text = re.sub(r"(?<=\d)\s*[,.:]\s*(?=\d{3}\b)", ",", text)
    text = re.sub(r"(?<=\d)\s+(?=\d{3}\b)", ",", text)
    text = re.sub(r"([합총]계|결제금액|총액)\s*[:=]?\s*([0-9OoIl, ]{3,})", lambda m: f"{m.group(1)} {m.group(2).replace('O', '0').replace('o', '0').replace('I', '1').replace('l', '1')}", text)
    return text

def extract_with_google_vision(image_bytes: bytes) -> str:
    api_key = os.environ.get("GOOGLE_VISION_API_KEY", "")
    if not api_key:
        st.error("GOOGLE_VISION_API_KEY가 .env 파일에 없어요.")
        return ""
    url = f"https://vision.googleapis.com/v1/images:annotate?key={api_key}"
    payload = {
        "requests": [{
            "image": {"content": base64.b64encode(image_bytes).decode("utf-8")},
            "features": [{"type": "TEXT_DETECTION"}]
        }]
    }
    try:
        response = req.post(url, json=payload, timeout=15)
        result = response.json()
        if "error" in result.get("responses", [{}])[0]:
            st.error(f"Google Vision 오류: {result['responses'][0]['error']}")
            return ""
        return result["responses"][0]["fullTextAnnotation"]["text"]
    except Exception as e:
        st.error(f"Google Vision 호출 오류: {e}")
        return ""

def score_ocr_text(text: str) -> int:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return 0
    amount_hits = len(re.findall(r"\d[\d,]{2,}", text))
    keyword_hits = sum(
        1 for kw in ["합계", "총액", "결제", "카드", "현금", "TOTAL", "VAT"] if kw.lower() in text.lower()
    )
    store_hint_hits = len(re.findall(r"(마트|편의점|카페|식당|스토어|점)", text))
    return len(lines) + amount_hits * 3 + keyword_hits * 5 + store_hint_hits * 2

def classify_receipt_type(text: str) -> str:
    lower = text.lower()
    if any(token in lower for token in ["승인", "카드", "매입", "승인번호", "거래일시"]):
        return "card"
    if any(token in lower for token in ["품목", "수량", "단가", "할인", "상품", "vat"]):
        return "itemized"
    return "general"


def extract_receipt_text(image_file) -> str:
    image_bytes = image_file.getvalue()
    text = extract_with_google_vision(image_bytes)
    if not text:
        raise RuntimeError("ocr_no_text")
    text = normalize_ocr_text(text)
    text = fix_korean_common_errors(text)
    st.session_state["last_ocr_engine"] = "Google Vision"
    st.session_state["ocr_candidates"] = {"tesseract_text": "", "paddle_text": ""}
    return text

def guess_category(text: str) -> str:
    normalized = text.lower()
    rules = {
        "식비": ["스타벅스", "커피", "카페", "음식", "배달", "치킨", "식당", "편의점"],
        "교통": ["버스", "택시", "지하철", "교통", "주유", "주차"],
        "생활": ["다이소", "쿠팡", "마트", "올리브영", "생활", "세제"],
        "구독": ["넷플릭스", "유튜브", "멜론", "구독", "정기결제"],
    }
    for category, keywords in rules.items():
        if any(keyword.lower() in normalized for keyword in keywords):
            return category
    return "기타"

def parse_receipt_info(text: str):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    skip_patterns = ["사업자", "전화", "카드", "승인", "vat", "합계", "주문번호", "주문전표",
                     "배달주소", "배달:", "요청사항", "품절", "결제금액", "고객용", "영수증", "거래일",
                     "승인번호", "대표자", "사업자번호", "http", "www", "문 앞", "벨x", "노크x",
                     "가져갈게요", "놔주세요", "두면", "초인종", "조심히"]
    def is_valid_merchant(line):
        if any(token in line.lower() for token in skip_patterns):
            return False
        if len(re.findall(r"\d", line)) > max(3, len(line) // 2):
            return False
        korean_count = len(re.findall(r"[가-힣]", line))
        return korean_count >= 3 and len(line) >= 4

    def clean_bracket(line):
        m = re.match(r"^\[(.+)\]$", line.strip())
        if m:
            inner = m.group(1)
            return inner if len(re.findall(r"[가-힣]", inner)) >= 3 else None
        return line

    merchant = "알 수 없음"
    top_candidate = ""
    for line in lines[:10]:
        cleaned = clean_bracket(line)
        if cleaned and is_valid_merchant(cleaned):
            top_candidate = cleaned
            break

    menu_candidate = ""
    in_menu = False
    for line in lines:
        if line in ["메뉴", "메 뉴"]:
            in_menu = True
            continue
        if in_menu:
            cleaned = clean_bracket(line)
            if cleaned and is_valid_merchant(cleaned) and len(re.findall(r"[가-힣]", cleaned)) >= 4:
                menu_candidate = cleaned
                break
            if any(k in line for k in ["합계", "결제금액", "주문금액"]):
                break

    if menu_candidate and len(menu_candidate) >= len(top_candidate):
        merchant = menu_candidate
    elif top_candidate:
        merchant = top_candidate
    def extract_amounts(line: str) -> List[int]:
        line = re.sub(r"(?<=\d)[OoQqD](?=\d)", "0", line)
        line = re.sub(r"(?<=\d)[lI|!](?=\d)", "1", line)
        nums: List[int] = []
        for token in re.findall(r"-?\d[\d,]{2,}", line):
            token = token.replace(",", "")
            try:
                value = abs(int(token))
            except ValueError:
                continue
            if 100 <= value <= 5_000_000:
                nums.append(value)
        return nums
    total_positive = ["합계", "총액", "결제금액", "받을금액", "총 결제", "최종", "승인금액", "total", "amount"]
    total_negative = ["할인", "쿠폰", "부가세", "vat", "면세", "소계", "잔액", "포인트", "거스름", "change"]
    ranked_candidates: List[tuple[int, int]] = []
    for idx, line in enumerate(lines):
        lower = line.lower()
        neighbors = lines[max(0, idx - 1): min(len(lines), idx + 2)]
        near_text = " ".join(neighbors).lower()
        amounts = extract_amounts(" ".join(neighbors))
        if not amounts:
            continue
        best_amt = max(amounts)
        priority = 0
        
        if any(k in lower for k in total_positive):
            priority = 100
        if any(k in lower for k in total_negative):
            priority -= 60
        if priority != 0:
            ranked_candidates.append((priority, best_amt))
        elif any(k in near_text for k in total_positive):
            ranked_candidates.append((70, best_amt))
        elif not any(k in lower for k in total_negative):
            ranked_candidates.append((30, best_amt))
    if ranked_candidates:
        ranked_candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        total_amount = ranked_candidates[0][1]
    else:
        amount_candidates = []
        for match in re.findall(r"\d[\d,]{2,}", text):
            try:
                amount_candidates.append(int(match.replace(",", "")))
            except ValueError:
                continue
        amount_candidates = [x for x in amount_candidates if 100 <= x <= 5_000_000]
        total_amount = max(amount_candidates) if amount_candidates else 0
    category = guess_category(text)
    return merchant, total_amount, category

def _merchant_quality_score(name: str) -> int:
    if not name or name == "알 수 없음":
        return -100
    digit_penalty = len(re.findall(r"\d", name)) * 3
    symbol_penalty = len(re.findall(r"[^0-9A-Za-z가-힣\s\(\)&\-]", name)) * 2
    korean_hits = len(re.findall(r"[가-힣]", name))
    alpha_hits = len(re.findall(r"[A-Za-z]", name))
    return len(name) + korean_hits * 2 + alpha_hits - digit_penalty - symbol_penalty

def _amount_confidence_score(text: str, amount: int) -> int:
    if amount <= 0:
        return -100
    score = 0
    compact = str(amount)
    comma = f"{amount:,}"
    lines = [line.strip().lower() for line in text.splitlines() if line.strip()]
    for line in lines:
        if compact in line or comma in line:
            score += 20
            if any(k in line for k in ["합계", "총액", "결제", "승인", "total"]):
                score += 40
            if any(k in line for k in ["할인", "쿠폰", "부가세", "vat", "잔액", "거스름"]):
                score -= 25
    if 100 <= amount <= 5_000_000:
        score += 15
    return score

def merge_receipt_fields(primary_text: str, tesseract_text: str, paddle_text: str) -> Tuple[str, int, str]:
    p_merchant, p_amount, p_category = parse_receipt_info(primary_text)
    t_merchant, t_amount, t_category = parse_receipt_info(tesseract_text) if tesseract_text else ("알 수 없음", 0, "기타")
    pd_merchant, pd_amount, pd_category = parse_receipt_info(paddle_text) if paddle_text else ("알 수 없음", 0, "기타")

    merchant_candidates = [p_merchant, t_merchant, pd_merchant]
    best_merchant = max(merchant_candidates, key=_merchant_quality_score)

    amount_candidates = [
        (p_amount, _amount_confidence_score(primary_text, p_amount)),
        (t_amount, _amount_confidence_score(tesseract_text, t_amount)),
        (pd_amount, _amount_confidence_score(paddle_text, pd_amount)),
    ]
    best_amount, best_amount_score = max(amount_candidates, key=lambda x: x[1])
    if best_amount_score < 0:
        best_amount = p_amount

    if best_merchant == p_merchant and best_amount == p_amount:
        return p_merchant, p_amount, p_category
    if best_merchant == t_merchant or best_amount == t_amount:
        return best_merchant, best_amount, t_category if t_category != "기타" else p_category
    return best_merchant, best_amount, pd_category if pd_category != "기타" else p_category

def parse_receipt_items(text: str) -> pd.DataFrame:
    items = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        if len(line) < 4:
            continue
        if any(token in line for token in ["합계", "총액", "부가세", "카드", "현금", "vat", "TOTAL"]):
            continue
        match = re.search(r"(.+?)\s+(\d[\d,]{1,})$", line)
        if not match:
            continue
        name = match.group(1).strip()
        amount_text = match.group(2).replace(",", "")
        if len(name) < 2:
            continue
        try:
            amount = int(amount_text)
        except ValueError:
            continue
        if amount <= 0:
            continue
        items.append({"품목": name, "금액": amount})
    if not items:
        return pd.DataFrame(columns=["품목", "금액"])
    return pd.DataFrame(items)

DATA_DIR = Path(".")
TRANSACTION_CSV = DATA_DIR / "transactions.csv"
RECEIPT_CSV = DATA_DIR / "receipt_records.csv"

def load_csv_or_empty(path: Path, columns: List[str]) -> pd.DataFrame:
    if path.exists():
        try:
            df = pd.read_csv(path)
            for col in columns:
                if col not in df.columns:
                    df[col] = ""
            return df[columns]
        except Exception:
            return pd.DataFrame(columns=columns)
    return pd.DataFrame(columns=columns)

def save_records():
    pass

@st.cache_resource
def load_chatbot_model():
    model_path = Path("model.pkl")
    if not model_path.exists():
        return None
    with open(model_path, "rb") as f:
        return pickle.load(f)

RESPONSES = {
    "식비": [
        "식비는 '외식/배달/장보기' 3개로 나눠 관리해보세요.",
        "배달 앱은 주 2~3회로 제한하면 한 달에 3~5만원 아낄 수 있어요.",
        "편의점 대신 마트에서 주 1회 장보기로 바꾸면 식비가 줄어요.",
        "카페는 주간 횟수를 정해두면 지출이 안정돼요.",
    ],
    "저축": [
        "월급날 바로 저축 계좌로 자동이체 설정하는 게 제일 효과적이에요.",
        "비상금은 최소 3개월치 생활비를 목표로 모아보세요.",
        "저축은 '남으면 저축'이 아니라 '먼저 저축'이 핵심이에요.",
        "적금은 소액이라도 꾸준히 넣는 게 중요해요.",
    ],
    "고정비": [
        "구독 서비스 목록을 한 번 정리해보세요. 안 쓰는 것들이 생각보다 많아요.",
        "통신비는 알뜰폰으로 바꾸면 월 2~3만원 절약할 수 있어요.",
        "고정비는 매달 1회 점검하는 루틴을 만들어보세요.",
        "OTT 서비스는 가족/친구와 공유하면 비용을 줄일 수 있어요.",
    ],
    "소비분석": [
        f"소비 분석은 왼쪽 목표/예산 상태 섹션에서 확인할 수 있어요.",
        "카테고리별 지출을 보면 어디서 돈이 새는지 파악할 수 있어요.",
        "이번 달 지출이 많다면 고정비와 변동비를 구분해서 살펴보세요.",
        "수입 대비 지출이 80% 넘으면 지출을 줄이는 게 좋아요.",
    ],
    "절약": [
        "충동구매를 줄이려면 장바구니에 담고 하루 뒤에 결제해보세요.",
        "소액 결제도 하루 합계를 확인하면 지출 통제가 쉬워져요.",
        "불필요한 지출을 찾으려면 한 달 지출 내역을 쭉 훑어보세요.",
        "예산을 미리 정하고 봉투에 나눠담듯 관리하면 효과적이에요.",
    ],
}

def _has_foreign_chars(text: str) -> bool:
    # 일본어, 힌디어, 한자, 키릴, 아랍어 + 라틴 확장(베트남어/프랑스어 성조 등)
    foreign = re.compile(
        r'[぀-ヿ'       # 일본어 히라가나/가타카나
        r'ऀ-ॿ'         # 힌디어(데바나가리)
        r'一-鿿'        # 한자
        r'Ѐ-ӿ'         # 키릴 문자(러시아어 등)
        r'؀-ۿ'         # 아랍어
        r'À-ÿ'  # 라틴-1 확장(é, á, ñ 등)
        r'Ā-ɏ'  # 라틴 확장-A/B
        r'̀-ͯ'  # 결합 발음 기호
        r'Ḁ-ỿ'  # 라틴 확장 추가(베트남어)
        r']'
    )
    return bool(foreign.search(text))


def _call_ai(user_input: str, total_income: int, total_expense: int, total_balance: int, cat_spending: dict, budgets: dict, chat_history: list) -> str:
    import requests as req

    def fmt(n): return f"{n:,}원"

    spending_lines = []
    for cat, amount in sorted(cat_spending.items(), key=lambda x: x[1], reverse=True):
        budget_val = budgets.get(cat, 0)
        if budget_val > 0:
            ratio = amount / budget_val * 100
            spending_lines.append(f"- {cat}: {fmt(amount)} (예산의 {ratio:.0f}%)")
        else:
            spending_lines.append(f"- {cat}: {fmt(amount)}")
    spending_summary = "\n".join(spending_lines) if spending_lines else "없음"

    system_prompt = f"""[언어 규칙 - 최우선 적용] 반드시 한국어(한글)로만 답변하세요. 일본어, 힌디어, 한자, 아랍어, 러시아어 등 어떤 외국 문자도 절대 사용하지 마세요. 한글, 숫자, 문장부호만 사용하세요.

당신은 사용자의 개인 재정 비서입니다. 격식 있고 차분하며 신뢰감 있는 말투로 응대하세요.

사용자 재정 현황:
- 수입: {fmt(total_income)} / 지출: {fmt(total_expense)} / 잔액: {fmt(total_balance)}
- 카테고리별 지출: {spending_summary}

응대 원칙:
- 존댓말을 사용하되 품위 있고 자연스럽게 말하세요
- 어떤 질문이든 간결하고 명확하게 답변하세요. 재정과 무관한 일상 질문도 자연스럽게 답하세요
- 재정 관련 질문에는 위 데이터를 바탕으로 구체적인 수치와 함께 조언하세요
- 감탄사, 과장된 표현, 유치한 말투 절대 금지
- 맥락과 무관하게 재정 이야기를 억지로 끌어들이지 마세요
- 답변은 짧고 핵심만 말하세요"""

    api_key = os.environ.get("GROQ_API_KEY", "")

    def _build_messages(extra_instruction: str = "") -> list:
        prompt = system_prompt + extra_instruction
        msgs = [{"role": "system", "content": prompt}]
        for msg in chat_history[:-1][-10:]:
            if msg["role"] in ("user", "assistant"):
                msgs.append({"role": msg["role"], "content": msg["content"]})
        msgs.append({"role": "user", "content": user_input})
        return msgs

    def _request(messages: list) -> str:
        response = req.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile", "messages": messages, "max_tokens": 500, "temperature": 0.1},
            timeout=30,
        )
        if not response.ok:
            return f"[AI 오류] {response.status_code}: {response.text[:300]}"
        return response.json()["choices"][0]["message"]["content"]

    try:
        result = _request(_build_messages())
        if _has_foreign_chars(result):
            result = _request(_build_messages(
                "\n\n[경고] 이전 답변에 외국 문자가 포함됐습니다. 이번엔 반드시 한글과 숫자, 문장부호만 사용하세요."
            ))
        return result
    except Exception as e:
        return f"[AI 오류] {e}"


def local_finance_chatbot(user_input: str, total_income: int, total_expense: int, total_balance: int, tx_df: pd.DataFrame = None, budgets: dict = None) -> str:
    if budgets is None:
        budgets = {}

    cat_spending = {}
    if tx_df is not None and not tx_df.empty:
        expense_df = tx_df[tx_df["구분"] == "지출"].copy()
        expense_df["금액"] = pd.to_numeric(expense_df["금액"], errors="coerce").fillna(0).astype(int)
        cat_spending = expense_df.groupby("카테고리")["금액"].sum().to_dict()

    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    if groq_key and groq_key != "여기에_Groq_키_붙여넣기":
        chat_history = st.session_state.get("chat_history", [])
        return _call_ai(user_input, total_income, total_expense, total_balance, cat_spending, budgets, chat_history)

    # API 키 없을 때 ML 분류 기반 템플릿 답변
    pipeline = load_chatbot_model()
    if pipeline is None:
        return "모델 파일이 없어요. train.py를 먼저 실행해주세요."

    if total_income == 0 and total_expense == 0:
        return "아직 소비 데이터가 없어요. 먼저 수입/지출을 입력해보세요."

    category = pipeline.predict([user_input])[0]
    proba = pipeline.predict_proba([user_input]).max()

    if proba < 0.4:
        return (
            f"현재 요약: 수입 {total_income:,}원 / 지출 {total_expense:,}원 / 잔액 {total_balance:,}원\n"
            "질문 예시: '식비 줄이고 싶어', '저축 방법 알려줘', '내 소비 어때'"
        )

    def fmt(n): return f"{n:,}원"

    if category == "식비":
        food_spent = cat_spending.get("식비", 0)
        food_budget = budgets.get("식비", 0)
        lines = [f"이번 달 식비로 {fmt(food_spent)} 쓰셨어요."]
        if food_budget > 0:
            ratio = food_spent / food_budget * 100
            lines.append(f"예산 {fmt(food_budget)}의 {ratio:.0f}% 사용했어요.")
            if ratio >= 100:
                lines.append(f"예산을 {fmt(food_spent - food_budget)} 초과했어요. 배달 앱 사용 횟수를 줄이거나 직접 요리를 늘려보세요.")
            elif ratio >= 80:
                lines.append(f"이번 달 남은 식비 예산은 {fmt(food_budget - food_spent)}이에요. 조금만 더 아껴보세요.")
            else:
                lines.append(f"아직 {fmt(food_budget - food_spent)} 여유가 있어요. 잘 관리하고 있어요!")
        else:
            lines.append("사이드바에서 식비 예산을 설정하면 더 정확하게 분석해드릴 수 있어요.")
        return "\n".join(lines)

    elif category == "저축":
        savings_rate = (total_balance / total_income * 100) if total_income > 0 else 0
        lines = [f"이번 달 수입 {fmt(total_income)} 중 {fmt(total_balance)}를 남기셨어요. (저축률 {savings_rate:.0f}%)"]
        if savings_rate >= 30:
            lines.append("저축률 30% 이상! 훌륭한 재정 관리예요.")
            lines.append("이 페이스라면 비상금 3개월치도 금방 모을 수 있어요.")
        elif savings_rate >= 20:
            lines.append("저축률이 양호해요. 20~30% 유지를 목표로 해보세요.")
            lines.append("자동이체로 먼저 저축하면 30%도 가능해요.")
        elif savings_rate >= 10:
            lines.append("저축률이 조금 낮아요.")
            if cat_spending:
                top_cat, top_amt = max(cat_spending.items(), key=lambda x: x[1])
                lines.append(f"지출 중 '{top_cat}'({fmt(top_amt)})을 줄이면 저축률을 더 높일 수 있어요.")
        elif savings_rate > 0:
            lines.append("저축률이 10% 미만이에요. 월급날 바로 자동이체로 먼저 저축하는 방법을 써보세요.")
        else:
            lines.append("현재 적자 상태예요. 지출 중 줄일 수 있는 항목을 먼저 찾아야 해요.")
        return "\n".join(lines)

    elif category == "고정비":
        sub_spent = cat_spending.get("구독", 0)
        sub_budget = budgets.get("구독", 0)
        lines = []
        if sub_spent > 0:
            lines.append(f"이번 달 구독/고정비로 {fmt(sub_spent)} 지출했어요.")
            if sub_budget > 0:
                ratio = sub_spent / sub_budget * 100
                lines.append(f"구독 예산 {fmt(sub_budget)}의 {ratio:.0f}% 사용했어요.")
                if ratio >= 100:
                    lines.append("구독 예산을 초과했어요. 안 쓰는 서비스를 해지해보세요.")
                else:
                    lines.append(f"남은 구독 예산은 {fmt(sub_budget - sub_spent)}이에요.")
        else:
            lines.append("아직 구독/고정비 내역이 없어요.")
        lines.append("고정비는 매달 1회 점검해서 안 쓰는 항목을 정리하는 게 좋아요.")
        return "\n".join(lines)

    elif category == "소비분석":
        if not cat_spending:
            return "아직 지출 내역이 없어요. 거래를 추가하면 분석해드릴게요."
        lines = [f"수입 {fmt(total_income)} / 지출 {fmt(total_expense)} / 잔액 {fmt(total_balance)}\n카테고리별 지출:"]
        for cat, amount in sorted(cat_spending.items(), key=lambda x: x[1], reverse=True):
            budget_val = budgets.get(cat, 0)
            if budget_val > 0:
                ratio = amount / budget_val * 100
                status = " ⚠ 초과" if ratio >= 100 else ""
                lines.append(f"- {cat}: {fmt(amount)} (예산의 {ratio:.0f}%{status})")
            else:
                lines.append(f"- {cat}: {fmt(amount)}")
        top_cat, top_amt = max(cat_spending.items(), key=lambda x: x[1])
        lines.append(f"\n가장 많이 쓴 항목은 '{top_cat}'({fmt(top_amt)})이에요.")
        if total_balance < 0:
            lines.append("현재 적자예요. 가장 큰 지출 항목부터 줄여보세요.")
        return "\n".join(lines)

    elif category == "절약":
        if cat_spending:
            top_cat, top_amt = max(cat_spending.items(), key=lambda x: x[1])
            lines = [f"지출 내역을 보면 '{top_cat}'에 {fmt(top_amt)}으로 가장 많이 쓰셨어요."]
            lines.append(f"'{top_cat}' 지출을 10%만 줄여도 한 달에 {fmt(int(top_amt * 0.1))} 절약할 수 있어요.")
            if total_expense > 0:
                expense_rate = total_expense / total_income * 100 if total_income > 0 else 0
                lines.append(f"현재 수입의 {expense_rate:.0f}%를 지출하고 있어요.")
        else:
            lines = ["아직 지출 내역이 없어요. 거래를 추가하면 맞춤 절약 팁을 드릴게요."]
        return "\n".join(lines)

    return random.choice(RESPONSES.get(category, ["소비 관련 질문을 해주세요."]))

st.set_page_config(page_title="자취생 소비 관리", page_icon="💸", layout="wide", menu_items={})

st.markdown("""
<script>
(function(){
    document.documentElement.setAttribute('translate','no');
    document.documentElement.setAttribute('lang','ko');
    var m=document.createElement('meta');
    m.name='google'; m.content='notranslate';
    document.head.appendChild(m);
})();
</script>
""", unsafe_allow_html=True)

# ─── Auth ─────────────────────────────────────────────────────────────────────

if "user" not in st.session_state:
    st.session_state.user = None

params = st.query_params

if "code" in params and st.session_state.user is None:
    with st.spinner("로그인 중..."):
        try:
            token = google_exchange_code(params["code"])
            user = google_get_user(token)
            st.session_state.user = user
            st.session_state.transaction_records = load_transactions(user["id"])
            st.session_state.receipt_records = load_receipts(user["id"])
            st.query_params.clear()
            st.rerun()
        except Exception as e:
            st.error(f"로그인 실패: {e}")
            st.query_params.clear()
elif "error" in params:
    st.warning("구글 로그인이 취소됐어요.")
    st.query_params.clear()

if st.session_state.user is None:
    _auth_url = google_auth_url()
    st.markdown(f"""
    <div style="max-width:400px;margin:80px auto;text-align:center;padding:0 16px">
        <h1 style="font-size:2rem">💸 자취생 소비 관리 AI</h1>
        <p style="color:#6b7280;margin-bottom:32px">
            구글 계정으로 로그인하면<br>어디서든 내 데이터를 유지할 수 있어요.
        </p>
        <a href="{_auth_url}" target="_self" style="
            display:flex;align-items:center;justify-content:center;gap:10px;
            background:#fff;color:#3c4043;border:1px solid #dadce0;
            padding:14px 0;border-radius:10px;font-size:15px;
            font-weight:500;text-decoration:none;box-sizing:border-box;">
            <img src="https://www.google.com/favicon.ico" width="20">
            Google 계정으로 로그인
        </a>
    </div>
    """, unsafe_allow_html=True)
    st.stop()

user_id: str = st.session_state.user["id"]
user_name: str = st.session_state.user["name"]

if "transaction_records" not in st.session_state:
    st.session_state.transaction_records = load_transactions(user_id)
if "receipt_records" not in st.session_state:
    st.session_state.receipt_records = load_receipts(user_id)
if "budgets_loaded" not in st.session_state:
    _b = load_budgets(user_id)
    st.session_state.budget_food = _b.get("food", 300000)
    st.session_state.budget_transport = _b.get("transport", 100000)
    st.session_state.budget_life = _b.get("life", 150000)
    st.session_state.budget_sub = _b.get("sub", 50000)
    st.session_state.save_goal = _b.get("save_goal", 300000)
    st.session_state.budgets_loaded = True

# ─────────────────────────────────────────────────────────────────────────────

st.markdown("""
<style>
.block-container { padding-top: 2rem; padding-bottom: 2rem; max-width: 1180px; }
.hero-card { background: linear-gradient(135deg, #0ea5e9 0%, #6366f1 100%); color: white; border-radius: 18px; padding: 20px 24px; margin-bottom: 14px; }
.section-card { background: #ffffff; border: 1px solid #e5e7eb; border-radius: 14px; padding: 14px 16px; margin-bottom: 14px; }
.small-muted { font-size: 0.9rem; color: #6b7280; margin-top: -4px; }
.m-tbl-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; width: 100%; margin-bottom: 8px; }
.m-tbl { border-collapse: collapse; font-size: .9em; min-width: 360px; width: 100%; }
.m-tbl th { background: #f8fafc; padding: 7px 12px; border-bottom: 2px solid #e2e8f0; white-space: nowrap; text-align: left; font-weight: 600; }
.m-tbl td { padding: 6px 12px; border-bottom: 1px solid #f1f5f9; }
[data-testid="stHorizontalBlock"] { align-items: center !important; }
footer { visibility: hidden !important; }
[data-testid="stSidebar"] { display: none !important; }
[data-testid="collapsedControl"] { display: none !important; }
:root { color-scheme: light !important; }
html, body, [data-testid="stAppViewContainer"], [data-testid="stApp"] {
    background-color: #ffffff !important;
    color: #111111 !important;
    color-scheme: light !important;
}
[data-testid="stButton"] > button {
    background-color: #ffffff !important;
    color: #111111 !important;
    border-color: #e5e7eb !important;
}
[data-testid="stSelectbox"] *, [data-testid="stTextInput"] *,
[data-testid="stNumberInput"] *, [data-testid="stDateInput"] * {
    background-color: #ffffff !important;
    color: #111111 !important;
}
button[data-baseweb="tab"] {
    color: #111111 !important;
    background-color: transparent !important;
}
button[data-baseweb="tab"][aria-selected="true"] {
    color: #e53935 !important;
}
[data-testid="stTabs"] * {
    color: #111111 !important;
}
</style>
""", unsafe_allow_html=True)

if "chat_history" not in st.session_state: st.session_state.chat_history = []
if "last_df" not in st.session_state: st.session_state.last_df = pd.DataFrame(columns=["수입", "지출", "잔액"])
if "receipt_records" not in st.session_state:
    st.session_state.receipt_records = pd.DataFrame(columns=["메뉴", "추정금액", "카테고리", "OCR원문"])
if "transaction_records" not in st.session_state:
    st.session_state.transaction_records = pd.DataFrame(columns=["날짜", "구분", "카테고리", "금액", "메모"])
if "last_ocr_result" not in st.session_state: st.session_state.last_ocr_result = None
if "ocr_results" not in st.session_state: st.session_state.ocr_results = []
if "uploader_key" not in st.session_state: st.session_state.uploader_key = 0

st.markdown("""
<div class="hero-card">
    <h2 style="margin: 0;">💸 자취생 소비 관리 AI</h2>
    <p style="margin: 6px 0 0 0; opacity: 0.95;"> 예산 상태를 빠르게 확인하고, 소비 상담 챗봇으로 맞춤 조언까지 받아보세요. </p>
</div>
""", unsafe_allow_html=True)

groq_key = os.environ.get("GROQ_API_KEY", "")
open_chat = True

if "show_budget" not in st.session_state: st.session_state.show_budget = False
if "show_reset" not in st.session_state: st.session_state.show_reset = False

btn1, btn2 = st.columns(2)
budget_arrow = "▲" if st.session_state.show_budget else "▼"
reset_arrow = "▲" if st.session_state.show_reset else "▼"
if btn1.button(f"💰 예산 설정 {budget_arrow}", use_container_width=True, key="budget_btn"):
    st.session_state.show_budget = not st.session_state.show_budget
    st.session_state.show_reset = False
    st.rerun()
if btn2.button(f"🔄 초기화 {reset_arrow}", use_container_width=True, key="reset_btn"):
    st.session_state.show_reset = not st.session_state.show_reset
    st.session_state.show_budget = False
    st.rerun()

if st.session_state.show_budget:
    with st.container(border=True):
        c1, c2 = st.columns(2)
        budget_food = c1.number_input("식비", min_value=0, step=10000, key="budget_food")
        budget_transport = c2.number_input("교통", min_value=0, step=10000, key="budget_transport")
        budget_life = c1.number_input("생활", min_value=0, step=10000, key="budget_life")
        budget_sub = c2.number_input("구독", min_value=0, step=10000, key="budget_sub")
        save_goal = st.number_input("월 저축 목표", min_value=0, step=50000, key="save_goal")
        if st.button("💾 저장", use_container_width=True):
            save_budgets(user_id, {
                "food": st.session_state.budget_food,
                "transport": st.session_state.budget_transport,
                "life": st.session_state.budget_life,
                "sub": st.session_state.budget_sub,
                "save_goal": st.session_state.save_goal,
            })
            st.success("예산이 저장됐어요.")
else:
    budget_food = st.session_state.get("budget_food", 300000)
    budget_transport = st.session_state.get("budget_transport", 100000)
    budget_life = st.session_state.get("budget_life", 150000)
    budget_sub = st.session_state.get("budget_sub", 50000)
    save_goal = st.session_state.get("save_goal", 300000)

if st.session_state.show_reset:
    with st.container(border=True):
        if st.button("대화 초기화", use_container_width=True):
            st.session_state.chat_history = []
            st.success("채팅 기록을 초기화했어요.")
        if st.button("입출금 내역 초기화", use_container_width=True):
            clear_transactions(user_id)
            st.session_state.transaction_records = pd.DataFrame(columns=TX_COLS)
            st.success("입출금 내역을 초기화했어요.")
        if st.button("영수증 기록 초기화", use_container_width=True):
            clear_receipts(user_id)
            st.session_state.receipt_records = pd.DataFrame(columns=RC_COLS)
            st.success("영수증 기록을 초기화했어요.")
        st.markdown("---")
        if st.button("로그아웃", use_container_width=True):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()

transactions = st.session_state.transaction_records.copy()
total_income = int(transactions[transactions["구분"] == "수입"]["금액"].sum())
total_expense = int(transactions[transactions["구분"] == "지출"]["금액"].sum())
total_balance = total_income - total_expense

tab1, tab2, tab3, tab4 = st.tabs(["📅 입출금 내역", "🎯 예산/목표", "🤖 챗봇", "📷 영수증 OCR"])

with tab1:
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    tx_date = st.date_input("거래 날짜", value=date.today())
    tx_type = st.selectbox("구분", options=["수입", "지출"])
    tx_category = st.selectbox("카테고리", options=["식비", "교통", "생활", "구독", "월급", "용돈", "기타"])
    tx_amount = st.number_input("금액 (원)", min_value=0, step=1000, format="%d", key="tx_amount")
    tx_memo = st.text_input("메모(선택)", placeholder="예: 점심, 카페, 알바비")
    if st.button("내역 추가", use_container_width=True):
        if tx_amount <= 0:
            st.warning("금액은 1원 이상 입력해주세요.")
        else:
            new_row = {"날짜": pd.to_datetime(tx_date), "구분": tx_type, "카테고리": tx_category, "금액": int(tx_amount), "메모": tx_memo.strip()}
            add_transaction(user_id, new_row)
            st.session_state.transaction_records = load_transactions(user_id)
            st.rerun()
    tx_df = st.session_state.transaction_records.copy()
    if not tx_df.empty:
        tx_df["날짜"] = pd.to_datetime(tx_df["날짜"])
        tx_df["금액"] = pd.to_numeric(tx_df["금액"], errors="coerce").fillna(0).astype(int)
        daily = (tx_df.assign(
            수입금액=tx_df.apply(lambda row: row["금액"] if row["구분"] == "수입" else 0, axis=1),
            지출금액=tx_df.apply(lambda row: row["금액"] if row["구분"] == "지출" else 0, axis=1),
        ).groupby(tx_df["날짜"].dt.date)[["수입금액", "지출금액"]].sum().reset_index().rename(columns={"날짜": "일자"}))
        daily["순변동"] = daily["수입금액"] - daily["지출금액"]
        daily["일자표시"] = pd.to_datetime(daily["일자"]).map(lambda d: f"{d.month}/{d.day}")
        st.write("### 일자별 요약")
        daily_chart = (alt.Chart(daily[["일자표시", "지출금액"]]).mark_bar(color="#0ea5e9").encode(
            x=alt.X("일자표시:N", sort=list(daily["일자표시"]), axis=alt.Axis(labelAngle=0, title="일자")),
            y=alt.Y("지출금액:Q", title="지출 금액"),
            tooltip=["일자표시:N", "지출금액:Q"],
        ).properties(height=250))
        st.altair_chart(daily_chart, use_container_width=True)
        tx_view = st.session_state.transaction_records.copy()
        tx_view["날짜"] = pd.to_datetime(tx_view["날짜"])
        tx_view = tx_view.sort_values("날짜", ascending=False)
        tx_view["날짜"] = tx_view["날짜"].dt.strftime("%Y-%m-%d")
        st.write("### 거래 내역")
        tx_disp = tx_view[TX_COLS].copy()
        tx_disp["금액"] = tx_disp["금액"].apply(lambda x: f"{int(x):,}원")
        st.markdown(
            '<div class="m-tbl-wrap">'
            + tx_disp.to_html(index=False, border=0, escape=True, classes="m-tbl")
            + "</div>",
            unsafe_allow_html=True,
        )
        valid_tx = tx_view[tx_view["_id"].notna()].reset_index(drop=True)
        if not valid_tx.empty:
            labels = [
                f"{r['날짜']} · {r['구분']} · {r['카테고리']} · {int(r['금액']):,}원"
                for _, r in valid_tx.iterrows()
            ]
            sel = st.selectbox("삭제할 항목", labels, key="del_tx_sel", label_visibility="collapsed")
            if st.button("🗑️ 삭제", key="del_tx_btn", use_container_width=True):
                rid = int(valid_tx.iloc[labels.index(sel)]["_id"])
                delete_transaction(rid)
                st.session_state.transaction_records = load_transactions(user_id)
                st.rerun()
        st.write("### 반복지출 감지")
        expense_df = tx_df[tx_df["구분"] == "지출"].copy()
        one_week_ago = pd.Timestamp.today() - pd.Timedelta(days=7)
        expense_df = expense_df[expense_df["날짜"] >= one_week_ago]
        repeat = expense_df.groupby(["카테고리"], dropna=False).size().reset_index(name="횟수").sort_values("횟수", ascending=False)
        repeat = repeat[repeat["횟수"] >= 5]
        if repeat.empty:
            st.info("반복지출 후보가 없어요. 최근 7일간 같은 카테고리 지출이 5회 이상이면 표시됩니다.")
        else:
            st.dataframe(repeat.head(10), use_container_width=True)
    else:
        st.info("아직 등록된 입출금 내역이 없어요. 위에서 내역을 추가해보세요.")
    st.markdown("</div>", unsafe_allow_html=True)

with tab2:
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    budgets = {"식비": budget_food, "교통": budget_transport, "생활": budget_life, "구독": budget_sub}
    b1, b2, b3 = st.columns(3)
    b1.metric("총 수입", f"{total_income:,}원")
    b2.metric("총 지출", f"{total_expense:,}원")
    b3.metric("총 잔액", f"{total_balance:,}원", delta=f"{(total_income - total_expense):,}원")
    goal_progress = (total_balance / save_goal * 100) if save_goal > 0 else 0
    st.metric("월 저축 목표 달성률", f"{goal_progress:.1f}%")
    if save_goal > 0:
        if total_balance >= save_goal: st.success("목표 저축액을 달성했어요.")
        else: st.info(f"목표까지 {save_goal - total_balance:,}원 남았어요.")
    tx_budget = st.session_state.transaction_records.copy()
    if not tx_budget.empty:
        tx_budget["금액"] = pd.to_numeric(tx_budget["금액"], errors="coerce").fillna(0).astype(int)
        tx_budget = tx_budget[tx_budget["구분"] == "지출"]
        budget_rows = []
        for cat, budget in budgets.items():
            used = int(tx_budget[tx_budget["카테고리"] == cat]["금액"].sum())
            ratio = (used / budget * 100) if budget > 0 else 0
            budget_rows.append({"카테고리": cat, "예산": budget, "사용": used, "사용률(%)": round(ratio, 1)})
        budget_df = pd.DataFrame(budget_rows)
        st.dataframe(budget_df, use_container_width=True)
        for _, row in budget_df.iterrows():
            if row["사용률(%)"] >= 100: st.error(f"{row['카테고리']} 예산 초과: {int(row['사용']):,}원 / {int(row['예산']):,}원")
            elif row["사용률(%)"] >= 80: st.warning(f"{row['카테고리']} 예산 80% 이상 사용")
    else:
        st.info("예산 사용률을 보려면 거래 내역을 먼저 추가해주세요.")
    st.markdown("</div>", unsafe_allow_html=True)

with tab3:
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<p class="small-muted">재정 관리부터 일상 질문까지 AI 비서에게 무엇이든 물어보세요.</p>', unsafe_allow_html=True)
    if not open_chat:
        st.info("사이드바에서 '챗봇 열기'를 켜면 사용할 수 있어요.")
    else:
        chat_html = ""
        for msg in st.session_state.chat_history:
            if msg["role"] == "user":
                chat_html += f'<div style="text-align:right;margin:6px 0;"><span style="background:#0ea5e9;color:white;padding:6px 12px;border-radius:12px;display:inline-block;max-width:80%;">{msg["content"]}</span></div>'
            else:
                chat_html += f'<div style="text-align:left;margin:6px 0;"><span style="background:#f3f4f6;color:#111;padding:6px 12px;border-radius:12px;display:inline-block;max-width:80%;white-space:pre-wrap;">{msg["content"]}</span></div>'
        st.markdown(f"""
        <div style="height:420px;overflow-y:auto;border:1px solid #e5e7eb;border-radius:10px;padding:12px;background:#fff;margin-bottom:8px;">
            {chat_html}
        </div>
        """, unsafe_allow_html=True)
        user_input = st.chat_input("메시지를 입력하세요")
        if user_input:
            st.session_state.chat_history.append({"role": "user", "content": user_input})
            tx_for_chat = st.session_state.transaction_records.copy()
            _income = int(tx_for_chat[tx_for_chat["구분"] == "수입"]["금액"].sum())
            _expense = int(tx_for_chat[tx_for_chat["구분"] == "지출"]["금액"].sum())
            answer = local_finance_chatbot(
                user_input, _income, _expense, _income - _expense,
                tx_df=st.session_state.transaction_records,
                budgets=budgets,
            )
            st.session_state.chat_history.append({"role": "assistant", "content": answer})
            st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

with tab4:
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.markdown('<p class="small-muted">영수증 사진을 올리면 텍스트/금액/카테고리를 추정합니다.</p>', unsafe_allow_html=True)
    receipt_files = st.file_uploader("영수증 이미지 업로드", type=["png", "jpg", "jpeg", "webp"], key=f"receipt_uploader_{st.session_state.uploader_key}", accept_multiple_files=True)
    st.info("📸 인식률을 높이려면: 영수증을 평평하게 펴고 화면에 꽉차게 정면으로 촬영해주세요.")
    if receipt_files:
        if st.button("OCR 분석 실행", use_container_width=True):
            results = []
            for receipt_file in receipt_files:
                try:
                    ocr_text = extract_receipt_text(receipt_file)
                    merchant, total_amount, category = parse_receipt_info(ocr_text)
                    new_rc = {"메뉴": merchant, "추정금액": total_amount, "카테고리": category, "OCR원문": ocr_text[:4000]}
                    add_receipt(user_id, new_rc)
                    st.session_state.receipt_records = load_receipts(user_id)
                    results.append({"파일": receipt_file.name, "메뉴": merchant, "추정금액": f"{total_amount:,}원", "카테고리": category})
                except Exception as e:
                    results.append({"파일": receipt_file.name, "메뉴": "오류", "추정금액": "-", "카테고리": str(e)})
            save_records()
            st.session_state.ocr_results = results
            st.success(f"{len(receipt_files)}개 분석 완료!")
    if st.session_state.ocr_results:
        st.table(pd.DataFrame(st.session_state.ocr_results))
        # 항목별 삭제
        valid_ocr = [r for r in st.session_state.ocr_results if r["메뉴"] != "오류"]
        if valid_ocr:
            ocr_labels = [f"{r['메뉴']} · {r['추정금액']}" for r in valid_ocr]
            sel_ocr = st.selectbox("삭제할 항목", ocr_labels, key="del_ocr_sel", label_visibility="collapsed")
            if st.button("🗑️ 삭제", key="del_ocr_btn", use_container_width=True):
                idx = ocr_labels.index(sel_ocr)
                st.session_state.ocr_results.pop(
                    st.session_state.ocr_results.index(valid_ocr[idx])
                )
                st.session_state.uploader_key += 1
                st.rerun()
        if st.button("지출 내역에 추가", use_container_width=True):
            added = 0
            for r in st.session_state.ocr_results:
                if r["메뉴"] == "오류":
                    continue
                try:
                    amount = int(r["추정금액"].replace(",", "").replace("원", ""))
                except ValueError:
                    continue
                new_row = {"날짜": pd.to_datetime(date.today()), "구분": "지출", "카테고리": r["카테고리"], "금액": amount, "메모": r["메뉴"]}
                add_transaction(user_id, new_row)
                added += 1
            st.session_state.transaction_records = load_transactions(user_id)
            st.session_state.ocr_results = []
            st.session_state.uploader_key += 1
            st.success(f"{added}건을 지출 내역에 추가했어요.")
            st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)