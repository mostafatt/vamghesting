import os
import logging
import asyncio
from datetime import datetime, timedelta

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from aiohttp import web

from db import (
    add_loan,
    get_active_loans,
    get_loan_by_id,
    add_installment,
    get_installments,
    close_loan,
    get_due_loans,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Conversation states ───────────────────────────────────────────────────────
NAME, AMOUNT, RATE, DURATION = range(4)

# ─── Auth helper ──────────────────────────────────────────────────────────────
AUTHORIZED_CHAT_ID = int(os.environ["AUTHORIZED_CHAT_ID"])


def authorized(update: Update) -> bool:
    return update.effective_chat and update.effective_chat.id == AUTHORIZED_CHAT_ID


# ─── /start ───────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    await update.message.reply_text(
        "سلام! به ربات مدیریت وام خوش آمدید.\n\n"
        "دستورات موجود:\n"
        "➕ /new_loan — ثبت وام جدید\n"
        "📋 /loans — لیست وام‌های فعال\n"
        "💳 /pay — ثبت قسط\n"
        "📊 /report — گزارش وام\n"
        "🔒 /close_loan — بستن وام"
    )


# ─── /new_loan conversation ───────────────────────────────────────────────────
async def new_loan_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not authorized(update):
        return ConversationHandler.END
    await update.message.reply_text("نام وام‌گیرنده را وارد کنید:")
    return NAME


async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["name"] = update.message.text.strip()
    await update.message.reply_text("مبلغ وام را (به تومان) وارد کنید:")
    return AMOUNT


async def get_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        amount = float(update.message.text.replace(",", "").strip())
        if amount <= 0:
            raise ValueError
        context.user_data["amount"] = amount
    except ValueError:
        await update.message.reply_text("مبلغ نامعتبر است. لطفاً عدد مثبت وارد کنید:")
        return AMOUNT
    await update.message.reply_text("نرخ بهره ماهانه (درصد) را وارد کنید (یا 0 برای بدون بهره):")
    return RATE


async def get_rate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        rate = float(update.message.text.strip())
        if rate < 0:
            raise ValueError
        context.user_data["rate"] = rate
    except ValueError:
        await update.message.reply_text("نرخ نامعتبر است. لطفاً یک عدد معتبر (مثلاً 0 یا 2.5) وارد کنید:")
        return RATE
    await update.message.reply_text("تعداد اقساط ماهانه را وارد کنید:")
    return DURATION


async def get_duration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        duration = int(update.message.text.strip())
        if duration <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("تعداد اقساط نامعتبر است. لطفاً عدد صحیح مثبت وارد کنید:")
        return DURATION

    name = context.user_data.get("name")
    amount = context.user_data.get("amount")
    rate = context.user_data.get("rate")

    if not name or amount is None or rate is None:
        await update.message.reply_text("❌ اطلاعات ثبت وام منقضی شده است. لطفاً دستور /new_loan را مجدد اجرا کنید.")
        context.user_data.clear()
        return ConversationHandler.END

    try:
        # محاسبه مبلغ هر قسط
        if rate > 0:
            r = rate / 100
            installment = amount * r * ((1 + r) ** duration) / (((1 + r) ** duration) - 1)
        else:
            installment = amount / duration

        # ذخیره در دیتابیس
        loan_id = await add_loan(name, amount, rate, duration, round(installment, 0))

        await update.message.reply_text(
            f"✅ وام با موفقیت ثبت شد.\n\n"
            f"🔹 شناسه وام: {loan_id}\n"
            f"👤 وام‌گیرنده: {name}\n"
            f"💰 مبلغ: {amount:,.0f} تومان\n"
            f"📈 نرخ بهره: {rate}٪ ماهانه\n"
            f"🗓 تعداد اقساط: {duration} ماه\n"
            f"💵 مبلغ هر قسط: {installment:,.0f} تومان"
        )
    except Exception as e:
        logger.error(f"Error while saving loan: {e}", exc_info=True)
        await update.message.reply_text(
            f"❌ خطایی در ثبت وام در پایگاه داده رخ داد:\n`{e}`",
            parse_mode="Markdown"
        )
    finally:
        context.user_data.clear()

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("عملیات لغو شد.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ─── /loans ───────────────────────────────────────────────────────────────────
async def list_loans(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    try:
        loans = await get_active_loans()
        if not loans:
            await update.message.reply_text("هیچ وام فعالی وجود ندارد.")
            return
        text = "📋 وام‌های فعال:\n\n"
        for loan in loans:
            text += (
                f"🔹 شناسه: {loan['id']}\n"
                f"   نام: {loan['borrower_name']}\n"
                f"   مبلغ: {loan['amount']:,.0f} تومان\n"
                f"   اقساط: {loan['paid_installments']}/{loan['duration']} پرداخت شده\n\n"
            )
        await update.message.reply_text(text)
    except Exception as e:
        logger.error(f"Error in list_loans: {e}", exc_info=True)
        await update.message.reply_text(f"❌ خطا در دریافت لیست وام‌ها: {e}")


# ─── /pay ─────────────────────────────────────────────────────────────────────
async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text("استفاده صحیح: /pay <loan_id> <amount>\nمثال: `/pay 3 500000`", parse_mode="Markdown")
        return
    try:
        loan_id = int(args[0])
        amount = float(args[1].replace(",", ""))
    except ValueError:
        await update.message.reply_text("ورودی نامعتبر است. مثال: `/pay 3 500000`", parse_mode="Markdown")
        return

    try:
        loan = await get_loan_by_id(loan_id)
        if not loan:
            await update.message.reply_text(f"وامی با شناسه {loan_id} یافت نشد.")
            return

        await add_installment(loan_id, amount)
        await update.message.reply_text(
            f"✅ قسط {amount:,.0f} تومان برای وام {loan_id} ({loan['borrower_name']}) ثبت شد."
        )
    except Exception as e:
        logger.error(f"Error in pay: {e}", exc_info=True)
        await update.message.reply_text(f"❌ خطا در ثبت قسط: {e}")


# ─── /report ──────────────────────────────────────────────────────────────────
async def report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text("استفاده: /report <loan_id>")
        return
    try:
        loan_id = int(args[0])
    except ValueError:
        await update.message.reply_text("شناسه وام نامعتبر است.")
        return

    try:
        loan = await get_loan_by_id(loan_id)
        if not loan:
            await update.message.reply_text(f"وامی با شناسه {loan_id} یافت نشد.")
            return

        installments = await get_installments(loan_id)
        total_paid = sum(i["amount"] for i in installments)
        total_payable = loan["installment_amount"] * loan["duration"]
        remaining = max(0.0, total_payable - total_paid)

        text = (
            f"📊 گزارش وام {loan_id}\n\n"
            f"وام‌گیرنده: {loan['borrower_name']}\n"
            f"مبلغ اصلی: {loan['amount']:,.0f} تومان\n"
            f"نرخ بهره: {loan['interest_rate']}٪\n"
            f"تعداد اقساط: {loan['duration']}\n"
            f"مبلغ هر قسط: {loan['installment_amount']:,.0f} تومان\n\n"
            f"پرداخت‌ها:\n"
        )
        for i, inst in enumerate(installments, 1):
            date_str = inst.get("paid_at", "")[:10]
            text += f"  {i}. {inst['amount']:,.0f} تومان — {date_str}\n"

        text += (
            f"\nجمع پرداخت‌ها: {total_paid:,.0f} تومان\n"
            f"باقی‌مانده: {remaining:,.0f} تومان\n"
            f"وضعیت: {'بسته شده' if loan['status'] == 'closed' else 'فعال'}"
        )
        await update.message.reply_text(text)
    except Exception as e:
        logger.error(f"Error in report: {e}", exc_info=True)
        await update.message.reply_text(f"❌ خطا در دریافت گزارش: {e}")


# ─── /close_loan ──────────────────────────────────────────────────────────────
async def close_loan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text("استفاده: /close_loan <loan_id>")
        return
    try:
        loan_id = int(args[0])
    except ValueError:
        await update.message.reply_text("شناسه وام نامعتبر است.")
        return

    try:
        loan = await get_loan_by_id(loan_id)
        if not loan:
            await update.message.reply_text(f"وامی با شناسه {loan_id} یافت نشد.")
            return

        await close_loan(loan_id)
        await update.message.reply_text(
            f"✅ وام {loan_id} ({loan['borrower_name']}) با موفقیت بسته شد."
        )
    except Exception as e:
        logger.error(f"Error in close_loan: {e}", exc_info=True)
        await update.message.reply_text(f"❌ خطا در بستن وام: {e}")


# ─── Cron: send reminders for due loans ───────────────────────────────────────
async def send_due_reminders(bot) -> None:
    loans = await get_due_loans()
    for loan in loans:
        try:
            await bot.send_message(
                chat_id=AUTHORIZED_CHAT_ID,
                text=(
                    f"⏰ یادآوری قسط\n\n"
                    f"وام‌گیرنده: {loan['borrower_name']}\n"
                    f"شناسه وام: {loan['id']}\n"
                    f"مبلغ قسط: {loan['installment_amount']:,.0f} تومان"
                ),
            )
        except Exception as e:
            logger.error(f"Failed to send reminder for loan {loan['id']}: {e}")


# ─── Global Error Handler ─────────────────────────────────────────────────────
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            "⚠️ خطایی در اجرای درخواست رخ داد. لاگ سرور را بررسی کنید."
        )


# ─── aiohttp web app ───────────────────────────────────────────────────────────
def build_web_app(application: Application) -> web.Application:
    web_app = web.Application()

    async def webhook_handler(request: web.Request) -> web.Response:
        try:
            data = await request.json()
            update = Update.de_json(data, application.bot)
            await application.process_update(update)
            return web.Response(status=200)
        except Exception as e:
            logger.error(f"Webhook error: {e}", exc_info=True)
            return web.Response(status=500)

    async def cron_handler(request: web.Request) -> web.Response:
        secret = request.headers.get("X-Cron-Secret", "")
        if secret != os.environ.get("CRON_SECRET", ""):
            return web.Response(status=403, text="Forbidden")
        try:
            await send_due_reminders(application.bot)
            return web.Response(status=200, text="OK")
        except Exception as e:
            logger.error(f"Cron error: {e}", exc_info=True)
            return web.Response(status=500)

    async def health_handler(request: web.Request) -> web.Response:
        return web.Response(status=200, text="OK")

    web_app.router.add_post("/webhook", webhook_handler)
    web_app.router.add_get("/cron", cron_handler)
    web_app.router.add_get("/health", health_handler)

    return web_app


# ─── Main ──────────────────────────────────────────────────────────────────────
async def main() -> None:
    token = os.environ["BOT_TOKEN"]
    base_url = os.environ["RAILWAY_PUBLIC_URL"].rstrip("/")
    port = int(os.environ.get("PORT", 8080))

    application = Application.builder().token(token).updater(None).build()

    # Conversation handler for new_loan
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("new_loan", new_loan_start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_amount)],
            RATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_rate)],
            DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_duration)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("loans", list_loans))
    application.add_handler(CommandHandler("pay", pay))
    application.add_handler(CommandHandler("report", report))
    application.add_handler(CommandHandler("close_loan", close_loan_cmd))

    # ثبت هندلر خطای عمومی
    application.add_error_handler(error_handler)

    # راه‌اندازی و تنظیم وب‌هوک
    await application.initialize()
    await application.bot.set_webhook(url=f"{base_url}/webhook")
    await application.start()

    # اجرای سرور aiohttp
    web_app = build_web_app(application)
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    logger.info(f"Bot running on port {port}, webhook: {base_url}/webhook")

    try:
        await asyncio.Event().wait()
    finally:
        await application.stop()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
