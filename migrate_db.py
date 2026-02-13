import firebase_admin
from firebase_admin import credentials, firestore
import gspread
import json
import os
import time
import sys
from datetime import datetime

# 強制立即輸出
sys.stdout.reconfigure(line_buffering=True)

# --- 初始化 Firebase ---
# 切換到專案目錄
os.chdir('/mnt/c/pyy/AC_Refrigerant')

if not firebase_admin._apps:
    cred = credentials.Certificate('firebase-adminsdk.json')
    firebase_admin.initialize_app(cred)

db = firestore.client()

# --- 初始化 Google Sheets ---
CREDENTIALS_FILE = 'credentials.json'
SHEET_NAME = 'AC_Refrigerant_DB'

def get_gspread_client():
    # 這裡直接讀取本地 credentials.json
    return gspread.service_account(filename=CREDENTIALS_FILE)

def migrate_users():
    print("🚀 開始遷移使用者資料...")
    max_retries = 3
    for attempt in range(max_retries):
        try:
            client = get_gspread_client()
            sheet = client.open(SHEET_NAME).worksheet('Users')
            records = sheet.get_all_records()
            break
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"⚠️ 讀取 Sheets 失敗 (嘗試 {attempt+1}/{max_retries}): {e}. 5秒後重試...")
                time.sleep(5)
            else:
                print(f"❌ 讀取 Sheets 徹底失敗: {e}")
                return

    try:
        batch = db.batch()
        count = 0
        
        for r in records:
            phone = str(r.get('phone', '')).strip().lstrip("'")
            if len(phone) == 9 and phone.isdigit(): phone = "0" + phone
            
            if not phone:
                continue
                
            doc_ref = db.collection('users').document(phone)
            user_data = {
                'phone': phone,
                'email': str(r.get('email', '')).strip(),
                'name': str(r.get('name', '')).strip(),
                'shop_name': str(r.get('shop_name', '')).strip(),
                'password_hash': str(r.get('password', '')).strip(),
                'reset_code': str(r.get('reset_code', '')).strip(),
                'created_at': firestore.SERVER_TIMESTAMP,
                'card_image_url': '' # 舊資料無圖片
            }
            batch.set(doc_ref, user_data)
            count += 1
            
            if count % 400 == 0:
                batch.commit()
                batch = db.batch()
                print(f"...已處理 {count} 筆")
                
        batch.commit()
        print(f"✅ 使用者遷移完成！共 {count} 筆。")
    except Exception as e:
        print(f"❌ 使用者遷移失敗: {e}")

def migrate_reports():
    print("🚀 開始遷移回報紀錄...")
    max_retries = 3
    records = []
    for attempt in range(max_retries):
        try:
            client = get_gspread_client()
            sheet = client.open(SHEET_NAME).worksheet('Reports')
            records = sheet.get_all_records()
            break
        except Exception as e:
            if "worksheet not found" in str(e).lower():
                print("⚠️ 找不到 Reports 工作表，跳過。")
                return
            if attempt < max_retries - 1:
                print(f"⚠️ 讀取 Reports 失敗 (嘗試 {attempt+1}/{max_retries}): {e}. 5秒後重試...")
                time.sleep(5)
            else:
                print(f"❌ 讀取 Reports 徹底失敗: {e}")
                return

    try:
        batch = db.batch()
        count = 0
        
        for r in records:
            doc_ref = db.collection('reports').document()
            
            report_data = {
                'timestamp': str(r.get('時間', datetime.now().isoformat())),
                'user_display': str(r.get('使用者', '')),
                'car_info': str(r.get('車型資訊', '')),
                'message': str(r.get('錯誤描述', '')),
                'car_id': str(r.get('Car ID', '')),
                'status': str(r.get('狀態', '待處理')),
                'migrated_at': firestore.SERVER_TIMESTAMP
            }
            batch.set(doc_ref, report_data)
            count += 1
            
            if count % 400 == 0:
                batch.commit()
                batch = db.batch()
                print(f"...已處理 {count} 筆")
                
        batch.commit()
        print(f"✅ 回報紀錄遷移完成！共 {count} 筆。")
    except Exception as e:
        print(f"❌ 回報遷移失敗: {e}")

if __name__ == '__main__':
    migrate_users()
    migrate_reports()
