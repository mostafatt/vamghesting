# -*- coding: utf-8 -*-
"""لایه دسترسی به دیتابیس Supabase برای ربات وام."""
import os
import logging
from datetime import date, timedelta
from typing import Optional

from supabase import create_client, Client

logger = logging.getLogger("loan_bot.db")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

_client: Optional[Client] = None

def _sb() -> Client:
    """ساخت/بازگرداندن کلاینت Supabase (سینگلتون)."""
    global _client
    if _client is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise RuntimeError("SUPABASE_URL / SUPABASE_KEY تنظیم نشده‌اند.")
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _client

async def create_loan(name: str, total_amount: int, installments_count: int, first_due) -> dict:
    """ایجاد وام + تولید خودکار اقساط ماهانه هم‌اندازه (باقیمانده در قسط آخر)."""
    sb = _sb()
    per = total_amount // installments_count
    res = sb.table("loans").insert({
        "name": name,
        "total_amount": total_amount,
        "installments_count": installments_count,
        "status": "active",
    }).execute()
    loan = res.data[0]
    rows = []
    for i in range(1, installments_count + 1):
        amount = per if i < installments_count else total_amount - per * (installments_count - 1)
        # افزودن ماه به‌صورت دستی (تا سال/ماه درست پیش برود)
        y = first_due.year + (first_due.month - 1 + (i - 1)) // 12
        m = (first_due.month - 1 + (i - 1)) % 12 + 1
        d = min(first_due.day, [31, 29 if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 28,
                                31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
        rows.append({"loan_id": loan["id"], "installment_no": i,
                     "amount": amount, "due_date": date(y, m, d).isoformat(),
                     "status": "pending", "reminded_date": None})
    sb.table("installments").insert(rows).execute()
    loan["installments"] = rows
    return loan

async def list_active_loans() -> list:
    """لیست وام‌های در حال پرداخت."""
    res = _sb().table("loans").select("*").eq("status", "active").order("id").execute()
    return res.data or []

async def list_settled_loans() -> list:
    """لیست وام‌های تسویه‌شده."""
    res = _sb().table("loans").select("*").eq("status", "settled").order("id").execute()
    return res.data or []

async def mark_installment_paid(loan_id: int, installment_no: int) -> None:
    """پرداخت قسط + به‌روزرسانی وضعیت وام در صورت پرداخت همه اقساط."""
    sb = _sb()
    sb.table("installments").update({"status": "paid"}).match(
        {"loan_id": loan_id, "installment_no": installment_no}).execute()
    res = sb.table("installments").select("status").eq("loan_id", loan_id).execute()
    rows = res.data or []
    if rows and all(r["status"] == "paid" for r in rows):
        sb.table("loans").update({"status": "settled"}).eq("id", loan_id).execute()

async def get_upcoming_installments(days: int = 2) -> list:
    """اقساط pending با سررسید در N روز آینده که امروز یادآوری نشده‌اند + نام وام."""
    sb = _sb()
    today = date.today().isoformat()
    until = (date.today() + timedelta(days=days)).isoformat()
    res = (
        sb.table("installments")
        .select("*, loans(name)")
        .eq("status", "pending")
        .gte("due_date", today)
        .lte("due_date", until)
        .execute()
    )
    out = []
    for r in res.data or []:
        if r.get("reminded_date") == today:
            continue  # امروز یادآوری شده
        sb.table("installments").update({"reminded_date": today}).eq("id", r["id"]).execute()
        out.append({
            "loan_id": r["loan_id"],
            "loan_name": r["loans"]["name"] if r.get("loans") else "",
            "installment_no": r["installment_no"],
            "amount": r["amount"],
            "due_date": r["due_date"],
        })
    return out

async def get_loan_detail(loan_id: int) -> Optional[dict]:
    """جزئیات وام به همراه همه اقساط."""
    sb = _sb()
    res = sb.table("loans").select("*").eq("id", loan_id).limit(1).execute()
    if not res.data:
        return None
    loan = res.data[0]
    ins = sb.table("installments").select("*").eq("loan_id", loan_id).order("installment_no").execute()
    loan["installments"] = ins.data or []
    return loan
