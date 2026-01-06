import os
import random
import string
import sqlite3
from datetime import datetime
from flask import g
from markupsafe import Markup
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    jsonify,   # <--- add this
)

import qrcode   # <--- add this

app = Flask(__name__)
app.secret_key = "super_secret_scanpaygo_key"
# Simple admin credentials (change for your project/demo)
@app.context_processor
def inject_user_and_wallet():
    user_id = session.get("user_id")
    if not user_id:
        return {"current_user": None, "wallet_balance": None}

    conn = get_db_connection()
    user = conn.execute(
        "SELECT id, name, email, wallet_balance FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()
    conn.close()

    if not user:
        return {"current_user": None, "wallet_balance": None}

    return {
        "current_user": user,
        "wallet_balance": user["wallet_balance"],
    }

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"   # change this before submitting!
DEPOSIT_PERCENT = 0.15   # 15% deposit 
DB_NAME = "tickets.db"
QR_FOLDER = os.path.join("static", "qr")

# ----------------------- DB HELPER ----------------------- #

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db_connection()
    cur = conn.cursor()

    # Enable foreign keys
    cur.execute("PRAGMA foreign_keys = ON")

    # USERS table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            wallet_balance REAL NOT NULL DEFAULT 2000.0
        )
    """)

    # Buses table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS buses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operator TEXT NOT NULL,
            from_city TEXT NOT NULL,
            to_city TEXT NOT NULL,
            departure TEXT NOT NULL,
            arrival TEXT NOT NULL,
            price REAL NOT NULL,
            total_seats INTEGER NOT NULL,
            bus_type TEXT NOT NULL
        )
    """)

    # Tickets table – now includes refund + deposit columns
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_code TEXT UNIQUE NOT NULL,
            bus_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            passenger_name TEXT NOT NULL,
            passenger_email TEXT NOT NULL,
            passenger_phone TEXT NOT NULL,
            seat_numbers TEXT NOT NULL,      -- "1,2,3"
            quantity INTEGER NOT NULL,
            total_amount REAL NOT NULL,

            -- NEW deposit logic
            deposit_amount  REAL NOT NULL DEFAULT 0,
            remaining_amount REAL NOT NULL DEFAULT 0,

            payment_status TEXT NOT NULL,    -- PENDING / PAID / REFUNDED
            payment_id TEXT,
            booked_at TEXT NOT NULL,

            -- Refund fields
            refund_status   TEXT,            -- REQUESTED / APPROVED / REJECTED
            refund_reason   TEXT,            -- passenger reason
            refund_response TEXT,            -- admin reply

            FOREIGN KEY (bus_id) REFERENCES buses (id),
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)

    # ---- MIGRATION for old DBs ----
    cur.execute("PRAGMA table_info(tickets)")
    existing_cols = {row[1] for row in cur.fetchall()}

    # add deposit columns if missing
    if "deposit_amount" not in existing_cols:
        cur.execute("ALTER TABLE tickets ADD COLUMN deposit_amount REAL NOT NULL DEFAULT 0")
    if "remaining_amount" not in existing_cols:
        cur.execute("ALTER TABLE tickets ADD COLUMN remaining_amount REAL NOT NULL DEFAULT 0")

    # add refund columns if missing
    if "refund_status" not in existing_cols:
        cur.execute("ALTER TABLE tickets ADD COLUMN refund_status TEXT")
    if "refund_reason" not in existing_cols:
        cur.execute("ALTER TABLE tickets ADD COLUMN refund_reason TEXT")
    if "refund_response" not in existing_cols:
        cur.execute("ALTER TABLE tickets ADD COLUMN refund_response TEXT")

    # For old tickets where remaining_amount is still 0, set it to total_amount
    cur.execute("""
        UPDATE tickets
        SET remaining_amount = total_amount
        WHERE (remaining_amount IS NULL OR remaining_amount = 0)
          AND payment_status != 'PAID'
    """)

    # Seed buses if empty (your original seed)
    cur.execute("SELECT COUNT(*) AS c FROM buses")
    c = cur.fetchone()[0]

    if c == 0:
        sample_buses = [
            ("Skyline Travels", "Chennai", "Bangalore", "2025-12-20 07:00", "2025-12-20 13:00", 899.0, 40, "AC Sleeper"),
            ("MetroLink", "Chennai", "Bangalore", "2025-12-20 21:30", "2025-12-21 04:30", 999.0, 36, "AC Seater"),
            ("GreenBus", "Bangalore", "Hyderabad", "2025-12-21 08:00", "2025-12-21 15:00", 1100.0, 44, "Non-AC Seater"),
            ("NightRider", "Hyderabad", "Chennai", "2025-12-22 22:00", "2025-12-23 06:00", 1300.0, 40, "AC Sleeper"),
            ("CityExpress", "Chennai", "Coimbatore", "2025-12-20 06:30", "2025-12-20 12:00", 750.0, 32, "AC Seater"),
        ]
        cur.executemany("""
            INSERT INTO buses (operator, from_city, to_city, departure, arrival,
                               price, total_seats, bus_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, sample_buses)

    conn.commit()
    conn.close()

# ----------------------- UTILS ----------------------- #

def generate_ticket_code():
    return "SPG-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


def generate_payment_id():
    return "PAY-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=12))


def get_booked_seats(bus_id):
    """Return a set of seat numbers that are already booked (PAID tickets only)."""
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT seat_numbers FROM tickets
        WHERE bus_id = ? AND payment_status = 'PAID'
    """, (bus_id,)).fetchall()
    conn.close()

    booked = set()
    for r in rows:
        seats_str = r["seat_numbers"]
        if seats_str:
            for s in seats_str.split(","):
                s = s.strip()
                if s.isdigit():
                    booked.add(int(s))
    return booked


def calculate_bus_occupancy(bus_row):
    """Return dict with booked_seats, available_seats, occupancy_percent."""
    bus_id = bus_row["id"]
    total_seats = bus_row["total_seats"]
    booked = len(get_booked_seats(bus_id))
    available = max(total_seats - booked, 0)
    occ = (booked / total_seats * 100) if total_seats > 0 else 0
    return {
        "booked_seats": booked,
        "available_seats": available,
        "occupancy_percent": round(occ, 1),
    }


# ----------------------- ROUTES ----------------------- #

# folder for NFC payment QRs
NFC_QR_FOLDER = os.path.join("static", "nfc_qr")
os.makedirs(NFC_QR_FOLDER, exist_ok=True)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()

        if not all([name, email, password]):
            flash("Please fill all fields.", "danger")
            return render_template("register.html")

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO users (name, email, password, wallet_balance)
                VALUES (?, ?, ?, ?)
            """, (name, email, password, 2000.0))
            conn.commit()
        except sqlite3.IntegrityError:
            conn.close()
            flash("Email already registered.", "danger")
            return render_template("register.html")

        user_id = cur.lastrowid
        conn.close()

        session["user_id"] = user_id
        session["user_name"] = name
        flash("Account created. You have ₹2000 in your wallet.", "success")
        return redirect(url_for("home"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()

        conn = get_db_connection()
        user = conn.execute("""
            SELECT * FROM users WHERE email = ? AND password = ?
        """, (email, password)).fetchone()
        conn.close()

        if not user:
            flash("Invalid email or password.", "danger")
            return render_template("login.html")

        session["user_id"] = user["id"]
        session["user_name"] = user["name"]
        flash("Logged in successfully.", "success")
        return redirect(url_for("home"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("home"))

@app.route("/nfc_qr/<int:ticket_id>")
def nfc_qr(ticket_id):
    """
    Laptop-side page:
    - shows a QR that encodes the mobile payment URL
    - polls backend to know when payment is completed
    """
    conn = get_db_connection()
    ticket = conn.execute("""
        SELECT t.*, b.operator, b.from_city, b.to_city, b.departure, b.arrival
        FROM tickets t
        JOIN buses b ON t.bus_id = b.id
        WHERE t.id = ?
    """, (ticket_id,)).fetchone()
    conn.close()

    if not ticket:
        flash("Ticket not found.", "danger")
        return redirect(url_for("home"))

    # full mobile URL (phone opens this)
    pay_url = url_for("nfc_pay", ticket_id=ticket_id, _external=True)

    # generate / reuse QR image
    qr_filename = f"nfc_qr_{ticket_id}.png"
    qr_path = os.path.join(NFC_QR_FOLDER, qr_filename)
    if not os.path.exists(qr_path):
        img = qrcode.make(pay_url)
        img.save(qr_path)

    return render_template(
        "nfc_qr.html",
        ticket=ticket,
        qr_filename=f"nfc_qr/{qr_filename}",
    )
@app.route("/api/payment_status/<int:ticket_id>")
def payment_status(ticket_id):
    """Return simple JSON: is this ticket paid yet? (for polling from laptop QR page)."""
    conn = get_db_connection()
    row = conn.execute(
        "SELECT payment_status, ticket_code FROM tickets WHERE id = ?",
        (ticket_id,)
    ).fetchone()
    conn.close()

    if not row:
        return jsonify({"exists": False}), 404

    return jsonify({
        "exists": True,
        "paid": row["payment_status"] == "PAID",
        "ticket_code": row["ticket_code"],
    })
@app.route("/nfc_pay/<int:ticket_id>", methods=["GET", "POST"])
def nfc_pay(ticket_id):
    """
    Phone-side page:
    - shows trip summary and a big 'Pay with NFC' button
    - POST marks ticket as PAID
    """
    conn = get_db_connection()
    ticket = conn.execute("""
        SELECT t.*, b.operator, b.from_city, b.to_city, b.departure, b.arrival
        FROM tickets t
        JOIN buses b ON t.bus_id = b.id
        WHERE t.id = ?
    """, (ticket_id,)).fetchone()

    if not ticket:
        conn.close()
        return "Ticket not found", 404

    if request.method == "POST":
        # already paid? just show success
        if ticket["payment_status"] != "PAID":
            payment_id = generate_payment_id()
            conn.execute("""
                UPDATE tickets
                SET payment_status = ?, payment_id = ?
                WHERE id = ?
            """, ("PAID", payment_id, ticket_id))
            conn.commit()

        conn.close()
        # Show simple success page
        return render_template("nfc_pay.html", ticket=ticket, paid=True)

    conn.close()
    # GET – show pay button
    return render_template("nfc_pay.html", ticket=ticket, paid=(ticket["payment_status"] == "PAID"))

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    """Admin login page."""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["is_admin"] = True
            flash("Welcome, admin.", "success")
            return redirect(url_for("admin"))

        flash("Invalid admin credentials.", "danger")
        return render_template("admin_login.html")

    # GET request: show login page
    return render_template("admin_login.html")

@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    flash("Admin logged out.", "info")
    return redirect(url_for("home"))

@app.route("/", methods=["GET", "POST"])
def home():
    """Home page with search form."""
    conn = get_db_connection()
    cities = conn.execute("""
        SELECT DISTINCT from_city AS city FROM buses
        UNION
        SELECT DISTINCT to_city AS city FROM buses
    """).fetchall()
    conn.close()

    # each row has key "city"
    city_list = sorted({c["city"] for c in cities})

    # Handle quick search redirect
    if request.method == "POST":
        from_city = request.form.get("from_city")
        to_city = request.form.get("to_city")
        travel_date = request.form.get("travel_date")  # YYYY-MM-DD

        return redirect(url_for(
            "buses",
            from_city=from_city or "",
            to_city=to_city or "",
            travel_date=travel_date or "",
        ))

    return render_template("home.html", cities=city_list)

@app.route("/buses")
def buses():
    """List buses that match search criteria + show occupancy."""
    from_city = request.args.get("from_city", "").strip()
    to_city = request.args.get("to_city", "").strip()
    travel_date = request.args.get("travel_date", "").strip()

    query = "SELECT * FROM buses WHERE 1=1"
    params = []

    if from_city:
        query += " AND from_city = ?"
        params.append(from_city)
    if to_city:
        query += " AND to_city = ?"
        params.append(to_city)
    if travel_date:
        # match date part of departure
        query += " AND date(departure) = ?"
        params.append(travel_date)

    conn = get_db_connection()
    buses_rows = conn.execute(query, params).fetchall()
    conn.close()

    buses_list = []
    for b in buses_rows:
        occ = calculate_bus_occupancy(b)
        buses_list.append({
            "id": b["id"],
            "operator": b["operator"],
            "from_city": b["from_city"],
            "to_city": b["to_city"],
            "departure": b["departure"],
            "arrival": b["arrival"],
            "price": b["price"],
            "bus_type": b["bus_type"],
            "total_seats": b["total_seats"],
            **occ
        })

    return render_template(
        "buses.html",
        buses=buses_list,
        from_city=from_city,
        to_city=to_city,
        travel_date=travel_date,
    )

@app.route("/api/wallet_pay/<ticket_code>", methods=["POST"])
def wallet_pay(ticket_code):
    conn = get_db_connection()

    # Load ticket + its owner + their wallet balance
    ticket = conn.execute("""
        SELECT t.*, u.wallet_balance, u.id AS user_id
        FROM tickets t
        JOIN users u ON t.user_id = u.id
        WHERE t.ticket_code = ?
    """, (ticket_code,)).fetchone()

    if not ticket:
        conn.close()
        return jsonify({"success": False, "reason": "Ticket not found"}), 404

    # If already paid, nothing to do
    if ticket["payment_status"] == "PAID":
        conn.close()
        return jsonify({"success": True, "paid": True}), 200

    remaining = float(ticket["remaining_amount"] or 0.0)

    # In case of old tickets or weird data, fall back
    if remaining <= 0:
        # treat as already paid
        conn.close()
        return jsonify({"success": True, "paid": True, "info": "no_remaining_amount"}), 200

    balance = float(ticket["wallet_balance"])

    if balance < remaining:
        conn.close()
        return jsonify({"success": False, "reason": "Insufficient Wallet Balance"}), 400

    # Deduct remaining from user's wallet and mark ticket as PAID
    conn.execute(
        "UPDATE users SET wallet_balance = wallet_balance - ? WHERE id = ?",
        (remaining, ticket["user_id"])
    )
    conn.execute(
        """
        UPDATE tickets
        SET payment_status = 'PAID',
            payment_id = ?,
            remaining_amount = 0
        WHERE ticket_code = ?
        """,
        (generate_payment_id(), ticket_code)
    )

    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "ticket_code": ticket_code,
        "status": "PAID",
        "deducted_amount": remaining
    }), 200

@app.route("/book/<int:bus_id>", methods=["GET", "POST"])
def book(bus_id):
    """Seat selection + passenger details (creates a PENDING ticket + takes deposit)."""

    # User must be logged in so we can charge their wallet later
    user_id = session.get("user_id")
    if not user_id:
        flash("Please log in before booking a ticket.", "warning")
        return redirect(url_for("login"))

    conn = get_db_connection()

    # Get bus
    bus = conn.execute("SELECT * FROM buses WHERE id = ?", (bus_id,)).fetchone()
    if not bus:
        conn.close()
        flash("Bus not found.", "danger")
        return redirect(url_for("home"))

    # Get logged-in user (for auto-filling name + email + wallet)
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    total_seats = bus["total_seats"]
    booked_seats = get_booked_seats(bus_id)

    if request.method == "POST":
        # Auto-take these from the logged-in account
        name = user["name"]
        email = user["email"]

        # Only phone comes from the form
        phone = request.form.get("phone", "").strip()
        selected_seats = request.form.get("selected_seats", "").strip()  # e.g. "1,2,3"

        # Basic validation
        if not phone or not selected_seats:
            flash("Please enter phone number and select at least one seat.", "danger")
            conn.close()
            return render_template(
                "book.html",
                bus=bus,
                user=user,
                booked_seats=booked_seats,
                total_seats=total_seats,
            )

        # Parse seats
        seats_list = []
        for s in selected_seats.split(","):
            s = s.strip()
            if s.isdigit():
                seats_list.append(int(s))

        if not seats_list:
            flash("Invalid seat selection.", "danger")
            conn.close()
            return render_template(
                "book.html",
                bus=bus,
                user=user,
                booked_seats=booked_seats,
                total_seats=total_seats,
            )

        # Check that none of the selected seats are already booked
        for s in seats_list:
            if s in booked_seats:
                flash(f"Seat {s} has already been booked. Please refresh and try again.", "danger")
                conn.close()
                return render_template(
                    "book.html",
                    bus=bus,
                    user=user,
                    booked_seats=booked_seats,
                    total_seats=total_seats,
                )

        quantity = len(seats_list)
        total_amount = quantity * bus["price"]

        # --- Deposit logic (15%) ---
        deposit_amount = round(total_amount * DEPOSIT_PERCENT, 2)
        remaining_amount = round(total_amount - deposit_amount, 2)

        # Safety checks
        if deposit_amount < 1:
            deposit_amount = 1.0  # minimum ₹1 deposit to avoid ₹0 bookings
            remaining_amount = total_amount - deposit_amount

        wallet_balance = float(user["wallet_balance"])

        if wallet_balance < deposit_amount:
            conn.close()
            flash(
                f"You need at least ₹{deposit_amount:.2f} in your wallet for the booking deposit.",
                "danger"
            )
            return render_template(
                "book.html",
                bus=bus,
                user=user,
                booked_seats=booked_seats,
                total_seats=total_seats,
            )

        # Deduct deposit from wallet now
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET wallet_balance = wallet_balance - ? WHERE id = ?",
            (deposit_amount, user_id),
        )

        ticket_code = generate_ticket_code()
        booked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        seats_str = ",".join(str(s) for s in sorted(seats_list))

        # Create ticket with deposit + remaining
        cur.execute(
            """
            INSERT INTO tickets (
                ticket_code,
                bus_id,
                user_id,
                passenger_name,
                passenger_email,
                passenger_phone,
                seat_numbers,
                quantity,
                total_amount,
                deposit_amount,
                remaining_amount,
                payment_status,
                booked_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticket_code,
                bus_id,
                user_id,
                name,
                email,
                phone,
                seats_str,
                quantity,
                total_amount,
                deposit_amount,
                remaining_amount,
                "PENDING",   # will be fully paid on scan / NFC
                booked_at,
            ),
        )
        ticket_id = cur.lastrowid
        conn.commit()
        conn.close()

        # store ticket_code in session so "My Bookings" can show it
        session.setdefault("ticket_codes", [])
        if ticket_code not in session["ticket_codes"]:
            session["ticket_codes"].append(ticket_code)

        flash(f"Booking deposit of ₹{deposit_amount:.2f} has been taken from your wallet.", "info")
        return redirect(url_for("checkout", ticket_id=ticket_id))

    # GET -> show seat selection page (with user data prefilled)
    conn.close()
    return render_template(
        "book.html",
        bus=bus,
        user=user,
        booked_seats=booked_seats,
        total_seats=total_seats,
    )

@app.route("/bookings")
def bookings():
    """Show bookings associated with this browser (using session ticket_codes)."""
    codes = session.get("ticket_codes", [])
    if not codes:
        return render_template("bookings.html", tickets=[])

    placeholders = ",".join(["?"] * len(codes))
    query = f"""
        SELECT t.*, b.operator, b.from_city, b.to_city, b.departure, b.arrival
        FROM tickets t
        JOIN buses b ON t.bus_id = b.id
        WHERE t.ticket_code IN ({placeholders})
        ORDER BY t.booked_at DESC
    """

    conn = get_db_connection()
    tickets_rows = conn.execute(query, codes).fetchall()
    conn.close()

    return render_template("bookings.html", tickets=tickets_rows)

@app.route("/checkout/<int:ticket_id>", methods=["GET", "POST"])
def checkout(ticket_id):
    """Payment simulation page – ticket stays PENDING, wallet is charged on scan."""
    conn = get_db_connection()
    ticket = conn.execute("""
        SELECT t.*, b.operator, b.from_city, b.to_city, b.departure, b.arrival, b.bus_type
        FROM tickets t
        JOIN buses b ON t.bus_id = b.id
        WHERE t.id = ?
    """, (ticket_id,)).fetchone()
    conn.close()

    if not ticket:
        flash("Ticket not found.", "danger")
        return redirect(url_for("home"))

    if request.method == "POST":
        # ❌ DO NOT mark as PAID here
        # Just go to ticket page; ticket is still PENDING
        flash("Ticket generated. Fare will be deducted from your wallet when scanned at the bus.", "info")
        return redirect(url_for("ticket", ticket_code=ticket["ticket_code"]))

    return render_template("checkout.html", ticket=ticket)

@app.route("/ticket/<ticket_code>")
def ticket(ticket_code):
    conn = get_db_connection()
    ticket = conn.execute("""
        SELECT t.*, b.operator, b.from_city, b.to_city, b.departure, b.arrival,
               b.bus_type, b.total_seats, b.price
        FROM tickets t
        JOIN buses b ON t.bus_id = b.id
        WHERE t.ticket_code = ?
    """, (ticket_code,)).fetchone()
    conn.close()

    if not ticket:
        flash("Ticket not found.", "danger")
        return redirect(url_for("home"))

    seats_list = [s.strip() for s in ticket["seat_numbers"].split(",") if s.strip()]

    # --- QR code generation ---
    if not os.path.exists(QR_FOLDER):
        os.makedirs(QR_FOLDER, exist_ok=True)

    # filename relative to static/ (for url_for)
    qr_filename = f"qr/{ticket['ticket_code']}.png"
    qr_file_path = os.path.join("static", "qr", f"{ticket['ticket_code']}.png")

    if not os.path.exists(qr_file_path):
        # QR content: full ticket URL so scanning with phone opens the ticket page
        qr_content = request.url  # e.g. http://127.0.0.1:5000/ticket/SPG-XXXXYYYY
        img = qrcode.make(qr_content)
        img.save(qr_file_path)

    return render_template(
        "ticket.html",
        ticket=ticket,
        seats_list=seats_list,
        qr_filename=qr_filename,  # pass QR path to template
    )

@app.route("/admin")
def admin():
    """Simple admin dashboard showing all buses with occupancy."""
    if not session.get("is_admin"):
        flash("Please log in as admin.", "warning")
        return redirect(url_for("admin_login"))

    conn = get_db_connection()
    buses_rows = conn.execute("SELECT * FROM buses ORDER BY departure").fetchall()
    conn.close()

    buses_list = []
    for b in buses_rows:
        occ = calculate_bus_occupancy(b)
        buses_list.append({
            "id": b["id"],
            "operator": b["operator"],
            "from_city": b["from_city"],
            "to_city": b["to_city"],
            "departure": b["departure"],
            "arrival": b["arrival"],
            "price": b["price"],
            "bus_type": b["bus_type"],
            "total_seats": b["total_seats"],
            **occ
        })

    return render_template("admin.html", buses=buses_list)

@app.route("/admin/bus/new", methods=["GET", "POST"])
def admin_bus_new():
    if not session.get("is_admin"):
        flash("Please log in as admin.", "warning")
        return redirect(url_for("admin_login"))

    if request.method == "POST":
        operator = request.form.get("operator", "").strip()
        from_city = request.form.get("from_city", "").strip()
        to_city = request.form.get("to_city", "").strip()
        departure = request.form.get("departure", "").strip()
        arrival = request.form.get("arrival", "").strip()
        price = request.form.get("price", "").strip()
        total_seats = request.form.get("total_seats", "").strip()
        bus_type = request.form.get("bus_type", "").strip()

        if not all([operator, from_city, to_city, departure, arrival, price, total_seats, bus_type]):
            flash("Please fill all fields.", "danger")
            return render_template("admin_bus_form.html", bus=None, mode="new")

        conn = get_db_connection()
        conn.execute("""
            INSERT INTO buses (operator, from_city, to_city, departure, arrival,
                               price, total_seats, bus_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (operator, from_city, to_city, departure, arrival,
              float(price), int(total_seats), bus_type))
        conn.commit()
        conn.close()

        flash("Bus created successfully.", "success")
        return redirect(url_for("admin"))

    return render_template("admin_bus_form.html", bus=None, mode="new")


@app.route("/admin/bus/<int:bus_id>/edit", methods=["GET", "POST"])
def admin_bus_edit(bus_id):
    if not session.get("is_admin"):
        flash("Please log in as admin.", "warning")
        return redirect(url_for("admin_login"))

    conn = get_db_connection()
    bus = conn.execute("SELECT * FROM buses WHERE id = ?", (bus_id,)).fetchone()

    if not bus:
        conn.close()
        flash("Bus not found.", "danger")
        return redirect(url_for("admin"))

    if request.method == "POST":
        operator = request.form.get("operator", "").strip()
        from_city = request.form.get("from_city", "").strip()
        to_city = request.form.get("to_city", "").strip()
        departure = request.form.get("departure", "").strip()
        arrival = request.form.get("arrival", "").strip()
        price = request.form.get("price", "").strip()
        total_seats = request.form.get("total_seats", "").strip()
        bus_type = request.form.get("bus_type", "").strip()

        if not all([operator, from_city, to_city, departure, arrival, price, total_seats, bus_type]):
            flash("Please fill all fields.", "danger")
            conn.close()
            return render_template("admin_bus_form.html", bus=bus, mode="edit")

        conn.execute("""
            UPDATE buses
            SET operator = ?, from_city = ?, to_city = ?, departure = ?, arrival = ?,
                price = ?, total_seats = ?, bus_type = ?
            WHERE id = ?
        """, (operator, from_city, to_city, departure, arrival,
              float(price), int(total_seats), bus_type, bus_id))
        conn.commit()
        conn.close()

        flash("Bus updated successfully.", "success")
        return redirect(url_for("admin"))

    conn.close()
    return render_template("admin_bus_form.html", bus=bus, mode="edit")


@app.route("/admin/bus/<int:bus_id>/delete", methods=["POST"])
def admin_bus_delete(bus_id):
    if not session.get("is_admin"):
        flash("Please log in as admin.", "warning")
        return redirect(url_for("admin_login"))

    conn = get_db_connection()
    conn.execute("DELETE FROM buses WHERE id = ?", (bus_id,))
    conn.commit()
    conn.close()

    flash("Bus deleted.", "info")
    return redirect(url_for("admin"))


@app.route("/admin/tickets")
def admin_tickets():
    if not session.get("is_admin"):
        flash("Please log in as admin.", "warning")
        return redirect(url_for("admin_login"))

    conn = get_db_connection()
    rows = conn.execute("""
        SELECT t.*, b.operator, b.from_city, b.to_city, b.departure, b.arrival, b.bus_type
        FROM tickets t
        JOIN buses b ON t.bus_id = b.id
        ORDER BY t.booked_at DESC
    """).fetchall()
    conn.close()

    return render_template("admin_tickets.html", tickets=rows)


@app.route("/admin/ticket/<int:ticket_id>/delete", methods=["POST"])
def admin_ticket_delete(ticket_id):
    if not session.get("is_admin"):
        flash("Please log in as admin.", "warning")
        return redirect(url_for("admin_login"))

    conn = get_db_connection()
    conn.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
    conn.commit()
    conn.close()

    flash("Ticket deleted.", "info")
    return redirect(url_for("admin_tickets"))

@app.route("/api/tap_pay/<ticket_code>", methods=["GET", "POST"])
def tap_pay(ticket_code):
    """JSON API for real NFC / mobile app."""
    data, status = process_tap(ticket_code)
    return jsonify(data), status

@app.route("/simulate_nfc/<int:ticket_id>")
def simulate_nfc(ticket_id):
    """Simulated NFC payment — no ticket code required."""
    conn = get_db_connection()
    ticket = conn.execute("""
        SELECT * FROM tickets WHERE id = ?
    """, (ticket_id,)).fetchone()

    if not ticket:
        conn.close()
        flash("Ticket not found.", "danger")
        return redirect(url_for("home"))

    # Simulate successful NFC payment
    payment_id = generate_payment_id()
    conn.execute("""
        UPDATE tickets
        SET payment_status = 'PAID', payment_id = ?
        WHERE id = ?
    """, (payment_id, ticket_id))
    conn.commit()
    conn.close()

    flash("NFC payment successful!", "success")

    # Redirect to final ticket page
    return redirect(url_for("ticket", ticket_code=ticket["ticket_code"]))

@app.route("/fake_tap/<ticket_code>")
def fake_tap(ticket_code):
    """Browser-friendly tap simulator – shows a nice page instead of raw JSON."""
    data, status = process_tap(ticket_code)

    if not data["success"]:
        flash(f"NFC tap failed: {data.get('reason', 'Unknown error')}", "danger")
        return redirect(url_for("simulate_nfc"))

    return render_template("nfc_result.html", result=data)
@app.route("/pay_nfc/<ticket_id>")
def pay_nfc(ticket_id):
    """Redirect to the NFC simulation screen."""
    return render_template("simulate_nfc.html", ticket_id=ticket_id)


@app.route("/simulate_nfc_process/<int:ticket_id>")
def simulate_nfc_process(ticket_id):
    """
    Simulate NFC payment and deduct from the logged-in user's wallet.

    This is the same logic as scanning with the hardware scanner,
    but triggered from the fancy NFC animation page.
    """
    import time
    time.sleep(2)  # simulate NFC scan delay

    conn = get_db_connection()

    # Load ticket + its owner + wallet balance
    ticket = conn.execute("""
        SELECT t.*, u.wallet_balance, u.id AS user_id
        FROM tickets t
        JOIN users u ON t.user_id = u.id
        WHERE t.id = ?
    """, (ticket_id,)).fetchone()

    if not ticket:
        conn.close()
        flash("Ticket not found.", "danger")
        return redirect(url_for("home"))

    # If already paid, just go to ticket page
    if ticket["payment_status"] == "PAID":
        code = ticket["ticket_code"]
        conn.close()
        flash("This ticket has already been paid.", "info")
        return redirect(url_for("ticket", ticket_code=code))

    fare = ticket["total_amount"]
    balance = ticket["wallet_balance"]

    # Not enough money in wallet
    if balance < fare:
        conn.close()
        flash("Insufficient wallet balance for NFC payment.", "danger")
        return redirect(url_for("checkout", ticket_id=ticket_id))

    # Deduct from wallet
    conn.execute(
        "UPDATE users SET wallet_balance = wallet_balance - ? WHERE id = ?",
        (fare, ticket["user_id"])
    )

    # Mark ticket as paid
    conn.execute("""
        UPDATE tickets
        SET payment_status = 'PAID',
            payment_id = ?
        WHERE id = ?
    """, (generate_payment_id(), ticket_id))

    conn.commit()
    code = ticket["ticket_code"]
    conn.close()

    flash(f"NFC Payment Successful! ₹{fare:.2f} deducted from wallet.", "success")
    return redirect(url_for("ticket", ticket_code=code))

@app.route("/api/validate/<ticket_code>")
def api_validate(ticket_code):
    """API for hardware/scanner to validate a ticket by its code."""
    conn = get_db_connection()
    ticket = conn.execute("""
        SELECT t.*, b.operator, b.from_city, b.to_city, b.departure, b.arrival
        FROM tickets t
        JOIN buses b ON t.bus_id = b.id
        WHERE t.ticket_code = ?
    """, (ticket_code,)).fetchone()
    conn.close()

    if not ticket:
        return jsonify({"valid": False, "reason": "Ticket Not Found"}), 404

    if ticket["payment_status"] != "PAID":
        return jsonify({"valid": False, "reason": "unpaid"}), 400

    # if later you add a 'boarded' column, you can handle it here
    return jsonify({
        "valid": True,
        "ticket_code": ticket["ticket_code"],
        "bus_id": ticket["bus_id"],
        "passenger": ticket["passenger_name"],
        "seat_numbers": ticket["seat_numbers"],
        "status": ticket["payment_status"],
    })

if __name__ == "__main__":
    if not os.path.exists(DB_NAME):
        init_db()
    else:
        init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
