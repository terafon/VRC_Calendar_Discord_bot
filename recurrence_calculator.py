from datetime import datetime, timedelta
from typing import List, Optional
import calendar

class RecurrenceCalculator:
    @staticmethod
    def calculate_dates(
        recurrence: str,
        nth_weeks: List[int],
        weekday: int,
        start_date: datetime,
        months_ahead: int = 3,
        end_date_limit: Optional[datetime] = None
    ) -> List[datetime]:
        """
        繰り返しパターンから日付リストを生成
        
        Args:
            recurrence: 繰り返しタイプ (weekly, biweekly, nth_week)
            nth_weeks: 第n週のリスト（nth_weekの場合）
            weekday: 曜日（0=月, 6=日）
            start_date: 開始日
            months_ahead: 何ヶ月先まで生成するか
            end_date_limit: 生成の最終期限（months_aheadより優先される）
        
        Returns:
            日付のリスト
        """
        dates = []
        current_date = start_date
        
        if end_date_limit:
            end_date = end_date_limit
        else:
            end_date = start_date + timedelta(days=30 * months_ahead)
        
        if recurrence == "weekly":
            # 毎週
            # 開始日以降の最初の該当曜日を探す
            while current_date.weekday() != weekday:
                current_date += timedelta(days=1)
            
            while current_date <= end_date:
                dates.append(current_date)
                current_date += timedelta(weeks=1)
        
        elif recurrence == "biweekly":
            # 隔週
            # 開始日以降の最初の該当曜日を探す
            while current_date.weekday() != weekday:
                current_date += timedelta(days=1)
            
            while current_date <= end_date:
                dates.append(current_date)
                current_date += timedelta(weeks=2)
        
        elif recurrence == "nth_week":
            # 第n週
            # 当月の1日から開始して月ごとにループ
            temp_date = current_date.replace(day=1)
            
            while temp_date <= end_date:
                year = temp_date.year
                month = temp_date.month
                
                for week_num in nth_weeks:
                    date = RecurrenceCalculator._get_nth_weekday(
                        year, month, week_num, weekday
                    )
                    # 開始日以降かつ終了日以前の場合のみ追加
                    if date and start_date.date() <= date.date() <= end_date.date():
                        dates.append(date)
                
                # 次の月へ
                if month == 12:
                    temp_date = temp_date.replace(year=year+1, month=1)
                else:
                    temp_date = temp_date.replace(month=month+1)
        
        return sorted(list(set(dates)))
    
    @staticmethod
    def _get_nth_weekday(
        year: int,
        month: int,
        nth: int,
        weekday: int
    ) -> Optional[datetime]:
        """
        指定月の第n週の特定曜日を取得
        
        Args:
            year: 年
            month: 月
            nth: 第n週（1-5）
            weekday: 曜日（0=月, 6=日）
        
        Returns:
            該当日（存在しない場合None）
        """
        # 月の1日
        first_day = datetime(year, month, 1)
        
        # 月の1日の曜日
        first_weekday = first_day.weekday()
        
        # 最初の該当曜日までの日数
        days_until_weekday = (weekday - first_weekday) % 7
        
        # 第n週の該当曜日
        target_date = first_day + timedelta(days=days_until_weekday + (nth - 1) * 7)
        
        # 月をまたいでいないか確認
        if target_date.month != month:
            return None
        
        return target_date
