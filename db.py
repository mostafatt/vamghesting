import os
from typing import Any, Optional

from supabase import acreate_client, AsyncClient

SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_KEY: str = os.environ["SUPABASE_KEY"]

_client: Optional[AsyncClient] = None


async def _get_client() -> AsyncClient:
    global _client
    if _client is None:
        _client = await acreate_client(SUPABASE_URL, SUPABASE_KEY)
    return _client


# ── loans ─────────────────────────────────────────────────────────────────────

async def add_loan(borrower: str, amount: int, description: str = "") -> dict[str, Any]:
    client = await _get_client()
    res = (
        await client.table("loans")
        .insert({"borrower": borrower, "amount": amount, "description": description, "settled": False})
        .execute()
    )
    return res.data[0]


async def get_active_loans() -> list[dict[str, Any]]:
    client = await _get_client()
    res = (
        await client.table("loans")
        .select("*")
        .eq("settled", False)
        .order("created_at", desc=True)
        .execute()
    )
    return res.data


async def get_settled_loans() -> list[dict[str, Any]]:
    client = await _get_client()
    res = (
        await client.table("loans")
        .select("*")
        .eq("settled", True)
        .order("created_at", desc=True)
        .execute()
    )
    return res.data


async def get_loan_by_id(loan_id: int) -> Optional[dict[str, Any]]:
    client = await _get_client()
    res = (
        await client.table("loans")
        .select("*")
        .eq("id", loan_id)
        .single()
        .execute()
    )
    return res.data


async def settle_loan(loan_id: int) -> None:
    client = await _get_client()
    await (
        client.table("loans")
        .update({"settled": True, "settled_at": "now()"})
        .eq("id", loan_id)
        .execute()
    )


async def delete_loan(loan_id: int) -> None:
    client = await _get_client()
    await client.table("loans").delete().eq("id", loan_id).execute()


async def update_loan_note(loan_id: int, note: str) -> None:
    client = await _get_client()
    await (
        client.table("loans")
        .update({"note": note})
        .eq("id", loan_id)
        .execute()
    )
