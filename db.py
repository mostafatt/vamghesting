import os
import asyncio
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


async def add_loan(user_id: int, borrower_name: str, total_amount: float, installment_amount: float, total_installments: int):
    data = {
        "user_id": user_id,
        "borrower_name": borrower_name,
        "total_amount": total_amount,
        "installment_amount": installment_amount,
        "total_installments": total_installments,
        "paid_installments": 0,
        "status": "active",
        "created_at": datetime.utcnow().isoformat()
    }
    res = await asyncio.to_thread(lambda: supabase.table("loans").insert(data).execute())
    return res.data[0] if res.data else None


async def create_schedule(loan_id: int, total_installments: int, installment_amount: float, start_date: date = None):
    if start_date is None:
        start_date = date.today()

    records = []
    for i in range(1, total_installments + 1):
        due = start_date + relativedelta(months=i)
        records.append({
            "loan_id": loan_id,
            "number": i,
            "amount": installment_amount,
            "due_date": due.isoformat(),
            "paid": False
        })

    res = await asyncio.to_thread(lambda: supabase.table("installments").insert(records).execute())
    return res.data


async def get_loans_by_user(user_id: int):
    res = await asyncio.to_thread(lambda: supabase.table("loans").select("*").eq("user_id", user_id).execute())
    return res.data or []


async def get_loan_by_id(loan_id: int):
    res = await asyncio.to_thread(lambda: supabase.table("loans").select("*").eq("id", loan_id).execute())
    return res.data[0] if res.data else None


async def get_installments_by_loan(loan_id: int):
    res = await asyncio.to_thread(
        lambda: supabase.table("installments").select("*").eq("loan_id", loan_id).order("number").execute()
    )
    return res.data or []


async def mark_installment_paid(installment_id: int, loan_id: int):
    now = datetime.utcnow().isoformat()
    # 1. تغییر وضعیت قسط
    await asyncio.to_thread(
        lambda: supabase.table("installments").update({"paid": True, "paid_at": now}).eq("id", installment_id).execute()
    )

    # 2. به‌روزرسانی تعداد اقساط پرداخت شده در جدول وام
    loan = await get_loan_by_id(loan_id)
    if loan:
        paid_count = loan.get("paid_installments", 0) + 1
        update_data = {"paid_installments": paid_count}
        if paid_count >= loan.get("total_installments", 0):
            update_data["status"] = "settled"

        await asyncio.to_thread(
            lambda: supabase.table("loans").update(update_data).eq("id", loan_id).execute()
        )
        loan.update(update_data)
        return loan
    return None


async def get_upcoming_unpaid(days_ahead: int = 3):
    today = date.today().isoformat()
    target = (date.today() + relativedelta(days=days_ahead)).isoformat()

    res = await asyncio.to_thread(
        lambda: supabase.table("installments")
        .select("*, loans(*)")
        .eq("paid", False)
        .gte("due_date", today)
        .lte("due_date", target)
        .execute()
    )
    return res.data or []
