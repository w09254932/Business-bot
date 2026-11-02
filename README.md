# Telegram Business Monitor PoC

إثبات مفهوم لبوت Telegram Business باستخدام Bot API وFastAPI.

## الوظائف
- استقبال business_connection وbusiness_message.
- حفظ الرسائل الواردة والوسائط في SQLite.
- مقارنة الرسائل المعدّلة وإشعار المالك.
- استرجاع نسخة الرسالة المحذوفة إذا كانت محفوظة.
- محاولة إعادة إرسال الوسيط الموجود في reply_to_message.
- منع معالجة update_id المكرر.
- تسجيل الأحداث في logs/app.log.

## تشغيل محلي
1. انسخ `.env.example` إلى `.env`.
2. أدخل BOT_TOKEN وWEBHOOK_SECRET.
3. ثبّت المتطلبات: `pip install -r requirements.txt`
4. شغّل: `uvicorn app.main:app --reload`
5. شغّل الاختبارات: `pytest -q`

## النشر على Render
المشروع يتضمن render.yaml. اربط المستودع في Render عبر Blueprint.
أدخل BOT_TOKEN في متغيرات البيئة. WEBHOOK_SECRET يتم توليده تلقائياً.
Render يضبط RENDER_EXTERNAL_URL تلقائياً، والتطبيق يسجل Webhook عند بدء التشغيل.

## ملاحظات مهمة
- يجب ربط البوت بحساب Telegram Business ومنحه الصلاحيات المطلوبة.
- أحداث الحذف لا تتضمن محتوى الرسالة؛ لذلك يعتمد الاسترجاع على النسخة المحفوظة مسبقاً.
- Telegram لا يضمن إتاحة محتوى الوسائط المؤقتة للبوت.
- محاولة إعادة إرسال media من reply_to_message ليست ضماناً لاستعادة كل وسائط العرض لمرة واحدة.
- SQLite والوسائط هنا إثبات مفهوم؛ لا تستخدمه لأرشفة بيانات حساسة دون سياسة موافقة واحتفاظ وحماية مناسبة.
