# -*- coding: utf-8 -*-
"""Reference copy of the main.py rules, used only by the bundled regression tests.
The live Widget integration calls the original main.py compute_battle() directly.
"""

TIERS_INDIVIDUAL = [
    (0, 2000, 6),
    (2001, 4000, 10),
    (4001, 8000, 14),
    (8001, 15000, 16),
    (15001, 50000, 20),
    (50001, 120000, 24),
    (120001, 260000, 28),
    (260001, 500000, 32),
    (500001, 900000, 36),
    (900001, 2000000, 40),
    (2000001, 8000000, 42),
    (8000001, float("inf"), 44),
]

TIERS_TEAM = [
    (0, 5000, 6),
    (5001, 12000, 10),
    (12001, 26000, 14),
    (26001, 48000, 16),
    (48001, 120000, 20),
    (120001, 200000, 24),
    (200001, 400000, 28),
    (400001, 560000, 32),
    (560001, 800000, 34),
    (800001, 1600000, 36),
    (1600001, 3200000, 38),
    (3200001, 6000000, 40),
    (6000001, float("inf"), 42),
]

MODE_LABELS = {"individual": "الفردية", "team": "الفريق", "home": "المنزل"}


def points_for(n: int, mode: str = "individual") -> int:
    tiers = TIERS_TEAM if mode == "team" else TIERS_INDIVIDUAL
    for lo, hi, pts in tiers:
        if lo <= n <= hi:
            return pts
    return 0


def fmt(x) -> str:
    return str(int(x)) if float(x) == int(x) else f"{x:.1f}"


def compute_battle(my_number: int, opp_number: int, mode: str):
    """Keep original behavior: ties are losses; home uses individual tiers."""
    if mode not in MODE_LABELS:
        raise ValueError("نوع المعركة غير معروف")
    if isinstance(my_number, bool) or isinstance(opp_number, bool):
        raise ValueError("القيم يجب أن تكون أعدادًا صحيحة")
    if not isinstance(my_number, int) or not isinstance(opp_number, int):
        raise ValueError("القيم يجب أن تكون أعدادًا صحيحة")
    if my_number < 0 or opp_number < 0:
        raise ValueError("القيم لا يمكن أن تكون سالبة")

    my_points = points_for(my_number, mode)
    opp_points = points_for(opp_number, mode)
    outcome = "win" if my_number > opp_number else "loss"

    if outcome == "win":
        result_label = "فوز ✅"
        my_result = my_points + opp_points / 2
        opp_result = opp_points / 2
    else:
        result_label = "خسارة ❌"
        my_result = my_points / 2
        opp_result = opp_points + my_points / 2

    if mode == "team":
        note = (
            "فوز = نقاطكم كاملة + نصف نقاط التيم ضدكم"
            if outcome == "win"
            else "خسارة = تأخذون نصف نقاطكم فقط"
        )
        text = (
            "🏆 نتيجة معركة الشعبية الفريق\n"
            "━━━━━━━━━━━━━━\n"
            f"👤 دعم تيمي : {my_number:,}  =  {my_points} نقطة\n"
            f"🎯 دعم تيم خصمي : {opp_number:,}  =  {opp_points} نقطة\n"
            "━━━━━━━━━━━━━━\n"
            f"النتيجة : {result_label}\n"
            f"لكل شخص من تيمك : {fmt(my_result)} نقطة\n"
            f"لكل شخص من تيم الخصم : {fmt(opp_result)} نقطة\n"
            f"{note}"
        )
    else:
        note = (
            "فوز = نقاطك+نصف نقاط خصمك"
            if outcome == "win"
            else "خسارة = تأخذ نصف نقاطك فقط"
        )
        text = (
            f"🏆 نتيجة معركة الشعبية {MODE_LABELS.get(mode, '')}\n"
            "━━━━━━━━━━━━━━\n"
            f"👤 نقاطك: {my_number:,}  =  {my_points} نقطة\n"
            f"🎯 نقاط الخصم: {opp_number:,}  =  {opp_points} نقطة\n"
            "━━━━━━━━━━━━━━\n"
            f"النتيجة : {result_label}\n"
            f"نقاطك : {fmt(my_result)} نقطة\n"
            f"نقاط الخصم : {fmt(opp_result)} نقطة\n"
            f"{note}"
        )

    return {
        "text": text,
        "my_points": my_points,
        "opp_points": opp_points,
        "result_label": result_label,
        "my_result": my_result,
        "opp_result": opp_result,
    }
