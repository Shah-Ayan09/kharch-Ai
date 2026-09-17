import sqlite3
import re
from flask import Flask, request, jsonify
import json
import os
from datetime import datetime, timedelta
import psycopg2
from psycopg2.extras import RealDictCursor
from pywebpush import webpush, WebPushException

# --- VAPID CONFIGURATION ---
VAPID_PUBLIC_KEY = os.environ.get('VAPID_PUBLIC_KEY')
VAPID_PRIVATE_KEY = os.environ.get('VAPID_PRIVATE_KEY')
VAPID_EMAIL = os.environ.get('VAPID_EMAIL', 'mailto:test@example.com')

# --- DATABASE CONNECTION ---
DATABASE_URL = os.environ.get('DATABASE_URL')

def get_db_connection():
    """Connect to PostgreSQL database."""
    return psycopg2.connect(DATABASE_URL)

def initialize_database():
    """Create tables if they don't exist."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
    CREATE TABLE IF NOT EXISTS transactions (
        id SERIAL PRIMARY KEY,
        date TEXT NOT NULL,
        time TEXT NOT NULL,
        amount REAL NOT NULL,
        category TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS push_subscriptions (
        id SERIAL PRIMARY KEY,
        subscription TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    conn.commit()
    cur.close()
    conn.close()
    print("✅ Database ready (PostgreSQL)")

# --- SMS PARSER ---
def parse_sms(text):
    if "OTP" in text.upper() or "Valid for" in text:
        print("⏭️ Ignored: OTP message")
        return None
    if "received from" in text.lower():
        print("⏭️ Ignored: Credit transaction")
        return None
    if "debited" not in text.lower() and "sent to" not in text.lower():
        print("⏭️ Ignored: Not a transaction SMS")
        return None
    
    amount_match = re.search(r"PKR\s*([\d,]+(?:\.\d{2})?)", text, re.IGNORECASE)
    if not amount_match:
        print("❌ Could not find amount in SMS")
        return None
    amount_str = amount_match.group(1).replace(",", "")
    amount = float(amount_str)
    
    date_match = re.search(r"on\s+(\d{2}-[A-Za-z]{3}-\d{4})", text, re.IGNORECASE)
    if not date_match:
        print("❌ Could not find date in SMS")
        return None
    date = date_match.group(1)
    
    time_match = re.search(r"at\s+(\d{2}:\d{2})", text, re.IGNORECASE)
    if not time_match:
        print("❌ Could not find time in SMS")
        return None
    time = time_match.group(1)
    
    if "sent to" in text.lower():
        recipient_match = re.search(r"sent to\s+([A-Za-z\.\s\(\)]+?)(?:\s+\(|\s+as\s+|$)", text, re.IGNORECASE)
        merchant = recipient_match.group(1).strip() if recipient_match else "Unknown Recipient"
    else:
        merchant = "Unknown Merchant"
    
    return {
        "amount": amount,
        "date": convert_date_to_iso(date),
        "time": time,
        "merchant": merchant
    }

def convert_date_to_iso(date_str):
    try:
        dt = datetime.strptime(date_str, "%d-%b-%Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return date_str

app = Flask(__name__)

@app.route("/api/pending")
def api_pending():
    """Return transactions without a category."""
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT id, date, time, amount
        FROM transactions
        WHERE category IS NULL
        ORDER BY id DESC
        LIMIT 20
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify([dict(r) for r in rows])

# --- PWA ROUTES ---
@app.route("/manifest.json")
def manifest():
    with open('manifest.json', 'r', encoding='utf-8') as f:
        return f.read(), 200, {'Content-Type': 'application/manifest+json'}

@app.route("/service-worker.js")
def service_worker():
    with open('service-worker.js', 'r', encoding='utf-8') as f:
        return f.read(), 200, {'Content-Type': 'application/javascript'}

# --- PUSH NOTIFICATION SUBSCRIPTION ---
@app.route("/subscribe", methods=["POST"])
def subscribe():
    """Save the user's push subscription."""
    data = request.get_json()
    subscription = json.dumps(data)
    
    conn = get_db_connection()
    cur = conn.cursor()
    # Clear old subscriptions (only one user)
    cur.execute('DELETE FROM push_subscriptions')
    cur.execute('INSERT INTO push_subscriptions (subscription) VALUES (%s)', (subscription,))
    conn.commit()
    cur.close()
    conn.close()
    
    print("✅ Push subscription saved")
    return jsonify({"success": True}), 200

@app.route("/vapid-public-key")
def vapid_public_key():
    """Return the VAPID public key for the frontend."""
    return jsonify({"publicKey": VAPID_PUBLIC_KEY})

# --- SEND PUSH NOTIFICATION ---
def send_push_notification(amount, date, time, transaction_id):
    """Send a push notification to all subscribed devices."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT subscription FROM push_subscriptions')
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    if not rows:
        print("⚠️ No push subscriptions found")
        return
    
    payload = json.dumps({
        "title": "💰 New Transaction",
        "body": f"Rs {amount:.2f} on {date} at {time}\nTap to categorize",
        "url": f"/categorize?id={transaction_id}"
    })
    
    for row in rows:
        try:
            subscription_info = json.loads(row[0])
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_EMAIL}
            )
            print(f"📨 Push notification sent!")
        except WebPushException as e:
            print(f"❌ Push failed: {e}")

# --- CLEAR DATA ---
@app.route("/clear")
def clear_data():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('DELETE FROM transactions')
    conn.commit()
    cur.close()
    conn.close()
    return "✅ All transactions deleted!"

# --- API SPENDING ---
@app.route("/api/spending")
def api_spending():
    days = request.args.get('days', default=30, type=int)
    cutoff_date = datetime.now() - timedelta(days=days)
    cutoff_str = cutoff_date.strftime('%Y-%m-%d')
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT category, SUM(amount) as total
        FROM transactions
        WHERE category IS NOT NULL
        AND date >= %s
        GROUP BY category
        ORDER BY total DESC
    """, (cutoff_str,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    total_spent = sum([row[1] for row in rows])
    data = {
        "labels": [row[0] for row in rows],
        "values": [row[1] for row in rows],
        "total": total_spent,
        "days": days
    }
    return jsonify(data)

# --- RECEIVE SMS ---
@app.route("/sms", methods=["POST"])
def handle_sms():
    if request.form:
        sms_text = request.form.get('sms', '')
    else:
        sms_text = request.data.decode("utf-8")
    
    print(f"📩 Extracted SMS: {sms_text}")
    
    data = parse_sms(sms_text)
    if not data:
        return "Could not parse SMS", 400
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
    INSERT INTO transactions (date, time, amount, category)
    VALUES (%s, %s, %s, %s) RETURNING id
    ''', (data["date"], data["time"], data["amount"], None))
    transaction_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    
    print(f"✅ Saved: Rs {data['amount']} on {data['date']} at {data['time']}")
    
    # Send push notification
    send_push_notification(data["amount"], data["date"], data["time"], transaction_id)
    
    return "Transaction saved!", 200

# --- CATEGORIZE PAGE (loaded when notification is tapped) ---
@app.route("/categorize")
def categorize_page():
    with open('categorize.html', 'r', encoding='utf-8') as f:
        return f.read()

# --- UPDATE CATEGORY (called from categorize page) ---
@app.route("/api/update_category", methods=["POST"])
def update_category():
    data = request.get_json()
    transaction_id = data.get('transaction_id')
    category = data.get('category')
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE transactions SET category = %s WHERE id = %s", (category, transaction_id))
    conn.commit()
    cur.close()
    conn.close()
    
    print(f"✅ Updated transaction #{transaction_id} → {category}")
    return jsonify({"success": True}), 200

# --- GET SINGLE TRANSACTION (for categorize page) ---
@app.route("/api/transaction/<int:transaction_id>")
def get_transaction(transaction_id):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM transactions WHERE id = %s", (transaction_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(row)

# --- VIEW ---
@app.route("/view")
def view_transactions():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM transactions ORDER BY id DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    output = "<h1>📊 Transactions</h1><ul>"
    for row in rows:
        output += f"<li>#{row['id']} | Rs {row['amount']} | {row['date']} {row['time']} | Category: {row['category'] or 'Not set'}</li>"
    output += "</ul>"
    return output

# --- TEST ROUTE ---
@app.route("/test")
def test():
    print("🔍 Test route was hit!")
    return "Test OK!", 200

# --- DASHBOARD ---
@app.route("/dashboard")
def dashboard():
    with open('dashboard.html', 'r', encoding='utf-8') as file:
        return file.read()

# --- API TRANSACTIONS ---
@app.route("/api/transactions")
def api_transactions():
    days = request.args.get('days', default=30, type=int)
    cutoff_date = datetime.now() - timedelta(days=days)
    cutoff_str = cutoff_date.strftime('%Y-%m-%d')
    
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT date, time, amount, category
        FROM transactions
        WHERE date >= %s
        AND category IS NOT NULL
        ORDER BY id DESC
        LIMIT 50
    """, (cutoff_str,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    transactions = []
    for row in rows:
        transactions.append({
            "date": row["date"],
            "time": row["time"],
            "amount": row["amount"],
            "category": row["category"]
        })
    return jsonify(transactions)

# --- ADD TRANSACTION ---
@app.route("/api/add_transaction", methods=["POST"])
def add_transaction():
    data = request.get_json()
    amount = data.get('amount')
    category = data.get('category')
    date = convert_date_to_iso(data.get('date'))
    time = data.get('time')
    
    if not amount or not category or not date or not time:
        return jsonify({"error": "Missing required fields"}), 400
    
    try:
        amount = float(amount)
    except ValueError:
        return jsonify({"error": "Invalid amount"}), 400
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO transactions (date, time, amount, category)
        VALUES (%s, %s, %s, %s)
    ''', (date, time, amount, category))
    conn.commit()
    cur.close()
    conn.close()
    
    return jsonify({"success": True, "message": "Transaction added!"}), 200

# --- RUN SERVER ---
if __name__ == "__main__":
    initialize_database()
    print("🚀 Server is starting")
    app.run(host="0.0.0.0", port=5000, debug=True)

# --- RENDER DEPLOYMENT ---
initialize_database()