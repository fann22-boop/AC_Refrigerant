import pandas as pd
import gspread
import sqlite3
import secrets
import smtplib
from email.mime.text import MIMEText
from flask import Flask, render_template, request, redirect, url_for, flash, make_response
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import time
import requests
import base64
import os
from functools import wraps
from flask_compress import Compress
import json

# Firebase Imports
import firebase_admin
from firebase_admin import credentials, firestore, storage
from google.cloud import vision
import re

app = Flask(__name__)
app.secret_key = 'super_secret_key_fuyi_ac' 
Compress(app)

# --- Firebase Admin SDK 初始化 ---
FIREBASE_CREDS_PATH = 'firebase-adminsdk.json'
if os.path.exists(FIREBASE_CREDS_PATH):
    cred = credentials.Certificate(FIREBASE_CREDS_PATH)
    firebase_admin.initialize_app(cred, {
        'storageBucket': 'caracsystem.firebasestorage.app'
    })
    db_firestore = firestore.client()
    bucket = storage.bucket()
    print("🔥 Firebase Admin SDK 已成功啟動")
else:
    print("⚠️ 找不到 firebase-adminsdk.json，Firebase 功能將受限")

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
MAIL_SERVER = 'smtp.gmail.com'
MAIL_PORT = 587
MAIL_USERNAME = 'fuyi9188@gmail.com'
MAIL_PASSWORD = 'nkeasjhllsdzmopm'

# --- Telegram Notify & OCR Helper ---
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '8285863471:AAHgmjpGfJztqzM6dg8ZGYYtliMaLMfRvDA')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '1494097322')

def send_telegram_notification(message):
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == 'YOUR_BOT_TOKEN_HERE':
        print("⚠️ Telegram Bot Token 未設定")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, json=payload)
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Telegram 通知發送失敗: {e}")
        return False

def extract_card_info(image_content):
    """使用 Google Vision OCR 提取姓名與手機號碼"""
    try:
        # 如果有環境變數指向憑證檔案，Vision 會自動讀取
        # 或者我們可以顯式傳遞憑證。這裡假設環境已配置或與現有憑證共用。
        client = vision.ImageAnnotatorClient()
        image = vision.Image(content=image_content)
        response = client.text_detection(image=image)
        texts = response.text_annotations
        
        if not texts:
            return None, None

        full_text = texts[0].description
        lines = full_text.split('\n')
        
        # 提取手機號碼 (台灣格式 09xx-xxx-xxx 或 09xxxxxxxx)
        phone_match = re.search(r'09\d{2}-?\d{3}-?\d{3}', full_text)
        phone = phone_match.group().replace('-', '') if phone_match else None
        
        # 啟發式提取姓名: 通常在前面幾行，排除包含地址、電話、Email 的行
        name = None
        for line in lines[:5]:
            line = line.strip()
            # 排除明顯不是名字的行
            if len(line) < 2 or len(line) > 10: continue
            if any(k in line for k in ['市', '路', '街', '巷', '號', '樓', 'Tel', 'Fax', 'Mobile', '@', 'http']):
                continue
            if re.search(r'\d', line): continue
            name = line
            break
            
        return name, phone
    except Exception as e:
        print(f"❌ OCR 處理失敗: {e}")
        return None, None

# --- 裝飾器 ---
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.phone not in ADMIN_PHONES:
            flash('⚠️ 探索受限：這區域僅供系統管理員訪問。', 'error')
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function

# --- SQLite 快取管理 (冷媒資料用) ---
def init_local_db():
    try:
        if not os.path.exists(DB_PATH):
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)''')
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"⚠️ SQLite 初始化失敗: {e}")

def get_cached_data():
    try:
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql_query("SELECT * FROM cars", conn)
        conn.close()
        return df
    except:
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

# --- 核心資料讀取 (智慧同步版 - Google Sheets) ---
_data_cache = None
_last_update = 0
_db_version_cache = None

def get_db_metadata():
    try:
        client = get_gspread_client()
        spreadsheet = client.open(SHEET_NAME)
        sheet = spreadsheet.sheet1
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
        
        # 強制確保 id 欄位存在且為字串
        if 'id' in df.columns:
            df['id'] = df['id'].astype(str)
        else:
            # 如果試算表沒 id 欄位，則以列號作為 id
            df['id'] = [str(i+1) for i in range(len(df))]
            
        save_to_cache(df, current_version)
        _data_cache = df
        _db_version_cache = current_version
        _last_update = current_time
        return df
    except Exception as e:
        print(f"❌ 快取同步失敗: {e}")
        return get_cached_data() if get_cached_data() is not None else pd.DataFrame()

# --- 會員系統 (Firestore 版) ---
class User(UserMixin):
    def __init__(self, phone, email, name, shop_name, password_hash, reset_code=None, card_image_url=None):
        self.id = str(phone).strip() 
        self.phone = str(phone).strip()
        self.email = email
        self.name = name
        self.shop_name = shop_name
        self.password_hash = password_hash
        self.reset_code = reset_code
        self.card_image_url = card_image_url

def get_user_from_firestore(phone):
    try:
        doc = db_firestore.collection('users').document(phone).get()
        if doc.exists:
            d = doc.to_dict()
            return User(
                phone=d.get('phone'),
                email=d.get('email'),
                name=d.get('name'),
                shop_name=d.get('shop_name'),
                password_hash=d.get('password_hash'),
                reset_code=d.get('reset_code'),
                card_image_url=d.get('card_image_url')
            )
    except Exception as e:
        print(f"❌ Firestore 讀取使用者失敗: {e}")
    return None

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return get_user_from_firestore(user_id)

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
        user = get_user_from_firestore(phone)
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
    # 增加安全檢查，如果 car_id 為空或 undefined，導回首頁
    if not car_id or car_id == 'undefined':
        return redirect(url_for('home'))
    car = df[df['id'].astype(str) == str(car_id)].to_dict('records')
    return render_template('detail.html', car=car[0]) if car else "找不到該車型資料。"

@app.route('/detail/')
@login_required
def show_detail_empty():
    return redirect(url_for('home'))

@app.route('/report', methods=['POST'])
@login_required
def report_error():
    car_id = request.form.get('car_id')
    car_info = request.form.get('car_info')
    message = request.form.get('message', '').strip()
    try:
        db_firestore.collection('reports').add({
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
            'user_phone': current_user.phone,
            'user_name': current_user.name,
            'car_info': car_info,
            'message': message,
            'car_id': car_id,
            'status': '待處理'
        })
        flash('✨ 感謝回報！這份貢獻讓我們的資料庫變得更加卓越。', 'success')
        
        # 發送 Telegram 通知
        notify_msg = f"<b>📢 收到錯誤回報</b>\n👤 使用者: {current_user.name} ({current_user.phone})\n🚗 車型: {car_info}\n📝 內容: {message}"
        send_telegram_notification(notify_msg)
        
    except Exception as e:
        print(f"Report error: {e}")
        flash('🔧 暫時無法處理回報，請稍後再試。', 'error')
    return redirect(url_for('show_detail', car_id=car_id))

# --- 管理功能 ---
@app.route('/admin')
@login_required
@admin_required
def admin_dashboard():
    try:
        user_count = len(list(db_firestore.collection('users').stream()))
        report_count = len(list(db_firestore.collection('reports').where('status', '==', '待處理').stream()))
    except:
        user_count = 0
        report_count = 0
    return render_template('admin/dashboard.html', user_count=user_count, report_count=report_count)

@app.route('/admin/reports')
@login_required
@admin_required
def admin_reports():
    try:
        reports = []
        docs = db_firestore.collection('reports').order_by('timestamp', direction=firestore.Query.DESCENDING).stream()
        for doc in docs:
            d = doc.to_dict()
            d['doc_id'] = doc.id
            reports.append(d)
        return render_template('admin/reports.html', reports=reports)
    except Exception as e:
        print(f"Admin reports error: {e}")
        return "讀取回報失敗。"

@app.route('/admin/users')
@login_required
@admin_required
def admin_users():
    try:
        users = []
        docs = db_firestore.collection('users').stream()
        for doc in docs:
            users.append(doc.to_dict())
        return render_template('admin/users.html', users=users)
    except:
        return "讀取會員失敗。"

@app.route('/admin/db')
@login_required
@admin_required
def admin_db():
    df = get_data()
    # 這裡暫時維持 Google Sheets 連結
    client = get_gspread_client()
    sheet_url = f"https://docs.google.com/spreadsheets/d/{client.open(SHEET_NAME).id}/edit"
    return render_template('admin/db.html', cars=df.to_dict('records'), sheet_url=sheet_url)

@app.route('/admin/handle_report/<doc_id>')
@login_required
@admin_required
def handle_report(doc_id):
    try:
        db_firestore.collection('reports').document(doc_id).update({'status': '已處理'})
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
        card_image = request.files.get('card_image')
        
        if not email:
            flash('📧 Email 為必填項目，以便找回密碼。', 'error')
            return redirect(url_for('register'))
            
        if len(phone) == 9 and phone.isdigit(): phone = "0" + phone
        
        try:
            # 上傳名片並進行 OCR
            card_image_url = ""
            if card_image:
                image_content = card_image.read()
                ocr_name, ocr_phone = extract_card_info(image_content)
                
                # 自動填充 (如果使用者沒填)
                if not name and ocr_name: name = ocr_name
                if not phone and ocr_phone: phone = ocr_phone
                
                # 再次檢查格式
                if phone and len(phone) == 9 and phone.isdigit(): phone = "0" + phone

                # 上傳到 Firebase Storage
                filename = f"business_cards/{phone if phone else 'unknown'}_{int(time.time())}.jpg"
                blob = bucket.blob(filename)
                blob.upload_from_string(image_content, content_type='image/jpeg')
                blob.make_public()
                card_image_url = blob.public_url

            # 檢查是否已存在 (移到 OCR 之後，因為 phone 可能被 OCR 更新)
            if phone and db_firestore.collection('users').document(phone).get().exists:
                flash('🔒 該號碼已註冊。請直接登入。', 'error')
                return redirect(url_for('login'))
            
            if not phone:
                flash('📱 手機號碼為必填項目。', 'error')
                return redirect(url_for('register'))

            hashed_password = generate_password_hash(password)
            db_firestore.collection('users').document(phone).set({
                'email': email,
                'password_hash': hashed_password,
                'name': name,
                'phone': phone,
                'shop_name': shop_name,
                'card_image_url': card_image_url,
                'reset_code': "",
                'created_at': firestore.SERVER_TIMESTAMP
            })
            flash('🎨 歡迎加入！帳號已準備就緒，請登入。', 'success')
            
            # 發送 Telegram 通知
            notify_msg = f"<b>🆕 新使用者註冊</b>\n👤 姓名: {name}\n📱 電話: {phone}\n🏢 店名: {shop_name}"
            send_telegram_notification(notify_msg)
            
            return redirect(url_for('login'))
        except Exception as e:
            print(f"Register error: {e}")
            flash('⚙️ 註冊服務暫時中斷，請稍後。', 'error')
    return render_template('register.html')

@app.route('/forgot_password', methods=['POST'])
def forgot_password():
    phone = request.form.get('phone', '').strip()
    if len(phone) == 9 and phone.isdigit(): phone = "0" + phone
    user = get_user_from_firestore(phone)
    if not user or not user.email:
        flash('🚫 找不到對應的會員或 Email 資料。', 'error')
        return redirect(url_for('login'))
    reset_code = ''.join([str(secrets.SystemRandom().randint(0, 9)) for _ in range(6)])
    try:
        db_firestore.collection('users').document(phone).update({'reset_code': reset_code})
        subject = "【京富毅冷媒系統】密碼重設驗證碼"
        body = f"親愛的 {user.name} 您好：\n\n您正在申請重設密碼。\n您的六位數驗證碼為：{reset_code}\n\n請在重設頁面輸入此驗證碼以設定新密碼。\n\n京富毅汽車材料 敬上"
        if send_mail(user.email, subject, body):
            flash(f'📧 驗證碼已寄送到您的信箱：{user.email}，請於下方輸入。', 'success')
            return render_template('reset_password.html', phone=phone)
        else:
            flash('⚠️ 驗證碼產生成功但郵件發送失敗，請聯繫管理員。', 'error')
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
            doc_ref = db_firestore.collection('users').document(phone)
            doc = doc_ref.get()
            if doc.exists:
                data = doc.to_dict()
                if data.get('reset_code') == reset_code and reset_code != "":
                    hashed_pw = generate_password_hash(new_password)
                    doc_ref.update({
                        'password_hash': hashed_pw,
                        'reset_code': ""
                    })
                    # 自動登入
                    user = User(phone, data.get('email'), data.get('name'), data.get('shop_name'), hashed_pw)
                    login_user(user)
                    flash('🎉 密碼重設成功！已為您自動登入。', 'success')
                    return redirect(url_for('ad_page'))
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
    response = make_response({
        'version': _db_version_cache or str(time.time()),
        'data': df.to_dict('records')
    })
    # Cloudflare 快取優化：瀏覽器快取 1 小時，Cloudflare 快取 7 天 (s-maxage)
    response.headers['Cache-Control'] = 'public, max-age=3600, s-maxage=604800'
    return response

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
def manifest():
    response = make_response(app.send_static_file('manifest.json'))
    response.headers['Cache-Control'] = 'public, max-age=86400'
    return response

@app.route('/service-worker.js')
def service_worker():
    response = make_response(app.send_static_file('service-worker.js'))
    response.headers['Cache-Control'] = 'public, max-age=86400'
    return response

# --- 全域快取優化 (針對靜態檔案) ---
@app.after_request
def add_header(response):
    # 如果是圖片或字體，讓 Cloudflare 快取久一點
    if request.path.startswith('/static/'):
        response.headers['Cache-Control'] = 'public, max-age=31536000'
    return response

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
