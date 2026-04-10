import streamlit as st
import pandas as pd
import altair as alt
from typing import List
from io import BytesIO
import re
from PIL import Image
from datetime import date
from pathlib import Path


def extract_receipt_text(image_file) -> str:
    try:
        import pytesseract
    except ImportError:
        raise RuntimeError("pytesseract_not_installed")

    image_bytes = image_file.read()
    image = Image.open(BytesIO(image_bytes)).convert("L")
    text = pytesseract.image_to_string(image, lang="kor+eng")
    return text.strip()


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
    merchant = lines[0] if lines else "알 수 없음"

    amount_candidates = []
    for match in re.findall(r"\d[\d,]{2,}", text):
        try:
            amount_candidates.append(int(match.replace(",", "")))
        except ValueError:
            continue
    total_amount = max(amount_candidates) if amount_candidates else 0
    category = guess_category(text)
    return merchant, total_amount, category


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
    st.session_state.transaction_records.to_csv(TRANSACTION_CSV, index=False)
    st.session_state.receipt_records.to_csv(RECEIPT_CSV, index=False)


def local_finance_chatbot(user_input: str, total_income: int, total_expense: int, total_balance: int) -> str:
    text = user_input.strip().lower()
    tips: List[str] = []

    if total_income == 0 and total_expense == 0:
        return (
            "아직 소비 데이터가 없어요. 먼저 수입/지출을 입력하고 계산하기를 눌러주세요.\n\n"
            "- 시작 팁: 고정비(월세/통신비)와 변동비(식비/배달비)를 나눠서 기록하면 관리가 쉬워요."
        )

    if "분석" in text or "상태" in text or "어때" in text:
        if total_balance < 0:
            return (
                f"현재는 적자 상태예요. (잔액 {total_balance:,}원)\n\n"
                "- 1순위: 식비/배달비 상한을 정해서 즉시 지출을 줄여보세요.\n"
                "- 2순위: 구독/자동결제 항목을 점검해서 불필요한 항목을 해지하세요."
            )
        if total_balance < max(10000, int(total_income * 0.1)):
            return (
                f"현재 잔액은 {total_balance:,}원으로 여유가 크지 않아요.\n\n"
                "- 다음 지출 전 '필수/선택' 체크를 하고 결제하세요.\n"
                "- 이번 주 예산을 미리 정하면 과소비를 줄일 수 있어요."
            )
        return (
            f"좋아요. 현재 잔액은 {total_balance:,}원으로 비교적 안정적이에요.\n\n"
            "- 남는 금액의 일부를 비상금으로 따로 분리해보세요.\n"
            "- 다음 달 목표 저축액을 정하면 소비가 더 안정됩니다."
        )

    if "절약" in text or "아껴" in text or "돈" in text:
        tips.extend(
            [
                "배달/카페는 주간 횟수 제한을 두면 체감 절약이 큽니다.",
                "장보기 전 목록을 고정하면 충동구매를 줄일 수 있어요.",
                "소액 결제도 하루 합계를 확인하면 지출 통제가 쉬워집니다.",
            ]
        )

    if "식비" in text or "밥" in text:
        tips.extend(
            [
                "식비는 '외식/배달/장보기' 3개로 나눠 관리해보세요.",
                "주 1회 밀프렙(미리 식사 준비)만 해도 배달비를 크게 줄일 수 있어요.",
            ]
        )

    if "고정비" in text or "구독" in text or "통신비" in text:
        tips.extend(
            [
                "고정비는 한 달 1회 점검 루틴을 만들어 자동결제를 정리해보세요.",
                "통신/구독은 사용 빈도 기준으로 유지 여부를 결정하면 좋아요.",
            ]
        )

    if not tips:
        tips = [
            f"현재 요약: 수입 {total_income:,}원 / 지출 {total_expense:,}원 / 잔액 {total_balance:,}원",
            "질문 예시: '내 소비 상태 분석해줘', '식비 줄이는 방법 알려줘', '고정비 절약 팁 줘'",
            "원하면 소비 항목별 예산표(식비/교통/생활비) 기준도 같이 만들어줄게요.",
        ]

    return "\n".join(f"- {tip}" for tip in tips)

st.set_page_config(
    page_title="자취생 소비 관리",
    page_icon="💸",
    layout="wide",
)

st.markdown(
    """
    <style>
        .block-container {
            padding-top: 2rem;
            padding-bottom: 2rem;
            max-width: 1180px;
        }
        .hero-card {
            background: linear-gradient(135deg, #0ea5e9 0%, #6366f1 100%);
            color: white;
            border-radius: 18px;
            padding: 20px 24px;
            margin-bottom: 14px;
        }
        .section-card {
            background: #ffffff;
            border: 1px solid #e5e7eb;
            border-radius: 14px;
            padding: 14px 16px;
            margin-bottom: 14px;
        }
        .small-muted {
            font-size: 0.9rem;
            color: #6b7280;
            margin-top: -4px;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "last_df" not in st.session_state:
    st.session_state.last_df = pd.DataFrame(columns=["수입", "지출", "잔액"])
if "receipt_records" not in st.session_state:
    st.session_state.receipt_records = load_csv_or_empty(
        RECEIPT_CSV, ["가맹점", "추정금액", "카테고리", "OCR원문"]
    )
if "transaction_records" not in st.session_state:
    st.session_state.transaction_records = load_csv_or_empty(
        TRANSACTION_CSV, ["날짜", "구분", "카테고리", "금액", "메모"]
    )
if "last_ocr_result" not in st.session_state:
    st.session_state.last_ocr_result = None

st.markdown(
    """
    <div class="hero-card">
        <h2 style="margin: 0;">💸 자취생 소비 관리 AI</h2>
        <p style="margin: 6px 0 0 0; opacity: 0.95;">
            예산 상태를 빠르게 확인하고, 소비 상담 챗봇으로 맞춤 조언까지 받아보세요.
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("설정")
    st.caption("외부 API 없이 동작하는 로컬 챗봇 모드입니다.")
    st.markdown("### 월 예산 설정")
    budget_food = st.number_input("식비 예산", min_value=0, step=10000, value=300000)
    budget_transport = st.number_input("교통 예산", min_value=0, step=10000, value=100000)
    budget_life = st.number_input("생활 예산", min_value=0, step=10000, value=150000)
    budget_sub = st.number_input("구독 예산", min_value=0, step=10000, value=50000)
    save_goal = st.number_input("월 저축 목표", min_value=0, step=50000, value=300000)

    open_chat = st.toggle("💬 챗봇 열기", value=True)
    if st.button("대화 초기화", use_container_width=True):
        st.session_state.chat_history = []
        st.success("채팅 기록을 초기화했어요.")
    if st.button("입출금 내역 초기화", use_container_width=True):
        st.session_state.transaction_records = pd.DataFrame(
            columns=["날짜", "구분", "카테고리", "금액", "메모"]
        )
        save_records()
        st.success("입출금 내역을 초기화했어요.")

left_col, right_col = st.columns([1.2, 1], gap="large")

with left_col:
    transactions = st.session_state.transaction_records.copy()
    total_income = int(transactions[transactions["구분"] == "수입"]["금액"].sum())
    total_expense = int(transactions[transactions["구분"] == "지출"]["금액"].sum())
    total_balance = total_income - total_expense

    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.subheader("📅 날짜별 입출금 내역")
    st.markdown(
        '<p class="small-muted">토스처럼 날짜 기준으로 수입/지출 흐름을 기록하고 확인할 수 있어요.</p>',
        unsafe_allow_html=True,
    )

    t1, t2 = st.columns(2)
    with t1:
        tx_date = st.date_input("거래 날짜", value=date.today())
        tx_type = st.selectbox("구분", options=["수입", "지출"])
    with t2:
        tx_category = st.selectbox(
            "카테고리",
            options=["식비", "교통", "생활", "구독", "월급", "용돈", "기타"],
        )
        tx_amount = st.number_input("금액 (원)", min_value=0, step=1000, format="%d", key="tx_amount")
    tx_memo = st.text_input("메모(선택)", placeholder="예: 점심, 카페, 알바비")

    if st.button("내역 추가", use_container_width=True):
        if tx_amount <= 0:
            st.warning("금액은 1원 이상 입력해주세요.")
        else:
            new_tx = pd.DataFrame(
                [
                    {
                        "날짜": pd.to_datetime(tx_date),
                        "구분": tx_type,
                        "카테고리": tx_category,
                        "금액": int(tx_amount),
                        "메모": tx_memo.strip(),
                    }
                ]
            )
            st.session_state.transaction_records = pd.concat(
                [st.session_state.transaction_records, new_tx], ignore_index=True
            )
            save_records()
            st.success("입출금 내역이 추가됐어요.")

    tx_df = st.session_state.transaction_records.copy()
    if not tx_df.empty:
        tx_df["날짜"] = pd.to_datetime(tx_df["날짜"])
        tx_df["금액"] = pd.to_numeric(tx_df["금액"], errors="coerce").fillna(0).astype(int)
        daily = (
            tx_df.assign(
                수입금액=tx_df.apply(lambda row: row["금액"] if row["구분"] == "수입" else 0, axis=1),
                지출금액=tx_df.apply(lambda row: row["금액"] if row["구분"] == "지출" else 0, axis=1),
            )
            .groupby(tx_df["날짜"].dt.date)[["수입금액", "지출금액"]]
            .sum()
            .reset_index()
            .rename(columns={"날짜": "일자"})
        )
        daily["순변동"] = daily["수입금액"] - daily["지출금액"]
        daily["일자표시"] = pd.to_datetime(daily["일자"]).map(lambda d: f"{d.month}/{d.day}")

        st.write("### 일자별 요약")
        st.dataframe(daily[["일자", "수입금액", "지출금액", "순변동"]], use_container_width=True)
        daily_chart_data = daily[["일자표시", "지출금액"]].copy()
        daily_chart = (
            alt.Chart(daily_chart_data)
            .mark_line(point=True)
            .encode(
                x=alt.X("일자표시:N", sort=list(daily["일자표시"]), axis=alt.Axis(labelAngle=0, title="일자")),
                y=alt.Y("지출금액:Q", title="지출 금액"),
                tooltip=["일자표시:N", "지출금액:Q"],
            )
        )
        st.altair_chart(daily_chart, use_container_width=True)

        tx_view = tx_df.sort_values("날짜", ascending=False).copy()
        tx_view["날짜"] = tx_view["날짜"].dt.strftime("%Y-%m-%d")
        st.write("### 거래 내역")
        st.dataframe(tx_view, use_container_width=True)

        st.write("### 반복지출 감지")
        expense_df = tx_df[tx_df["구분"] == "지출"].copy()
        repeat = (
            expense_df.groupby(["카테고리"], dropna=False)
            .size()
            .reset_index(name="횟수")
            .sort_values("횟수", ascending=False)
        )
        repeat = repeat[repeat["횟수"] >= 3]
        if repeat.empty:
            st.info("반복지출 후보가 아직 없어요. 같은 카테고리 지출이 3회 이상 쌓이면 표시됩니다.")
        else:
            st.dataframe(repeat.head(10), use_container_width=True)
    else:
        st.info("아직 등록된 입출금 내역이 없어요. 위에서 내역을 추가해보세요.")
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.subheader("🎯 목표/예산 상태")
    b1, b2, b3 = st.columns(3)
    b1.metric("총 수입", f"{total_income:,}원")
    b2.metric("총 지출", f"{total_expense:,}원")
    b3.metric("총 잔액", f"{total_balance:,}원", delta=f"{(total_income - total_expense):,}원")

    goal_progress = (total_balance / save_goal * 100) if save_goal > 0 else 0
    st.metric("월 저축 목표 달성률", f"{goal_progress:.1f}%")
    if save_goal > 0:
        if total_balance >= save_goal:
            st.success("목표 저축액을 달성했어요.")
        else:
            st.info(f"목표까지 {save_goal - total_balance:,}원 남았어요.")

    budgets = {
        "식비": budget_food,
        "교통": budget_transport,
        "생활": budget_life,
        "구독": budget_sub,
    }
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
            if row["사용률(%)"] >= 100:
                st.error(f"{row['카테고리']} 예산 초과: {int(row['사용']):,}원 / {int(row['예산']):,}원")
            elif row["사용률(%)"] >= 80:
                st.warning(f"{row['카테고리']} 예산 80% 이상 사용")
    else:
        st.info("예산 사용률을 보려면 거래 내역을 먼저 추가해주세요.")
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.subheader("📷 영수증 OCR 분석")
    st.markdown(
        '<p class="small-muted">영수증 사진을 올리면 텍스트/금액/카테고리를 추정합니다.</p>',
        unsafe_allow_html=True,
    )
    receipt_file = st.file_uploader(
        "영수증 이미지 업로드",
        type=["png", "jpg", "jpeg", "webp"],
        key="receipt_uploader",
    )

    if receipt_file is not None:
        st.image(receipt_file, caption="업로드된 영수증", use_container_width=True)
        if st.button("OCR 분석 실행", use_container_width=True):
            try:
                ocr_text = extract_receipt_text(receipt_file)
                merchant, total_amount, category = parse_receipt_info(ocr_text)
                item_df = parse_receipt_items(ocr_text)

                new_row = pd.DataFrame(
                    [
                        {
                            "가맹점": merchant,
                            "추정금액": total_amount,
                            "카테고리": category,
                            "OCR원문": ocr_text[:4000],
                        }
                    ]
                )
                st.session_state.receipt_records = pd.concat(
                    [st.session_state.receipt_records, new_row], ignore_index=True
                )
                save_records()
                st.session_state.last_ocr_result = {
                    "가맹점": merchant,
                    "추정금액": int(total_amount),
                    "카테고리": category,
                }

                st.success("OCR 분석을 완료했어요.")
                c1, c2, c3 = st.columns(3)
                c1.metric("가맹점", merchant)
                c2.metric("추정금액", f"{total_amount:,}원")
                c3.metric("카테고리", category)
                st.text_area("추출 텍스트", ocr_text, height=180)
                if item_df.empty:
                    st.info("품목 라인 자동 인식이 어려웠어요. 영수증 형식에 따라 일부만 인식될 수 있어요.")
                else:
                    st.write("### 인식된 소비 품목")
                    st.dataframe(item_df, use_container_width=True)

                if total_amount > 0 and st.button("이 영수증을 지출 내역에 추가", use_container_width=True):
                    ocr_tx = pd.DataFrame(
                        [
                            {
                                "날짜": pd.to_datetime(date.today()),
                                "구분": "지출",
                                "카테고리": category,
                                "금액": int(total_amount),
                                "메모": f"OCR:{merchant}",
                            }
                        ]
                    )
                    st.session_state.transaction_records = pd.concat(
                        [st.session_state.transaction_records, ocr_tx], ignore_index=True
                    )
                    save_records()
                    st.success("영수증 지출이 거래 내역에 추가됐어요.")
            except RuntimeError as err:
                if str(err) == "pytesseract_not_installed":
                    st.error(
                        "pytesseract가 설치되지 않았어요. 터미널에서 `pip install pytesseract pillow` 실행 후 재시도해주세요."
                    )
                    st.info(
                        "추가로 윈도우에 Tesseract OCR 프로그램 설치가 필요해요: https://github.com/UB-Mannheim/tesseract/wiki"
                    )
                else:
                    st.error("OCR 처리 중 오류가 발생했어요. 이미지 품질을 확인해주세요.")
            except Exception:
                st.error("OCR 처리 중 오류가 발생했어요. 이미지 품질을 확인해주세요.")

    if not st.session_state.receipt_records.empty:
        st.write("### 영수증 분석 기록")
        st.dataframe(
            st.session_state.receipt_records[["가맹점", "추정금액", "카테고리"]],
            use_container_width=True,
        )
    st.markdown("</div>", unsafe_allow_html=True)

with right_col:
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.subheader("🤖 소비 상담 챗봇")
    st.markdown('<p class="small-muted">소비 고민을 입력하면 AI가 현실적인 절약 팁을 제안해요.</p>', unsafe_allow_html=True)

    if not open_chat:
        st.info("사이드바에서 '챗봇 열기'를 켜면 사용할 수 있어요.")
    else:
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]):
                st.write(msg["content"])

        user_input = st.chat_input("메시지를 입력하세요")
        if user_input:
            st.session_state.chat_history.append({"role": "user", "content": user_input})
            with st.chat_message("user"):
                st.write(user_input)

            tx_for_chat = st.session_state.transaction_records.copy()
            total_income = int(tx_for_chat[tx_for_chat["구분"] == "수입"]["금액"].sum())
            total_expense = int(tx_for_chat[tx_for_chat["구분"] == "지출"]["금액"].sum())
            total_balance = total_income - total_expense

            answer = local_finance_chatbot(
                user_input=user_input,
                total_income=total_income,
                total_expense=total_expense,
                total_balance=total_balance,
            )

            st.session_state.chat_history.append({"role": "assistant", "content": answer})
            with st.chat_message("assistant"):
                st.write(answer)
    st.markdown("</div>", unsafe_allow_html=True)