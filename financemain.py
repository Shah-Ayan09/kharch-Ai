import sqlite3
import re
from flask import Flask, request, jsonify
import json
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from datetime import datetime, timedelta
from datetime import datetime 
import os

# --- SLACK CREDENTIALS ---
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")  # <-- PASTE YOUR BOT TOKEN HERE
SLACK_CHANNEL = "#general"

# --- DATABASE ---
def initialize_database():
    connection = sqlite3.connect('finapp.db')
    cursor = connection.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        time TEXT NOT NULL,
        amount REAL NOT NULL,
        category TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    connection.commit()
    connection.close()
    print("✅ Database ready")

# --- SMS PARSER ---
def parse_sms(text):
    """
    Extract amount, time, and date from SMS text.
    Handles:
    - Debit: "PKR X.XX has been debited at HH:MM on DD-Mon-YYYY"
    - Sent (RAAST Transfer): "PKR X.XX sent to NAME ... on DD-Mon-YYYY at HH:MM"
    - Sent (RAAST Payment): "PKR X.XX sent to NAME as RAAST payment ... on DD-Mon-YYYY at HH:MM"
    Ignores:
    - OTP messages
    - Credits (received money)
    """
    
    # --- IGNORE: OTP messages ---
    if "OTP" in text.upper() or "Valid for" in text:
        print("⏭️ Ignored: OTP message")
        return None
    
    # --- IGNORE: Credits (received money) ---
    if "received from" in text.lower():
        print("⏭️ Ignored: Credit transaction (received money)")
        return None
    
    # --- IGNORE: Anything that doesn't look like an expense ---
    # If it doesn't contain "debited" or "sent to", ignore it
    if "debited" not in text.lower() and "sent to" not in text.lower():
        print("⏭️ Ignored: Not a transaction SMS")
        return None
    
    # --- 1. EXTRACT AMOUNT ---
    amount_match = re.search(r"PKR\s*([\d,]+(?:\.\d{2})?)", text, re.IGNORECASE)
    if not amount_match:
        print("❌ Could not find amount in SMS")
        return None
    
    amount_str = amount_match.group(1).replace(",", "")
    amount = float(amount_str)
    
    # --- 2. EXTRACT DATE ---
    date_match = re.search(r"on\s+(\d{2}-[A-Za-z]{3}-\d{4})", text, re.IGNORECASE)
    if not date_match:
        print("❌ Could not find date in SMS")
        return None
    
    date = date_match.group(1)
    
    # --- 3. EXTRACT TIME ---
    time_match = re.search(r"at\s+(\d{2}:\d{2})", text, re.IGNORECASE)
    if not time_match:
        print("❌ Could not find time in SMS")
        return None
    
    time = time_match.group(1)
    
    # --- 4. DETERMINE TYPE AND MERCHANT ---
    if "sent to" in text.lower():
        transaction_type = "expense"
        recipient_match = re.search(r"sent to\s+([A-Za-z\.\s\(\)]+?)(?:\s+\(|\s+as\s+|$)", text, re.IGNORECASE)
        if recipient_match:
            merchant = recipient_match.group(1).strip()
        else:
            merchant = "Unknown Recipient"
    else:  # "debited" is present
        transaction_type = "expense"
        merchant = "Unknown Merchant"
    
    return {
        "amount": amount,
        "date": convert_date_to_iso(date),
        "time": time,
        "merchant": merchant,
        "type": transaction_type
    }
def convert_date_to_iso(date_str):
    """
    Convert DD-Mon-YYYY to YYYY-MM-DD
    Example: "29-Aug-2026" → "2026-08-29"
    """
    try:
        # Parse the date string
        dt = datetime.strptime(date_str, "%d-%b-%Y")
        # Return in ISO format
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        # If it's already ISO, return as-is
        return date_str

app = Flask(__name__)

@app.route("/api/spending")
def api_spending():
    """Return spending data grouped by category with date filtering."""
    
    # Get the 'days' parameter from the URL (default: 30 days)
    days = request.args.get('days', default=30, type=int)
    
    # Calculate the cutoff date
    cutoff_date = datetime.now() - timedelta(days=days)
    cutoff_str = cutoff_date.strftime('%Y-%m-%d')
    
    # Connect to the database
    connection = sqlite3.connect('finapp.db')
    cursor = connection.cursor()
    
    # Query: sum amounts by category, only recent transactions
    cursor.execute("""
        SELECT category, SUM(amount) as total
        FROM transactions
        WHERE category IS NOT NULL
        AND date >= ?
        GROUP BY category
        ORDER BY total DESC
    """, (cutoff_str,))
    
    rows = cursor.fetchall()
    connection.close()
    
    # Calculate total spending
    total_spent = sum([row[1] for row in rows])
    
    # Build the response
    data = {
        "labels": [row[0] for row in rows],
        "values": [row[1] for row in rows],
        "total": total_spent,
        "days": days
    }
    
    return jsonify(data)

# --- SLACK CLIENT ---
client = WebClient(token=SLACK_BOT_TOKEN)

# --- SEND SLACK MESSAGE WITH BUTTONS ---
def send_slack_buttons(amount, date, time, transaction_id):
    message = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"💰 *Rs {amount:.2f} spent*\n📅 {date} at {time}\n\nWhat category?"
            }
        },
        {
            "type": "actions",
            "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "🍔 Food"}, "value": f"Food_{transaction_id}", "action_id": "category_food"},
                {"type": "button", "text": {"type": "plain_text", "text": "⛽ Fuel"}, "value": f"Fuel_{transaction_id}", "action_id": "category_fuel"},
                {"type": "button", "text": {"type": "plain_text", "text": "🛒 Groceries"}, "value": f"Groceries_{transaction_id}", "action_id": "category_groceries"},
                {"type": "button", "text": {"type": "plain_text", "text": "📺 Entertainment"}, "value": f"Entertainment_{transaction_id}", "action_id": "category_entertainment"},
                {"type": "button", "text": {"type": "plain_text", "text": "🛍️ Shopping"}, "value": f"Shopping_{transaction_id}", "action_id": "category_shopping"},
                {"type": "button", "text": {"type": "plain_text", "text": "🏠 Bills"}, "value": f"Bills_{transaction_id}", "action_id": "category_bills"},
                {"type": "button", "text": {"type": "plain_text", "text": "💸 Transfer"}, "value": f"Transfer_{transaction_id}", "action_id": "category_transfer"},
                {"type": "button", "text": {"type": "plain_text", "text": "❓ Other"}, "value": f"Other_{transaction_id}", "action_id": "category_other"}
            ]
        }
    ]
    
    try:
        client.chat_postMessage(
            channel=SLACK_CHANNEL,
            blocks=message,
            text="Categorize your transaction"
        )
        print("📨 Slack message sent!")
    except Exception as e:
        print(f"❌ Slack error: {e}")

# --- ROUTE 1: Receive SMS ---
@app.route("/sms", methods=["POST"])
def handle_sms():
    # Get SMS text
    if request.form:
        sms_text = request.form.get('sms', '')
    else:
        sms_text = request.data.decode("utf-8")
    
    print(f"📩 Extracted SMS: {sms_text}")
    
    data = parse_sms(sms_text)
    if not data:
        return "Could not parse SMS", 400  # <-- MUST HAVE THIS RETURN
    
    # Save to database
    connection = sqlite3.connect('finapp.db')
    cursor = connection.cursor()
    cursor.execute('''
    INSERT INTO transactions (date, time, amount, category)
    VALUES (?, ?, ?, ?)
    ''', (data["date"], data["time"], data["amount"], None))
    connection.commit()
    transaction_id = cursor.lastrowid
    connection.close()
    
    print(f"✅ Saved: Rs {data['amount']} on {data['date']} at {data['time']}")
    
    # Send Slack notification
    send_slack_buttons(data["amount"], data["date"], data["time"], transaction_id)
    
    return "Transaction saved!", 200  # <-- MUST HAVE THIS RETURN
    # ... rest of your save code ...

# --- ROUTE 2: Handle Button Taps from Slack ---
@app.route("/slack/interactive", methods=["POST"])
def handle_slack_interaction():
    print("🔍 Button tapped!")
    
    # Get the payload from Slack
    payload = request.form["payload"]
    print(f"🔍 Raw payload: {payload}")
    
    # Parse the JSON
    data = json.loads(payload)
    
    # Get the button value
    action = data["actions"][0]
    value = action["value"]
    category, transaction_id = value.split("_")
    
    # Update the database
    connection = sqlite3.connect('finapp.db')
    cursor = connection.cursor()
    cursor.execute("UPDATE transactions SET category = ? WHERE id = ?", (category, transaction_id))
    connection.commit()
    connection.close()
    
    print(f"✅ Updated transaction #{transaction_id} → {category}")
    
    # --- SEND A SECOND "THANK YOU" MESSAGE TO SLACK ---
    try:
        # Get transaction details for the thank you message
        connection = sqlite3.connect('finapp.db')
        cursor = connection.cursor()
        cursor.execute("SELECT amount, date, time FROM transactions WHERE id = ?", (transaction_id,))
        row = cursor.fetchone()
        connection.close()
        
        if row:
            amount, date, time = row
            thank_you_message = (
                f"✅ *Thank you!* 🙌\n"
                f"Your purchase of *Rs {amount:.2f}* on {date} at {time} has been categorized as *{category}*."
            )
        else:
            thank_you_message = f"✅ Thank you! Transaction #{transaction_id} saved as *{category}*!"
        
        # Send the thank you message to Slack
        client.chat_postMessage(
            channel=SLACK_CHANNEL,
            text=thank_you_message
        )
        print("📨 Thank you message sent to Slack!")
    except Exception as e:
        print(f"❌ Failed to send thank you message: {e}")
    
    # Update the original message to show it's been categorized
    response = {
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"✅ Categorized as *{category}*!"
                }
            }
        ]
    }
    return jsonify(response)
# --- ROUTE 3: View Transactions ---
@app.route("/view")
def view_transactions():
    connection = sqlite3.connect('finapp.db')
    cursor = connection.cursor()
    cursor.execute("SELECT * FROM transactions ORDER BY id DESC")
    rows = cursor.fetchall()
    connection.close()
    
    output = "<h1>📊 Transactions</h1><ul>"
    for row in rows:
        output += f"<li>#{row[0]} | Rs {row[3]} | {row[1]} {row[2]} | Category: {row[4] or 'Not set'}</li>"
    output += "</ul>"
    return output

# --- ROUTE 4: Test Route ---
@app.route("/test")
def test():
    print("🔍 Test route was hit!")
    return "Test OK!", 200

@app.route("/dashboard")
def dashboard():
    with open('dashboard.html', 'r', encoding='utf-8') as file:
        return file.read() 

@app.route("/api/transactions")
def api_transactions():
    """Return recent transactions for the dashboard list."""
    
    # Get the 'days' parameter (default: 30)
    days = request.args.get('days', default=30, type=int)
    
    # Calculate cutoff date
    cutoff_date = datetime.now() - timedelta(days=days)
    cutoff_str = cutoff_date.strftime('%Y-%m-%d')
    
    # Connect to database
    connection = sqlite3.connect('finapp.db')
    cursor = connection.cursor()
    
    # Get transactions from the last X days, ordered by date (newest first)
    cursor.execute("""
        SELECT date, time, amount, category
        FROM transactions
        WHERE date >= ?
        AND category IS NOT NULL
        ORDER BY id DESC
        LIMIT 50
    """, (cutoff_str,))
    
    rows = cursor.fetchall()
    connection.close()
    
    # Build response
    transactions = []
    for row in rows:
        transactions.append({
            "date": row[0],
            "time": row[1],
            "amount": row[2],
            "category": row[3]
        })
    
    return jsonify(transactions)

@app.route("/api/add_transaction", methods=["POST"])
def add_transaction():
    data = request.get_json()
    amount = data.get('amount')
    category = data.get('category')
    date = convert_date_to_iso(data.get('date'))  # <-- Convert here too
    time = data.get('time')
    # ... rest of code
    
    # Validate required fields
    if not amount or not category or not date or not time:
        return jsonify({"error": "Missing required fields"}), 400
    
    try:
        amount = float(amount)
    except ValueError:
        return jsonify({"error": "Invalid amount"}), 400
    
    # Save to database
    connection = sqlite3.connect('finapp.db')
    cursor = connection.cursor()
    cursor.execute('''
        INSERT INTO transactions (date, time, amount, category)
        VALUES (?, ?, ?, ?)
    ''', (date, time, amount, category))
    connection.commit()
    connection.close()
    
    return jsonify({"success": True, "message": "Transaction added!"}), 200

# --- RUN SERVER ---
if __name__ == "__main__":
    initialize_database()
    print("🚀 Server is starting")
    print("📱 SMS receiver: http://192.168.100.137:5000/sms")
    print("Press Ctrl+C to stop the server")
    app.run(host="0.0.0.0", port=5000, debug=True)