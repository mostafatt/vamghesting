import os
import asyncio
from datetime import datetime, date, timedelta

from supabase import create_client, Client


SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL or SUPABASE_KEY is missing")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


async def add_loan(
    user_id: int,
    borrower_name: str,
    total_amount: float,
    installment_amount: float,
    total_installments: int,
    first_due_date: date,
    reminder_days: int = 5,
):
    data = {
        "user_id": user_id,
        "borrower_name": borrower_name,
        "total_amount": total_amount,
        "installment_amount": installment_amount,
        "total_installments": total_installments,
        "paid_installments": 0,
        "status": "active",
        "first_due_date": first_due_date.isoformat(),
        "reminder_days": reminder_days,
        "created_at": datetime.utcnow().isoformat(),
    }

    res = await asyncio.to_thread(
        lambda: supabase.table("loans").insert(data).execute()
    )

    return res.data[0] if res.data else None


async def create_schedule(
    loan_id: int,
    total_installments: int,
    installment_amount: float,
    start_date: date,
    reminder_days: int = 5,
):
    records = []

    for i in range(total_installments):
        due_date = start_date + timedelta(days=30 * i)
        reminder_date = due_date - timedelta(days=reminder_days)

        records.append(
            {
                "loan_id": loan_id,
                "number": i + 1,
                "amount": installment_amount,
                "due_date": due_date.isoformat(),
                "reminder_date": reminder_date.isoformat(),
                "reminder_sent": False,
                "paid": False,
            }
        )

    res = await asyncio.to_thread(
        lambda: supabase.table("installments").insert(records).execute()
    )

    return res.data or []


async def get_loans_by_user(user_id: int):
    res = await asyncio.to_thread(
        lambda: supabase.table("loans")
        .select("*")
        .eq("user_id", user_id)
        .order("id", desc=True)
        .execute()
    )

    return res.data or []


async def get_active_loans(user_id: int):
    res = await asyncio.to_thread(
        lambda: supabase.table("loans")
        .select("*")
        .eq("user_id", user_id)
        .neq("status", "settled")
        .order("id", desc=True)
        .execute()
    )

    return res.data or []


async def get_loan_by_id(loan_id: int, user_id: int = None):
    query = supabase.table("loans").select("*").eq("id", loan_id)

    if user_id is not None:
        query = query.eq("user_id", user_id)

    res = await asyncio.to_thread(lambda: query.execute())

    return res.data[0] if res.data else None


async def get_installments_by_loan(loan_id: int):
    res = await asyncio.to_thread(
        lambda: supabase.table("installments")
        .select("*")
        .eq("loan_id", loan_id)
        .order("number")
        .execute()
    )

    return res.data or []


async def mark_installment_paid(
    installment_id: int,
    loan_id: int,
    user_id: int = None,
):
    loan = await get_loan_by_id(loan_id, user_id)

    if not loan:
        return None

    installment_res = await asyncio.to_thread(
        lambda: supabase.table("installments")
        .select("*")
        .eq("id", installment_id)
        .eq("loan_id", loan_id)
        .execute()
    )

    installment = (
        installment_res.data[0] if installment_res.data else None
    )

    if not installment:
        return None

    if installment.get("paid"):
        return loan

    now = datetime.utcnow().isoformat()

    await asyncio.to_thread(
        lambda: supabase.table("installments")
        .update(
            {
                "paid": True,
                "paid_at": now,
            }
        )
        .eq("id", installment_id)
        .eq("loan_id", loan_id)
        .execute()
    )

    paid_count = loan.get("paid_installments", 0) + 1

    update_data = {
        "paid_installments": paid_count,
    }

    if paid_count >= loan.get("total_installments", 0):
        update_data["status"] = "settled"

    await asyncio.to_thread(
        lambda: supabase.table("loans")
        .update(update_data)
        .eq("id", loan_id)
        .execute()
    )

    loan.update(update_data)

    return loan


async def get_installments_for_reminder():
    today = date.today().isoformat()

    res = await asyncio.to_thread(
        lambda: supabase.table("installments")
        .select("*, loans(*)")
        .eq("paid", False)
        .eq("reminder_sent", False)
        .eq("reminder_date", today)
        .execute()
    )

    return res.data or []


async def mark_reminder_sent(installment_id: int):
    await asyncio.to_thread(
        lambda: supabase.table("installments")
        .update({"reminder_sent": True})
        .eq("id", installment_id)
        .execute()
    )


async def get_upcoming_unpaid(days_ahead: int = 3):
    today = date.today()
    target = today + timedelta(days=days_ahead)

    res = await asyncio.to_thread(
        lambda: supabase.table("installments")
        .select("*, loans(*)")
        .eq("paid", False)
        .gte("due_date", today.isoformat())
        .lte("due_date", target.isoformat())
        .execute()
    )

    return res.data or []
