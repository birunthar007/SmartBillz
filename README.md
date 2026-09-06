# SmartBillz

Smart business management system for small shop owners.

## Stack
- Python 3
- Flask
- SQLite
- HTML5/CSS3
- Vanilla JavaScript
- Chart.js
- ReportLab
- Pillow

## Features
- Owner registration/login
- Multiple shops
- Bakery and Juice Shop profiles
- Optional branches
- Shop-isolated products and sales
- Bakery/Juice default inventory
- Add/edit/delete products
- Stock management and low-stock alerts
- Billing with manual/scanner-style product selection
- Discount and Cash/UPI/PhonePe/Online payment options
- Automatic stock deduction after completed payment
- Daily/weekly/monthly reports
- PDF report download
- Notifications
- Analytics with charts and business suggestions
- Profile and settings
- Responsive desktop/mobile UI

## Run on Windows
1. Install Python 3.10+.
2. Open Command Prompt/PowerShell in this folder.
3. Create environment:
   `python -m venv venv`
4. Activate:
   PowerShell: `venv\Scripts\Activate.ps1`
   CMD: `venv\Scripts\activate`
5. Install:
   `pip install -r requirements.txt`
6. Start:
   `python app.py`
7. Open:
   http://127.0.0.1:5000

The SQLite database is created automatically as `smartbillz.db`.

## Demo
You can register a new owner account. After registration, create one or more shops. Each shop has its own products, sales, notifications and reports.

## Persistent history and notifications
Notifications are stored in SQLite per shop and remain after logout/login. The Notifications page has five independent history cards (Stock, Analytics, Reports, Payment, Add +); each card shows only its own timestamped events and does not redirect to another module. Billing creates persistent payment, per-product stock/current-stock, low-stock, reports and analytics events.

Each authenticated page includes a Back button.


## Persistent business history
All owner, shop, product, sale, sale-item, and notification records are stored in `smartbillz.db`. Logout only clears the browser session; it does not delete business data. On the next login, SmartBillz restores the last selected shop and continues from its saved stock, bills, reports, analytics, and notification history. Notification history is stored by shop and category (Stock, Analytics, Reports, Payment, Add +).
