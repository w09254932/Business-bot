# تحويل المستودع الحالي بالكامل إلى TR Battle Calculator

بعد فك ضغط حزمة TR Battle Calculator إلى **جذر** مستودع `Business-bot` مع اختيار استبدال الملفات المتطابقة، احذف بقايا مشروع الأرشفة القديمة التالية لأنها غير مستخدمة في مشروع TR:

```bash
rm -f app/store.py
rm -f widgetbot.py
rm -f battle_calculator_telegram_widget.html
rm -f BATTLE_WIDGET_INTEGRATION.md
rm -f install_widget.py
rm -f tests/test_store.py
```

تم استبدال `app/main.py` و`app/__init__.py` و`README.md` و`README_AR.md` و`.env.example` و`.gitignore` و`requirements.txt` و`render.yaml` بإصدارات TR Battle Calculator.

لا تحذف ملف `.env` المحلي إن كان يحتوي إعدادات تحتاجها، لكن راجع محتواه يدويًا وأزل متغيرات الأرشفة القديمة بعد نقل `BOT_TOKEN` و`WEBHOOK_SECRET` و`FIREBASE_CREDENTIALS_JSON`.
