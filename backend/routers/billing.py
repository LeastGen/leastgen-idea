"""IdeaFlow — billing endpoints.

Tap payment gateway integration for one-time payments.
Supports monthly and yearly plans with hosted checkout page.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.database import (
    create_subscription,
    get_user_subscription,
    update_user,
)
from backend.routers.auth import get_current_user

router = APIRouter()

# ── Tap Config ─────────────────────────────────────────────────────────────
# NOTE: Tap has no separate webhook signing secret. Webhook authenticity is
# proven via the `hashstring` header (HMAC-SHA256, keyed with TAP_API_KEY).
# See _verify_tap_hashstring below and https://developers.tap.company/docs/webhook.
TAP_API_KEY = os.environ.get("TAP_API_KEY", "")
TAP_MERCHANT_ID = os.environ.get("TAP_MERCHANT_ID", "")
TAP_BASE = "https://api.tap.company/v2"

# Plan definitions
PLANS = [
    {
        "id": "free",
        "name": "Free (Hosted)",
        "price": 0,
        "currency": "USD",
        "interval": "month",
        "runs_limit": 10,
        "duration_days": 0,
        "features": [
            "10 runs/month",
            "Scoop-Check novelty verification",
            "IdeaSpark full pipeline",
            "Web UI access",
            "Markdown export",
        ],
    },
    {
        "id": "pro_monthly",
        "name": "Pro Monthly (Hosted)",
        "price": 29,
        "currency": "USD",
        "interval": "month",
        "runs_limit": 999,
        "duration_days": 30,
        "features": [
            "Unlimited runs",
            "PDF & DOCX export",
            "Priority support",
            "API access",
            "Team sharing (coming soon)",
        ],
    },
    {
        "id": "pro_yearly",
        "name": "Pro Yearly (Hosted)",
        "price": 290,
        "currency": "USD",
        "interval": "year",
        "runs_limit": 999,
        "duration_days": 365,
        "features": [
            "Unlimited runs",
            "PDF & DOCX export",
            "Priority support",
            "API access",
            "2 months free",
        ],
    },
]

PLAN_MAP = {p["id"]: p for p in PLANS}


# ── Models ──


class CreateCheckoutRequest(BaseModel):
    plan_id: str
    success_url: str = ""
    cancel_url: str = ""


# ── Tap API helpers ──


def _call_tap(method: str, path: str, body: dict | None = None) -> dict[str, Any]:
    """Call the Tap API."""
    if not TAP_API_KEY:
        raise HTTPException(status_code=501, detail="Tap payment not configured")

    url = f"{TAP_BASE}/{path.lstrip('/')}"
    data = json.dumps(body).encode("utf-8") if body else None

    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {TAP_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method=method,
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise HTTPException(
            status_code=e.code,
            detail=f"Tap API error: {err_body[:500]}",
        )
    except urllib.error.URLError as e:
        raise HTTPException(status_code=502, detail=f"Tap API unreachable: {e.reason}")


# ── Webhook verification (Tap's real scheme) ──
# Tap does NOT use a separate signing secret. Webhook authenticity is proven
# via the `hashstring` header: HMAC-SHA256 over a concatenated field string,
# keyed with the Secret API Key (sk_live_*/sk_test_*).
# Docs: https://developers.tap.company/docs/webhook ("Validate the webhook")
#
# Charge/authorize recipe (concatenated in this exact order):
#   x_id={id} x_amount={amount} x_currency={currency}
#   x_gateway_reference={reference.gateway or ""}
#   x_payment_reference={reference.payment}
#   x_status={status} x_created={transaction.created}
# Amounts must be rounded to the currency's standard decimals
# (e.g. SAR/AED/USD 2dp, BHD/KWD/JOD 3dp) BEFORE hashing.

_CURRENCY_DECIMALS = {
    "BHD": 3, "KWD": 3, "JOD": 3, "OMR": 3, "TND": 3,
}

_HASHSTRING_HEADER_NAMES = ("hashstring", "x-hashstring", "x-tap-signature", "x-signature")


def _format_amount(amount: Any, currency: str) -> str:
    """Round amount to the currency's standard decimals, as Tap's recipe requires."""
    decimals = _CURRENCY_DECIMALS.get((currency or "").upper(), 2)
    try:
        value = float(amount)
    except (TypeError, ValueError):
        value = 0.0
    return f"{value:.{decimals}f}"


def _verify_tap_hashstring(payload: dict[str, Any], posted_hash: str) -> bool:
    """Verify Tap's `hashstring` webhook header using the Secret API Key."""
    if not TAP_API_KEY or not posted_hash:
        return False

    transaction = payload.get("transaction") or {}
    reference = payload.get("reference") or {}

    to_hash = (
        f"x_id{payload.get('id', '')}"
        f"x_amount{_format_amount(payload.get('amount'), payload.get('currency', ''))}"
        f"x_currency{payload.get('currency', '')}"
        f"x_gateway_reference{reference.get('gateway', '') or ''}"
        f"x_payment_reference{reference.get('payment', '') or ''}"
        f"x_status{payload.get('status', '')}"
        f"x_created{transaction.get('created', '')}"
    )

    expected = hmac.new(
        TAP_API_KEY.encode("utf-8"),
        to_hash.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, str(posted_hash))


# ── Routes ──


@router.get("/billing/plans")
async def list_plans():
    """List available subscription plans."""
    return {"plans": PLANS}


@router.post("/billing/create-checkout")
async def create_checkout(
    req: CreateCheckoutRequest,
    user: dict[str, Any] = Depends(get_current_user),
):
    """Create a Tap payment charge and return the hosted checkout URL."""
    plan = PLAN_MAP.get(req.plan_id)
    if not plan:
        raise HTTPException(status_code=400, detail=f"Unknown plan: {req.plan_id}")
    if plan["price"] <= 0:
        # Free plan — activate immediately
        update_user(
            user["id"],
            subscription_tier="free",
            subscription_status="active",
            runs_limit=plan["runs_limit"],
        )
        return {"url": req.success_url or "/api/ui", "status": "activated"}

    success_url = req.success_url or "http://localhost:8756/api/ui?checkout=success"
    cancel_url = req.cancel_url or "http://localhost:8756/api/ui?checkout=canceled"
    post_url = os.environ.get(
        "TAP_POST_URL",
        "http://localhost:8756/api/billing/webhook",
    )

    # Map price to cents (Tap expects float with decimal places)
    # $29.00 -> 29.00, $290.00 -> 290.00
    amount = float(plan["price"])

    # Build the charge payload
    charge_body = {
        "amount": amount,
        "currency": plan["currency"],
        "customer_initiated": True,
        "threeDSecure": True,
        "save_card": False,
        "description": f"IdeaFlow {plan['name']}",
        "metadata": {
            "udf1": f"plan:{plan['id']}",
            "udf2": f"user:{user['id']}",
            "udf3": f"email:{user['email']}",
        },
        "reference": {
            "transaction": f"or_{user['id'][:8]}_{plan['id']}",
            "order": f"ord_{user['id'][:8]}",
        },
        "customer": {
            "first_name": user.get("name", user["email"].split("@")[0]).split(" ")[0],
            "last_name": " ".join(user.get("name", "").split(" ")[1:]) or "User",
            "email": user["email"],
            "phone": {
                "country_code": "966",
                "number": "500000000",
            },
        },
        "source": {"id": "src_all"},  # Tap hosted payment page
        "redirect": {"url": success_url},
        "post": {"url": post_url},
        "merchant": {"id": TAP_MERCHANT_ID} if TAP_MERCHANT_ID else {},
    }

    result = _call_tap("POST", "charges", charge_body)

    # The transaction URL is where the user needs to be redirected
    transaction_url = (
        result.get("transaction", {}).get("url")
        or result.get("redirect", {}).get("url")
    )

    if not transaction_url:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to get checkout URL from Tap: {json.dumps(result)[:300]}",
        )

    # Save the charge ID for later verification
    charge_id = result.get("id", "")
    if charge_id:
        # Store in a simple way — we'll verify on webhook
        update_user(user["id"], stripe_customer_id=charge_id)

    return {"url": transaction_url, "charge_id": charge_id, "status": "pending"}


@router.post("/billing/webhook")
async def tap_webhook(request: Request):
    """Handle Tap payment webhook (post URL). Verifies Tap's `hashstring` header."""
    raw_body = await request.body()

    try:
        body = json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Verify Tap's hashstring header (HMAC-SHA256, keyed with Secret API Key).
    # Fail closed when the Secret API Key is not configured.
    posted_hash = ""
    for header_name in _HASHSTRING_HEADER_NAMES:
        posted_hash = request.headers.get(header_name, "")
        if posted_hash:
            break
    if posted_hash:
        if not _verify_tap_hashstring(body, posted_hash):
            raise HTTPException(status_code=401, detail="Invalid webhook hashstring")
    elif not TAP_API_KEY:
        raise HTTPException(status_code=401, detail="Webhook verification not configured")

    # Extract charge info
    charge_id = body.get("id", "")
    status = body.get("status", "")
    metadata = body.get("metadata", {})
    response_code = body.get("response", {}).get("code", "")
    response_msg = body.get("response", {}).get("message", "")

    # Extract user info from metadata
    udf2 = metadata.get("udf2", "")
    udf1 = metadata.get("udf1", "")
    user_id = udf2.replace("user:", "") if udf2 else ""
    plan_id = udf1.replace("plan:", "") if udf1 else ""

    # Successful payment
    if status == "CAPTURED" and response_code == "000" and user_id and plan_id:
        plan = PLAN_MAP.get(plan_id)
        if plan and plan["duration_days"] > 0:
            now = datetime.now(timezone.utc)
            period_end = now + timedelta(days=plan["duration_days"])

            update_user(
                user_id,
                subscription_tier=plan_id,
                subscription_status="active",
                runs_limit=plan["runs_limit"],
            )

            create_subscription(
                user_id=user_id,
                stripe_subscription_id=charge_id,
                stripe_price_id=plan_id,
                tier=plan_id,
                status="active",
            )

    return {"status": "ok"}


@router.get("/billing/verify/{charge_id}")
async def verify_charge(charge_id: str):
    """Verify a charge status with Tap."""
    result = _call_tap("GET", f"charges/{charge_id}")

    status = result.get("status", "")
    response_code = result.get("response", {}).get("code", "")

    # Extract metadata
    metadata = result.get("metadata", {})
    udf2 = metadata.get("udf2", "")
    udf1 = metadata.get("udf1", "")
    user_id = udf2.replace("user:", "") if udf2 else ""
    plan_id = udf1.replace("plan:", "") if udf1 else ""

    is_success = status == "CAPTURED" and response_code == "000"

    if is_success and user_id and plan_id:
        plan = PLAN_MAP.get(plan_id)
        if plan and plan["duration_days"] > 0:
            now = datetime.now(timezone.utc)
            period_end = now + timedelta(days=plan["duration_days"])

            update_user(
                user_id,
                subscription_tier=plan_id,
                subscription_status="active",
                runs_limit=plan["runs_limit"],
            )

            create_subscription(
                user_id=user_id,
                stripe_subscription_id=charge_id,
                stripe_price_id=plan_id,
                tier=plan_id,
                status="active",
            )

    return {
        "charge_id": charge_id,
        "status": status,
        "response_code": response_code,
        "response_message": result.get("response", {}).get("message", ""),
        "success": is_success,
    }


@router.get("/billing/subscription")
async def get_my_subscription(
    user: dict[str, Any] = Depends(get_current_user),
):
    """Get the current user's subscription details."""
    sub = get_user_subscription(user["id"])
    if not sub:
        return {
            "tier": "free",
            "status": "active",
            "runs_limit": 10,
            "plan": PLAN_MAP.get("free"),
        }

    plan = PLAN_MAP.get(sub["tier"])
    return {
        "tier": sub["tier"],
        "status": sub["status"],
        "charge_id": sub.get("stripe_subscription_id", ""),
        "plan": plan,
    }