import os
import asyncio
from datetime import datetime, date, timedelta
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

_client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


async def add_loan(borrower_name, amount, interest_rate, duration, installment_amount) -> int:
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
result = await asyncio.to_thread(lambda: _client.table("loans").insert(data).execute())
if result.data:
return result.data[0]["id"]
raise RuntimeError("خطا در درج وام در Supabase. داده‌ای برگشت داده نشد.")


async def get_active_loans() -> list:
result = await asyncio.to_thread(
lambda: _client.table("loans").select("*").eq("status", "active")
.order("created_at", desc=False).execute()
)
return result.data or []


async def get_loan_by_id(loan_id: int) -> dict | None:
result = await asyncio.to_thread(
lambda: _client.table("loans").select("*").eq("id", loan_id).execute()
)
return result.data[0] if result.data else None


# ─── اقساط زمان‌بندی‌شده ────────────────────────────────────────────
async def create_schedule(loan_id: int, count: int, amount: float, interval_days: int = 30):
"""ساخت اقساط با تاریخ سررسید ماهانه، از یک ماه دیگر شروع می‌شود."""
today = date.today()
rows = [
{
"loan_id": loan_id,
"number": i + 1,
"amount": amount,
"due_date": str(today + timedelta(days=interval_days * (i + 1))),
"paid": False,
}
for i in range(count)
]
await asyncio.to_thread(lambda: _client.table("installments").insert(rows).execute())


async def get_installments(loan_id: int) -> list:
result = await asyncio.to_thread(
lambda: _client.table("installments").select("*").eq("loan_id", loan_id)
.order("number", desc=False).execute()
)
return result.data or []


async def get_installment_by_id(inst_id: int) -> dict | None:
result = await asyncio.to_thread(
lambda: _client.table("installments").select("*").eq("id", inst_id).execute()
)
return result.data[0] if result.data else None


async def mark_installment_paid(inst_id: int) -> None:
"""تیک زدن یک قسط: paid=true + ثبت paid_at + به‌روزرسانی شمارنده وام"""
now = datetime.utcnow().isoformat()
await asyncio.to_thread(
lambda: _client.table("installments")
.update({"paid": True, "paid_at": now}).eq("id", inst_id).execute()
)
inst = await get_installment_by_id(inst_id)
if inst:
loan = await get_loan_by_id(inst["loan_id"])
if loan:
new_count = loan.get("paid_installments", 0) + 1
updates = {"paid_installments": new_count}
# اگر همه اقساط پرداخت شد، وام بسته شود
if new_count >= loan.get("duration", 0):
updates["status"] = "closed"
await asyncio.to_thread(
lambda: _client.table("loans").update(updates).eq("id", loan["id"]).execute()
)


async def close_loan(loan_id: int) -> None:
await asyncio.to_thread(
lambda: _client.table("loans").update({"status": "closed"}).eq("id", loan_id).execute()
)


async def get_upcoming_unpaid(days_ahead: int = 3) -> list:
"""اقساط پرداخت‌نشده که تا n روز آینده سررسید می‌شوند (برای یادآوری)"""
today = date.today()
limit = today + timedelta(days=days_ahead)
result = await asyncio.to_thread(
lambda: _client.table("installments")
.select("*, loans(borrower_name)")
.eq("paid", False)
.gte("due_date", str(today - timedelta(days=7)))   # عقب‌مانده‌های اخیر هم یادآوری شوند
.lte("due_date", str(limit))
.order("due_date", desc=False).execute()
)
return result.data or []
