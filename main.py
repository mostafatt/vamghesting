# -*- coding: utf-8 -*-
"""
ربات پیگیری وام (Telegram Loan Tracker)
----------------------------------------
معماری:
- از python-telegram-bot نسخه 21 استفاده می‌کنیم.
- چون روی Railway باید هم وب‌هوک تلگرام و هم یک اندپوینت اضافه (/cron) را سرو کنیم،
  از run_webhook با یک aiohttp WebApp سفارشی استفاده می‌کنیم:
    * روت POST /webhook/<TOKEN>  -> آپدیت‌های تلگرام را به application.process_update می‌دهد
    * روت GET/POST /cron?secret=... -> بررسی اقساط نزدیک و ارسال یادآوری
- چرا run_webhook و نه Flask جدا؟ چون run_webhook خودش وب‌سرور aiohttp را بالا می‌آورد،
  setWebhook را صدا می‌زند و به‌صورت async آپدیت‌ها را پردازش می‌کند؛ با اضافه کردن روت‌های
  سفارشی به همان وب‌اپ، هر دو نیاز (وب‌هوک + cron) با یک پروسه حل می‌شود.
"""

import os
import logging
from datetime import date, datetime, timedelta

from aiohttp import web

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

import db

# ---------- تنظیمات از متغیرهای محیطی ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
AUTHORIZED_CHAT_ID = os.environ.get("AUTHORIZED_CHAT_ID", "")
CRON_SECRET = os.environ.get("CRON_SECRET", "")
RAILWAY_PUBLIC_URL = os.environ.get("RAILWAY_PUBLIC_URL", "")
PORT = int(os.environ.get("PORT", "8080"))
WEBHOOK_PATH = "/webhook"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger("loan_bot")


# ---------- ابزارها ----------
def fmt_amount(n: int) -> str:
    """نمایش مبلغ با جداکننده هزارگان."""
    return f"{int(n):,}"

def authorized(update: Update) -> bool:
    """بررسی مجاز بودن chat_id."""
    if not AUTHORIZED_CHAT_ID:
        return False
    chat = update.effective_chat
    return chat is not None and str(chat.id) == AUTHORIZED_CHAT_ID

async def deny(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پاسخ برای کاربر غیرمجاز."""
    q = update.callback_query
    if q:
        await q.answer("دسترسی غیرمجاز", show_alert=True)
    else:
        await update.effective_message.reply_text("دسترسی غیرمجاز")

def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        [InlineKeyboardButton("➕ ثبت وام جدید", callback_data="new_loan")][0],
    ] + [
        [InlineKeyboardButton("📋 وام‌های در حال پرداخت", callback_data="list_active")],
        [InlineKeyboardButton("✅ وام‌های تسویه‌شده", callback_data="list_settled")],
    ]])

def back_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="menu")]])

# ---------- مراحل مکالمه ثبت وام ----------
(ASK_NAME, ASK_AMOUNT, ASK_COUNT, ASK_DATE) = range(4)

async def cb_new_loan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شروع ثبت وام جدید."""
    q = update.callback_query
    await q.answer()
    context.user_data.clear()
    await q.edit_message_text("📝 نام وام را وارد کنید (مثلاً: وام خودرو):")
    return ASK_NAME

async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return ConversationHandler.END
    context.user_data["loan_name"] = update.message.text.strip()[:100]
    await update.message.reply_text("💰 مبلغ کل وام (تومان) را وارد کنید، مثلاً: 120000000")
    return ASK_AMOUNT

async def ask_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(",", "").replace("،", "")
    # تبدیل ارقام فارسی به انگلیسی
    text = text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    if not text.isdigit():
        await update.message.reply_text("❌ لطفاً فقط عدد وارد کنید، مثلاً: 120000000")
        return ASK_AMOUNT
    context.user_data["loan_amount"] = int(text)
    await update.message.reply_text("🔢 تعداد اقساط را وارد کنید:")

async def ask_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    if not text.isdigit() or int(text) < 1 or int(text) > 360:
        await update.message.reply_text("❌ تعداد اقساط نامعتبر است. یک عدد بین ۱ تا ۳۶۰ وارد کنید:")
        return ASK_COUNT
    context.user_data["installments"] = int(text)
    await update.message.reply_text(
        "📅 تاریخ سررسید قسط اول را به فرمت YYYY-MM-DD وارد کنید (مثلاً 2025-07-01).\n"
        "اقساط بعدی ماهانه و هم‌اندازه در نظر گرفته می‌شوند (باقیمانده در قسط آخر)."
    )
    return ASK_DATE

async def ask_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    try:
        first_due = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        await update.message.reply_text("❌ فرمت تاریخ نامعتبر است. لطفاً به شکل YYYY-MM-DD وارد کنید:")
        return ASK_DATE
    name = context.user_data["loan_name"]
    amount = context.user_data["loan_amount"]
    count = context.user_data["installments"]
    try:
        loan = await db.create_loan(name, amount, count, first_due)
    except Exception:
        logger.exception("خطای دیتابیس در ایجاد وام")
        await update.message.reply_text("⚠️ خطا در ذخیره‌سازی. دوباره تلاش کنید.")
        return ConversationHandler.END
    await update.message.reply_text(
        f"✅ وام ثبت شد!\n\n"
        f"نام: {name}\n"
        f"مبلغ کل: {fmt_amount(amount)} تومان\n"
        f"تعداد اقساط: {count}\n"
        f"مبلغ هر قسط: {fmt_amount(amount // count)} تومان\n"
        f"(قسط آخر: {fmt_amount(amount - (amount // count) * (count - 1))} تومان)\n"
        f"سررسید اول: {first_due.isoformat()}",
        reply_markup=back_menu_kb(),
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.effective_message.reply_text("عملیات لغو شد.", reply_markup=main_menu_kb())
    return ConversationHandler.END

# ---------- منو و لیست‌ها ----------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await deny(update, context)
        return
    await update.message.reply_text(
        "سلام! 👋\nبه ربات پیگیری وام خوش آمدید.", reply_markup=main_menu_kb()
    )

async def cb_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("منوی اصلی:", reply_markup=main_menu_kb())

async def cb_list_active(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        loans = await db.list_active_loans()
    except Exception:
        logger.exception("خطا در دریافت وام‌های فعال")
        loans = []
    if not loans:
        await q.edit_message_text("وام فعالی وجود ندارد.", reply_markup=back_menu_kb())
        return
    kb = [[InlineKeyboardButton(f"{l['name']} ({l['id']})", callback_data=f"loan:{l['id']}")]
          for l in loans]
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="menu")])
    await q.edit_message_text("📋 وام‌های در حال پرداخت:", reply_markup=InlineKeyboardMarkup(kb))

async def cb_list_settled(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        loans = await db.list_settled_loans()
    except Exception:
        logger.exception("خطا در دریافت وام‌های تسویه‌شده")
        loans = []
    if not loans:
        await q.edit_message_text("وام تسویه‌شده‌ای وجود ندارد.", reply_markup=back_menu_kb())
        return
    kb = [[InlineKeyboardButton(f"{l['name']} ({l['id']})", callback_data=f"loan:{l['id']}")]
          for l in loans]
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="menu")])
    await q.edit_message_text("✅ وام‌های تسویه‌شده:", reply_markup=InlineKeyboardMarkup(kb))

def loan_detail_kb(loan: dict, installments: list) -> InlineKeyboardMarkup:
    """ساخت کیبورد جزئیات وام + دکمه پرداخت برای اقساط در انتظار."""
    kb = []
    for ins in installments:
        if ins["status"] == "pending":
            kb.append([InlineKeyboardButton(
                f"✅ پرداخت شد — قسط {ins['installment_no']} ({ins['due_date']})",
                callback_data=f"pay:{loan['id']}:{ins['installment_no']}")])
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="menu")])
    return InlineKeyboardMarkup(kb)

def loan_detail_text(loan: dict, installments: list) -> str:
    paid_total = sum(i["amount"] for i in installments if i["status"] == "paid")
    remaining = loan["total_amount"] - paid_total
    next_due = next((i["due_date"] for i in installments if i["status"] == "pending"), None)
    lines = [
        f"📄 *{loan['name']}*",
        f"مبلغ کل: {fmt_amount(loan['total_amount'])} تومان",
        f"پرداخت‌شده: {fmt_amount(paid_total)} تومان",
        f"باقیمانده: {fmt_amount(remaining)} تومان",
        f"سررسید بعدی: {next_due if next_due else '—'}",
        "",
        "اقساط:",
    ]
    for i in installments:
        emoji = "✅" if i["status"] == "paid" else "⏳"
        lines.append(f"{emoji} قسط {i['installment_no']} — {i['due_date']} — {fmt_amount(i['amount'])} تومان")
    return "\n".join(lines)

async def cb_loan_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    loan_id = int(q.data.split(":")[1])
    try:
        loan = await db.get_loan_detail(loan_id)
        if not loan:
            await q.edit_message_text("وام یافت نشد.", reply_markup=back_menu_kb())
            return
        installments = loan.get("installments", [])
    except Exception:
        logger.exception("خطا در جزئیات وام")
        await q.edit_message_text("⚠️ خطا در دریافت اطلاعات.", reply_markup=back_menu_kb())
        return
    text = loan_detail_text(loan, installments)
    if loan["status"] == "settled":
        await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_menu_kb())
    else:
        await q.edit_message_text(
            text, parse_mode=ParseMode.MARKDOWN, reply_markup=loan_detail_kb(loan, installments)
        )

async def cb_pay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """وقتی کاربر دکمه «✅ پرداخت شد» را برای یک قسط می‌زند."""
    q = update.callback_query
    if not authorized(update):
        await q.answer("دسترسی غیرمجاز", show_alert=True)
        return
    await q.answer()

    _, loan_id_s, no_s = q.data.split(":")
    loan_id, installment_no = int(loan_id_s), int(no_s)

    try:
        await db.mark_installment_paid(loan_id, installment_no)
        loan = await db.get_loan_detail(loan_id)
    except Exception as e:
        logger.exception("خطا در ثبت پرداخت قسط")
        await q.edit_message_text(f"خطا در ثبت پرداخت: {e}")
        return
    if not loan:
        await q.edit_message_text("وام یافت نشد.", reply_markup=back_menu_kb())
        return

    installments = loan.get("installments", [])
    text = loan_detail_text(loan, installments)

    if loan["status"] == "settled":
        await q.edit_message_text(
            text + "\n\n🎉 تبریک! این وام کاملاً تسویه شد!",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_menu_kb(),
        )
    else:
        await q.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=loan_detail_kb(loan, installments),
        )

async def send_reminder(context: ContextTypes.DEFAULT_TYPE) -> int:
    """بررسی اقساط با سررسید حداکثر ۲ روز آینده و ارسال پیام یادآوری به کاربر مجاز."""
    try:
        upcoming = await db.get_upcoming_installments(days=2)
    except Exception:
        logger.exception("خطا در دریافت اقساط پیش‌رو برای یادآوری")
        return 0

    if not upcoming:
        return 0

    lines = ["🔔 یادآوری اقساط نزدیک به سررسید:\n"]
    buttons = []
    for inst in upcoming:
        due = inst["due_date"]
        amount = fmt_amount(inst["amount"])
        loan_name = inst.get("loan_name", "")
        no = inst.get("installment_no", "")
        lines.append(f"• {loan_name} — قسط {no} — {amount} تومان — سررسید: {due}")
        buttons.append([
            InlineKeyboardButton(
                f"✅ پرداخت شد ({loan_name} قسط {no})",
                callback_data=f"pay:{inst['loan_id']}:{no}",
            )
        ])

    text = "\n".join(lines)
    try:
        await context.bot.send_message(
            chat_id=int(AUTHORIZED_CHAT_ID),
            text=text,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    except Exception:
        logger.exception("خطا در ارسال پیام یادآوری")
    return len(upcoming)

async def cron_handler(request: web.Request) -> web.Response:
    """اندپوینت HTTP که cron-job.org روزانه صدا می‌زند. محافظت با secret."""
    secret = request.query.get("secret", "") or request.headers.get("X-Cron-Secret", "")
    if not CRON_SECRET or secret != CRON_SECRET:
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    app: Application = request.app["ptb_app"]
    sent = await send_reminder(app)
    return web.json_response({"ok": True, "reminders_sent": sent})

async def webhook_handler(request: web.Request) -> web.Response:
    """دریافت آپدیت تلگرام و ارسال به صف پردازش PTB."""
    app: Application = request.app["ptb_app"]
    data = await request.json()
    update = Update.de_json(data, app.bot)
    await app.process_update(update)
    return web.Response(ok=True)

# ---------- خطاها ----------
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("خطای پردازش آپدیت", exc_info=context.error)

# ---------- ساخت اپلیکیشن ----------
def build_app() -> Application:
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_new_loan, pattern="^new_loan$")],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],
            ASK_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_amount)],
            ASK_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_count)],
            ASK_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_date)],
        },
        fallbacks=[CommandHandler("start", lambda u, c: ConversationHandler.END)],
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(cb_menu, pattern="^menu$"))
    app.add_handler(CallbackQueryHandler(cb_list_active, pattern="^list_active$"))
    app.add_handler(CallbackQueryHandler(cb_list_settled, pattern="^list_settled$"))
    app.add_handler(CallbackQueryHandler(cb_loan_detail, pattern="^loan:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_pay, pattern="^pay:\d+:\d+$"))
    app.add_error_handler(on_error)
    return app

def main():
    """راه‌اندازی: وب‌هوک تلگرام + روت‌های سفارشی (cron) روی همان سرور aiohttp."""
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN تنظیم نشده است.")
    app = build_app()

    async def on_startup(app: Application):
        # افزودن روت‌های سفارشی به وب‌اپ داخلی PTB
        webapp = app.webhook_app  # aiohttp web.Application ساخته‌شده توسط run_webhook
        webapp.router.add_post(f"{WEBHOOK_PATH}/" + BOT_TOKEN, webhook_handler)
        webapp.router.add_route("*", "/cron", cron_handler)

    url = RAILWAY_PUBLIC_URL.rstrip("/") if RAILWAY_PUBLIC_URL else ""
    webhook_url = f"{url}{WEBHOOK_PATH}/{BOT_TOKEN}"
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=f"{WEBHOOK_PATH}/{BOT_TOKEN}",
        webhook_url=webhook_url,
        secret_token=os.environ.get("WEBHOOK_SECRET", ""),
    )

if __name__ == "__main__":
    main()
