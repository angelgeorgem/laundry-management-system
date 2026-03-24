from flask import Flask, render_template, request, redirect, url_for, flash, session
import sqlite3
import datetime
import random
from functools import wraps
import os

# Get parent directory (project root)
basedir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = Flask(__name__, 
            template_folder=os.path.join(basedir, 'templates'),
            static_folder=os.path.join(basedir, 'static'))
app.secret_key = 'supersecretkey'

DB_NAME = os.path.join(basedir, 'laundry.db')

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    # Create Users Table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('owner', 'customer'))
        )
    ''')
    
    conn.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT UNIQUE NOT NULL,
            fabric_type TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            dirt_level TEXT NOT NULL,
            estimated_time INTEGER NOT NULL,
            status TEXT DEFAULT 'Processing',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            customer_id INTEGER,
            total_amount REAL DEFAULT 0.0,
            payment_status TEXT DEFAULT 'Pending',
            FOREIGN KEY (customer_id) REFERENCES users (id)
        )
    ''')
    
    # Create Payments Table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            payment_method TEXT NOT NULL,
            transaction_id TEXT UNIQUE,
            status TEXT DEFAULT 'Pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES orders (id)
        )
    ''')
    print("Payments table created/verified.")
    
    # Fix for existing databases: Check if customer_id exists, if not, add it.
    try:
        conn.execute("SELECT customer_id FROM orders LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating database: Adding customer_id column...")
        conn.execute("ALTER TABLE orders ADD COLUMN customer_id INTEGER")
        conn.commit()
    
    # Add payment-related columns if they don't exist
    try:
        conn.execute("SELECT total_amount FROM orders LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating database: Adding payment columns...")
        conn.execute("ALTER TABLE orders ADD COLUMN total_amount REAL DEFAULT 0.0")
        conn.execute("ALTER TABLE orders ADD COLUMN payment_status TEXT DEFAULT 'Pending'")
        conn.commit()

    # Add customer feedback column if missing
    try:
        conn.execute("SELECT customer_feedback FROM orders LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating database: Adding customer_feedback column...")
        conn.execute("ALTER TABLE orders ADD COLUMN customer_feedback TEXT")
        conn.commit()

    conn.commit()
    conn.close()

# Initialize DB on startup
with app.app_context():
    init_db()

# --- Helpers ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to access this page.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def calculate_time(fabric_type, quantity, dirt_level):
    base_time = 4 if fabric_type == 'Light' else 8
    dirt_adder = 2 if dirt_level == 'High' else 0
    return (base_time + dirt_adder) * int(quantity)

def calculate_amount(fabric_type, quantity, dirt_level):
    # Pricing in INR: ₹160 per light fabric item, ₹320 per heavy fabric item, +₹80 for high dirt level
    base_price = 160.0 if fabric_type == 'Light' else 320.0
    dirt_adder = 80.0 if dirt_level == 'High' else 0.0
    return (base_price + dirt_adder) * int(quantity)

def generate_token():
    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M")
    rand_num = random.randint(1000, 9999)
    return f"ORD{timestamp}{rand_num}"

# --- Routes ---

@app.route('/')
def home():
    if 'user_id' in session:
        if session['role'] == 'owner':
            return redirect(url_for('owner_home'))
        else:
            return redirect(url_for('order_page'))
    return render_template('landing.html')

@app.route('/owner-home')
@login_required
def owner_home():
    if session.get('role') != 'owner':
        flash('Access Denied: Owner only area.', 'error')
        return redirect(url_for('home'))

    conn = get_db_connection()
    orders = conn.execute("""
        SELECT orders.*, users.username as customer_name 
        FROM orders 
        LEFT JOIN users ON orders.customer_id = users.id 
        WHERE orders.status != 'Pending Payment'
        ORDER BY created_at DESC
    """).fetchall()
    conn.close()

    total_orders = len(orders)
    completed_orders = sum(1 for o in orders if o['status'] == 'Completed')
    pending_orders = sum(1 for o in orders if o['status'] == 'Pending')
    processing_orders = sum(1 for o in orders if o['status'] == 'Processing')
    ready_to_deliver = sum(1 for o in orders if o['status'] == 'Ready to Deliver')
    clothes_processing = sum(o['quantity'] for o in orders if o['status'] != 'Completed')

    recent_feedback = [
        {
            'token': o['token'],
            'customer_name': o['customer_name'] or 'Guest',
            'feedback': o['customer_feedback']
        }
        for o in orders
        if o['customer_feedback'] and o['customer_feedback'].strip()
    ][:5]

    stats = {
        'total_orders': total_orders,
        'pending_orders': pending_orders,
        'processing_orders': processing_orders,
        'ready_to_deliver': ready_to_deliver,
        'completed_orders': completed_orders,
        'clothes_processing': clothes_processing,
    }

    return render_template('owner_home.html', stats=stats, recent_feedback=recent_feedback)

@app.route('/order')
@login_required
def order_page():
    if session['role'] == 'owner':
        return redirect(url_for('owner_home'))
    return render_template('order.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        conn.close()
        
        if user and user['password'] == password:
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            flash('Logged in successfully!', 'success')
            return redirect(url_for('owner_home') if user['role'] == 'owner' else url_for('order_page'))
        else:
            flash('Invalid username or password', 'error')
            
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        role = request.form['role']
        
        try:
            conn = get_db_connection()
            conn.execute('INSERT INTO users (username, password, role) VALUES (?, ?, ?)',
                         (username, password, role))
            conn.commit()
            conn.close()
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Username already exists.', 'error')
            
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'success')
    return redirect(url_for('login'))

@app.route('/submit', methods=['POST'])
@login_required
def submit_order():
    if session['role'] == 'owner':
        return "Owners cannot place orders", 403

    # Support mixed orders (Light + Heavy) in one booking
    dirt_level = request.form['dirt_level']
    special_instructions = request.form.get('special_instructions', '')

    qty_light = int(request.form.get('quantity_light', 0) or 0)
    qty_heavy = int(request.form.get('quantity_heavy', 0) or 0)

    if qty_light <= 0 and qty_heavy <= 0:
        flash('Please add at least one item to place an order.', 'error')
        return redirect(url_for('order'))

    items = []
    if qty_light > 0:
        items.append(('Light', qty_light))
    if qty_heavy > 0:
        items.append(('Heavy', qty_heavy))

    # Use a simple descriptor for the order
    fabric_type = 'Mixed' if len(items) > 1 else items[0][0]
    quantity = sum(q for _, q in items)

    if special_instructions:
        special_instructions += ' | '
    special_instructions += ' + '.join([f"{q}×{ft}" for ft, q in items])

    estimated_time = sum(calculate_time(ft, q, dirt_level) for ft, q in items)
    total_amount = sum(calculate_amount(ft, q, dirt_level) for ft, q in items)
    token = generate_token()

    # Store order details in session (not in database yet - will be saved after payment)
    session['pending_order'] = {
        'token': token,
        'fabric_type': fabric_type,
        'quantity': quantity,
        'dirt_level': dirt_level,
        'estimated_time': estimated_time,
        'total_amount': total_amount,
        'special_instructions': special_instructions
    }
    session.modified = True
    
    flash(f"Order Placed! Token: {token}. Est. Time: {estimated_time} mins. Total: ₹{total_amount:.2f} (Pay to start processing)", 'success')
    return redirect(url_for('payment', token=token))

@app.route('/my-orders')
@login_required
def my_orders():
    if session['role'] == 'owner':
        return redirect(url_for('dashboard'))
        
    conn = get_db_connection()
    orders = conn.execute("SELECT * FROM orders WHERE customer_id = ? ORDER BY created_at DESC", 
                          (session['user_id'],)).fetchall()
    conn.close()
    return render_template('status.html', orders=orders)

@app.route('/dashboard')
@login_required
def dashboard():
    if session['role'] != 'owner':
        flash('Access Denied: Owner only area.', 'error')
        return redirect(url_for('home'))
        
    conn = get_db_connection()
    # Join users table to get customer names
    orders = conn.execute("""
        SELECT orders.*, users.username as customer_name 
        FROM orders 
        LEFT JOIN users ON orders.customer_id = users.id 
        ORDER BY created_at DESC
    """).fetchall()
    conn.close()

    # Dashboard statistics (for charts/visuals)
    total_orders = len(orders)
    completed_orders = sum(1 for o in orders if o['status'] == 'Completed')
    processing_orders = total_orders - completed_orders
    clothes_processing = sum(o['quantity'] for o in orders if o['status'] != 'Completed')
    clothes_total = sum(o['quantity'] for o in orders)

    recent_feedback = [
        {
            'token': o['token'],
            'customer_name': o['customer_name'] or 'Guest',
            'feedback': o['customer_feedback']
        }
        for o in orders
        if o['customer_feedback'] and o['customer_feedback'].strip()
    ][:5]

    stats = {
        'total_orders': total_orders,
        'completed_orders': completed_orders,
        'processing_orders': processing_orders,
        'clothes_processing': clothes_processing,
        'clothes_total': clothes_total,
    }

    return render_template('dashboard.html', orders=orders, stats=stats, recent_feedback=recent_feedback)

@app.route('/update/<token>')
@login_required
def update_status(token):
    if session['role'] != 'owner':
        return "Unauthorized", 403
        
    conn = get_db_connection()
    order = conn.execute("SELECT payment_status, status FROM orders WHERE token = ?", (token,)).fetchone()
    if not order:
        conn.close()
        flash('Order not found.', 'error')
        return redirect(url_for('dashboard'))

    if order['payment_status'] != 'Paid':
        conn.close()
        flash('Cannot proceed: payment not completed.', 'error')
        return redirect(url_for('dashboard'))

    if order['status'] == 'Completed':
        conn.close()
        flash('Order already completed.', 'info')
        return redirect(url_for('dashboard'))

    conn.execute("UPDATE orders SET status = 'Completed' WHERE token = ?", (token,))
    conn.commit()
    conn.close()
    flash('Order status updated.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/track', methods=['GET', 'POST'])
def track_order():
    order = None
    if request.method == 'POST':
        token = request.form['token'].strip()
        conn = get_db_connection()
        order = conn.execute("SELECT * FROM orders WHERE token = ?", (token,)).fetchone()
        conn.close()
        
        if not order:
            flash(f'Order with token "{token}" not found.', 'error')
            
    return render_template('track.html', order=order)

@app.route('/feedback/<token>', methods=['GET', 'POST'])
@login_required
def feedback(token):
    conn = get_db_connection()
    order = conn.execute("SELECT * FROM orders WHERE token = ? AND customer_id = ?",
                         (token, session['user_id'])).fetchone()

    if not order:
        conn.close()
        flash('Order not found or access denied.', 'error')
        return redirect(url_for('my_orders'))

    if order['status'] != 'Completed':
        conn.close()
        flash('Feedback can only be submitted after the order is completed.', 'error')
        return redirect(url_for('my_orders'))

    if request.method == 'POST':
        feedback_text = request.form.get('feedback', '').strip()
        conn.execute("UPDATE orders SET customer_feedback = ? WHERE id = ?", (feedback_text, order['id']))
        conn.commit()
        conn.close()
        flash('Thank you for your feedback!', 'success')
        return redirect(url_for('my_orders'))

    conn.close()
    return render_template('feedback.html', order=order)

@app.route('/payment/<token>')
@login_required
def payment(token):
    # Check if order is in session (new order) or in database (existing order being reprocessed)
    pending_order = session.get('pending_order')
    
    if pending_order and pending_order['token'] == token:
        # New order from session
        order = pending_order
        order['id'] = None  # Placeholder for ID
    else:
        # Existing order from database
        conn = get_db_connection()
        order = conn.execute("SELECT * FROM orders WHERE token = ? AND customer_id = ?",
                             (token, session['user_id'])).fetchone()
        conn.close()
        
        if not order:
            flash('Order not found or access denied.', 'error')
            return redirect(url_for('my_orders'))
        
        if order['payment_status'] == 'Paid':
            flash('This order has already been paid.', 'info')
            return redirect(url_for('my_orders'))

    return render_template('payment.html', order=order)

@app.route('/process_payment/<token>', methods=['POST'])
@login_required
def process_payment(token):
    payment_method = request.form['payment_method']
    
    pending_order = session.get('pending_order')
    conn = get_db_connection()
    
    # Check if this is a new order from session or existing order from database
    if pending_order and pending_order['token'] == token:
        # New order - create it in database now after payment
        order_data = pending_order
        order_id = None
        
        try:
            # Generate transaction ID
            transaction_id = f"TXN{random.randint(100000, 999999)}"
            
            # Insert order into database
            cursor = conn.execute(
                "INSERT INTO orders (token, fabric_type, quantity, dirt_level, estimated_time, total_amount, payment_status, status, customer_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (order_data['token'], order_data['fabric_type'], order_data['quantity'], order_data['dirt_level'],
                 order_data['estimated_time'], order_data['total_amount'], 'Paid', 'Processing', session['user_id'])
            )
            order_id = cursor.lastrowid
            
            # Insert payment record
            conn.execute(
                "INSERT INTO payments (order_id, amount, payment_method, transaction_id, status) VALUES (?, ?, ?, ?, ?)",
                (order_id, order_data['total_amount'], payment_method, transaction_id, 'Completed')
            )
            
            conn.commit()
            conn.close()
            
            # Clear pending order from session
            if 'pending_order' in session:
                del session['pending_order']
            session.modified = True
            
            flash(f'Payment successful! Transaction ID: {transaction_id}', 'success')
            return redirect(url_for('my_orders'))
        
        except Exception as e:
            conn.rollback()
            conn.close()
            flash(f'Error processing payment: {e}', 'error')
            return redirect(url_for('payment', token=token))
    
    else:
        # Existing order from database (reprocessing)
        order = conn.execute("SELECT * FROM orders WHERE token = ? AND customer_id = ?",
                             (token, session['user_id'])).fetchone()
        
        if not order:
            conn.close()
            flash('Order not found or access denied.', 'error')
            return redirect(url_for('my_orders'))
        
        if order['payment_status'] == 'Paid':
            conn.close()
            flash('This order has already been paid.', 'info')
            return redirect(url_for('my_orders'))
        
        # Generate transaction ID
        transaction_id = f"TXN{random.randint(100000, 999999)}"
        
        try:
            # Insert payment record
            conn.execute(
                "INSERT INTO payments (order_id, amount, payment_method, transaction_id, status) VALUES (?, ?, ?, ?, ?)",
                (order['id'], order['total_amount'], payment_method, transaction_id, 'Completed')
            )
            
            # Update order payment status and move it to processing
            conn.execute(
                "UPDATE orders SET payment_status = 'Paid', status = 'Processing' WHERE id = ?",
                (order['id'],)
            )
            
            conn.commit()
            conn.close()
            
            flash(f'Payment successful! Transaction ID: {transaction_id}', 'success')
            return redirect(url_for('my_orders'))
        
        except Exception as e:
            conn.rollback()
        conn.close()
        flash(f'Payment failed: {str(e)}', 'error')
        return redirect(url_for('payment', token=token))

@app.route('/payment-history')
@login_required
def payment_history():
    conn = get_db_connection()
    payments = conn.execute("""
        SELECT payments.*, orders.token, orders.fabric_type, orders.quantity, orders.total_amount
        FROM payments 
        JOIN orders ON payments.order_id = orders.id 
        WHERE orders.customer_id = ?
        ORDER BY payments.created_at DESC
    """, (session['user_id'],)).fetchall()
    conn.close()
    
    return render_template('payment_history.html', payments=payments)

if __name__ == '__main__':
    app.run(debug=True)


