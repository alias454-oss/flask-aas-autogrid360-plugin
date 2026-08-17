# app/plugins/autogrid360/tests/test_paycalc.py
from decimal import Decimal
import unittest

from app.plugins.autogrid360.services.paycalc import calculate_payment


class AutoGrid360PaymentCalculatorTests(unittest.TestCase):
    def test_zero_interest_monthly_payment_is_principal_divided_by_term(self):
        result = calculate_payment(
            amount=Decimal("12000.00"),
            down_payment=Decimal("0.00"),
            annual_interest_rate=Decimal("0"),
            loan_years=1,
            frequency="monthly",
        )

        self.assertEqual(result.principal, Decimal("12000.00"))
        self.assertEqual(result.payment_count, 12)
        self.assertEqual(result.payment, Decimal("1000.00"))
        self.assertEqual(result.total_interest, Decimal("0.00"))
        self.assertEqual(result.total_paid, Decimal("12000.00"))

    def test_monthly_payment_matches_expected_amortized_result(self):
        result = calculate_payment(
            amount=Decimal("20000.00"),
            down_payment=Decimal("2000.00"),
            annual_interest_rate=Decimal("6"),
            loan_years=5,
            frequency="monthly",
        )

        self.assertEqual(result.principal, Decimal("18000.00"))
        self.assertEqual(result.payment_count, 60)
        self.assertEqual(result.payment, Decimal("347.99"))
        self.assertEqual(result.total_interest, Decimal("2879.41"))
        self.assertEqual(result.total_paid, Decimal("20879.41"))

    def test_weekly_and_biweekly_terms_use_expected_payment_counts(self):
        weekly = calculate_payment(
            amount=Decimal("10000.00"),
            down_payment=Decimal("0.00"),
            annual_interest_rate=Decimal("5"),
            loan_years=2,
            frequency="weekly",
        )
        biweekly = calculate_payment(
            amount=Decimal("10000.00"),
            down_payment=Decimal("0.00"),
            annual_interest_rate=Decimal("5"),
            loan_years=2,
            frequency="biweekly",
        )

        self.assertEqual(weekly.payment_count, 104)
        self.assertEqual(biweekly.payment_count, 52)
        self.assertEqual(weekly.frequency_label, "Weekly")
        self.assertEqual(biweekly.frequency_label, "Bi-weekly")

    def test_optional_amortization_schedule_ends_at_zero_balance(self):
        result = calculate_payment(
            amount=Decimal("12000.00"),
            down_payment=Decimal("0.00"),
            annual_interest_rate=Decimal("0"),
            loan_years=1,
            frequency="monthly",
            include_schedule=True,
        )

        self.assertEqual(len(result.schedule), 12)
        self.assertEqual(result.schedule[0].principal, Decimal("1000.00"))
        self.assertEqual(result.schedule[-1].balance, Decimal("0.00"))


if __name__ == "__main__":
    unittest.main()
