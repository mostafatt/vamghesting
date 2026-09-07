import asyncio
import logging
import os
from datetime import datetime

from aiohttp import web
from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from db import (
    add_loan,
    delete_loan,
    get_active_loans,
    get_loan_by_id,
    get_settled_loans,
    settle_loan,
    update_loan_note,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── env vars ────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ["BOT_TOKEN"]
AUTHORIZED_CHAT_ID = int(os.environ["AUTHORIZED_CHAT_ID"])
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
RAILWAY_PUBLIC_URL = os.environ.get("RAILWAY_PUBLIC_URL", "").rstrip("/")

# ── conversation states ──────────────────────────────────────────────────────
(
    ASK_BORROWER,
    ASK_AMOUNT,
    ASK_DESC,
    ASK_SETTLE_CONFIRM,
    ASK_DELETE_CONFIRM,
    ASK_NOTE,
) = range(6)


# ── helpers ──────────────────────────────────────────────────────────────────
def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("➕ وام جدید", callback_data="new_loan"),
                InlineKeyboardButton("📋 وام‌های فعال", callback_data="list_active"),
            ],
            [
                InlineKeyboardButton("✅ وام‌های تسویه‌شده", callback_data="list_settled"),
            ],
        ]
    )


def auth(func):
    """Decorator: only allow AUTHORIZED_CHAT_ID."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = (
            update.effective_user.id
            if update.effective_user
            else None
        )
        if uid != AUTHORIZED_CHAT_ID:
            if update.message:
                await update.message.reply_text("⛔ دسترسی غیرمجاز.")
            return
        return await func(update, context)
    return wrapper


# ── /start ───────────────────────────────────────────────────────────────────
@auth
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام! ربات مدیریت وام آماده است.",
        reply_markup=main_menu_kb(),
    )


# ── main menu callback ────────────────────────────────────────────────────────
@auth
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "new_loan":
        await query.edit_message_text("نام وام‌گیرنده را وارد کنید:")
        return ASK_BORROWER

    if data == "list_active":
        loans = await get_active_loans()
        if not loans:
            await query.edit_message_text(
                "هیچ وام فعالی وجود ندارد.", reply_markup=main_menu_kb()
            )
            return ConversationHandler.END
        rows = [
            [
                InlineKeyboardButton(
                    f"#{loan['id']} — {loan['borrower']} — {loan['amount']:,} تومان",
                    callback_data=f"loan_{loan['id']}",
                )
            ]
            for loan in loans
        ]
        rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back")])
        await query.edit_message_text(
            "وام‌های فعال:", reply_markup=InlineKeyboardMarkup(rows)
        )
        return ConversationHandler.END

    if data == "list_settled":
        loans = await get_settled_loans()
        if not loans:
            await query.edit_message_text(
                "هیچ وام تسویه‌شده‌ای وجود ندارد.", reply_markup=main_menu_kb()
            )
            return ConversationHandler.END
        rows = [
            [
                InlineKeyboardButton(
                    f"#{loan['id']} — {loan['borrower']} — {loan['amount']:,} تومان",
                    callback_data=f"loan_{loan['id']}",
                )
            ]
            for loan in loans
        ]
        rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back")])
        await query.edit_message_text(
            "وام‌های تسویه‌شده:", reply_markup=InlineKeyboardMarkup(rows)
        )
        return ConversationHandler.END

    if data == "back":
        await query.edit_message_text("منوی اصلی:", reply_markup=main_menu_kb())
        return ConversationHandler.END

    # single loan view
    if data.startswith("loan_"):
        loan_id = int(data.split("_")[1])
        loan = await get_loan_by_id(loan_id)
        if not loan:
            await query.edit_message_text("وام یافت نشد.", reply_markup=main_menu_kb())
            return ConversationHandler.END
        status = "تسویه‌شده" if loan["settled"] else "فعال"
        text = (
            f"🆔 شناسه: {loan['id']}\n"
            f"👤 وام‌گیرنده: {loan['borrower']}\n"
            f"💰 مبلغ: {loan['amount']:,} تومان\n"
            f"📝 توضیح: {loan.get('description') or '—'}\n"
            f"🗒 یادداشت: {loan.get('note') or '—'}\n"
            f"📅 تاریخ: {loan['created_at'][:10]}\n"
            f"📌 وضعیت: {status}"
        )
        buttons = []
        if not loan["settled"]:
            buttons.append(
                InlineKeyboardButton("✅ تسویه", callback_data=f"settle_{loan_id}")
            )
        buttons.append(
            InlineKeyboardButton("🗒 یادداشت", callback_data=f"note_{loan_id}")
        )
        buttons.append(
            InlineKeyboardButton("🗑 حذف", callback_data=f"delete_{loan_id}")
        )
        kb = InlineKeyboardMarkup(
            [buttons, [InlineKeyboardButton("🔙 بازگشت", callback_data="back")]]
        )
        await query.edit_message_text(text, reply_markup=kb)
        context.user_data["current_loan_id"] = loan_id
        return ConversationHandler.END

    if data.startswith("settle_"):
        loan_id = int(data.split("_")[1])
        context.user_data["current_loan_id"] = loan_id
        kb = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("بله", callback_data="settle_yes"),
                InlineKeyboardButton("خیر", callback_data="settle_no"),
            ]]
        )
        await query.edit_message_text("آیا از تسویه این وام مطمئن هستید؟", reply_markup=kb)
        return ASK_SETTLE_CONFIRM

    if data.startswith("delete_"):
        loan_id = int(data.split("_")[1])
        context.user_data["current_loan_id"] = loan_id
        kb = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("بله", callback_data="delete_yes"),
                InlineKeyboardButton("خیر", callback_data="delete_no"),
            ]]
        )
        await query.edit_message_text("آیا از حذف این وام مطمئن هستید؟", reply_markup=kb)
        return ASK_DELETE_CONFIRM

    if data.startswith("note_"):
        loan_id = int(data.split("_")[1])
        context.user_data["current_loan_id"] = loan_id
        await query.edit_message_text("یادداشت جدید را وارد کنید:")
        return ASK_NOTE

    return ConversationHandler.END


# ── new loan conversation ─────────────────────────────────────────────────────
@auth
async def ask_borrower(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["borrower"] = update.message.text.strip()
    await update.message.reply_text("مبلغ وام را به تومان وارد کنید:")
    return ASK_AMOUNT


@auth
async def ask_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip().replace(",", "").replace("،", "")
    if not raw.isdigit():
        await update.message.reply_text("لطفاً یک عدد صحیح وارد کنید:")
        return ASK_AMOUNT
    context.user_data["amount"] = int(raw)
    await update.message.reply_text("توضیح کوتاه (یا /skip برای رد کردن):")
    return ASK_DESC


@auth
async def ask_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["description"] = "" if text == "/skip" else text
    borrower = context.user_data["borrower"]
    amount = context.user_data["amount"]
    desc = context.user_data["description"]
    loan = await add_loan(borrower, amount, desc)
    await update.message.reply_text(
        f"وام #{loan['id']} برای {borrower} به مبلغ {amount:,} تومان ثبت شد.",
        reply_markup=main_menu_kb(),
    )
    return ConversationHandler.END


# ── settle conversation ───────────────────────────────────────────────────────
@auth
async def settle_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    loan_id = context.user_data.get("current_loan_id")
    if query.data == "settle_yes" and loan_id:
        await settle_loan(loan_id)
        await query.edit_message_text(
            f"وام #{loan_id} تسویه شد.", reply_markup=main_menu_kb()
        )
    else:
        await query.edit_message_text("لغو شد.", reply_markup=main_menu_kb())
    return ConversationHandler.END


# ── delete conversation ───────────────────────────────────────────────────────
@auth
async def delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    loan_id = context.user_data.get("current_loan_id")
    if query.data == "delete_yes" and loan_id:
        await delete_loan(loan_id)
        await query.edit_message_text(
            f"وام #{loan_id} حذف شد.", reply_markup=main_menu_kb()
        )
    else:
        await query.edit_message_text("لغو شد.", reply_markup=main_menu_kb())
    return ConversationHandler.END


# ── note conversation ─────────────────────────────────────────────────────────
@auth
async def save_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loan_id = context.user_data.get("current_loan_id")
    note = update.message.text.strip()
    if loan_id:
        await update_loan_note(loan_id, note)
        await update.message.reply_text(
            f"یادداشت وام #{loan_id} ذخیره شد.", reply_markup=main_menu_kb()
        )
    return ConversationHandler.END


# ── cancel ────────────────────────────────────────────────────────────────────
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("لغو شد.", reply_markup=main_menu_kb())
    return ConversationHandler.END


# ── build application ─────────────────────────────────────────────────────────
def build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(menu_handler),
        ],
        states={
            ASK_BORROWER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_borrower)
            ],
            ASK_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ask_amount)
            ],
            ASK_DESC: [
                MessageHandler(filters.TEXT, ask_desc)
            ],
            ASK_SETTLE_CONFIRM: [
                CallbackQueryHandler(settle_confirm, pattern="^settle_(yes|no)$")
            ],
            ASK_DELETE_CONFIRM: [
                CallbackQueryHandler(delete_confirm, pattern="^delete_(yes|no)$")
            ],
            ASK_NOTE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, save_note)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
    )

    app.add_handler(conv)
    return app


# ── webhook server ────────────────────────────────────────────────────────────
async def webhook_handler(request: web.Request) -> web.Response:
    # optional secret check
    if WEBHOOK_SECRET:
        if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
            return web.Response(status=403)

    app: Application = request.app["ptb"]
    data = await request.json()
    update = Update.de_json(data, app.bot)
    await app.process_update(update)
    return web.Response(text="ok")


async def health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def main():
    ptb_app = build_app()
    await ptb_app.initialize()

    # register webhook
    webhook_url = f"{RAILWAY_PUBLIC_URL}/webhook"
    await ptb_app.bot.set_webhook(
        url=webhook_url,
        secret_token=WEBHOOK_SECRET or None,
    )
    logger.info("Webhook set to %s", webhook_url)

    # aiohttp server
    server = web.Application()
    server["ptb"] = ptb_app
    server.router.add_post("/webhook", webhook_handler)
    server.router.add_get("/health", health)

    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8080)))
    await site.start()
    logger.info("Server listening on port %s", os.environ.get("PORT", 8080))

    # keep alive
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
