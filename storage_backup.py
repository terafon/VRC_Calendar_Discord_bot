from google.cloud import storage
import os
import schedule
import time
import threading

class StorageBackup:
    def __init__(self, bucket_name: str, db_path: str):
        self.bucket_name = bucket_name
        self.db_path = db_path
        try:
            self.client = storage.Client()
        except Exception as e:
            print(f"Warning: Google Cloud Storage client could not be initialized: {e}")
            self.client = None
    
    def backup_to_cloud(self):
        """Cloud StorageにSQLiteファイルをバックアップ"""
        if not self.client or not self.bucket_name:
            print("Backup skipped: Cloud Storage client or bucket name not configured.")
            return

        try:
            bucket = self.client.bucket(self.bucket_name)
            blob = bucket.blob(os.path.basename(self.db_path))
            
            blob.upload_from_filename(self.db_path)
            print(f'Backed up {self.db_path} to gs://{self.bucket_name}/{os.path.basename(self.db_path)}')
        except Exception as e:
            print(f"Failed to backup to cloud: {e}")
    
    def restore_from_cloud(self):
        """Cloud StorageからSQLiteファイルを復元"""
        if not self.client or not self.bucket_name:
            print("Restore skipped: Cloud Storage client or bucket name not configured.")
            return

        try:
            bucket = self.client.bucket(self.bucket_name)
            blob = bucket.blob(os.path.basename(self.db_path))
            
            if blob.exists():
                blob.download_to_filename(self.db_path)
                print(f'Restored {self.db_path} from gs://{self.bucket_name}/{os.path.basename(self.db_path)}')
            else:
                print('No backup found in Cloud Storage')
        except Exception as e:
            print(f"Failed to restore from cloud: {e}")
    
    def auto_backup(self, interval_hours: int = 24):
        """定期的に自動バックアップ"""
        schedule.every(interval_hours).hours.do(self.backup_to_cloud)
        
        while True:
            schedule.run_pending()
            time.sleep(60) # 1分ごとにチェック
    
    def start_background_backup(self, interval_hours: int = 6):
        """バックグラウンドで定期バックアップを開始"""
        thread = threading.Thread(
            target=self.auto_backup,
            args=(interval_hours,),
            daemon=True
        )
        thread.start()
        return thread
