import os, sqlite3, secrets
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "smartbillz.db")
app = Flask(__name__)
app.secret_key = os.environ.get("SMARTBILLZ_SECRET", secrets.token_hex(24))

BAKERY_DEFAULTS = [
("Bread",40,45,10),("Bun",35,30,10),("Cake",12,450,3),("Biscuits",50,25,10),
("Puffs",25,30,8),("Cookies",30,60,8),("Donut",20,45,5),("Muffin",20,50,5)
]
JUICE_DEFAULTS = [
("Orange Juice",30,80,8),("Apple Juice",25,90,8),("Mango Juice",35,100,8),
("Lime Juice",30,50,8),("Watermelon Juice",25,70,6),("Pineapple Juice",25,90,6),
("Mixed Fruit Juice",20,120,5),("Milkshake",20,130,5)
]

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con

def init_db():
    con=db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS owners(
      id INTEGER PRIMARY KEY AUTOINCREMENT,email TEXT UNIQUE NOT NULL,password TEXT NOT NULL,created_at TEXT NOT NULL,last_shop_id INTEGER,session_version INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS shops(
      id INTEGER PRIMARY KEY AUTOINCREMENT,owner_id INTEGER NOT NULL,name TEXT NOT NULL,type TEXT NOT NULL,
      email TEXT NOT NULL,location TEXT,phone TEXT,branch_enabled INTEGER DEFAULT 0,
      branch_name TEXT,created_at TEXT NOT NULL,FOREIGN KEY(owner_id) REFERENCES owners(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS products(
      id INTEGER PRIMARY KEY AUTOINCREMENT,shop_id INTEGER NOT NULL,name TEXT NOT NULL,
      stock INTEGER DEFAULT 0,price REAL DEFAULT 0,low_stock INTEGER DEFAULT 5,
      category TEXT,created_at TEXT NOT NULL,FOREIGN KEY(shop_id) REFERENCES shops(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS sales(
      id INTEGER PRIMARY KEY AUTOINCREMENT,shop_id INTEGER NOT NULL,total REAL NOT NULL,
      discount REAL DEFAULT 0,payment TEXT NOT NULL,created_at TEXT NOT NULL,
      FOREIGN KEY(shop_id) REFERENCES shops(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS sale_items(
      id INTEGER PRIMARY KEY AUTOINCREMENT,sale_id INTEGER NOT NULL,product_id INTEGER NOT NULL,
      quantity INTEGER NOT NULL,price REAL NOT NULL,FOREIGN KEY(sale_id) REFERENCES sales(id) ON DELETE CASCADE,
      FOREIGN KEY(product_id) REFERENCES products(id));
    CREATE TABLE IF NOT EXISTS notifications(
      id INTEGER PRIMARY KEY AUTOINCREMENT,shop_id INTEGER NOT NULL,title TEXT NOT NULL,
      message TEXT NOT NULL,kind TEXT DEFAULT 'info',is_read INTEGER DEFAULT 0,created_at TEXT NOT NULL,
      FOREIGN KEY(shop_id) REFERENCES shops(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS shop_settings(
      shop_id INTEGER PRIMARY KEY, theme TEXT DEFAULT 'light', compact_mode INTEGER DEFAULT 0,
      notify_all INTEGER DEFAULT 1, stock_alerts INTEGER DEFAULT 1, low_stock_alerts INTEGER DEFAULT 1,
      payment_notifications INTEGER DEFAULT 1, report_notifications INTEGER DEFAULT 1, analytics_notifications INTEGER DEFAULT 1,
      product_notifications INTEGER DEFAULT 1, notification_sound INTEGER DEFAULT 0,
      low_stock_threshold INTEGER DEFAULT 5, critical_stock_threshold INTEGER DEFAULT 2,
      auto_stock_deduction INTEGER DEFAULT 1, allow_negative_stock INTEGER DEFAULT 0,
      default_payment TEXT DEFAULT 'Cash', discount_type TEXT DEFAULT 'amount', tax_enabled INTEGER DEFAULT 0, tax_rate REAL DEFAULT 0,
      invoice_prefix TEXT DEFAULT 'SB', show_shop_info INTEGER DEFAULT 1, auto_print INTEGER DEFAULT 0,
      report_daily INTEGER DEFAULT 1, report_weekly INTEGER DEFAULT 1, report_monthly INTEGER DEFAULT 1,
      language TEXT DEFAULT 'English', session_timeout INTEGER DEFAULT 60,
      business_hours TEXT DEFAULT '9:00 AM - 9:00 PM', updated_at TEXT,
      FOREIGN KEY(shop_id) REFERENCES shops(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS login_events(
      id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER NOT NULL, event TEXT NOT NULL, created_at TEXT NOT NULL,
      FOREIGN KEY(owner_id) REFERENCES owners(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS customers(
      id INTEGER PRIMARY KEY AUTOINCREMENT, shop_id INTEGER NOT NULL, name TEXT NOT NULL, phone TEXT, email TEXT, notes TEXT, created_at TEXT NOT NULL,
      FOREIGN KEY(shop_id) REFERENCES shops(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS expenses(
      id INTEGER PRIMARY KEY AUTOINCREMENT, shop_id INTEGER NOT NULL, title TEXT NOT NULL, amount REAL NOT NULL, category TEXT DEFAULT 'Other', notes TEXT, created_at TEXT NOT NULL,
      FOREIGN KEY(shop_id) REFERENCES shops(id) ON DELETE CASCADE);
    """)
    # Keep the last selected shop so logout/login can continue from the same shop.
    owner_cols = [r[1] for r in con.execute("PRAGMA table_info(owners)").fetchall()]
    if "last_shop_id" not in owner_cols:
        con.execute("ALTER TABLE owners ADD COLUMN last_shop_id INTEGER")
        con.execute("UPDATE owners SET last_shop_id=(SELECT id FROM shops WHERE shops.owner_id=owners.id ORDER BY id DESC LIMIT 1)")
    if "session_version" not in owner_cols:
        con.execute("ALTER TABLE owners ADD COLUMN session_version INTEGER DEFAULT 1")
        con.execute("UPDATE owners SET session_version=1 WHERE session_version IS NULL")
    sales_cols = [r[1] for r in con.execute("PRAGMA table_info(sales)").fetchall()]
    if "customer_id" not in sales_cols:
        con.execute("ALTER TABLE sales ADD COLUMN customer_id INTEGER")

    # Backward-compatible notification category migration.
    cols = [r[1] for r in con.execute("PRAGMA table_info(notifications)").fetchall()]
    if "category" not in cols:
        con.execute("ALTER TABLE notifications ADD COLUMN category TEXT DEFAULT 'info'")
        con.execute("UPDATE notifications SET category=kind WHERE category IS NULL OR category='info'")
    con.commit(); con.close()

def owner():
    return session.get("owner_id")

def get_settings(shop_id):
    con=db(); row=con.execute("SELECT * FROM shop_settings WHERE shop_id=?",(shop_id,)).fetchone()
    if not row:
        con.execute("INSERT INTO shop_settings(shop_id,updated_at) VALUES(?,?)",(shop_id,datetime.now().isoformat(timespec="seconds"))); con.commit()
        row=con.execute("SELECT * FROM shop_settings WHERE shop_id=?",(shop_id,)).fetchone()
    con.close(); return row

def setting(shop_id, key, default=1):
    row=get_settings(shop_id); return row[key] if key in row.keys() else default

def login_required(f):
    @wraps(f)
    def wrap(*a,**kw):
        if not owner(): return redirect(url_for("login"))
        con=db(); o=con.execute("SELECT session_version FROM owners WHERE id=?",(owner(),)).fetchone(); con.close()
        if not o or int(session.get("session_version",0)) != int(o["session_version"]):
            session.clear(); flash("Your session has expired. Please log in again.","error"); return redirect(url_for("login"))
        return f(*a,**kw)
    return wrap

def get_shop():
    if not owner(): return None
    con=db()
    sid=session.get("shop_id")
    shop=con.execute("SELECT * FROM shops WHERE id=? AND owner_id=?",(sid,owner())).fetchone() if sid else None
    if not shop:
        shop=con.execute("SELECT * FROM shops WHERE owner_id=? ORDER BY id",(owner(),)).fetchone()
        if shop: session["shop_id"]=shop["id"]
    con.close(); return shop

def notify(shop_id,title,message,kind="info",category=None):
    category = category or kind
    colmap={"stock":"stock_alerts","payment":"payment_notifications","reports":"report_notifications","analytics":"analytics_notifications","add":"product_notifications"}
    if not setting(shop_id,"notify_all",1): return
    if category in colmap and not setting(shop_id,colmap[category],1): return
    con=db(); con.execute("INSERT INTO notifications(shop_id,title,message,kind,category,created_at) VALUES(?,?,?,?,?,?)",
      (shop_id,title,message,kind,category,datetime.now().isoformat(timespec="seconds"))); con.commit(); con.close()

def seed_shop(shop_id, typ):
    con=db()
    rows=BAKERY_DEFAULTS if typ=="Bakery" else JUICE_DEFAULTS
    for n,s,p,l in rows:
        con.execute("INSERT INTO products(shop_id,name,stock,price,low_stock,category,created_at) VALUES(?,?,?,?,?,?,?)",
                    (shop_id,n,s,p,l,typ,datetime.now().isoformat(timespec="seconds")))
    con.commit(); con.close()
    notify(shop_id,"Default inventory ready",f"{typ} starter products were added. You can edit or delete them.","success","add")

@app.route("/")
def index():
    return redirect(url_for("dashboard") if owner() else url_for("login"))

@app.route("/register",methods=["GET","POST"])
def register():
    if request.method=="POST":
        email=request.form["email"].strip().lower(); pw=request.form["password"]
        if len(pw)<6: flash("Password must contain at least 6 characters.","error")
        else:
            try:
                con=db(); cur=con.execute("INSERT INTO owners(email,password,created_at) VALUES(?,?,?)",
                    (email,generate_password_hash(pw),datetime.now().isoformat(timespec="seconds")))
                con.commit(); session["owner_id"]=cur.lastrowid; session["session_version"]=1; con.close()
                # First login: no shop exists yet, so go to shop registration.
                return redirect(url_for("shops"))
            except sqlite3.IntegrityError: flash("Email already registered.","error")
    return render_template("auth.html",mode="register")

@app.route("/login",methods=["GET","POST"])
def login():
    if request.method=="POST":
        con=db(); o=con.execute("SELECT * FROM owners WHERE email=?",(request.form["email"].strip().lower(),)).fetchone()
        if o and check_password_hash(o["password"],request.form["password"]):
            session["owner_id"]=o["id"]
            session["session_version"]=o["session_version"] or 1
            con.execute("INSERT INTO login_events(owner_id,event,created_at) VALUES(?,?,?)",(o["id"],"login",datetime.now().isoformat(timespec="seconds")))
            # Restore the exact shop the owner was using before logout.
            sid=o["last_shop_id"]
            if sid and con.execute("SELECT 1 FROM shops WHERE id=? AND owner_id=?",(sid,o["id"])).fetchone():
                session["shop_id"]=sid
                ss=con.execute("SELECT theme,compact_mode FROM shop_settings WHERE shop_id=?",(sid,)).fetchone()
                session["theme_class"]="dark" if ss and ss["theme"]=="dark" else ""
                session["compact"]=bool(ss["compact_mode"]) if ss else False
                con.close(); return redirect(url_for("dashboard"))
            first=con.execute("SELECT id FROM shops WHERE owner_id=? ORDER BY id LIMIT 1",(o["id"],)).fetchone()
            if first:
                session["shop_id"]=first["id"]
                ss=con.execute("SELECT theme,compact_mode FROM shop_settings WHERE shop_id=?",(first["id"],)).fetchone()
                session["theme_class"]="dark" if ss and ss["theme"]=="dark" else ""
                session["compact"]=bool(ss["compact_mode"]) if ss else False
            con.close(); return redirect(url_for("shops"))
        flash("Invalid email or password.","error")
    return render_template("auth.html",mode="login")

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))

@app.route("/shops",methods=["GET","POST"])
@login_required
def shops():
    if request.method=="POST":
        name=request.form["name"].strip(); typ=request.form["type"]; email=request.form["shop_email"].strip().lower()
        branch=1 if request.form.get("branch_enabled") else 0
        con=db(); cur=con.execute("""INSERT INTO shops(owner_id,name,type,email,location,phone,branch_enabled,branch_name,created_at)
          VALUES(?,?,?,?,?,?,?,?,?)""",(owner(),name,typ,email,request.form.get("location",""),request.form.get("phone",""),branch,request.form.get("branch_name",""),datetime.now().isoformat(timespec="seconds")))
        sid=cur.lastrowid; con.commit(); con.close(); seed_shop(sid,typ)
        session["shop_id"]=sid
        con=db(); con.execute("UPDATE owners SET last_shop_id=? WHERE id=?",(sid,owner())); con.commit(); con.close()
        notify(sid,"Shop registered",f"{name} ({typ}) was registered successfully.","success","add")
        return redirect(url_for("dashboard"))
    con=db(); rows=con.execute("SELECT * FROM shops WHERE owner_id=? ORDER BY id",(owner(),)).fetchall(); con.close()
    return render_template("shops.html",shops=rows)

@app.route("/switch/<int:sid>")
@login_required
def switch_shop(sid):
    con=db(); ok=con.execute("SELECT id FROM shops WHERE id=? AND owner_id=?",(sid,owner())).fetchone()
    if ok:
        session["shop_id"]=sid
        con.execute("UPDATE owners SET last_shop_id=? WHERE id=?",(sid,owner()))
        con.commit()
    con.close()
    return redirect(url_for("dashboard"))

@app.route("/dashboard")
@login_required
def dashboard():
    shop=get_shop()
    if not shop: return redirect(url_for("shops"))
    con=db(); sid=shop["id"]; today=datetime.now().strftime("%Y-%m-%d")
    total=con.execute("SELECT COUNT(*) c FROM products WHERE shop_id=?",(sid,)).fetchone()["c"]
    stock=con.execute("SELECT COALESCE(SUM(stock),0) s FROM products WHERE shop_id=?",(sid,)).fetchone()["s"]
    low=con.execute("SELECT COUNT(*) c FROM products WHERE shop_id=? AND stock<=low_stock",(sid,)).fetchone()["c"]
    turnover=con.execute("SELECT COALESCE(SUM(total),0) s FROM sales WHERE shop_id=? AND created_at LIKE ?",(sid,today+"%")).fetchone()["s"]
    expenses=con.execute("SELECT COALESCE(SUM(amount),0) s FROM expenses WHERE shop_id=? AND created_at LIKE ?",(sid,today+"%")).fetchone()["s"]
    bills=con.execute("SELECT COUNT(*) c FROM sales WHERE shop_id=? AND created_at LIKE ?",(sid,today+"%")).fetchone()["c"]
    con.close(); return render_template("dashboard.html",shop=shop,total=total,stock=stock,low=low,turnover=turnover,expenses=expenses,profit=turnover-expenses,bills=bills)

@app.route("/products",methods=["GET","POST"])
@login_required
def products():
    shop=get_shop()
    con=db()
    if request.method=="POST":
        pid=request.form.get("id")
        data=(request.form["name"].strip(),int(request.form["stock"]),float(request.form["price"]),int(request.form.get("low_stock",5)),request.form.get("category","General"))
        if pid:
            before=con.execute("SELECT * FROM products WHERE id=? AND shop_id=?",(int(pid),shop["id"])).fetchone()
            con.execute("UPDATE products SET name=?,stock=?,price=?,low_stock=?,category=? WHERE id=? AND shop_id=?",data+(int(pid),shop["id"]))
            con.commit(); con.close()
            notify(shop["id"],"Item updated",f'{data[0]} updated. Current stock: {data[1]}. Price: ₹{data[2]:.2f}.',"info","add")
            if before and before["stock"] != data[1]:
                notify(shop["id"],"Stock manually updated",f'{data[0]} stock changed from {before["stock"]} to {data[1]}. Current stock is {data[1]}.',"info","stock")
                if data[1] <= data[3]:
                    notify(shop["id"],"Low stock alert",f'{data[0]} is low. Current stock: {data[1]}; limit: {data[3]}.',"warning","stock")
        else:
            cur=con.execute("INSERT INTO products(shop_id,name,stock,price,low_stock,category,created_at) VALUES(?,?,?,?,?,?,?)",(shop["id"],*data,datetime.now().isoformat(timespec="seconds")))
            con.commit(); con.close()
            notify(shop["id"],"Item added",f'{data[0]} added with stock {data[1]} at ₹{data[2]:.2f}.',"success","add")
        return redirect(url_for("products"))
    rows=con.execute("SELECT * FROM products WHERE shop_id=? ORDER BY name",(shop["id"],)).fetchall()
    lowrows=con.execute("SELECT * FROM products WHERE shop_id=? AND stock<=low_stock ORDER BY stock",(shop["id"],)).fetchall()
    con.close()
    return render_template("products.html",shop=shop,products=rows,low=lowrows)

@app.route("/products/delete/<int:pid>")
@login_required
def delete_product(pid):
    shop=get_shop(); con=db(); product=con.execute("SELECT name FROM products WHERE id=? AND shop_id=?",(pid,shop["id"])).fetchone(); con.execute("DELETE FROM products WHERE id=? AND shop_id=?",(pid,shop["id"])); con.commit(); con.close()
    if product: notify(shop["id"],"Item deleted",f'{product["name"]} was removed from the inventory.',"info","add")
    return redirect(url_for("products"))

@app.route("/stock")
@login_required
def stock():
    shop=get_shop(); con=db(); rows=con.execute("SELECT * FROM products WHERE shop_id=? ORDER BY stock ASC",(shop["id"],)).fetchall(); con.close()
    return render_template("stock.html",shop=shop,products=rows)

@app.route("/billing")
@login_required
def billing():
    shop=get_shop(); con=db(); products=con.execute("SELECT * FROM products WHERE shop_id=? AND stock>0 ORDER BY name",(shop["id"],)).fetchall(); customers=con.execute("SELECT * FROM customers WHERE shop_id=? ORDER BY name",(shop["id"],)).fetchall(); con.close()
    return render_template("billing.html",shop=shop,products=products,customers=customers,settings=get_settings(shop["id"]))

@app.route("/api/bill",methods=["POST"])
@login_required
def api_bill():
    shop=get_shop(); sid=shop["id"]
    try:
        data=request.get_json(silent=True) or request.form.to_dict()
        items=data.get("items",[])
        if isinstance(items,str):
            import json
            items=json.loads(items or "[]")
        if not isinstance(items,list) or not items:
            return jsonify(ok=False,error="Please add at least one product to the bill."),400
        settings=get_settings(sid)
        try: discount_input=float(data.get("discount",0) or 0)
        except (TypeError,ValueError): discount_input=0
        payment=str(data.get("payment") or settings["default_payment"] or "Cash").strip()
        customer_id=data.get("customer_id") or None
        if customer_id:
            try: customer_id=int(customer_id)
            except (TypeError,ValueError): customer_id=None
        con=db()
        validated=[]
        subtotal=0.0
        auto_deduct=bool(settings["auto_stock_deduction"])
        allow_negative=bool(settings["allow_negative_stock"])
        try:
            con.execute("BEGIN")
            requested={}
            for it in items:
                pid=int(it.get("id")); q=int(it.get("qty",0))
                if q < 1:
                    raise ValueError("Invalid product quantity.")
                requested[pid]=requested.get(pid,0)+q
            for pid,q in requested.items():
                p=con.execute("SELECT * FROM products WHERE id=? AND shop_id=?",(pid,sid)).fetchone()
                if not p: raise ValueError("Product not found in this shop.")
                if not allow_negative and q>int(p["stock"]):
                    raise ValueError(f"Only {p['stock']} stock available for {p['name']}.")
                subtotal += float(p["price"])*q
                validated.append((p,q))
            discount = min(100.0,max(0.0,discount_input))*subtotal/100 if settings["discount_type"]=="percent" else min(subtotal,max(0.0,discount_input))
            net=max(0.0,subtotal-discount)
            tax_rate=float(settings["tax_rate"] or 0) if settings["tax_enabled"] else 0.0
            tax=net*tax_rate/100
            total=round(net+tax,2)
            if customer_id and not con.execute("SELECT 1 FROM customers WHERE id=? AND shop_id=?",(customer_id,sid)).fetchone(): customer_id=None
            now=datetime.now().isoformat(timespec="seconds")
            cur=con.execute("INSERT INTO sales(shop_id,total,discount,payment,created_at,customer_id) VALUES(?,?,?,?,?,?)",(sid,total,discount,payment,now,customer_id))
            sale_id=cur.lastrowid
            for p,q in validated:
                con.execute("INSERT INTO sale_items(sale_id,product_id,quantity,price) VALUES(?,?,?,?)",(sale_id,p["id"],q,p["price"]))
                if auto_deduct:
                    con.execute("UPDATE products SET stock=stock-? WHERE id=? AND shop_id=?",(q,p["id"],sid))
            con.commit()
        except Exception:
            con.rollback(); raise
        finally:
            con.close()
        notify(sid,"Payment completed",f"Bill #{sale_id} paid via {payment}. Total ₹{total:.2f}.","success","payment")
        for p,q in validated:
            new_stock=int(p["stock"])-q if auto_deduct else int(p["stock"])
            notify(sid,"Stock updated",f'{p["name"]}: {q} sold. Current stock is {new_stock}.',"info","stock")
            if new_stock <= int(p["low_stock"]) and setting(sid,"low_stock_alerts",1):
                notify(sid,"Low stock alert",f'{p["name"]} is low after Bill #{sale_id}. Current stock: {new_stock}; limit: {p["low_stock"]}.',"warning","stock")
        notify(sid,"Reports updated",f"Daily, weekly and monthly sales history updated after Bill #{sale_id}.","info","reports")
        notify(sid,"Analytics updated",f"Sales analytics refreshed after Bill #{sale_id}.","info","analytics")
        return jsonify(ok=True,sale_id=sale_id,total=total,subtotal=round(subtotal,2),discount=round(discount,2),tax=round(tax,2),bill_url=url_for("bill_receipt",sale_id=sale_id))
    except Exception as e:
        return jsonify(ok=False,error=str(e)),400

@app.route("/bill/<int:sale_id>")
@login_required
def bill_receipt(sale_id):
    shop=get_shop(); con=db()
    sale=con.execute("SELECT s.*,c.name customer_name,c.phone customer_phone FROM sales s LEFT JOIN customers c ON c.id=s.customer_id WHERE s.id=? AND s.shop_id=?",(sale_id,shop["id"])).fetchone()
    if not sale: con.close(); return "Bill not found",404
    items=con.execute("SELECT si.*,p.name FROM sale_items si JOIN products p ON p.id=si.product_id WHERE si.sale_id=?",(sale_id,)).fetchall()
    con.close()
    subtotal=sum(float(i["price"])*int(i["quantity"]) for i in items)
    return render_template("bill_receipt.html",shop=shop,sale=sale,items=items,subtotal=subtotal)

@app.route("/customers",methods=["GET","POST"])
@login_required
def customers():
    shop=get_shop(); sid=shop["id"]
    if request.method=="POST":
        name=request.form.get("name","").strip(); phone=request.form.get("phone","").strip(); email=request.form.get("email","").strip(); notes=request.form.get("notes","").strip()
        if not name: flash("Customer name is required.","error")
        else:
            con=db(); con.execute("INSERT INTO customers(shop_id,name,phone,email,notes,created_at) VALUES(?,?,?,?,?,?)",(sid,name,phone,email,notes,datetime.now().isoformat(timespec="seconds"))); con.commit(); con.close(); notify(sid,"Customer added",f"{name} was added to customer records.","success","add"); flash("Customer added successfully.","success")
        return redirect(url_for("customers"))
    con=db(); rows=con.execute("SELECT c.*,COUNT(s.id) bills,COALESCE(SUM(s.total),0) spent FROM customers c LEFT JOIN sales s ON s.customer_id=c.id WHERE c.shop_id=? GROUP BY c.id ORDER BY c.id DESC",(sid,)).fetchall(); con.close()
    return render_template("customers.html",shop=shop,customers=rows)

@app.route("/customers/delete/<int:cid>",methods=["POST"])
@login_required
def customer_delete(cid):
    shop=get_shop(); con=db(); con.execute("DELETE FROM customers WHERE id=? AND shop_id=?",(cid,shop["id"])); con.commit(); con.close(); flash("Customer removed.","success"); return redirect(url_for("customers"))

@app.route("/expenses",methods=["GET","POST"])
@login_required
def expenses():
    shop=get_shop(); sid=shop["id"]
    if request.method=="POST":
        title=request.form.get("title","").strip(); category=request.form.get("category","Other"); notes=request.form.get("notes","").strip()
        try: amount=max(0,float(request.form.get("amount",0) or 0))
        except ValueError: amount=0
        if not title or amount<=0: flash("Enter a valid expense title and amount.","error")
        else:
            con=db(); con.execute("INSERT INTO expenses(shop_id,title,amount,category,notes,created_at) VALUES(?,?,?,?,?,?)",(sid,title,amount,category,notes,datetime.now().isoformat(timespec="seconds"))); con.commit(); con.close(); notify(sid,"Expense recorded",f"{title}: ₹{amount:.2f} added.","info","reports"); flash("Expense recorded.","success")
        return redirect(url_for("expenses"))
    con=db(); rows=con.execute("SELECT * FROM expenses WHERE shop_id=? ORDER BY id DESC",(sid,)).fetchall(); total=con.execute("SELECT COALESCE(SUM(amount),0) a FROM expenses WHERE shop_id=?",(sid,)).fetchone()["a"]; today_exp=con.execute("SELECT COALESCE(SUM(amount),0) a FROM expenses WHERE shop_id=? AND date(created_at)=date(?)",(sid,datetime.now().isoformat())).fetchone()["a"]; con.close(); return render_template("expenses.html",shop=shop,expenses=rows,total=total,today_exp=today_exp)

@app.route("/expenses/delete/<int:eid>",methods=["POST"])
@login_required
def expense_delete(eid):
    shop=get_shop(); con=db(); con.execute("DELETE FROM expenses WHERE id=? AND shop_id=?",(eid,shop["id"])); con.commit(); con.close(); flash("Expense removed.","success"); return redirect(url_for("expenses"))

@app.route("/reports")
@login_required
def reports():
    shop=get_shop(); con=db(); sid=shop["id"]; now=datetime.now()
    periods={"Daily":now.strftime("%Y-%m-%d"),"Weekly":(now-timedelta(days=6)).strftime("%Y-%m-%d"),"Monthly":(now-timedelta(days=29)).strftime("%Y-%m-%d")}
    cards=[]
    for label,start_date in periods.items():
        where="date(created_at)=date(?)" if label=="Daily" else "date(created_at)>=date(?)"
        row=con.execute(f"SELECT COALESCE(SUM(total),0) turnover,COUNT(*) bills FROM sales WHERE shop_id=? AND {where}",(sid,start_date)).fetchone()
        exp=con.execute(f"SELECT COALESCE(SUM(amount),0) amount FROM expenses WHERE shop_id=? AND {where}",(sid,start_date)).fetchone()["amount"]
        cards.append((label,row["turnover"],row["bills"],exp,row["turnover"]-exp))
    history=con.execute("SELECT s.*,c.name customer_name FROM sales s LEFT JOIN customers c ON c.id=s.customer_id WHERE s.shop_id=? ORDER BY s.id DESC",(sid,)).fetchall()
    expense_total=con.execute("SELECT COALESCE(SUM(amount),0) a FROM expenses WHERE shop_id=?",(sid,)).fetchone()["a"]
    sales_total=con.execute("SELECT COALESCE(SUM(total),0) a FROM sales WHERE shop_id=?",(sid,)).fetchone()["a"]
    con.close(); return render_template("reports.html",shop=shop,cards=cards,history=history,expense_total=expense_total,sales_total=sales_total,profit=sales_total-expense_total)

@app.route("/reports/pdf/<period>")
@login_required
def report_pdf(period):
    shop=get_shop(); con=db(); now=datetime.now()
    days={"daily":0,"weekly":6,"monthly":29}.get(period.lower(),0); start=(now-timedelta(days=days)).strftime("%Y-%m-%d")
    rows=con.execute("SELECT * FROM sales WHERE shop_id=? AND date(created_at)>=date(?) ORDER BY created_at DESC",(shop["id"],start)).fetchall()
    total=sum(r["total"] for r in rows); con.close()
    path=os.path.join(BASE,f"SmartBillz_{period}_{shop['id']}.pdf")
    c=canvas.Canvas(path,pagesize=A4); c.setFont("Helvetica-Bold",18); c.drawString(45,800,"SmartBillz - Sales Report")
    c.setFont("Helvetica",11); c.drawString(45,780,f"Shop: {shop['name']} | Period: {period.title()}"); c.drawString(45,760,f"Turnover: Rs. {total:.2f} | Bills: {len(rows)}")
    y=725; c.setFont("Helvetica-Bold",10); c.drawString(45,y,"Bill"); c.drawString(100,y,"Date"); c.drawString(280,y,"Payment"); c.drawString(390,y,"Total"); y-=18
    c.setFont("Helvetica",9)
    for r in rows:
        if y<55: c.showPage(); y=800
        c.drawString(45,y,str(r["id"])); c.drawString(100,y,r["created_at"][:16]); c.drawString(280,y,r["payment"]); c.drawRightString(500,y,f"Rs. {r['total']:.2f}"); y-=16
    c.save(); return send_file(path,as_attachment=True,download_name=os.path.basename(path))

@app.route("/notifications")
@login_required
def notifications():
    shop=get_shop(); con=db(); rows=con.execute("SELECT * FROM notifications WHERE shop_id=? ORDER BY id DESC LIMIT 250",(shop["id"],)).fetchall(); con.close()
    groups={k:[] for k in ["stock","analytics","reports","payment","add"]}
    for n in rows:
        cat=n["category"] if "category" in n.keys() and n["category"] else n["kind"]
        if cat in groups: groups[cat].append(n)
    return render_template("notifications.html",shop=shop,notifications=rows,groups=groups)

@app.route("/analytics")
@login_required
def analytics():
    shop=get_shop(); con=db()
    top=con.execute("""SELECT p.name,SUM(si.quantity) qty,SUM(si.quantity*si.price) revenue
      FROM sale_items si JOIN sales s ON s.id=si.sale_id JOIN products p ON p.id=si.product_id
      WHERE s.shop_id=? GROUP BY p.id ORDER BY qty DESC LIMIT 8""",(shop["id"],)).fetchall()
    hourly=con.execute("""SELECT substr(created_at,12,2) hour,COUNT(*) bills FROM sales WHERE shop_id=? GROUP BY hour ORDER BY hour""",(shop["id"],)).fetchall()
    con.close(); return render_template("analytics.html",shop=shop,top=top,hourly=hourly)

@app.route("/profile")
@login_required
def profile():
    shop=get_shop(); return render_template("profile.html",shop=shop)

@app.route("/settings",methods=["GET","POST"])
@login_required
def settings():
    shop=get_shop(); sid=shop["id"]
    if request.method=="POST":
        def b(name): return 1 if request.form.get(name) else 0
        vals={
          "theme":request.form.get("theme","light"),"compact_mode":b("compact_mode"),"notify_all":b("notify_all"),
          "stock_alerts":b("stock_alerts"),"low_stock_alerts":b("low_stock_alerts"),"payment_notifications":b("payment_notifications"),
          "report_notifications":b("report_notifications"),"analytics_notifications":b("analytics_notifications"),"product_notifications":b("product_notifications"),
          "notification_sound":b("notification_sound"),"low_stock_threshold":max(0,int(request.form.get("low_stock_threshold",5))),
          "critical_stock_threshold":max(0,int(request.form.get("critical_stock_threshold",2))),"auto_stock_deduction":b("auto_stock_deduction"),
          "allow_negative_stock":b("allow_negative_stock"),"default_payment":request.form.get("default_payment","Cash"),
          "discount_type":request.form.get("discount_type","amount"),"tax_enabled":b("tax_enabled"),"tax_rate":max(0,float(request.form.get("tax_rate",0) or 0)),
          "invoice_prefix":request.form.get("invoice_prefix","SB").strip()[:10] or "SB","show_shop_info":b("show_shop_info"),"auto_print":b("auto_print"),
          "report_daily":b("report_daily"),"report_weekly":b("report_weekly"),"report_monthly":b("report_monthly"),
          "session_timeout":max(5,int(request.form.get("session_timeout",60))),
          "business_hours":request.form.get("business_hours","9:00 AM - 9:00 PM").strip()[:80]
        }
        session["theme_class"]="dark" if vals["theme"]=="dark" else ""; session["compact"]=bool(vals["compact_mode"])
        con=db(); sets=", ".join(f"{k}=?" for k in vals); con.execute(f"UPDATE shop_settings SET {sets},updated_at=? WHERE shop_id=?",tuple(vals.values())+(datetime.now().isoformat(timespec="seconds"),sid)); con.commit(); con.close()
        flash("Settings saved successfully.","success")
        return redirect(url_for("settings"))
    current=get_settings(sid)
    session["theme_class"]="dark" if current["theme"]=="dark" else ""
    session["compact"]=bool(current["compact_mode"])
    return render_template("settings.html",shop=shop,settings=current)

@app.route("/settings/clear-notifications",methods=["POST"])
@login_required
def clear_notifications():
    shop=get_shop(); con=db(); con.execute("DELETE FROM notifications WHERE shop_id=?",(shop["id"],)); con.commit(); con.close(); flash("Notification history cleared for this shop.","success"); return redirect(url_for("settings"))

@app.route("/settings/export")
@login_required
def export_data():
    import json
    shop=get_shop(); con=db(); data={}
    for table in ["shops","products","sales","sale_items","notifications","shop_settings","customers","expenses"]:
        rows=con.execute(f"SELECT * FROM {table} WHERE shop_id=?",(shop["id"],)).fetchall() if table not in ["shops"] else con.execute("SELECT * FROM shops WHERE id=?",(shop["id"],)).fetchall()
        data[table]=[dict(r) for r in rows]
    con.close(); path=os.path.join(BASE,f"SmartBillz_Backup_{shop['id']}.json"); open(path,"w",encoding="utf-8").write(json.dumps(data,ensure_ascii=False,indent=2)); return send_file(path,as_attachment=True,download_name=os.path.basename(path))

@app.route("/settings/security")
@login_required
def security_activity():
    con=db(); rows=con.execute("SELECT * FROM login_events WHERE owner_id=? ORDER BY id DESC LIMIT 30",(owner(),)).fetchall(); con.close(); return render_template("settings.html",shop=get_shop(),settings=get_settings(get_shop()["id"]),login_events=rows)

@app.route("/settings/logout-all",methods=["POST"])
@login_required
def logout_all():
    oid=owner(); con=db(); con.execute("UPDATE owners SET session_version=session_version+1 WHERE id=?",(oid,)); con.execute("INSERT INTO login_events(owner_id,event,created_at) VALUES(?,?,?)",(oid,"logout-all",datetime.now().isoformat(timespec="seconds"))); con.commit(); con.close(); session.clear(); flash("All active sessions were signed out. Please log in again.","success"); return redirect(url_for("login"))

init_db()
if __name__=="__main__":
    app.run(debug=True)
