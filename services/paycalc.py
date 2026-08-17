# app/plugins/autogrid360/services/paycalc.py
"""Deterministic vehicle-loan payment calculations for buyer tools."""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, localcontext


CENT = Decimal("0.01")
PAYMENT_FREQUENCIES = {
    "monthly": ("Monthly", 12),
    "biweekly": ("Bi-weekly", 26),
    "weekly": ("Weekly", 52),
}


@dataclass(frozen=True)
class AmortizationRow:
    """One payment row in a generated amortization schedule."""

    number: int
    payment: Decimal
    principal: Decimal
    interest: Decimal
    balance: Decimal


@dataclass(frozen=True)
class PaymentResult:
    """Calculated buyer-facing loan summary."""

    amount: Decimal
    down_payment: Decimal
    principal: Decimal
    annual_interest_rate: Decimal
    loan_years: int
    frequency: str
    frequency_label: str
    payments_per_year: int
    payment_count: int
    payment: Decimal
    total_interest: Decimal
    total_paid: Decimal
    schedule: tuple[AmortizationRow, ...]


def _money(value: Decimal) -> Decimal:
    """Round one monetary value to cents using ordinary financial half-up rounding."""

    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def calculate_payment(
    *,
    amount: Decimal,
    down_payment: Decimal,
    annual_interest_rate: Decimal,
    loan_years: int,
    frequency: str,
    include_schedule: bool = False,
) -> PaymentResult:
    """Calculate periodic payment and optional amortization schedule.

    ``amount`` is the vehicle/purchase amount before the down payment. Interest is
    a nominal annual percentage rate divided by the selected payment frequency.
    """

    if frequency not in PAYMENT_FREQUENCIES:
        raise ValueError("Unsupported payment frequency.")
    if amount < 0 or down_payment < 0 or annual_interest_rate < 0:
        raise ValueError("Payment inputs cannot be negative.")
    if down_payment > amount:
        raise ValueError("Down payment cannot exceed the vehicle amount.")
    if not 1 <= int(loan_years) <= 10:
        raise ValueError("Loan term must be between 1 and 10 years.")

    frequency_label, payments_per_year = PAYMENT_FREQUENCIES[frequency]
    payment_count = int(loan_years) * payments_per_year
    principal = _money(amount - down_payment)

    if principal == 0:
        return PaymentResult(
            amount=_money(amount),
            down_payment=_money(down_payment),
            principal=principal,
            annual_interest_rate=annual_interest_rate,
            loan_years=int(loan_years),
            frequency=frequency,
            frequency_label=frequency_label,
            payments_per_year=payments_per_year,
            payment_count=payment_count,
            payment=Decimal("0.00"),
            total_interest=Decimal("0.00"),
            total_paid=Decimal("0.00"),
            schedule=(),
        )

    with localcontext() as context:
        context.prec = 36
        periodic_rate = (
            annual_interest_rate / Decimal("100") / Decimal(payments_per_year)
        )
        if periodic_rate == 0:
            exact_payment = principal / Decimal(payment_count)
        else:
            exact_payment = principal * periodic_rate / (
                Decimal("1")
                - (Decimal("1") + periodic_rate) ** Decimal(-payment_count)
            )

    regular_payment = _money(exact_payment)
    balance = principal
    rows: list[AmortizationRow] = []
    total_interest = Decimal("0.00")
    total_paid = Decimal("0.00")

    for number in range(1, payment_count + 1):
        interest = _money(balance * periodic_rate)
        principal_component = regular_payment - interest
        payment = regular_payment

        if principal_component >= balance or number == payment_count:
            principal_component = balance
            payment = _money(principal_component + interest)

        balance = _money(balance - principal_component)
        total_interest += interest
        total_paid += payment

        if include_schedule:
            rows.append(
                AmortizationRow(
                    number=number,
                    payment=payment,
                    principal=_money(principal_component),
                    interest=interest,
                    balance=balance,
                )
            )

        if balance == 0:
            break

    return PaymentResult(
        amount=_money(amount),
        down_payment=_money(down_payment),
        principal=principal,
        annual_interest_rate=annual_interest_rate,
        loan_years=int(loan_years),
        frequency=frequency,
        frequency_label=frequency_label,
        payments_per_year=payments_per_year,
        payment_count=payment_count,
        payment=regular_payment,
        total_interest=_money(total_interest),
        total_paid=_money(total_paid),
        schedule=tuple(rows),
    )
