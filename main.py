import os
import logging
import asyncio
from datetime import datetime, date, timedelta

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler,
    ContextTypes, CallbackQueryHandler, filters,
)
from aiohttp import web

# import db functions
from db import (
    add_loan, get_active_loans, get_loan_by_id, create_schedule,
    get_installments, mark_installment_paid, close_loan, get_upcoming_unpaid,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Conversation states ───────────────────────────────────────────────────────
NAME, AMOUNT, RATE, DURATION = range(4)

# ─── Auth helper ──────────────────────────────────────────────────────────────
# Read AUTHORIZED_CHAT_ID from environment variable
AUTHORIZED_CHAT_ID = int(os.environ.get("AUTHORIZED_CHAT_ID", 0)) # Default to 0 if not set


def authorized(update: Update) -> bool:
    """Checks if the chat ID is authorized."""
    if AUTHORIZED_CHAT_ID == 0: # If not set, consider it unauthorized
        logger.warning("AUTHORIZED_CHAT_ID is not set. All users will be unauthorized.")
        return False
    return update.effective_chat and update.effective_chat.id == AUTHORIZED_CHAT_ID


# ─── /start ───────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message when the /start command is issued."""
    if not authorized(update):
        return
    await update.message.reply_text(
        "سلام! به ربات مدیریت وام خوش آمدید.\n\n"
        "دستورات موجود:\n"
        "➕ /new_loan — ثبت وام جدید\n"
        "📋 /loans — لیست وام‌های فعال\n"
        "🗓 /installments <loan_id> — لیست اقساط و تیک پرداخت\n"
        "📊 /report — گزارش وام\n"
        "🔒 /close_loan — بستن وام"
    )


# ─── /new_loan conversation ───────────────────────────────────────────────────
async def new_loan_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the new loan conversation."""
    if not authorized(update):
        return ConversationHandler.END
    await update.message.reply_text("نام وام‌گیرنده را وارد کنید:")
    return NAME


async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gets the borrower's name."""
    context.user_data["name"] = update.message.text.strip()
    await update.message.reply_text("مبلغ وام را (به تومان) وارد کنید:")
    return AMOUNT


async def get_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gets and validates the loan amount."""
    try:
        amount = float(update.message.text.replace(",", "").strip())
        if amount <= 0:
            raise ValueError("Amount must be positive.")
        context.user_data["amount"] = amount
    except ValueError as e:
        await update.message.reply_text(f"مبلغ نامعتبر است: {e}. لطفاً عدد مثبت وارد کنید:")
        return AMOUNT
    await update.message.reply_text("نرخ بهره ماهانه (درصد) را وارد کنید (یا 0 برای بدون بهره):")
    return RATE


async def get_rate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gets and validates the interest rate."""
    try:
        rate = float(update.message.text.strip())
        if rate < 0:
            raise ValueError("Rate cannot be negative.")
        context.user_data["rate"] = rate
    except ValueError as e:
        await update.message.reply_text(f"نرخ نامعتبر است: {e}. لطفاً یک عدد معتبر (مثلاً 0 یا 2.5) وارد کنید:")
        return RATE
    await update.message.reply_text("تعداد اقساط ماهانه را وارد کنید:")
    return DURATION


async def get_duration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Gets and validates the loan duration, then saves the loan."""
    try:
        duration = int(update.message.text.strip())
        if duration <= 0:
            raise ValueError("Duration must be a positive integer.")
    except ValueError as e:
        await update.message.reply_text(f"تعداد اقساط نامعتبر است: {e}. لطفاً عدد صحیح مثبت وارد کنید:")
        return DURATION

    name = context.user_data.get("name")
    amount = context.user_data.get("amount")
    rate = context.user_data.get("rate")

    if not name or amount is None or rate is None:
        await update.message.reply_text("❌ اطلاعات ثبت وام منقضی شده است. لطفاً دستور /new_loan را مجدد اجرا کنید.")
        context.user_data.clear()
        return ConversationHandler.END

    try:
        # Calculate installment amount
        if rate > 0:
            r = rate / 100
            installment = amount * r * ((1 + r) ** duration) / (((1 + r) ** duration) - 1)
        else:
            installment = amount / duration

        # Save to database
        loan_id = await add_loan(name, amount, rate, duration, round(installment, 0))

        # --- Create installment schedule ---
        await create_schedule(loan_id, duration, round(installment, 0))
        # --- End create installment schedule ---

        await update.message.reply_text(
            f"✅ وام با موفقیت ثبت شد.\n\n"
            f"🔹 شناسه وام: {loan_id}\n"
            f"👤 وام‌گیرنده: {name}\n"
            f"💰 مبلغ: {amount:,.0f} تومان\n"
            f"📈 نرخ بهره: {rate}٪ ماهانه\n"
            f"🗓 تعداد اقساط: {duration} ماه\n"
            f"💵 مبلغ هر قسط: {installment:,.0f} تومان",
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
    """Cancels the conversation."""
    context.user_data.clear()
    await update.message.reply_text("عملیات لغو شد.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ─── /loans ───────────────────────────────────────────────────────────────────
async def list_loans(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lists all active loans."""
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


# ─── /pay (manual payment entry) ──────────────────────────────────────────────
async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manually records a payment for a loan."""
    if not authorized(update):
        return
    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text("استفاده صحیح: /pay <loan_id> <amount>\nمثال: `/pay 3 500000`", parse_mode="Markdown")
        return
    try:
        loan_id = int(args[0])
        amount = float(args[1].replace(",", ""))
        if amount <= 0:
            raise ValueError("Payment amount must be positive.")
    except ValueError as e:
        await update.message.reply_text(f"ورودی نامعتبر است: {e}. مثال: `/pay 3 500000`", parse_mode="Markdown")
        return

    try:
        loan = await get_loan_by_id(loan_id)
        if not loan:
            await update.message.reply_text(f"وامی با شناسه {loan_id} یافت نشد.")
            return

        # NOTE: This /pay command just records the amount paid.
        # It does NOT mark a specific installment as paid or handle schedules.
        # Use /installments for detailed tracking.
        await add_installment(loan_id, amount) # Assumes add_installment updates counts etc.
        await update.message.reply_text(
            f"✅ قسط {amount:,.0f} تومان برای وام {loan_id} ({loan['borrower_name']}) ثبت شد (به صورت دستی)."
        )
    except Exception as e:
        logger.error(f"Error in pay: {e}", exc_info=True)
        await update.message.reply_text(f"❌ خطا در ثبت قسط: {e}")


# ─── /report ──────────────────────────────────────────────────────────────────
async def report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generates a detailed report for a specific loan."""
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
        total_paid = sum(i["amount"] for i in installments if i["paid"])
        # Recalculate remaining based on scheduled amounts and paid status
        total_scheduled = sum(i["amount"] for i in installments)
        remaining = max(0.0, total_scheduled - total_paid)

        text = (
            f"📊 گزارش وام {loan_id}\n\n"
            f"وام‌گیرنده: {loan['borrower_name']}\n"
            f"مبلغ اصلی: {loan['amount']:,.0f} تومان\n"
            f"نرخ بهره: {loan['interest_rate']}٪\n"
            f"تعداد اقساط: {loan['duration']}\n"
            f"مبلغ هر قسط (برنامه): {loan['installment_amount']:,.0f} تومان\n\n"
            f"پرداخت‌ها:\n"
        )
        for i, inst in enumerate(installments, 1):
            status = "✅" if inst["paid"] else "⬜️"
            paid_at_str = (inst.get("paid_at") or "")[:10]
            due_date_str = inst.get("due_date", "?")
            text += f"  {i}. {status} {inst['amount']:,.0f} تومان — سررسید: {due_date_str}"
            if inst["paid"]:
                text += f" (پرداخت شده: {paid_at_str})"
            text += "\n"

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
    """Marks a loan as closed."""
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


# ─── /installments ────────────────────────────────────────────────────────────
async def list_installments(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lists installments for a loan with payment buttons."""
    if not authorized(update):
        return
    if not context.args:
        await update.message.reply_text("استفاده: /installments <loan_id>")
        return
    try:
        loan_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("شناسه وام نامعتبر است.")
        return

    try:
        loan = await get_loan_by_id(loan_id)
        if not loan:
            await update.message.reply_text(f"وامی با شناسه {loan_id} یافت نشد.")
            return

        rows = await get_installments(loan_id)
        text = f"📋 اقساط وام {loan_id} — {loan['borrower_name']}\n\n"
        buttons = []
        for r in rows:
            if r["paid"]:
                paid_date = (r.get("paid_at") or "")[:10]
                text += f"✅ قسط {r['number']}: پرداخت شد — {paid_date}\n"
            else:
                text += f"⬜️ قسط {r['number']}: {r['amount']:,.0f} تومان — سررسید: {r['due_date']}\n"
                buttons.append([InlineKeyboardButton(
                    f"✔️ پرداخت قسط {r['number']}",
                    callback_data=f"pay:{r['id']}:{loan_id}", # Callback data: pay:installment_id:loan_id
                )])

        text += f"\n💵 پرداخت‌شده: {loan['paid_installments']}/{loan['duration']}"
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
        )
    except Exception as e:
        logger.error(f"Error in list_installments: {e}", exc_info=True)
        await update.message.reply_text(f"❌ خطا: {e}")


# ─── Inline Button Handler for Payment Confirmation ─────────────────────────────
async def pay_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the callback query when a payment button is pressed."""
    query = update.callback_query
    await query.answer() # Acknowledge the callback first

    if not authorized(update):
        await query.edit_message_text("⛔️ شما مجوز این عملیات را ندارید.")
        return

    try:
        _, inst_id_str, loan_id_str = query.data.split(":")
        inst_id = int(inst_id_str)
        loan_id = int(loan_id_str)

        # Mark the installment as paid in the database
        await mark_installment_paid(inst_id)

        # Fetch updated loan and installment data
        loan = await get_loan_by_id(loan_id)
        rows = await get_installments(loan_id)

        # Rebuild the message text and buttons
        text = f"📋 اقساط وام {loan_id} — {loan['borrower_name']}\n\n"
        buttons = []
        for r in rows:
            if r["paid"]:
                paid_date = (r.get("paid_at") or "")[:10]
                text += f"✅ قسط {r['number']}: پرداخت شد — {paid_date}\n"
            else:
                text += f"⬜️ قسط {r['number']}: {r['amount']:,.0f} تومان — سررسید: {r['due_date']}\n"
                buttons.append([InlineKeyboardButton(
                    f"✔️ پرداخت قسط {r['number']}",
                    callback_data=f"pay:{r['id']}:{loan_id}",
                )])

        # Calculate remaining amount for the loan report part
        total_paid_for_loan = sum(r["amount"] for r in rows if r["paid"])
        total_scheduled_for_loan = sum(r["amount"] for r in rows)
        remaining_loan_amount = max(0.0, total_scheduled_for_loan - total_paid_for_loan)

        text += (
            f"\n💵 پرداخت‌شده: {loan['paid_installments']}/{loan['duration']}"
            f"\n💰 باقی‌مانده وام: {remaining_loan_amount:,.0f} تومان"
        )

        # Edit the original message with updated info
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
        )
        await query.answer("✅ پرداخت ثبت شد")

    except Exception as e:
        logger.error(f"Error in pay_button callback: {e}", exc_info=True)
        await query.edit_message_text(f"❌ بروز خطا هنگام ثبت پرداخت: {e}")
        await query.answer(f"❌ خطا: {e}")


# ─── Cron: send reminders for due loans ───────────────────────────────────────
async def send_due_reminders(bot) -> None:
    """Sends reminders for unpaid installments due within the next few days."""
    try:
        # Fetch installments due in the next 3 days (or overdue)
        rows = await get_upcoming_unpaid(days_ahead=3)
        if not rows:
            logger.info("No upcoming installments to remind.")
            return

        for r in rows:
            borrower = r.get("loans", {}).get("borrower_name", "?") if r.get("loans") else "?"
            loan_id = r.get("loan_id", "?")
            inst_number = r.get("number", "?")
            amount = r.get("amount", 0)
            due_date = r.get("due_date", "?")

            message = (
                f"⏰ **یادآوری قسط**\n\n"
                f"👤 **وام‌گیرنده:** {borrower}\n"
                f"🔢 **قسط شماره:** {inst_number}\n"
                f"💵 **مبلغ:** {amount:,.0f} تومان\n"
                f"🗓 **سررسید:** {due_date}\n\n"
                f"برای ثبت پرداخت یا مشاهده جزئیات، از دستور `/installments {loan_id}` استفاده کنید."
            )
            await bot.send_message(
                chat_id=AUTHORIZED_CHAT_ID,
                text=message,
                parse_mode="Markdown" # Use Markdown for bolding
            )
            await asyncio.sleep(0.5) # Small delay between messages to avoid rate limits

    except Exception as e:
        logger.error(f"Error in send_due_reminders: {e}", exc_info=True)


# ─── Global Error Handler ─────────────────────────────────────────────────────
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Logs errors caused by updates and informs the user."""
    logger.error("Exception while handling an update:", exc_info=context.error)
    # Try to send a message to the user if the update object is available and has a message
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ متاسفانه خطایی در پردازش درخواست شما رخ داد. لطفاً دقایقی دیگر تلاش کنید یا با پشتیبانی تماس بگیرید."
            )
        except Exception as e:
            logger.error(f"Failed to send error message to user: {e}")


# ─── aiohttp web app for webhook and cron ────────────────────────────────────
def build_web_app(application: Application) -> web.Application:
    """Builds the aiohttp web application to handle webhooks and cron jobs."""
    web_app = web.Application()

    async def webhook_handler(request: web.Request) -> web.Response:
        """Handles incoming Telegram updates via webhook."""
        try:
            data = await request.json()
            update = Update.de_json(data, application.bot)
            await application.process_update(update)
            return web.Response(status=200)
        except Exception as e:
            logger.error(f"Webhook handler error: {e}", exc_info=True)
            # Return 500 to Telegram if processing fails, so they can retry
            return web.Response(status=500, text="Internal Server Error")

    async def cron_handler(request: web.Request) -> web.Response:
        """Handles scheduled cron job requests."""
        # Verify the secret token from the header
        secret = request.headers.get("X-Cron-Secret", "")
        cron_secret_env = os.environ.get("CRON_SECRET", "")

        if not cron_secret_env:
            logger.warning("CRON_SECRET environment variable not set. Cron jobs may not be secure.")
            # Proceed if CRON_SECRET is not set, but log a warning

        if cron_secret_env and secret != cron_secret_env:
            logger.warning(f"Invalid cron secret received: {secret}")
            return web.Response(status=403, text="Forbidden: Invalid Secret")

        logger.info("Cron job triggered.")
        try:
            await send_due_reminders(application.bot)
            return web.Response(status=200, text="Cron job executed successfully.")
        except Exception as e:
            logger.error(f"Cron job execution error: {e}", exc_info=True)
            return web.Response(status=500, text="Internal Server Error during cron job execution")

    async def health_handler(request: web.Request) -> web.Response:
        """Basic health check endpoint."""
        return web.Response(status=200, text="OK")

    web_app.router.add_post("/webhook", webhook_handler)
    web_app.router.add_get("/cron", cron_handler)
    web_app.router.add_get("/health", health_handler)

    return web_app


# ─── Main Execution Block ──────────────────────────────────────────────────────
async def main() -> None:
    """Starts the bot, sets up the webhook, and runs the web server."""
    token = os.environ.get("BOT_TOKEN")
    base_url = os.environ.get("RAILWAY_PUBLIC_URL")
    port_str = os.environ.get("PORT", "8080")

    if not token:
        logger.critical("BOT_TOKEN environment variable not set. Bot cannot start.")
        return
    if not base_url:
        logger.critical("RAILWAY_PUBLIC_URL environment variable not set. Webhook cannot be configured.")
        return

    try:
        port = int(port_str)
    except ValueError:
        logger.warning(f"Invalid PORT environment variable: {port_str}. Defaulting to 8080.")
        port = 8080

    # Initialize the Application
    application = Application.builder().token(token).updater(None).build() # No updater needed for webhook

    # --- Conversation Handler for /new_loan ---
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("new_loan", new_loan_start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_amount)],
            RATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_rate)],
            DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_duration)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="new_loan_conversation", # Optional: Name the conversation handler
        persistence=None # Set persistence if needed, e.g., via FilePersistence
    )

    # --- Register Handlers ---
    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("loans", list_loans))
    application.add_handler(CommandHandler("pay", pay)) # Manual payment entry
    application.add_handler(CommandHandler("report", report))
    application.add_handler(CommandHandler("close_loan", close_loan_cmd))
    application.add_handler(CommandHandler("installments", list_installments)) # New handler for installments
    application.add_handler(CallbackQueryHandler(pay_button, pattern=r"^pay:\d+:\d+$")) # Handler for payment confirmation buttons

    # Register the global error handler
    application.add_error_handler(error_handler)

    # --- Initialize and Start the Bot ---
    await application.initialize()
    webhook_url = f"{base_url.rstrip('/')}/webhook"
    try:
        await application.bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook set successfully to: {webhook_url}")
    except Exception as e:
        logger.error(f"Failed to set webhook to {webhook_url}: {e}")
        # Depending on the error, you might want to exit or try polling instead

    await application.start() # Starts the internal update processing loop

    # --- Start the aiohttp web server ---
    web_app = build_web_app(application)
    runner = web.AppRunner(web_app)
    try:
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        logger.info(f"Bot is running. Webhook listener started on port {port}.")
        logger.info(f"Public URL: {base_url}")
        logger.info(f"Webhook URL: {webhook_url}")

        # Keep the bot running indefinitely
        await asyncio.Event().wait()

    except KeyboardInterrupt:
        logger.info("Bot stopped manually.")
    except Exception as e:
        logger.critical(f"Failed to start web server or run bot: {e}", exc_info=True)
    finally:
        # --- Clean up ---
        logger.info("Shutting down bot and web server...")
        await application.stop()
        await runner.cleanup()
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    # Ensure the event loop is managed correctly
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped.")
