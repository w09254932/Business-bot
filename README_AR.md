# TR Battle Calculator — حاسبة معركة الشعبية

مشروع مستقل لبوت Telegram يحسب معارك الشعبية عبر HTML Widget داخل المحادثة الخاصة. لم يعد هذا المستودع مشروع أرشفة Telegram Business، ولا يحتوي على Telethon أو SQLite أو قناة أرشفة.

## المزايا

- حاسبة واحدة داخل رسالة Telegram: الفردية، المنزل، والفريق.
- لوحة أرقام داخل Widget؛ لا تعتمد على حقول إدخال أو لوحة مفاتيح النظام.
- الحساب الرسمي في Python على الخادم، وليس في JavaScript.
- حفظ دائم لكل المعارك في Firestore بدون حذف تلقائي للسجلات القديمة.
- سجل خاص بكل مستخدم، مع عرض 5 معارك في كل صفحة.
- حماية Webhook باستخدام `WEBHOOK_SECRET`.
- فهارس Firestore جاهزة في `firestore.indexes.json`.

## قواعد الحساب المحفوظة

- جداول الفردية والفريق مطابقة للجداول الأصلية في بوت الحاسبة.
- وضع المنزل يستخدم جدول الفردية، كما كان في الكود الأصلي.
- الفوز: نقاط اللاعب كاملة + نصف نقاط الخصم.
- الخسارة: نصف نقاط اللاعب، والخصم يحصل على نقاطه كاملة + نصف نقاط اللاعب.
- التعادل يُعامل كخسارة، كما في الكود الأصلي.

المصدر الوحيد للمعادلات هو `battle_core.py`.

## تشغيل محلي

1. أنشئ بوتًا من [@BotFather](https://t.me/BotFather) وخذ `BOT_TOKEN`.
2. فعّل Cloud Firestore في مشروع Firebase وأنشئ Service Account بصلاحية مناسبة للوصول إلى قاعدة Firestore.
3. انسخ `.env.example` إلى `.env` وأكمل المتغيرات:

   - `BOT_TOKEN`: توكن البوت.
   - `WEBHOOK_SECRET`: سر عشوائي؛ يشتق التطبيق منه قيمة SHA-256 سداسية عشرية متوافقة مع قيود Telegram تلقائيًا.
   - `WEBHOOK_URL`: اختياري؛ عنوان الخدمة العام. عند تركه فارغًا يستخدم التطبيق `RENDER_EXTERNAL_URL` إن كانت موجودة.
   - `FIREBASE_CREDENTIALS_JSON`: محتوى JSON كامل لحساب الخدمة في سطر واحد. لا تضع الملف أو المفتاح في GitHub.

4. ثبّت المتطلبات وشغّل الخدمة:

   ```bash
   python -m venv .venv
   source .venv/bin/activate        # Windows: .venv\\Scripts\\activate
   pip install -r requirements.txt
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```

5. عند استخدام Webhook محليًا، وفّر عنوان HTTPS عامًا وضعه في `WEBHOOK_URL`. إذا لم يكن العنوان مضبوطًا، لن يسجل التطبيق Webhook تلقائيًا.
6. افتح البوت في الخاص وأرسل `/start` أو `/widget`.

## نشر Render

ملف `render.yaml` يحافظ على اسم خدمة Render الحالية `business-bot` لتجنب تغيير عنوان الخدمة أثناء التحويل؛ يمكنك إعادة تسميتها لاحقًا من لوحة Render إذا رغبت.

متغيرات Render المطلوبة:

- `BOT_TOKEN`
- `WEBHOOK_SECRET` (ينشئ Render قيمة تلقائية من إعداد `generateValue`)
- `FIREBASE_CREDENTIALS_JSON`
- `WEBHOOK_URL` اختياري، ويمكن تركه فارغًا لاستخدام `RENDER_EXTERNAL_URL`.

أمر التشغيل:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

فحص الخدمة: `/health`.

## فهارس Firestore

بعد إضافة ملفي `firebase.json` و`firestore.indexes.json` إلى مشروع Firebase، نفّذ:

```bash
firebase deploy --only firestore:indexes
```

## الاختبارات

```bash
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

الاختبارات تتحقق من حدود جميع شرائح الفردية والفريق، ومعادلات الفوز والخسارة والتعادل، والتحقق من طلبات Widget، وحجم HTML، والصفحات الخاصة بسجل كل مستخدم.

## قيود Telegram Widget

- في بيئة Telegram الإنتاجية الحالية، إرسال Widgets متاح في المحادثات الخاصة مع البوت فقط.
- يجب أن يظل HTML بحد أقصى 32 KB، وألا يتجاوز 4 KB بعد ضغط zlib بالمستوى 9. يفحص التطبيق هذين الحدين قبل الإرسال.
- Widget معزول ولا يملك اتصال شبكة. يتواصل مع Python من خلال `TelegramWidget.request()` فقط.
- لا توجد حقول نص أو لوحة مفاتيح نظام داخل Widget؛ الإدخال عبر الأزرار.
- لا يمكن استبدال HTML لرسالة Widget موجودة؛ بعد تحديث الواجهة أرسل Widget جديدة.
- يجب الرد على كل `widget_query` خلال 15 ثانية. يجيب التطبيق أولًا عن الاستعلام بعد تجهيز النتيجة وحفظها بمهلة محددة.

## بنية المشروع

```text
app/
  __init__.py
  main.py                 # FastAPI + Telegram webhook
battle_core.py            # جداول النقاط ومعادلات الحساب
widget_calculator.py      # بروتوكول Widget وسجل Firestore
widget.html               # واجهة Telegram Widget الفعلية
preview.html              # معاينة محلية ببيانات وهمية
firestore.indexes.json    # فهارس سجل المعارك
firebase.json
render.yaml
requirements.txt
requirements-dev.txt
tests/
  test_app.py
  test_battle_core.py
  test_widget.py
```

## الخصوصية والأمان

- لا ترفع `.env` أو توكن Telegram أو JSON حساب Firebase إلى GitHub.
- الخادم يأخذ هوية المستخدم من `widget_query.from.id` الذي يرسله Telegram، ولا يثق بأي User ID قادم من HTML.
- استعلامات Firestore مقيدة بمعرف Telegram للمستخدم.
- معرف سجل المعركة مشتق من `widget_query.id` لمنع تكرار حفظ نفس الطلب عند إعادة إرسال Webhook.
- لا توجد سياسة حذف تلقائي؛ المعارك تبقى في Firestore إلى أن تحذفها أنت من قاعدة البيانات.
