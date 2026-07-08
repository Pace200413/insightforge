"""E-commerce business schema.

Design notes:
- `orders` has NO region column: region lives on `customers`. This is
  deliberate -- regional analysis requires a join, which forces the AI to
  discover relationships instead of reading everything off one table.
- `order_items.unit_price` is the price AT PURCHASE TIME. `products.price`
  is the current price. A mid-2026 price change (see anomalies.yaml) makes
  these diverge -- naive queries using products.price get wrong revenue.
- `payments` intentionally contains duplicate rows for some orders (a
  data-quality anomaly). SUM(payments.amount) != revenue. The correct
  revenue source is order_items.
"""

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Region(Base):
    __tablename__ = "regions"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(8), unique=True)  # NA, EMEA, APAC, LATAM
    name: Mapped[str] = mapped_column(String(64))

    customers: Mapped[list["Customer"]] = relationship(back_populates="region")


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    email: Mapped[str] = mapped_column(String(256), unique=True)
    segment: Mapped[str] = mapped_column(String(16), index=True)  # consumer | smb | enterprise
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)

    region: Mapped[Region] = relationship(back_populates="customers")
    orders: Mapped[list["Order"]] = relationship(back_populates="customer")


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)

    products: Mapped[list["Product"]] = relationship(back_populates="category")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), index=True)
    price: Mapped[float] = mapped_column(Numeric(10, 2))  # CURRENT price (see module docstring)
    created_at: Mapped[datetime] = mapped_column(DateTime)

    category: Mapped[Category] = relationship(back_populates="products")


class MarketingCampaign(Base):
    __tablename__ = "marketing_campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    channel: Mapped[str] = mapped_column(String(32))  # email | social | search | display
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    spend: Mapped[float] = mapped_column(Numeric(12, 2))


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    campaign_id: Mapped[int | None] = mapped_column(
        ForeignKey("marketing_campaigns.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(16), index=True)  # completed | cancelled | pending
    order_date: Mapped[datetime] = mapped_column(DateTime, index=True)
    discount_amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0)

    customer: Mapped[Customer] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order")
    payments: Mapped[list["Payment"]] = relationship(back_populates="order")
    refunds: Mapped[list["Refund"]] = relationship(back_populates="order")


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    quantity: Mapped[int] = mapped_column()
    unit_price: Mapped[float] = mapped_column(Numeric(10, 2))  # price at purchase time

    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    amount: Mapped[float] = mapped_column(Numeric(10, 2))
    method: Mapped[str] = mapped_column(String(24))  # card | paypal | bank_transfer
    status: Mapped[str] = mapped_column(String(16))  # succeeded | failed
    paid_at: Mapped[datetime] = mapped_column(DateTime)

    order: Mapped[Order] = relationship(back_populates="payments")


class Refund(Base):
    __tablename__ = "refunds"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    amount: Mapped[float] = mapped_column(Numeric(10, 2))
    reason: Mapped[str] = mapped_column(String(64))
    refunded_at: Mapped[datetime] = mapped_column(DateTime, index=True)

    order: Mapped[Order] = relationship(back_populates="refunds")