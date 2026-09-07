import os
import logging
import asyncio
from datetime import datetime, date
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

from db import (
    add_loan,
    create_schedule,
    get_loans_by_user,
    get_active_loans,
    get_loan_by_id,
    get_installments_by_loan,
    mark_installment_paid,
    get_installments_for_reminder,
    mark_reminder_sent,
)

# تنظیمات لاگینگ
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# مراحل مکالمه ثبت وام
(
    BORROWER,
    TOTAL_AMOUNT,
    INST_AMOUNT,
    TOTAL_INST,
    FIRST_DUE_DATE,
    REMINDER_DAYS,
) = range(6)


# ---------------- دستورات اصلی ربات ---------------- #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "سلام! 👋 به ربات مدیریت وام و اقساط خوش آمدید.\n\n"
        "دستورات موجود:\n"
        "➕ /new_loan - ثبت وام جدید\n"
        "📋 /loans - لیست وام‌های فعال\n"
        "📁 /all_loans - مشاهده تمام وام‌ها\n"
        "❌ /cancel - لغو عملیات جاری\n"
    )
    await update.message.reply_text(welcome_text)


# ---------------- جریان ثبت وام جدید ---------------- #

async def new_loan_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👤 لطفاً نام وام‌گیرنده یا عنوان وام را وارد کنید:")
    return BORROWER


async def get_borrower(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["borrower_name"] = update.message.text.strip()
    await update.message.reply_text("💰 مبلغ کل وام را به تومان وارد کنید (فقط عدد):")
    return TOTAL_AMOUNT


async def get_total_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip().replace(",", ""))
        if amount <= 0:
            raise ValueError
        context.user_data["total_amount"] = amount
        await update.message.reply_text("💵 مبلغ هر قسط را به تومان وارد کنید (فقط عدد):")
        return INST_AMOUNT
    except ValueError:
        await update.message.reply_text("⚠️ لطفاً مبلغ را به صورت عدد معتبر و بزرگ‌تر از صفر وارد کنید:")
        return TOTAL_AMOUNT


async def get_inst_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip().replace(",", ""))
        if amount <= 0:
            raise ValueError
        context.user_data["installment_amount"] = amount
        await update.message.reply_text("🔢 تعداد کل اقساط را وارد کنید:")
        return TOTAL_INST
    except ValueError:
        await update.message.reply_text("⚠️ لطفاً تعداد اقساط را به صورت عدد صحیح وارد کنید:")
        return INST_AMOUNT


async def get_total_inst(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        count = int(update.message.text.strip())
        if count <= 0:
            raise ValueError
        context.user_data["total_installments"] = count
        await update.message.reply_text(
            "📅 تاریخ سررسید اولین قسط را وارد کنید.\n"
            "فرمت: YYYY-MM-DD\n"
            "مثال: 2026-10-05"
        )
        return FIRST_DUE_DATE
    except ValueError:
        await update.message.reply_text("⚠️ لطفاً تعداد اقساط را به صورت عدد صحیح و بزرگ‌تر از صفر وارد کنید:")
        return TOTAL_INST


async def get_first_due_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.message.text.strip()
        first_due_date = datetime.strptime(text, "%Y-%m-%d").date()
        context.user_data["first_due_date"] = first_due_date

        await update.message.reply_text(
            "🔔 چند روز قبل از سررسید یادآوری ارسال شود؟\n"
            "(برای پیش‌فرض عدد ۵ را بفرستید)\n\n"
            "مثال: 5"
        )
        return REMINDER_DAYS
    except ValueError:
        await update.message.reply_text(
            "⚠️ فرمت تاریخ نامعتبر است.\n"
            "لطفاً تاریخ را دقیقاً به شکل YYYY-MM-DD وارد کنید (مثلاً 2026-10-05):"
        )
        return FIRST_DUE_DATE


async def get_reminder_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.message.text.strip()
        reminder_days = int(text) if text else 5
        if reminder_days < 0 or reminder_days > 60:
            raise ValueError

        user_id = update.effective_user.id
        borrower = context.user_data["borrower_name"]
        total_amount = context.user_data["total_amount"]
        inst_amount = context.user_data["installment_amount"]
        count = context.user_data["total_installments"]
        first_due_date = context.user_data["first_due_date"]

        # ثبت وام در دیتابیس
        loan = await add_loan(
            user_id=user_id,
            borrower_name=borrower,
            total_amount=total_amount,
            installment_amount=inst_amount,
            total_installments=count,
            first_due_date=first_due_date,
            reminder_days=reminder_days,
        )

        if not loan:
            await update.message.reply_text("❌ خطا در ثبت اطلاعات وام در دیتابیس.")
            context.user_data.clear()
            return ConversationHandler.END

        # تولید جدول زمانی اقساط ۳۰ روزه
        await create_schedule(
            loan_id=loan["id"],
            total_installments=count,
            installment_amount=inst_amount,
            start_date=first_due_date,
            reminder_days=reminder_days,
        )

        success_text = (
            f"✅ وام با موفقیت ثبت شد!\n\n"
            f"🆔 شناسه وام: {loan['id']}\n"
            f"👤 وام‌گیرنده: {borrower}\n"
            f"💰 مبلغ کل: {total_amount:,.0f} تومان\n"
            f"💵 مبلغ هر قسط: {inst_amount:,.0f} تومان\n"
            f"🔢 تعداد اقساط: {count} ماهه\n"
            f"📅 سررسید اولین قسط: {first_due_date.isoformat()}\n"
            f"🔔 یادآوری: {reminder_days} روز قبل از سررسید\n\n"
            f"برای مدیریت و مشاهده اقساط روی دستور زیر بزنید:\n"
            f"/installments_{loan['id']}"
        )
        await update.message.reply_text(success_text)
        context.user_data.clear()
        return ConversationHandler.END

    except ValueError:
        await update.message.reply_text("⚠️ لطفاً یک عدد معتبر (بین ۰ تا ۶۰) وارد کنید:")
        return REMINDER_DAYS
    except Exception as e:
        logger.error(f"Error in saving loan: {e}", exc_info=True)
        await update.message.reply_text(f"❌ خطای پیش‌بینی نشده: {e}")
        context.user_data.clear()
        return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("🚫 عملیات ثبت وام لغو شد.")
    return ConversationHandler.END


# ---------------- مشاهده و مدیریت وام‌ها ---------------- #

async def list_loans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    loans = await get_active_loans(user_id)

    if not loans:
        await update.message.reply_text("📌 شما هیچ وام فعالی ندارید.")
        return

    text = "📋 لیست وام‌های فعال شما:\n\n"
    for l in loans:
        paid = l.get("paid_installments", 0)
        total = l.get("total_installments", 0)
        text += (
            f"🔹 وام #{l['id']}: {l.get('borrower_name', '-')}\n"
            f"💰 مبلغ قسط: {l.get('installment_amount', 0):,.0f} تومان\n"
            f"📊 وضعیت پرداخت: {paid} از {total} قسط\n"
            f"🔍 مشاهده اقساط: /installments_{l['id']}\n\n"
        )
    await update.message.reply_text(text)


async def list_all_loans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    loans = await get_loans_by_user(user_id)

    if not loans:
        await update.message.reply_text("📌 هیچ وامی ثبت نشده است.")
        return

    text = "📁 آرشیو تمام وام‌ها:\n\n"
    for l in loans:
        status_icon = "🟢 فعال" if l.get("status") != "settled" else "✅ تسویه‌شده"
        text += (
            f"🔹 وام #{l['id']}: {l.get('borrower_name', '-')} ({status_icon})\n"
            f"💰 مبلغ کل: {l.get('total_amount', 0):,.0f} تومان\n"
            f"📊 اقساط: {l.get('paid_installments', 0)}/{l.get('total_installments', 0)}\n"
            f"🔍 جزئیات: /installments_{l['id']}\n\n"
        )
    await update.message.reply_text(text)


async def show_installments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        command_text = update.message.text
        loan_id = int(command_text.split("_")[1])
    except (IndexError, ValueError):
        await update.message.reply_text("⚠️ فرمت دستور نامعتبر است.")
        return

    loan = await get_loan_by_id(loan_id, user_id)
    if not loan:
        await update.message.reply_text("❌ وام مورد نظر یافت نشد یا متعلق به شما نیست.")
        return

    installments = await get_installments_by_loan(loan_id)
    if not installments:
        await update.message.reply_text("📌 قسطی برای این وام یافت نشد.")
        return

    text = (
        f"📋 اقساط وام #{loan['id']} ({loan.get('borrower_name', '-')})\n"
        f"💰 مبلغ کل: {loan.get('total_amount', 0):,.0f} تومان\n"
        f"📊 پرداخت شده: {loan.get('paid_installments', 0)} از {loan.get('total_installments', 0)}\n\n"
    )

    keyboard = []
    for inst in installments:
        status = "✅ پرداخت شده" if inst.get("paid") else "⏳ پرداخت نشده"
        text += (
            f"قسط {inst['number']}: {inst.get('amount', 0):,.0f} تومان | "
            f"سررسید: {inst.get('due_date')} | {status}\n"
        )
        if not inst.get("paid"):
            keyboard.append([
                InlineKeyboardButton(
                    f"✅ پرداخت قسط {inst['number']} ({inst.get('amount', 0):,.0f} تومان)",
                    callback_data=f"pay:{inst['id']}:{loan_id}"
                )
            ])

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    await update.message.reply_text(text, reply_markup=reply_markup)


# ---------------- مدیریت دکمه‌های شیشه‌ای پرداخت ---------------- #

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("pay:"):
        _, inst_id_str, loan_id_str = data.split(":")
        inst_id = int(inst_id_str)
        loan_id = int(loan_id_str)
        user_id = update.effective_user.id

        updated_loan = await mark_installment_paid(inst_id, loan_id, user_id)
        if updated_loan:
            paid_count = updated_loan.get("paid_installments", 0)
            total_count = updated_loan.get("total_installments", 0)
            remaining_balance = (total_count - paid_count) * updated_loan.get("installment_amount", 0)

            msg = (
                f"✅ قسط با موفقیت ثبت شد!\n\n"
                f"📊 پیشرفت: {paid_count} از {total_count} قسط پرداخت شده است.\n"
                f"💵 مبلغ باقی‌مانده وام: {remaining_balance:,.0f} تومان\n"
            )
            if updated_loan.get("status") == "settled":
                msg += "\n🎉 تبریک! تمامی اقساط این وام تسویه شد."

            await query.edit_message_text(msg)
        else:
            await query.edit_message_text("❌ خطا در ثبت پرداخت قسط یا قسط قبلاً پرداخت شده است.")


# ---------------- سیستم یادآوری و وب‌سرور ---------------- #

async def process_reminders(application: Application):
    """بررسی اقساط موعد رسیده و ارسال پیام یادآوری با دکمه تلگرام"""
    reminders = await get_installments_for_reminder()
    for installment in reminders:
        loan = installment.get("loans")
        if not loan:
            continue

        user_id = loan.get("user_id")
        if not user_id:
            continue

        try:
            keyboard = [[
                InlineKeyboardButton(
                    "✅ پرداخت شد",
                    callback_data=f"pay:{installment['id']}:{loan['id']}"
                )
            ]]

            reminder_msg = (
                "⏰ یادآوری سررسید قسط\n\n"
                f"👤 وام‌گیرنده: {loan.get('borrower_name', '-')}\n"
                f"🔢 قسط شماره: {installment.get('number')}\n"
                f"💵 مبلغ: {installment.get('amount', 0):,.0f} تومان\n"
                f"📅 تاریخ سررسید: {installment.get('due_date')}\n\n"
                "جهت ثبت پرداخت روی دکمه زیر کلیک کنید:"
            )

            await application.bot.send_message(
                chat_id=user_id,
                text=reminder_msg,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            await mark_reminder_sent(installment["id"])
            logger.info(f"Reminder sent for installment {installment['id']}")
        except Exception as e:
            logger.error(f"Failed to send reminder for installment {installment.get('id')}: {e}")


async def start_web_server(application: Application):
    """وب‌سرور سبک aiohttp برای سازگاری با پورت Render و قابلیت Cron"""
    server = web.Application()

    async def health(request):
        return web.Response(text="Bot is running OK")

    async def cron_handler(request):
        await process_reminders(application)
        return web.Response(text="Reminders processed successfully.")

    server.router.add_get("/", health)
    server.router.add_get("/cron", cron_handler)

    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Aiohttp web server running on port {port}")


# ---------------- راه‌اندازی اصلی برنامه ---------------- #

async def post_init_callback(application: Application):
    """اجرای وب‌سرور داخل همان Event Loop بعد از مقداردهی ربات"""
    asyncio.create_task(start_web_server(application))


def main():
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        logger.error("TELEGRAM_BOT_TOKEN is not set in environment variables!")
        return

    application = (
        Application.builder()
        .token(bot_token)
        .post_init(post_init_callback)
        .build()
    )

    # هندلر چندمرحله‌ای ثبت وام
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("new_loan", new_loan_start)],
        states={
            BORROWER: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_borrower)],
            TOTAL_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_total_amount)],
            INST_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_inst_amount)],
            TOTAL_INST: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_total_inst)],
            FIRST_DUE_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_first_due_date)],
            REMINDER_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_reminder_days)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # ثبت هندلرها
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("loans", list_loans))
    application.add_handler(CommandHandler("all_loans", list_all_loans))
    application.add_handler(MessageHandler(filters.Regex(r"^/installments_\d+$"), show_installments))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(handle_callback_query))

    logger.info("Bot is starting polling...")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
