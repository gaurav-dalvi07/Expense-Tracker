from flask import Flask, render_template, request, redirect, session, jsonify, flash, url_for, send_file
import sqlite3, os, io
from datetime import datetime, timedelta
from collections import defaultdict
from werkzeug.security import generate_password_hash, check_password_hash
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

# -------- FLASK APP --------
app = Flask(__name__, instance_relative_config=True)
app.secret_key = "expense_secret_key_2024"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE_DIR, "instance", "expenses.db")
os.makedirs(os.path.join(BASE_DIR, "instance"), exist_ok=True)

# -------- SETTINGS --------
MAX_LOGIN_ATTEMPTS = 3
LOCK_MINUTES = 5

# -------- DATABASE --------
def get_db():
    conn = sqlite3.connect(DB, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

# -------- INIT --------
def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        attempts INTEGER DEFAULT 0,
        lock_time TEXT
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS expenses(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user TEXT,
        amount REAL,
        category TEXT,
        date TEXT
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS budgets(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user TEXT,
        month TEXT,
        amount REAL
    )""")

    conn.commit()
    conn.close()

init_db()

# -------- HELPERS --------
def parse_float_safe(x):
    try: return float(x)
    except: return 0.0

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ---------------- LOGIN ----------------
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = request.form.get("username")
        p = request.form.get("password")

        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username=?", (u,))
        user = cur.fetchone()

        if not user:
            flash("❌ Invalid Username/Password", "danger")
            return redirect("/")

        if user["lock_time"]:
            lock = datetime.strptime(user["lock_time"], "%Y-%m-%d %H:%M:%S")
            if datetime.now() < lock + timedelta(minutes=LOCK_MINUTES):
                flash("🔒 Account Locked. Try Later.", "danger")
                return redirect("/")

        if check_password_hash(user["password"], p):
            cur.execute("UPDATE users SET attempts=0, lock_time=NULL WHERE username=?", (u,))
            conn.commit()
            session["user"] = u
            flash("🎉 Welcome " + u, "success")
            return redirect("/dashboard")
        else:
            attempts = user["attempts"] + 1
            if attempts >= MAX_LOGIN_ATTEMPTS:
                cur.execute("UPDATE users SET attempts=?, lock_time=? WHERE username=?", (attempts, now_str(), u))
                conn.commit()
                flash("🔒 Account Locked!", "danger")
            else:
                cur.execute("UPDATE users SET attempts=? WHERE username=?", (attempts, u))
                conn.commit()
                flash("❌ Wrong Password", "danger")

        return redirect("/")
    return render_template("login.html")

# ---------------- SIGNUP ----------------
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        u = request.form["username"]
        p = request.form["password"]

        hashp = generate_password_hash(p)

        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("INSERT INTO users(username,password) VALUES(?,?)", (u, hashp))
            conn.commit()
            conn.close()
            flash("✅ Account Created", "success")
            return redirect("/")
        except:
            flash("⚠ Username Exists", "danger")
    return render_template("signup.html")

# ---------------- LOGOUT ----------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/") 

# ---------------- DASHBOARD ----------------
@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect("/")

    user = session["user"]
    month = request.args.get("month")

    conn = get_db()
    cur = conn.cursor()

    if month:
        cur.execute("SELECT * FROM expenses WHERE user=? AND strftime('%Y-%m',date)=?", (user, month))
    else:
        cur.execute("SELECT * FROM expenses WHERE user=?", (user,))

    expenses = cur.fetchall()
    total = sum(parse_float_safe(e["amount"]) for e in expenses)

    cat = defaultdict(float)
    for e in expenses:
        cat[e["category"]] += parse_float_safe(e["amount"])

    cur.execute("SELECT date, amount FROM expenses WHERE user=?", (user,))
    all_data = cur.fetchall()
    month_tot = defaultdict(float)
    for r in all_data:
        month_tot[r["date"][:7]] += parse_float_safe(r["amount"])

    mon = month if month else datetime.now().strftime("%Y-%m")
    cur.execute("SELECT amount FROM budgets WHERE user=? AND month=?", (user, mon))
    row = cur.fetchone()
    budget = row["amount"] if row else None
    alert = bool(budget and total >= budget)

    conn.close()

    return render_template("dashboard.html",
        expenses=expenses,
        total=total,
        category_amounts=dict(cat),
        monthly_totals=dict(month_tot),
        budget=budget,
        budget_alert=alert,
        selected_month=month,
        username=user
    )

# ---------------- ADD ----------------
@app.route("/add", methods=["GET","POST"])
def add():
    if "user" not in session: return redirect("/")
    if request.method == "POST":
        amt = parse_float_safe(request.form["amount"])
        cat = request.form["category"]
        date = request.form["date"]

        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO expenses VALUES(NULL,?,?,?,?)", (session["user"], amt, cat, date))
        conn.commit()
        conn.close()
        flash("✅ Expense Added", "success")
        return redirect("/dashboard")
    return render_template("add_expense.html")

# -------------- DELETE --------------
@app.route("/delete/<int:id>")
def delete(id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM expenses WHERE id=? AND user=?", (id, session["user"]))
    conn.commit()
    conn.close()
    return redirect("/dashboard")


# -------------- ⭐ EDIT EXPENSE (ADDED) ⭐ --------------

@app.route("/edit/<int:id>", methods=["GET", "POST"])
def edit(id):
    if "user" not in session:
        return redirect("/")

    conn = get_db()
    cur = conn.cursor()

    if request.method == "POST":
        amt = request.form["amount"]
        cat = request.form.get("custom_category") or request.form["category"]
        date = request.form["date"]

        cur.execute("UPDATE expenses SET amount=?, category=?, date=? WHERE id=? AND user=?",
                    (amt, cat, date, id, session["user"]))
        conn.commit()
        conn.close()

        flash("✏️ Expense Updated", "success")
        return redirect("/dashboard")

    cur.execute("SELECT * FROM expenses WHERE id=? AND user=?", (id, session["user"]))
    expense = cur.fetchone()
    conn.close()

    return render_template("edit_expense.html", expense=expense)




# -------------- SET BUDGET ------------
@app.route("/set_budget", methods=["POST"])
def set_budget():
    data = request.get_json()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM budgets WHERE user=? AND month=?", (session["user"], data["month"]))
    cur.execute("INSERT INTO budgets VALUES(NULL,?,?,?)", (session["user"], data["month"], data["amount"]))
    conn.commit()
    conn.close()
    return jsonify({"status":"ok"})

# -------------- CLEAR MONTH ----------
@app.route("/clear_month", methods=["POST"])
def clear_month():
    data = request.get_json()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM expenses WHERE user=? AND strftime('%Y-%m',date)=?", (session["user"], data["month"]))
    conn.commit()
    conn.close()
    return jsonify({"status":"ok"})

# -------------- CLEAR ALL ------------
@app.route("/clear_all", methods=["POST"])
def clear_all():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM expenses WHERE user=?", (session["user"],))
    conn.commit()
    conn.close()
    return jsonify({"done":1})

# -------------- PDF EXPORT ------------
@app.route("/export_pdf")
def export_pdf():
    if "user" not in session:
        return redirect("/")

    user = session["user"]
    month = request.args.get("month")

    conn = get_db()
    cur = conn.cursor()
    if month:
        cur.execute("SELECT * FROM expenses WHERE user=? AND strftime('%Y-%m',date)=?", (user, month))
        title = month
    else:
        cur.execute("SELECT * FROM expenses WHERE user=?", (user,))
        title = "All Data"

    rows = cur.fetchall()
    conn.close()

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    w,h = A4
    y = h - 40

    pdf.setFont("Helvetica-Bold",14)
    pdf.drawString(40,y,f"Expense Report - {title}")
    y -= 30

    total = 0
    pdf.setFont("Helvetica",10)
    for r in rows:
        if y < 50:
            pdf.showPage()
            y = h - 50
        pdf.drawString(40,y, r["date"])
        pdf.drawString(150,y, r["category"])
        pdf.drawString(300,y, str(r["amount"]))
        total += parse_float_safe(r["amount"])
        y -= 15

    pdf.setFont("Helvetica-Bold",12)
    pdf.drawString(40,y-20,f"TOTAL = ₹ {round(total,2)}")

    pdf.save()
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="Expense_Report.pdf")

# -------- RUN --------
if __name__ == "__main__":
    app.run(debug=True)
