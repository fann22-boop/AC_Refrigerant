import pandas as pd
import gspread
import sqlite3
import secrets
import smtplib
from email.mime.text import MIMEText
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import time
import requests
import base64
import os
from functools import wraps

from flask_compress import Compress
import json

app = Flask(__name__)
app.secret_key = 'super_secret_key_fuyi_ac' 
Compress(app)

# --- 設定區 ---
SHEET_NAME = 'AC_Refrigerant_DB'
CREDENTIALS_FILE = 'credentials.json'
ADMIN_PHONES = ['0937966850'] 
DB_PATH = 'data_cache.db'

# 支援雲端環境變數
GOOGLE_CREDENTIALS = os.environ.get('GOOGLE_CREDENTIALS')

def get_gspread_client():
    if GOOGLE_CREDENTIALS:
        creds_dict = json.loads(GOOGLE_CREDENTIALS)
        return gspread.service_account_from_dict(creds_dict)
    return gspread.service_account(filename=CREDENTIALS_FILE)

# --- 郵件設定 (Gmail) ---
# 注意：你需要到 Gmail 設定「應用程式密碼」才能發信
MAIL_SERVER = 'smtp.gmail.com'
MAIL_PORT = 587
MAIL_USERNAME = 'fuyi9188@gmail.com'
MAIL_PASSWORD = 'nkeasjhllsdzmopm' # 請在此處填入 16 位應用程式密碼

# --- 裝飾器 ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.phone not in ADMIN_PHONES:
            flash('⚠️ 探索受限：這區域僅供系統管理員訪問。', 'error')
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function

# --- SQLite 快取管理 ---
def init_local_db():
    try:
        if not os.path.exists(DB_PATH):
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)''')
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"⚠️ SQLite 初始化失敗 (可能是唯讀環境): {e}")

def get_cached_data():
    try:
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql_query("SELECT * FROM cars", conn)
        conn.close()
        return df
    except Exception as e:
        print(f"⚠️ 無法讀取本地快取: {e}")
        return None

def save_to_cache(df, version):
    try:
        conn = sqlite3.connect(DB_PATH)
        df.to_sql('cars', conn, if_exists='replace', index=False)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('version', ?)", (version,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ 快取儲存失敗: {e}")

# --- 核心資料讀取 (智慧同步版) ---
_data_cache = None
_last_update = 0
_db_version_cache = None

def get_db_metadata():
    try:
        client = get_gspread_client()
        spreadsheet = client.open(SHEET_NAME)
        sheet = spreadsheet.sheet1
        # 使用簡單特徵值組合當作版本號
        return f"{sheet.row_count}_{sheet.cell(1,1).value}"
    except:
        return str(time.time())

def get_data():
    global _data_cache, _last_update, _db_version_cache
    current_time = time.time()
    
    if _data_cache is not None and (current_time - _last_update) < 300:
        return _data_cache
        
    init_local_db()
    
    try:
        current_version = get_db_metadata()
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT value FROM config WHERE key='version'")
        row = c.fetchone()
        local_version = row[0] if row else None
        conn.close()

        cached_df = get_cached_data()
        if cached_df is not None and local_version == current_version:
            _data_cache = cached_df
            _db_version_cache = local_version
            _last_update = current_time
            return _data_cache

        print(f"🔄 偵測到雲端變動，正在優化本地快取...")
        client = get_gspread_client()
        sheet = client.open(SHEET_NAME).sheet1
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
        
        if 'id' in df.columns: df['id'] = df['id'].astype(str)
            
        save_to_cache(df, current_version)
        _data_cache = df
        _db_version_cache = current_version
        _last_update = current_time
        return df
    except Exception as e:
        print(f"❌ 快取同步失敗: {e}")
        return get_cached_data() if get_cached_data() is not None else pd.DataFrame()

# --- 會員系統 ---
class User(UserMixin):
    def __init__(self, phone, email, name, shop_name, password_hash, reset_code=None):
        self.id = str(phone).strip() 
        self.phone = str(phone).strip()
        self.email = email
        self.name = name
        self.shop_name = shop_name
        self.password_hash = password_hash
        self.reset_code = reset_code

_users_cache = {}
_users_last_update = 0

def get_all_users(force_refresh=False):
    global _users_cache, _users_last_update
    current_time = time.time()
    if not force_refresh and _users_cache and (current_time - _users_last_update) < 600:
        return _users_cache
        
    try:
        client = get_gspread_client()
        sheet = client.open(SHEET_NAME).worksheet('Users')
        records = sheet.get_all_records()
        new_cache = {}
        for r in records:
            phone = str(r.get('phone', '')).strip().lstrip("'")
            if len(phone) == 9 and phone.isdigit(): phone = "0" + phone
            if phone:
                new_cache[phone] = User(
                    phone, 
                    r.get('email'), 
                    r.get('name'), 
                    r.get('shop_name'), 
                    str(r.get('password', '')).strip(),
                    str(r.get('reset_code', '')).strip()
                )
        _users_cache = new_cache
        _users_last_update = current_time
        return _users_cache
    except:
        return _users_cache

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return get_all_users().get(user_id)

def send_mail(to_email, subject, body):
    try:
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = MAIL_USERNAME
        msg['To'] = to_email
        
        with smtplib.SMTP(MAIL_SERVER, MAIL_PORT) as server:
            server.starttls()
            server.login(MAIL_USERNAME, MAIL_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"❌ 郵件發送失敗: {e}")
        return False

# --- 路由邏輯 ---
@app.route('/')
def welcome():
    if current_user.is_authenticated: return redirect(url_for('ad_page'))
    return render_template('welcome.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        phone = request.form.get('phone', '').strip()
        password = request.form.get('password', '').strip()
        if len(phone) == 9 and phone.isdigit(): phone = "0" + phone
        
        user = get_all_users().get(phone)
        if not user: user = get_all_users(True).get(phone)
        
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            flash(f'☕ 歡迎回來，{user.name}。讓我們開始今天的工作。', 'success')
            return redirect(url_for('ad_page'))
        flash('🚫 認證失敗：請檢查號碼或密碼。', 'error')
    return render_template('login.html')

@app.route('/home')
@login_required
def home():
    df = get_data()
    if df.empty: return "系統正在初始化，請稍後刷新。"
    brands = sorted(df['brand'].unique().tolist())
    return render_template('index.html', brands=[{'brand': b} for b in brands])

@app.route('/detail/<car_id>')
@login_required
def show_detail(car_id):
    df = get_data()
    car = df[df['id'].astype(str) == str(car_id)].to_dict('records')
    return render_template('detail.html', car=car[0]) if car else "找不到該車型資料。"

@app.route('/report', methods=['POST'])
@login_required
def report_error():
    car_id = request.form.get('car_id')
    car_info = request.form.get('car_info')
    message = request.form.get('message', '').strip()
    
    try:
        client = get_gspread_client()
        spreadsheet = client.open(SHEET_NAME)
        try:
            report_sheet = spreadsheet.worksheet('Reports')
        except:
            report_sheet = spreadsheet.add_worksheet(title='Reports', rows=1000, cols=6)
            report_sheet.append_row(['時間', '使用者', '車型資訊', '錯誤描述', 'Car ID', '狀態'])
            
        report_sheet.append_row([time.strftime('%Y-%m-%d %H:%M:%S'), f"{current_user.name} ({current_user.phone})", car_info, message, car_id, '待處理'])
        flash('✨ 感謝回報！這份貢獻讓我們的資料庫變得更加卓越。', 'success')
    except:
        flash('🔧 暫時無法處理回報，請稍後再試。', 'error')
    return redirect(url_for('show_detail', car_id=car_id))

# --- 管理功能 ---
@app.route('/admin')
@login_required
@admin_required
def admin_dashboard():
    users = get_all_users(True)
    report_count = 0
    try:
        sheet = get_gspread_client().open(SHEET_NAME).worksheet('Reports')
        report_count = len([r for r in sheet.get_all_records() if r.get('狀態') == '待處理'])
    except: pass
    return render_template('admin/dashboard.html', user_count=len(users), report_count=report_count)

@app.route('/admin/reports')
@login_required
@admin_required
def admin_reports():
    try:
        records = get_gspread_client().open(SHEET_NAME).worksheet('Reports').get_all_records()
        # 加入 index 方便後續處理
        for i, r in enumerate(records): r['row_idx'] = i + 2
        return render_template('admin/reports.html', reports=records)
    except: return "讀取回報失敗。"

@app.route('/admin/users')
@login_required
@admin_required
def admin_users():
    users = get_all_users(True)
    return render_template('admin/users.html', users=users.values())

@app.route('/admin/db')
@login_required
@admin_required
def admin_db():
    df = get_data()
    # 建立 Google Sheets 連結
    sheet_url = f"https://docs.google.com/spreadsheets/d/{get_gspread_client().open(SHEET_NAME).id}/edit"
    return render_template('admin/db.html', cars=df.to_dict('records'), sheet_url=sheet_url)

@app.route('/admin/handle_report/<int:row_idx>')
@login_required
@admin_required
def handle_report(row_idx):
    try:
        sheet = get_gspread_client().open(SHEET_NAME).worksheet('Reports')
        sheet.update_cell(row_idx, 6, '已處理')
        flash('✅ 任務完成：該回報已標記為處理完畢。', 'success')
    except: flash('❌ 操作失敗。', 'error')
    return redirect(url_for('admin_reports'))

@app.route('/refresh')
def refresh():
    global _data_cache, _db_version_cache
    _data_cache = None
    _db_version_cache = None
    get_data()
    return "<script>alert('🚀 資料庫已全面同步完成');window.location.href='/home';</script>"

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        name = request.form.get('name', '').strip()
        phone = request.form.get('phone', '').strip()
        shop_name = request.form.get('shop_name', '').strip()
        
        if not email:
            flash('📧 Email 為必填項目，以便找回密碼。', 'error')
            return redirect(url_for('register'))
            
        if len(phone) == 9 and phone.isdigit(): phone = "0" + phone
        try:
            client = get_gspread_client()
            sheet = client.open(SHEET_NAME).worksheet('Users')
            if phone in sheet.col_values(4):
                flash('🔒 該號碼已註冊。請直接登入。', 'error')
                return redirect(url_for('login'))
            
            hashed_password = generate_password_hash(password)
            sheet.append_row([email, hashed_password, name, phone, shop_name, "", ""])
            get_all_users(True)
            flash('🎨 歡迎加入！帳號已準備就緒，請登入。', 'success')
            return redirect(url_for('login'))
        except: flash('⚙️ 註冊服務暫時中斷，請稍後。', 'error')
    return render_template('register.html')

@app.route('/forgot_password', methods=['POST'])
def forgot_password():
    phone = request.form.get('phone', '').strip()
    if len(phone) == 9 and phone.isdigit(): phone = "0" + phone
    
    users = get_all_users(True)
    user = users.get(phone)
    
    if not user or not user.email:
        flash('🚫 找不到對應的會員或 Email 資料。', 'error')
        return redirect(url_for('login'))
    
    # 產生 6 位數字驗證碼
    reset_code = ''.join([str(secrets.SystemRandom().randint(0, 9)) for _ in range(6)])
    
    try:
        # 更新 Google Sheets 的 reset_code (假設在第 7 欄)
        client = get_gspread_client()
        sheet = client.open(SHEET_NAME).worksheet('Users')
        phones = sheet.col_values(4)
        row_idx = -1
        for i, p in enumerate(phones):
            p_str = str(p).strip().lstrip("'")
            if len(p_str) == 9 and p_str.isdigit(): p_str = "0" + p_str
            if p_str == phone:
                row_idx = i + 1
                break
        
        if row_idx != -1:
            # 確保欄位存在，更新第 7 欄
            sheet.update_cell(row_idx, 7, reset_code)
            
            # 寄信
            subject = "【京富毅冷媒系統】密碼重設驗證碼"
            body = f"親愛的 {user.name} 您好：\n\n您正在申請重設密碼。\n您的六位數驗證碼為：{reset_code}\n\n請在重設頁面輸入此驗證碼以設定新密碼。\n\n京富毅汽車材料 敬上"
            
            if send_mail(user.email, subject, body):
                flash(f'📧 驗證碼已寄送到您的信箱：{user.email}，請於下方輸入。', 'success')
                return render_template('reset_password.html', phone=phone)
            else:
                flash('⚠️ 驗證碼產生成功但郵件發送失敗，請聯繫管理員。', 'error')
        else:
            flash('❌ 系統錯誤，請聯繫管理員。', 'error')
    except Exception as e:
        print(f"Forgot error: {e}")
        flash('🔧 暫時無法處理，請稍後再試。', 'error')
        
    return redirect(url_for('login'))

@app.route('/reset_password', methods=['GET', 'POST'])
def reset_password():
    if request.method == 'POST':
        phone = request.form.get('phone', '').strip()
        reset_code = request.form.get('reset_code', '').strip()
        new_password = request.form.get('new_password', '').strip()
        
        if len(phone) == 9 and phone.isdigit(): phone = "0" + phone
        
        try:
            client = get_gspread_client()
            sheet = client.open(SHEET_NAME).worksheet('Users')
            records = sheet.get_all_records()
            
            row_idx = -1
            stored_code = ""
            user_data = None
            
            for i, r in enumerate(records):
                p_str = str(r.get('phone', '')).strip().lstrip("'")
                if len(p_str) == 9 and p_str.isdigit(): p_str = "0" + p_str
                if p_str == phone:
                    row_idx = i + 2 # Header is row 1
                    stored_code = str(r.get('reset_code', '')).strip()
                    user_data = r
                    break
            
            if row_idx != -1 and stored_code == reset_code and reset_code != "":
                hashed_pw = generate_password_hash(new_password)
                sheet.update_cell(row_idx, 2, hashed_pw)
                sheet.update_cell(row_idx, 7, "") # 清除驗證碼
                
                # 自動登入
                user = User(phone, user_data.get('email'), user_data.get('name'), user_data.get('shop_name'), hashed_pw)
                login_user(user)
                
                flash('🎉 密碼重設成功！已為您自動登入。', 'success')
                return redirect(url_for('ad_page'))
            else:
                flash('❌ 驗證碼錯誤或已失效。', 'error')
                return render_template('reset_password.html', phone=phone)
        except Exception as e:
            print(f"Reset error: {e}")
            flash('🔧 重設失敗，請稍後再試。', 'error')
            return render_template('reset_password.html', phone=phone)
            
    return render_template('reset_password.html', phone=request.args.get('phone', ''))

@app.route('/profile')
@login_required
def profile(): return render_template('profile.html')

@app.route('/about')
@login_required
def about(): return render_template('about.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('welcome'))

@app.route('/api/db_sync')
@login_required
def db_sync():
    df = get_data()
    global _db_version_cache
    return {
        'version': _db_version_cache or str(time.time()),
        'data': df.to_dict('records')
    }

@app.route('/ad')
@login_required
def ad_page(): return render_template('ad_page.html', next_page=request.args.get('next', '/home'))

@app.route('/tools')
@login_required
def tools(): return render_template('tools.html')

@app.route('/models/<brand_name>')
@login_required
def show_models(brand_name):
    df = get_data()
    cars = df[df['brand'] == brand_name].to_dict('records')
    return render_template('models.html', brand=brand_name, cars=cars)

@app.route('/manifest.json')
def manifest(): return app.send_static_file('manifest.json')

@app.route('/service-worker.js')
def service_worker(): return app.send_static_file('service-worker.js')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
