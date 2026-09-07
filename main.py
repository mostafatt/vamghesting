import asyncio
import os
import logging
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)
from db import (
    add_loan,
    create_schedule,
    get_loans_by_user,
    get_loan_by_id,
    get_installments_by_loan,
    mark_installment_paid,
    get_upcoming_unpaid,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

BORROWER, TOTAL_AMOUNT, INST_AMOUNT, TOTAL_INST = range(4)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "سلام! به ربات مدیریت وام و اقساط خوش آمدید. 📊\n\n"
        "دستورات موجود:\n"
        "🔹 /new_loan - ثبت وام جدید\n"
        "🔹 /loans یا /loan - مشاهده لیست وام‌ها\n"
        "🔹 /installments <loan_id> - مشاهده و پرداخت اقساط وام\n"
        "🔹 /report <loan_id> - گزارش کامل یک وام\n"
        "🔹 /cancel - لغو عملیات جاری"
    )
    await update.message.reply_text(welcome_text)


# ---- فرآیند ثبت وام جدید ----
async def new_loan_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("لطفاً نام وام‌گیرنده (یا عنوان وام) را وارد کنید:")
    return BORROWER


async def get_borrower(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["borrower_name"] = update.message.text.strip()
    await update.message.reply_text("مبلغ کل وام را به تومان وارد کنید (فقط عدد):")
    return TOTAL_AMOUNT


async def get_total_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip().replace(",", ""))
        context.user_data["total_amount"] = amount
        await update.message.reply_text("مبلغ هر قسط را به تومان وارد کنید (فقط عدد):")
        return INST_AMOUNT
    except ValueError:
        await update.message.reply_text("لطفاً مبلغ معتبر را به‌صورت عددی وارد کنید:")
        return TOTAL_AMOUNT


async def get_inst_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip().replace(",", ""))
        context.user_data["installment_amount"] = amount
        await update.message.reply_text("تعداد کل اقساط را وارد کنید (مثلاً 12):")
        return TOTAL_INST
    except ValueError:
        await update.message.reply_text("لطفاً تعداد اقساط را به‌صورت عددی وارد کنید:")
        return INST_AMOUNT


async def get_total_inst(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        count = int(update.message.text.strip())
        user_id = update.effective_user.id
        borrower = context.user_data["borrower_name"]
        total_amount = context.user_data["total_amount"]
        inst_amount = context.user_data["installment_amount"]

        # ثبت وام در دیتابیس
        loan = await add_loan(user_id, borrower, total_amount, inst_amount, count)
        if loan:
            # ایجاد جدول اقساط
            await create_schedule(loan["id"], count, inst_amount)
            await update.message.reply_text(
                f"✅ وام با موفقیت ثبت شد!\n\n"
                f"🆔 شناسه وام: {loan['id']}\n"
                f"👤 وام‌گیرنده: {borrower}\n"
                f"💰 مبلغ کل: {total_amount:,.0f} تومان\n"
                f"💵 مبلغ هر قسط: {inst_amount:,.0f} تومان\n"
                f"🔢 تعداد اقساط: {count}\n\n"
                f"برای مشاهده اقساط روی دستور زیر بزنید:\n/installments_{loan['id']}"
            )
        else:
            await update.message.reply_text("❌ خطا در ثبت وام در دیتابیس.")

        context.user_data.clear()
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error in get_total_inst: {e}", exc_info=True)
        await update.message.reply_text(f"خطایی رخ داد: {str(e)}")
        return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("عملیات لغو شد.")
    return ConversationHandler.END


# ---- لیست وام‌ها ----
async def list_loans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        loans = await get_loans_by_user(user_id)

        if not loans:
            await update.message.reply_text("شما هنوز هیچ وامی ثبت نکرده‌اید.\nبرای ثبت از /new_loan استفاده کنید.")
            return

        active_loans = [l for l in loans if l.get("status") != "settled"]
        settled_loans = [l for l in loans if l.get("status") == "settled"]

        msg = "📋 **لیست وام‌های شما:**\n\n"
        if active_loans:
            msg += "⏳ **وام‌های جاری (تسویه‌نشده):**\n"
            for l in active_loans:
                paid_cnt = l.get("paid_installments", 0)
                tot_cnt = l.get("total_installments", 0)
                inst_amt = l.get("installment_amount", 0)
                tot_amt = l.get("total_amount", 0)
                rem = (tot_cnt - paid_cnt) * inst_amt

                msg += (
                    f"🔹 **وام #{l['id']} - {l.get('borrower_name', 'نامشخص')}**\n"
                    f"   مبلغ کل: {tot_amt:,.0f} | اقساط: {paid_cnt}/{tot_cnt}\n"
                    f"   مانده بدهی: {rem:,.0f} تومان\n"
                    f"   مشاهده و پرداخت: /installments_{l['id']}\n"
                    f"   گزارش وام: /report_{l['id']}\n\n"
                )

        if settled_loans:
            msg += "✅ **وام‌های تسویه‌شده:**\n"
            for l in settled_loans:
                msg += f"✔ **وام #{l['id']} - {l.get('borrower_name', 'نامشخص')}** (کامل پرداخت شد)\n"

        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error in list_loans: {e}", exc_info=True)
        await update.message.reply_text(f"❌ خطا در دریافت اطلاعات وام‌ها: {str(e)}")


# ---- مشاهده و پرداخت اقساط ----
async def show_installments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.message.text.strip()
        loan_id = None
        if "_" in text:
            loan_id = text.split("_")[1]
        elif context.args:
            loan_id = context.args[0]

        if not loan_id or not loan_id.isdigit():
            await update.message.reply_text("لطفاً شناسه وام را وارد کنید. مثال: `/installments 1`", parse_mode="Markdown")
            return

        loan_id = int(loan_id)
        loan = await get_loan_by_id(loan_id)
        if not loan:
            await update.message.reply_text("وامی با این شناسه یافت نشد.")
            return

        installments = await get_installments_by_loan(loan_id)
        if not installments:
            await update.message.reply_text("جدول اقساطی برای این وام یافت نشد.")
            return

        msg = f"📊 **اقساط وام #{loan_id} ({loan['borrower_name']}):**\n\n"
        keyboard = []

        for inst in installments:
            due = inst.get("due_date", "-")
            amt = inst.get("amount", 0)
            num = inst.get("number", 0)

            if inst.get("paid"):
                msg += f"✅ قسط {num}: {amt:,.0f} تومان (سررسید: {due}) - پرداخت شده\n"
            else:
                msg += f"⏳ قسط {num}: {amt:,.0f} تومان (سررسید: {due}) - پرداخت نشده\n"
                keyboard.append([
                    InlineKeyboardButton(
                        f"💳 پرداخت قسط {num} ({amt:,.0f} ت)",
                        callback_data=f"pay:{inst['id']}:{loan_id}"
                    )
                ])

        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error in show_installments: {e}", exc_info=True)
        await update.message.reply_text(f"❌ خطا: {str(e)}")


# ---- گزارش کامل یک وام ----
async def show_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.message.text.strip()
        loan_id = None
        if "_" in text:
            loan_id = text.split("_")[1]
        elif context.args:
            loan_id = context.args[0]

        if not loan_id or not loan_id.isdigit():
            await update.message.reply_text("لطفاً شناسه وام را وارد کنید. مثال: `/report 1`", parse_mode="Markdown")
            return

        loan_id = int(loan_id)
        loan = await get_loan_by_id(loan_id)
        if not loan:
            await update.message.reply_text("وامی با این شناسه یافت نشد.")
            return

        installments = await get_installments_by_loan(loan_id)
        total_inst = loan.get("total_installments", 0)
        paid_inst = loan.get("paid_installments", 0)
        inst_amt = loan.get("installment_amount", 0)
        tot_amt = loan.get("total_amount", 0)
        paid_amt = paid_inst * inst_amt
        rem_amt = (total_inst - paid_inst) * inst_amt
        status_txt = "✅ تسویه کامل" if loan.get("status") == "settled" else "⏳ در حال پرداخت"

        report_msg = (
            f"📑 **گزارش وضعیت وام #{loan['id']}**\n\n"
            f"👤 **وام‌گیرنده:** {loan.get('borrower_name')}\n"
            f"📌 **وضعیت:** {status_txt}\n"
            f"💰 **مبلغ کل وام:** {tot_amt:,.0f} تومان\n"
            f"💵 **مبلغ هر قسط:** {inst_amt:,.0f} تومان\n"
            f"📊 **اقساط پرداخت شده:** {paid_inst} از {total_inst}\n"
            f"🟢 **مجموع واریزی:** {paid_amt:,.0f} تومان\n"
            f"🔴 **مانده بدهی:** {rem_amt:,.0f} تومان\n\n"
            f"برای مشاهده جزئیات ریز اقساط: /installments_{loan_id}"
        )

        await update.message.reply_text(report_msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error in show_report: {e}", exc_info=True)
        await update.message.reply_text(f"❌ خطا در ایجاد گزارش: {str(e)}")


# ---- اکشن کلیک روی دکمه پرداخت ----
async def pay_installment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data.split(":")
    if len(data) == 3 and data[0] == "pay":
        inst_id = int(data[1])
        loan_id = int(data[2])

        loan = await mark_installment_paid(inst_id, loan_id)
        if loan:
            rem_inst = loan["total_installments"] - loan["paid_installments"]
            rem_amount = rem_inst * loan["installment_amount"]

            alert = f"✅ قسط با موفقیت پرداخت شد!\n\n"
            alert += f"🔢 اقساط باقیمانده: {rem_inst} از {loan['total_installments']}\n"
            alert += f"💰 مانده بدهی: {rem_amount:,.0f} تومان\n"

            if loan.get("status") == "settled":
                alert += "\n🎉 تبریک! این وام به طور کامل تسویه شد."

            await query.edit_message_text(alert)
        else:
            await query.edit_message_text("❌ خطا در ثبت پرداخت.")


# ---- سرور AioHTTP برای Render/Webhook ----
async def start_web_server(app: Application):
    server = web.Application()

    async def health(request):
        return web.Response(text="OK")

    async def cron_reminder(request):
        upcoming = await get_upcoming_unpaid(days_ahead=3)
        for inst in upcoming:
            loan = inst.get("loans")
            if loan and loan.get("user_id"):
                try:
                    await app.bot.send_message(
                        chat_id=loan["user_id"],
                        text=(
                            f"⏰ **یادآوری سررسید قسط**\n\n"
                            f"قسط شماره {inst.get('number')} از وام **{loan.get('borrower_name')}**\n"
                            f"مبلغ: {inst.get('amount'):,.0f} تومان\n"
                            f"تاریخ سررسید: {inst.get('due_date')}\n\n"
                            f"برای ثبت پرداخت: /installments_{loan.get('id')}"
                        ),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"Failed to send reminder: {e}")
        return web.Response(text="Reminders processed.")

    server.router.add_get("/", health)
    server.router.add_get("/cron", cron_reminder)

    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Web server started on port {port}")


async def post_init(application: Application):
    """اجرای وب‌سرور همراه با شروع به کار ربات"""
    asyncio.create_task(start_web_server(application))


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not found!")
        return

    application = Application.builder().token(token).post_init(post_init).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("new_loan", new_loan_start)],
        states={
            BORROWER: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_borrower)],
            TOTAL_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_total_amount)],
            INST_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_inst_amount)],
            TOTAL_INST: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_total_inst)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # ثبت تمامی دستورات و هندلرها
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler(["loans", "loan"], list_loans))
    application.add_handler(CommandHandler("report", show_report))
    application.add_handler(MessageHandler(filters.Regex(r"^/report_\d+$"), show_report))
    application.add_handler(CommandHandler("installments", show_installments))
    application.add_handler(MessageHandler(filters.Regex(r"^/installments_\d+$"), show_installments))
    application.add_handler(CallbackQueryHandler(pay_installment_callback, pattern=r"^pay:"))
    application.add_handler(conv_handler)

    # شروع ربات بدون مشکل لوپ
    application.run_polling()


if __name__ == "__main__":
    main()
