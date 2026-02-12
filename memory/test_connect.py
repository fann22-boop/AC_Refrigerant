import gspread
import json

CREDENTIALS_FILE = 'credentials.json'
SHEET_NAME = 'AC_Refrigerant_DB'

print("--- 開始診斷 ---")

# 1. 檢查憑證檔案
try:
    with open(CREDENTIALS_FILE, 'r') as f:
        creds = json.load(f)
    print(f"✅ 成功讀取 {CREDENTIALS_FILE}")
    print(f"   - 機器人 Email: {creds.get('client_email')}")
    print(f"   - 專案 ID: {creds.get('project_id')}")
except Exception as e:
    print(f"❌ 讀取 credentials.json 失敗: {e}")
    exit()

# 2. 測試連線
try:
    print(f"\n正在嘗試連線 Google Sheets...")
    client = gspread.service_account(filename=CREDENTIALS_FILE)
    
    # 嘗試開啟試算表
    print(f"正在尋找試算表: {SHEET_NAME}")
    sheet = client.open(SHEET_NAME).sheet1
    
    # 嘗試讀取資料
    print("正在讀取資料...")
    data = sheet.get_all_records()
    
    print("\n🎉 連線成功！讀取到的第一筆資料：")
    print(data[0] if data else "資料庫是空的")
    
except Exception as e:
    print(f"\n❌ 連線失敗！詳細錯誤如下：")
    print(e)
    
    # 如果錯誤包含 response，嘗試印出內容
    if hasattr(e, 'response'):
        print("\n--- Google 回傳的錯誤內容 ---")
        try:
            print(e.response.text)
        except:
            print("無法讀取回應內容")
