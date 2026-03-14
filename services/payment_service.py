"""
services/payment_service.py — Payment creation and verification logic.

Currently supports manual verification (admin confirms payment and triggers
credit award). The architecture is designed to be extended with automated
crypto verification or payment gateway webhooks.
"""

from __future__ import annotations

import logging
from typing import Optional

from config import PLANS, Plan
from services.database import complete_payment, create_payment, get_payment

logger = logging.getLogger(__name__)


async def initiate_purchase(user_db_id: int, plan_key: str) -> dict:
    """Create a pending payment record for a purchase request.

    Args:
        user_db_id: Internal DB user ID.
        plan_key:   One of 'basic', 'pro', 'enterprise'.

    Returns:
        Dict with payment_id, plan details, and payment instructions.

    Raises:
        ValueError: If the plan key is invalid.
    """
    plan: Optional[Plan] = PLANS.get(plan_key)
    if plan is None:
        raise ValueError(f"Unknown plan: {plan_key}. Valid: {list(PLANS.keys())}")

    amount_cents = int(plan.price_usd * 100)
    payment = await create_payment(
        user_id=user_db_id,
        plan=plan_key,
        amount_usd_cents=amount_cents,
        credits=plan.credits,
    )

    logger.info(
        "Purchase initiated: payment_id=%d plan=%s user_id=%d",
        payment.id, plan_key, user_db_id,
    )

    return {
        "payment_id": payment.id,
        "plan_name": plan.name,
        "price_usd": plan.price_usd,
        "credits": plan.credits,
        "is_unlimited": plan.is_unlimited,
    }


async def admin_approve_payment(
    payment_id: int, admin_note: Optional[str] = None
) -> dict:
    """Approve a pending payment and credit the user.

    Args:
        payment_id: DB payment ID to approve.
        admin_note: Optional admin comment.

    Returns:
        Dict with payment details and new user credit balance.

    Raises:
        ValueError: If payment not found or already completed.
    """
    payment = await complete_payment(payment_id, admin_note=admin_note)
    logger.info("Payment approved: id=%d by admin", payment_id)
    return {
        "payment_id": payment.id,
        "user_id": payment.user_id,
        "plan": payment.plan,
        "credits_added": payment.credits,
        "status": payment.status,
    }


async def get_payment_status(payment_id: int) -> Optional[dict]:
    """Return the current status of a payment."""
    payment = await get_payment(payment_id)
    if payment is None:
        return None
    return {
        "payment_id": payment.id,
        "plan": payment.plan,
        "amount_usd": payment.amount_usd / 100,
        "credits": payment.credits,
        "status": payment.status,
        "created_at": payment.created_at.isoformat(),
    }
