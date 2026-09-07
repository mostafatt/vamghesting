import os
import asyncio
from datetime import datetime
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


async def add_loan(
    borrower_name: str,
    amount: float,
    interest_rate: float,
    duration: int,
    installment_amount: float,
) -> int:
    data = {
        "borrower_name": borrower_name,
        "amount": amount,
        "interest_rate": interest_rate,
        "duration": duration,
        "installment_amount": installment_amount,
        "paid_installments": 0,
        "status": "active",
        "created_at": datetime.utcnow().isoformat(),
    }
    # اجرای فراخوانی supabase در thread مجزا برای جلوگیری از بلاک شدن event loop
    result = await asyncio.to_thread(lambda: _client.table("loans").insert(data).execute())
    if result.data and len(result.data) > 0:
        return result.data[0]["id"]
    raise RuntimeError("خطا در درج وام در Supabase. داده‌ای برگشت داده نشد.")


async def get_active_loans() -> list:
    result = await asyncio.to_thread(
        lambda: _client.table("loans")
        .select("*")
        .eq("status", "active")
        .order("created_at", desc=False)
        .execute()
    )
    return result.data or []


async def get_loan_by_id(loan_id: int) -> dict | None:
    result = await asyncio.to_thread(
        lambda: _client.table("loans").select("*").eq("id", loan_id).execute()
    )
    return result.data[0] if result.data else None


async def add_installment(loan_id: int, amount: float) -> None:
    # ثبت قسط پرداختی
    await asyncio.to_thread(
        lambda: _client.table("installments")
        .insert(
            {
                "loan_id": loan_id,
                "amount": amount,
                "paid_at": datetime.utcnow().isoformat(),
            }
        )
        .execute()
    )

    # افزایش شمارنده اقساط پرداخت شده
    loan = await get_loan_by_id(loan_id)
    if loan:
        new_paid_count = loan.get("paid_installments", 0) + 1
        await asyncio.to_thread(
            lambda: _client.table("loans")
            .update({"paid_installments": new_paid_count})
            .eq("id", loan_id)
            .execute()
        )


async def get_installments(loan_id: int) -> list:
    result = await asyncio.to_thread(
        lambda: _client.table("installments")
        .select("*")
        .eq("loan_id", loan_id)
        .order("paid_at", desc=False)
        .execute()
    )
    return result.data or []


async def close_loan(loan_id: int) -> None:
    await asyncio.to_thread(
        lambda: _client.table("loans").update({"status": "closed"}).eq("id", loan_id).execute()
    )


async def get_due_loans() -> list:
    """دریافت وام‌های فعالی که هنوز اقساط پرداخت‌نشده دارند."""
    result = await asyncio.to_thread(
        lambda: _client.table("loans").select("*").eq("status", "active").execute()
    )
    loans = result.data or []
    return [l for l in loans if l.get("paid_installments", 0) < l.get("duration", 0)]
