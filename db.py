import os
from supabase import create_client, Client
from datetime import datetime, timedelta

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
    result = _client.table("loans").insert(data).execute()
    return result.data[0]["id"]


async def get_active_loans() -> list:
    result = (
        _client.table("loans")
        .select("*")
        .eq("status", "active")
        .order("created_at", desc=False)
        .execute()
    )
    return result.data or []


async def get_loan_by_id(loan_id: int) -> dict | None:
    result = _client.table("loans").select("*").eq("id", loan_id).execute()
    return result.data[0] if result.data else None


async def add_installment(loan_id: int, amount: float) -> None:
    # Insert installment record
    _client.table("installments").insert(
        {
            "loan_id": loan_id,
            "amount": amount,
            "paid_at": datetime.utcnow().isoformat(),
        }
    ).execute()

    # Increment paid_installments counter
    loan = await get_loan_by_id(loan_id)
    if loan:
        _client.table("loans").update(
            {"paid_installments": loan["paid_installments"] + 1}
        ).eq("id", loan_id).execute()


async def get_installments(loan_id: int) -> list:
    result = (
        _client.table("installments")
        .select("*")
        .eq("loan_id", loan_id)
        .order("paid_at", desc=False)
        .execute()
    )
    return result.data or []


async def close_loan(loan_id: int) -> None:
    _client.table("loans").update({"status": "closed"}).eq("id", loan_id).execute()


async def get_due_loans() -> list:
    """Return active loans that still have unpaid installments."""
    result = (
        _client.table("loans")
        .select("*")
        .eq("status", "active")
        .execute()
    )
    loans = result.data or []
    return [l for l in loans if l["paid_installments"] < l["duration"]]
