import pandas as pd

print("===== 자취생 소비 관리 프로그램 =====")

records = []

while True:
    income_text = input("수입 입력 (종료: 0): ")

    if income_text == "0":
        break

    if not income_text.isdigit():
        print("수입은 숫자만 입력하세요.")
        continue

    expense_text = input("지출 입력: ")

    if not expense_text.isdigit():
        print("지출은 숫자만 입력하세요.")
        continue

    income = int(income_text)
    expense = int(expense_text)

    records.append({
        "수입": income,
        "지출": expense
    })

if len(records) == 0:
    print("입력된 기록이 없습니다.")
else:
    df = pd.DataFrame(records)
    df["잔액"] = df["수입"] - df["지출"]

    print("\n===== 전체 기록 =====")
    print(df)

    print("\n총 수입:", df["수입"].sum())
    print("총 지출:", df["지출"].sum())
    print("총 잔액:", df["잔액"].sum())

    avg_expense = df["지출"].mean()

    print("\n===== 소비 분석 =====")

    if df["잔액"].sum() < 0:
        print("⚠️ 전체적으로 적자입니다.")
    elif avg_expense > 20000:
        print("⚠️ 지출이 많은 편입니다.")
    else:
        print("✅ 소비 패턴이 안정적입니다.")