# 🤖 ربات پیگیری وام (Telegram Loan Tracker)

ربات تلگرامی برای ثبت وام‌ها، مشاهده اقساط، علامت‌گذاری اقساط پرداخت‌شده و دریافت یادآوری روزانه از طریق cron.

## معماری و انتخاب روش وب‌هوک
از `Application.run_webhook` کتابخانه **python-telegram-bot v21** استفاده شده است. این روش یک وب‌سرور aiohttp داخلی بالا می‌آورد که:
- روت POST `/webhook/<TOKEN>` را برای دریافت آپدیت‌های تلگرام سرو می‌کند،
- با `webhook_app` می‌توان روت اضافه‌ی `/cron` را به **همان سرور** اضافه کرد،
- دیگر نیازی به Flask یا پروسه دوم نیست و روی Railway free با یک پروسه کار می‌کند.
(polling روی Railway مناسب نیست چون به پورت HTTP عمومی برای /cron نیاز داریم.)

## مرحله ۱: آماده‌سازی Supabase
1. وارد پروژه Supabase شوید → **SQL Editor** → محتوای فایل `schema.sql` را اجرا کنید.
2. از **Project Settings → API** مقدار `Project URL` و کلاینت `anon` (یا بهتر: `service_role`) key را بردارید.

## مرحله ۲: گرفتن chat_id مجاز
1. به ربات خود یک پیام بدهید، سپس در مرورگر آدرس زیر را باز کنید:
   `https://api.telegram.org/bot<BOT_TOKEN>/getUpdates`
2. در خروجی، مقدار `message.chat.id` را پیدا کنید و در `AUTHORIZED_CHAT_ID` بگذارید.

## مرحله ۳: دیپلوی روی Railway
1. کد را در یک مخزن GitHub قرار دهید.
2. در Railway: **New Project → Deploy from GitHub repo**.
3. در تب **Variables** این متغیرها را اضافه کنید:
   - `BOT_TOKEN` : توکن ربات از @BotFather
   - `AUTHORIZED_CHAT_ID` : chat_id خودتان
   - `SUPABASE_URL` : آدرس پروژه Supabase
   - `SUPABASE_KEY` : کلید anon یا service_role
   - `CRON_SECRET` : یک رشته تصادفی برای محافظت از `/cron`
   - `RAILWAY_PUBLIC_URL` : آدرس عمومی سرویس (بعد از مرحله ۴)
   - `PORT` : لازم نیست؛ Railway خودش ست می‌کند (پیش‌فرض 8080)
4. **Networking → Generate Domain** بزنید تا آدرس عمومی مثل
   `https://your-service.up.railway.app` ساخته شود. همین را در `RAILWAY_PUBLIC_URL` بگذارید
   (بدون اسلش آخر) و سرویس را دوباره deploy کنید. ربات هنگام اجرا خودش `setWebhook` را صدا می‌زند.

## مرحله ۴: تنظیم cron-job.org (یادآوری روزانه)
1. در [cron-job.org](https://cron-job.org) یک حساب بسازید و **Create Cronjob** بزنید.
2. آدرس را وارد کنید:
   `https://your-service.up.railway.app/cron?secret=<مقدار CRON_SECRET>`
3. زمان‌بندی را روزانه (مثلاً ساعت 09:00) بگذارید و ذخیره کنید.
4. حالا هر روز لیست اقساط با سررسید حداکثر ۲ روز آینده برای شما پیام می‌شود و می‌توانید
   با دکمه «✅ پرداخت شد» داخل همان پیام، قسط را پرداخت‌شده کنید.

## استفاده از ربات
- `/start` → منوی اصلی با دکمه‌های شیشه‌ای:
  - ➕ ثبت وام جدید: نام، مبلغ کل، تعداد اقساط و تاریخ سررسید اول (فرمت `YYYY-MM-DD`) را می‌پرسد.
  - 📋 وام‌های در حال پرداخت: جزئیات وام + دکمه پرداخت هر قسط.
  - ✅ وام‌های تسویه‌شده: خلاصه فقط-خواندنی.
- فقط `AUTHORIZED_CHAT_ID` به ربات دسترسی دارد؛ بقیه «دسترسی غیرمجاز» می‌بینند.

## اجرای محلی (اختیاری)
```bash
cp .env.example .env   # مقادیر را پر کنید
pip install -r requirements.txt
python main.py
```

## نکته امنیتی
توکن ربات و کلیدهای Supabase را هرگز در کد کامیت نکنید؛ فایل `.env` در `.gitignore` است.
