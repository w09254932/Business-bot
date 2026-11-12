# -*- coding: utf-8 -*-
"""Authoritative TR Battle Calculator rules.

The tier tables and battle outcome formulas intentionally preserve the
original bot behavior: ties are losses, and home mode uses individual tiers.
"""
from __future__ import annotations

from typing import Any

TIERS_INDIVIDUAL = [
    (0, 2_000, 6),
    (2_001, 4_000, 10),
    (4_001, 8_000, 14),
    (8_001, 15_000, 16),
    (15_001, 50_000, 20),
    (50_001, 120_000, 24),
    (120_001, 260_000, 28),
    (260_001, 500_000, 32),
    (500_001, 900_000, 36),
    (900_001, 2_000_000, 40),
    (2_000_001, 8_000_000, 42),
    (8_000_001, float("inf"), 44),
]

TIERS_TEAM = [
    (0, 5_000, 6),
    (5_001, 12_000, 10),
    (12_001, 26_000, 14),
    (26_001, 48_000, 16),
    (48_001, 120_000, 20),
    (120_001, 200_000, 24),
    (200_001, 400_000, 28),
    (400_001, 560_000, 32),
    (560_001, 800_000, 34),
    (800_001, 1_600_000, 36),
    (1_600_001, 3_200_000, 38),
    (3_200_001, 6_000_000, 40),
    (6_000_001, float("inf"), 42),
]

MODE_LABELS = {"individual": "الفردية", "home": "المنزل", "team": "الفريق"}


def points_for(value: int, mode: str = "individual") -> int:
    """Return the tier points using the original individual/team tables."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("الشعبية يجب أن تكون عددًا صحيحًا غير سالب")
    if mode not in MODE_LABELS:
        raise ValueError("نوع المعركة غير معروف")
    tiers = TIERS_TEAM if mode == "team" else TIERS_INDIVIDUAL
    for low, high, points in tiers:
        if low <= value <= high:
            return points
    return 0


def fmt(value: float | int) -> str:
    number = float(value)
    return str(int(number)) if number.is_integer() else f"{number:.1f}"


def compute_battle(my_number: int, opp_number: int, mode: str) -> dict[str, Any]:
    """Calculate a battle without changing the original scoring behavior."""
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
    outcome = "win" if my_number > opp_number else "loss"  # التعادل = خسارة

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
        "outcome": outcome,
        "my_points": my_points,
        "opp_points": opp_points,
        "result_label": result_label,
        "my_result": my_result,
        "opp_result": opp_result,
    }
