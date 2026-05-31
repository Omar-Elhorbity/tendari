"""Seed demo data: one workspace (API-keyed), customers, and ~5 orders.

Idempotent — safe to run repeatedly (e.g. on every container start). Help-doc
ingestion is added in M1; this module focuses on the tenant + e-commerce data
needed for the demo script. Run with: ``python -m app.seed``.
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, settings
from app.db import SessionLocal
from app.models import Customer, Order, Workspace
from app.security import hash_api_key

logger = logging.getLogger("tendari.seed")

# --- demo customers ---
_CUSTOMERS = [
    {"email": "dana@example.com", "name": "Dana Lee"},
    {"email": "sam@example.com", "name": "Sam Rivera"},
]

# --- demo orders (order_number 1002 is the one used in the demo refund flow) ---
_ORDERS = [
    {
        "order_number": "1001",
        "customer_email": "dana@example.com",
        "status": "delivered",
        "shipping_status": "delivered",
        "tracking_number": "TRK1001",
        "total_amount": Decimal("120.00"),
        "items": [{"sku": "TENT-2P", "name": "2-Person Tent", "qty": 1, "unit_price": 120.00}],
        "stripe_payment_intent_id": None,
    },
    {
        "order_number": "1002",
        "customer_email": "dana@example.com",
        "status": "delivered",
        "shipping_status": "delivered",
        "tracking_number": "TRK1002",
        "total_amount": Decimal("89.50"),
        "items": [{"sku": "SLEEP-BAG", "name": "Down Sleeping Bag", "qty": 1, "unit_price": 89.50}],
        # Ties to a Stripe test-mode payment so the refund demo can run.
        "stripe_payment_intent_id": "pi_test_seeded_1002",
    },
    {
        "order_number": "1003",
        "customer_email": "sam@example.com",
        "status": "shipped",
        "shipping_status": "in_transit",
        "tracking_number": "TRK1003",
        "total_amount": Decimal("45.00"),
        "items": [{"sku": "HEADLAMP", "name": "LED Headlamp", "qty": 1, "unit_price": 45.00}],
        "stripe_payment_intent_id": "pi_test_seeded_1003",
    },
    {
        "order_number": "1004",
        "customer_email": "sam@example.com",
        "status": "paid",
        "shipping_status": None,
        "tracking_number": None,
        "total_amount": Decimal("210.00"),
        "items": [{"sku": "BACKPACK-65", "name": "65L Backpack", "qty": 1, "unit_price": 210.00}],
        "stripe_payment_intent_id": "pi_test_seeded_1004",
    },
    {
        "order_number": "1005",
        "customer_email": "dana@example.com",
        "status": "placed",
        "shipping_status": None,
        "tracking_number": None,
        "total_amount": Decimal("15.99"),
        "items": [{"sku": "WATER-TAB", "name": "Water Purification Tablets", "qty": 1, "unit_price": 15.99}],
        "stripe_payment_intent_id": None,
    },
]


async def _get_or_create_workspace(session: AsyncSession) -> Workspace:
    key_hash = hash_api_key(settings.seed_api_key)
    workspace = await session.scalar(
        select(Workspace).where(Workspace.api_key_hash == key_hash)
    )
    if workspace is None:
        workspace = Workspace(name=settings.seed_workspace_name, api_key_hash=key_hash)
        session.add(workspace)
        await session.flush()
        logger.info("Created workspace %s (%s)", workspace.id, workspace.name)
    return workspace


async def _seed_customers(session: AsyncSession, workspace: Workspace) -> dict[str, Customer]:
    by_email: dict[str, Customer] = {}
    for spec in _CUSTOMERS:
        customer = await session.scalar(
            select(Customer).where(
                Customer.workspace_id == workspace.id,
                Customer.email == spec["email"],
            )
        )
        if customer is None:
            customer = Customer(workspace_id=workspace.id, **spec)
            session.add(customer)
            await session.flush()
        by_email[spec["email"]] = customer
    return by_email


async def _seed_orders(
    session: AsyncSession, workspace: Workspace, customers: dict[str, Customer]
) -> None:
    for spec in _ORDERS:
        existing = await session.scalar(
            select(Order).where(
                Order.workspace_id == workspace.id,
                Order.order_number == spec["order_number"],
            )
        )
        if existing is not None:
            continue
        spec = dict(spec)
        customer = customers.get(spec.pop("customer_email"))
        session.add(
            Order(
                workspace_id=workspace.id,
                customer_id=customer.id if customer else None,
                **spec,
            )
        )


async def seed() -> None:
    async with SessionLocal() as session:
        workspace = await _get_or_create_workspace(session)
        customers = await _seed_customers(session, workspace)
        await _seed_orders(session, workspace, customers)
        await session.commit()

        logger.info("Seed complete.")
        # Only echo the raw key when it's the throwaway built-in demo default.
        # If an operator overrode SEED_API_KEY (i.e. a real deploy), never log it.
        default_key = Settings.model_fields["seed_api_key"].default
        is_demo_default = settings.seed_api_key == default_key

        print("=" * 60)
        print(f"Seeded workspace : {settings.seed_workspace_name}")
        print(f"Workspace id     : {workspace.id}")
        print(f"Customers        : {len(_CUSTOMERS)}   Orders: {len(_ORDERS)}")
        if is_demo_default:
            print(f"Demo API key     : {settings.seed_api_key}")
            print("Use:  Authorization: Bearer <Demo API key>")
        else:
            print("API key          : (custom SEED_API_KEY — not printed; only the hash is stored)")
        print("=" * 60)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    asyncio.run(seed())


if __name__ == "__main__":
    main()
