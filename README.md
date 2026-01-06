# ScanPayGo 🚍💳

ScanPayGo is a smart bus ticket booking and validation system built as an academic mini project.  
It integrates wallet-based payments, QR/NFC ticket validation, deposit-based booking, refunds, and admin control into a single Flask web application.

## 📌 Features

### 👤 Passenger Features
- User registration & login
- Wallet system (₹2000 initial balance)
- Seat selection with live availability
- Deposit-based booking (15% charged at booking)
- Remaining fare deducted on QR/NFC scan
- QR code based ticket
- NFC-style payment simulation
- View all bookings
- Refund request for paid tickets
- Cancel unpaid tickets

### 🚌 Booking Logic
- 15% deposit charged during booking
- Remaining 85% deducted only when ticket is scanned
- Prevents seat blocking without commitment
- Wallet balance updates automatically

### 🔍 Ticket Validation
- Unique QR code per ticket
- Scanner validates ticket
- Wallet deducted automatically on scan
- Prevents double payment

### 🔁 Refund System
- Passengers can request refunds for PAID tickets
- Refund reason required
- Admin can approve or reject refunds
- Approved refunds are credited back to wallet
- Rejected refunds include admin feedback

### 🛠 Admin Features
- Secure admin login
- Add / edit / delete buses
- View all tickets
- Seat occupancy visualization
- Approve / reject refund requests
- Ticket management

## 🧱 Tech Stack
- Backend: Flask (Python)
- Database: SQLite
- Frontend: HTML, CSS, Bootstrap
- QR Generation: Python qrcode library
- Authentication: Flask Sessions
- Payments: Wallet simulation
- Validation: QR & NFC simulation

## ⚙️ Installation & Setup

1. Clone the repository  
git clone https://github.com/your-username/ScanPayGo.git  
cd ScanPayGo  

2. Create virtual environment (recommended)  
python -m venv venv  
source venv/bin/activate  
Windows: venv\Scripts\activate  

3. Install dependencies  
pip install -r requirements.txt  

4. Run the application  
Run "python app.py" & "python scanner.py" in different terminals

Application will be available at:  
http://127.0.0.1:5000

## 🔐 Default Credentials

Admin Login  
Username: admin  
Password: admin123  

Wallet  
New users start with ₹2000  
15% deposit charged at booking  
Remaining amount charged on scan

## 📖 Academic Use Case
This project demonstrates:
- Database design & normalization
- Transaction workflows
- Payment lifecycle handling
- QR/NFC validation logic
- Refund approval systems
- Admin role management

Suitable for:
- Mini Project
- Web Technology Lab
- Software Engineering Project
- Flask-based academic demos

## ⚠️ Important Notes
- tickets.db is created automatically at runtime
- QR & NFC images are generated dynamically
- These files should NOT be committed (handled via .gitignore)
- NFC and payments are simulated (no real hardware)

## 📜 License
This project is developed for educational purposes only.

## 🙌 Author
ScanPayGo  
Built as an academic mini project using Flask and SQLite.

## 👨‍💻 Developed for
Project Based Learning

⭐ If you find this project useful, feel free to star the repository!
