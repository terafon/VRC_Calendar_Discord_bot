"""recurrence_calculator.py のユニットテスト"""
import unittest
from datetime import datetime
from recurrence_calculator import RecurrenceCalculator


class TestToRrule(unittest.TestCase):
    def test_weekly(self):
        result = RecurrenceCalculator.to_rrule("weekly", [], 2)
        self.assertEqual(result, "RRULE:FREQ=WEEKLY;BYDAY=WE")

    def test_biweekly(self):
        result = RecurrenceCalculator.to_rrule("biweekly", [], 5)
        self.assertEqual(result, "RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=SA")

    def test_nth_week(self):
        result = RecurrenceCalculator.to_rrule("nth_week", [2, 4], 3)
        self.assertEqual(result, "RRULE:FREQ=MONTHLY;BYDAY=2TH,4TH")

    def test_monthly_date(self):
        result = RecurrenceCalculator.to_rrule("monthly_date", [], 0, monthly_dates=[5, 20])
        self.assertEqual(result, "RRULE:FREQ=MONTHLY;BYMONTHDAY=5,20")

    def test_monthly_date_without_dates_raises(self):
        with self.assertRaises(ValueError):
            RecurrenceCalculator.to_rrule("monthly_date", [], 0, monthly_dates=None)

    def test_weekday_none_raises(self):
        """weekday=None で non-monthly_date の場合 ValueError"""
        with self.assertRaises(ValueError):
            RecurrenceCalculator.to_rrule("weekly", [], None)

    def test_unsupported_recurrence_raises(self):
        with self.assertRaises(ValueError):
            RecurrenceCalculator.to_rrule("daily", [], 0)


class TestCalculateDates(unittest.TestCase):
    def test_weekly_generates_dates(self):
        start = datetime(2026, 1, 1)  # 木曜日
        dates = RecurrenceCalculator.calculate_dates("weekly", [], 3, start, months_ahead=1)
        self.assertTrue(len(dates) >= 4)
        for d in dates:
            self.assertEqual(d.weekday(), 3)

    def test_biweekly_generates_dates(self):
        start = datetime(2026, 1, 1)
        dates = RecurrenceCalculator.calculate_dates("biweekly", [], 3, start, months_ahead=2)
        for i in range(1, len(dates)):
            delta = (dates[i] - dates[i - 1]).days
            self.assertEqual(delta, 14)

    def test_nth_week(self):
        start = datetime(2026, 1, 1)
        dates = RecurrenceCalculator.calculate_dates("nth_week", [2, 4], 2, start, months_ahead=2)
        self.assertTrue(len(dates) > 0)
        for d in dates:
            self.assertEqual(d.weekday(), 2)

    def test_monthly_date(self):
        start = datetime(2026, 1, 1)
        dates = RecurrenceCalculator.calculate_dates(
            "monthly_date", [], 0, start, months_ahead=3, monthly_dates=[15]
        )
        self.assertTrue(len(dates) >= 3)
        for d in dates:
            self.assertEqual(d.day, 15)

    def test_monthly_date_skips_invalid_day(self):
        """31日指定で2月はスキップされる"""
        start = datetime(2026, 1, 1)
        dates = RecurrenceCalculator.calculate_dates(
            "monthly_date", [], 0, start, months_ahead=3, monthly_dates=[31]
        )
        months = [d.month for d in dates]
        self.assertNotIn(2, months)  # 2月は31日がないのでスキップ

    def test_end_date_limit(self):
        start = datetime(2026, 1, 1)
        end = datetime(2026, 1, 15)
        dates = RecurrenceCalculator.calculate_dates(
            "weekly", [], 3, start, end_date_limit=end
        )
        for d in dates:
            self.assertLessEqual(d.date(), end.date())

    def test_months_ahead_accuracy(self):
        """months_ahead=3 で約3ヶ月先までカバーする"""
        start = datetime(2026, 1, 15)
        dates = RecurrenceCalculator.calculate_dates("weekly", [], 3, start, months_ahead=3)
        if dates:
            last = dates[-1]
            self.assertGreaterEqual(last.month, 4)


class TestGetNthWeekday(unittest.TestCase):
    def test_first_monday_january_2026(self):
        result = RecurrenceCalculator._get_nth_weekday(2026, 1, 1, 0)
        self.assertIsNotNone(result)
        self.assertEqual(result.day, 5)
        self.assertEqual(result.weekday(), 0)

    def test_fifth_week_returns_none_if_not_exists(self):
        """第5月曜日が存在しない月はNone"""
        result = RecurrenceCalculator._get_nth_weekday(2026, 2, 5, 0)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
