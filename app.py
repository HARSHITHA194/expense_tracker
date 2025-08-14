import os
import psycopg2
import psycopg2.extras
import json
import datetime
from decimal import Decimal
from dotenv import load_dotenv
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import google.generativeai as genai
import werkzeug
import uuid

# Load environment variables
load_dotenv()

# --- Gemini API Configuration ---
try:
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    model = genai.GenerativeModel('gemini-1.5-flash')
except Exception as e:
    print(f"Error configuring Gemini API: {e}")
    model = None

# --- Flask App Initialization ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'a_very_secret_and_secure_key'

# --- Custom JSON Encoder for Decimal and Datetime ---
class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, (datetime.datetime, datetime.date)):
            return obj.isoformat()
        return super(CustomJSONEncoder, self).default(obj)

app.json_encoder = CustomJSONEncoder

# --- Database Connection ---
def get_db_connection():
    conn = psycopg2.connect(
        dbname=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT')
    )
    return conn

# --- Decorators ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash("You need to be logged in to view this page.", "warning")
            return redirect(url_for('signin'))
        return f(*args, **kwargs)
    return decorated_function

# --- Helper Functions ---
def get_user_data(user_id):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    # The main query remains the same
    cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    user = cur.fetchone()

    if user:
        # --- THIS IS THE NEW PART ---
        # Fetch the currency for this user from the 'incomes' table
        cur.execute("SELECT currency FROM incomes WHERE user_id = %s", (user_id,))
        currency_row = cur.fetchone()
        # Add the currency to the user object, defaulting to '$' if not found
        user = dict(user) # Convert psycopg2.extras.DictRow to a mutable dict
        user['currency'] = currency_row['currency'] if currency_row else '$' 
        # --- END OF NEW PART ---

    cur.close()
    conn.close()
    return user

# --- Routes (Authentication and Onboarding) ---
@app.route('/')
def home():
    return render_template('home.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        full_name = request.form['full_name']
        email = request.form['email']
        password = request.form['password']
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        if cur.fetchone():
            flash("Email address already registered.", "danger")
            cur.close()
            conn.close()
            return redirect(url_for('signup'))

        password_hash = generate_password_hash(password)
        cur.execute(
            "INSERT INTO users (full_name, email, password_hash) VALUES (%s, %s, %s) RETURNING id",
            (full_name, email, password_hash)
        )
        user_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()

        session['user_id'] = user_id
        flash("Account created! Let's start with your income.", "success")
        return redirect(url_for('income'))

    return render_template('signup.html')


@app.route('/signin', methods=['GET', 'POST'])
def signin():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        cur.close()
        conn.close()
        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            return redirect(url_for('dashboard'))
        else:
            flash("Invalid email or password.", "danger")
    return render_template('signin.html')

@app.route('/logout')
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for('home'))

@app.route('/income', methods=['GET', 'POST'])
@login_required
def income():
    # --- THIS IS THE FIXED PART ---
    user_id = session['user_id']
    if request.method == 'POST':
        monthly_income = request.form['monthly_income']
        currency = request.form['currency']
        
        source_names = request.form.getlist('source_name[]')
        source_amounts = request.form.getlist('source_amount[]')

        conn = get_db_connection()
        cur = conn.cursor()

        # Upsert (insert or update) the main monthly income
        cur.execute(
            """
            INSERT INTO incomes (user_id, monthly_income, currency) VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET monthly_income = EXCLUDED.monthly_income, currency = EXCLUDED.currency;
            """,
            (user_id, monthly_income, currency)
        )

        # Clear old other incomes and insert the new ones
        cur.execute("DELETE FROM other_incomes WHERE user_id = %s", (user_id,))
        for name, amount in zip(source_names, source_amounts):
            if name and amount:
                cur.execute(
                    "INSERT INTO other_incomes (user_id, source_name, amount) VALUES (%s, %s, %s)",
                    (user_id, name, amount)
                )

        conn.commit()
        cur.close()
        conn.close()

        flash("Income details saved.", "success")
        # Redirect to the next step in the onboarding process
        return redirect(url_for('budget'))
    
    # GET request just renders the page
    return render_template('income.html')

# ... (keep all the code from the top of the file and other routes) ...

@app.route('/budget', methods=['GET', 'POST'])
@login_required
def budget():
    user_id = session['user_id']
    if request.method == 'POST':
        total_budget = request.form['total_monthly_budget']
        conn = get_db_connection()
        cur = conn.cursor()

        # Upsert the total monthly budget
        cur.execute(
            """
            INSERT INTO budgets (user_id, total_monthly_budget) VALUES (%s, %s)
            ON CONFLICT (user_id) DO UPDATE SET total_monthly_budget = EXCLUDED.total_monthly_budget;
            """, (user_id, total_budget)
        )
        
        # Clear old category budgets and insert the new ones
        cur.execute("DELETE FROM category_budgets WHERE user_id = %s", (user_id,))
        for key, value in request.form.items():
            if key.startswith('category_') and value:
                category_name = key.replace('category_', '').replace('_', ' ').title()
                cur.execute(
                    "INSERT INTO category_budgets (user_id, category_name, amount) VALUES (%s, %s, %s)",
                    (user_id, category_name, value)
                )
        
        conn.commit()
        cur.close()
        conn.close()

        flash("Budgets saved successfully! Now you can start adding expenses.", "success")
        
        # --- THIS IS THE LINE TO CHANGE ---
        # It now redirects to the 'expense' page instead of the 'dashboard'.
        return redirect(url_for('expense'))
        # --- END OF CHANGE ---

    # GET request just renders the page
    return render_template('budget.html')

# ... (rest of the file remains the same) ...
@app.route('/upload-profile-picture', methods=['POST'])
@login_required
def upload_profile_picture():
    user_id = session['user_id']
    if 'profile_pic' not in request.files:
        flash('No file part', 'danger')
        return redirect(url_for('dashboard'))
    
    file = request.files['profile_pic']
    if file.filename == '':
        flash('No selected file', 'danger')
        return redirect(url_for('dashboard'))

    if file:
        # Secure filename and create a unique name
        filename = werkzeug.utils.secure_filename(file.filename)
        unique_filename = str(uuid.uuid4()) + os.path.splitext(filename)[1]
        
        # Ensure upload folder exists
        upload_folder = os.path.join(app.static_folder, 'uploads', 'profile_pics')
        os.makedirs(upload_folder, exist_ok=True)
        
        filepath = os.path.join(upload_folder, unique_filename)
        file.save(filepath)
        
        # Store the relative path in the database
        db_path = f'uploads/profile_pics/{unique_filename}'
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE users SET profile_picture_url = %s WHERE id = %s", (db_path, user_id))
        conn.commit()
        cur.close()
        conn.close()
        
        flash('Profile picture updated!', 'success')
    return redirect(url_for('dashboard'))


@app.route('/add-goal', methods=['POST'])
@login_required
def add_goal():
    user_id = session['user_id']
    goal_name = request.form.get('goal_name')
    target_amount = request.form.get('target_amount')
    goal_type = request.form.get('goal_type')

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO financial_goals (user_id, goal_name, target_amount, goal_type) VALUES (%s, %s, %s, %s)",
                (user_id, goal_name, target_amount, goal_type))
    conn.commit()
    cur.close()
    conn.close()
    flash('Financial goal added!', 'success')
    return redirect(url_for('dashboard'))


@app.route('/delete-goal/<int:goal_id>', methods=['POST'])
@login_required
def delete_goal(goal_id):
    user_id = session['user_id']
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM financial_goals WHERE id = %s AND user_id = %s", (goal_id, user_id))
    conn.commit()
    cur.close()
    conn.close()
    flash('Goal removed.', 'info')
    return redirect(url_for('dashboard'))

# ... (Add similar routes for add/delete wishlist items if desired) ...


# --- Main Application Routes ---
@app.route('/dashboard')
@login_required
def dashboard():
    user_id = session['user_id']
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    # 1. SUMMARY CARDS DATA
    cur.execute("SELECT SUM(amount) as total FROM expenses WHERE user_id = %s AND date_part('month', expense_date) = date_part('month', CURRENT_DATE)", (user_id,))
    total_spent_this_month = cur.fetchone()['total'] or 0
    
    cur.execute("SELECT monthly_income FROM incomes WHERE user_id = %s", (user_id,))
    income_row = cur.fetchone()
    monthly_income = income_row['monthly_income'] if income_row else 0
    
    net_balance = monthly_income - total_spent_this_month

    cur.execute("SELECT total_monthly_budget FROM budgets WHERE user_id = %s", (user_id,))
    budget_row = cur.fetchone()
    total_budget = budget_row['total_monthly_budget'] if budget_row else 0
    budget_used_percent = (total_spent_this_month / total_budget * 100) if total_budget > 0 else 0

    # 2. GOALS AND WISHLIST DATA
    cur.execute("SELECT * FROM financial_goals WHERE user_id = %s", (user_id,))
    goals = cur.fetchall()
    # cur.execute("SELECT * FROM wishlist_items WHERE user_id = %s", (user_id,))
    # wishlist = cur.fetchall() # Add this if you implement the wishlist form

    # 3. QUICK CHARTS DATA
    # Weekly Expense Bar Chart
    cur.execute("SELECT to_char(expense_date, 'DY') as day, SUM(amount) as total FROM expenses WHERE user_id = %s AND expense_date >= current_date - interval '6 days' GROUP BY expense_date ORDER BY expense_date", (user_id,))
    weekly_expenses_data = {row['day'].strip().upper(): float(row['total']) for row in cur.fetchall()}

    # Expense by Category Pie Chart (Current Month)
    cur.execute("SELECT category, SUM(amount) as total FROM expenses WHERE user_id = %s AND date_part('month', expense_date) = date_part('month', CURRENT_DATE) GROUP BY category ORDER BY total DESC", (user_id,))
    expense_by_category = {row['category']: float(row['total']) for row in cur.fetchall()}

    # 4. RECENT ACTIVITY
    cur.execute("SELECT * FROM expenses WHERE user_id = %s ORDER BY expense_date DESC, id DESC LIMIT 5", (user_id,))
    recent_expenses_list = cur.fetchall()
    
    # 5. UPCOMING ALERTS (Budget Overspending)
    cur.execute("SELECT category_name, amount FROM category_budgets WHERE user_id = %s", (user_id,))
    budgeted_amounts = {row['category_name']: row['amount'] for row in cur.fetchall()}
    overspent_categories = []
    if budgeted_amounts:
        cur.execute("SELECT category, SUM(amount) as spent FROM expenses WHERE user_id = %s AND date_part('month', expense_date) = date_part('month', CURRENT_DATE) GROUP BY category", (user_id,))
        for expense in cur.fetchall():
            if expense['category'] in budgeted_amounts and expense['spent'] > budgeted_amounts[expense['category']]:
                overspent_categories.append(expense['category'])
    
    cur.close()
    conn.close()

    return render_template(
        'dashboard.html',
        # Summary Data
        monthly_income=monthly_income,
        total_spent_this_month=total_spent_this_month,
        net_balance=net_balance,
        budget_used_percent=budget_used_percent,
        # Goals Data
        goals=goals,
        # Recent Activity
        recent_expenses_list=recent_expenses_list,
        # Alerts
        overspent_categories=overspent_categories,
        # Chart Data (passed to data islands)
        weekly_expenses_data=weekly_expenses_data,
        expense_by_category=expense_by_category
    )

# ... (rest of the file is correct and unchanged) ...
@app.route('/expense', methods=['GET', 'POST'])
@login_required
def expense():
    if request.method == 'POST':
        title = request.form['title']
        amount = request.form['amount']
        category = request.form['category']
        date = request.form['date']
        description = request.form.get('description', '')
        payment_method = request.form['payment_method']
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO expenses (user_id, title, amount, category, expense_date, description, payment_method) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (session['user_id'], title, amount, category, date, description, payment_method)
        )
        conn.commit()
        cur.close()
        conn.close()
        flash("Expense added successfully!", "success")
        return redirect(url_for('expenses_list'))
    return render_template('expense.html')

@app.route('/expenses')
@login_required
def expenses_list():
    user_id = session['user_id']
    # Get user data which now includes the currency
    user = get_user_data(user_id)
    
    query = "SELECT * FROM expenses WHERE user_id = %s"
    params = [user_id]
    
    sort_by = request.args.get('sort', 'newest')
    if sort_by == 'amount_high_low':
        query += " ORDER BY amount DESC"
    else:
        query += " ORDER BY expense_date DESC, id DESC"
        
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute(query, tuple(params))
    expenses = cur.fetchall()
    cur.close()
    conn.close()
    
    # Pass both the expenses and the user object (with currency) to the template
    return render_template('expense2.html', expenses=expenses, user=user)



@app.route('/investments', methods=['GET', 'POST'])
@login_required
def investments():
    user_id = session['user_id']
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            cur.execute(
                "INSERT INTO investments (user_id, investment_name, amount_invested, investment_type, investment_date) VALUES (%s, %s, %s, %s, %s)",
                (user_id, request.form['investment_name'], request.form['amount_invested'], request.form['investment_type'], request.form['investment_date'])
            )
            flash("Investment added successfully!", "success")
        elif action == 'delete':
            investment_id = request.form.get('investment_id')
            cur.execute("DELETE FROM investments WHERE id = %s AND user_id = %s", (investment_id, user_id))
            flash("Investment deleted.", "info")
        conn.commit()
        cur.close()
        conn.close()
        return redirect(url_for('investments'))
    cur.execute("SELECT * FROM investments WHERE user_id = %s ORDER BY investment_date DESC", (user_id,))
    investments_list = cur.fetchall()
    cur.close()
    conn.close()
    return render_template('investments.html', investments=investments_list)

# ... (keep other routes and code as they are) ...

@app.route('/assets-debts', methods=['GET', 'POST'])
@login_required
def assetdebt():
    user_id = session['user_id']
    user = get_user_data(user_id) # We correctly get the user data here.
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    if request.method == 'POST':
        # ... (the POST logic is correct and does not need changes) ...
        action = request.form.get('action', 'add')
        if action == 'add':
            form_type = request.form.get('form_type')
            if form_type == 'asset':
                cur.execute("INSERT INTO assets (user_id, asset_name, value) VALUES (%s, %s, %s)",
                            (user_id, request.form['asset_name'], request.form['value']))
                flash("Asset added!", "success")
            elif form_type == 'liability':
                cur.execute("INSERT INTO liabilities (user_id, liability_name, amount_owed) VALUES (%s, %s, %s)",
                            (user_id, request.form['liability_name'], request.form['amount_owed']))
                flash("Liability added!", "success")
        elif action == 'delete':
            form_type = request.form.get('form_type')
            item_id = request.form.get('item_id')
            if form_type == 'asset':
                cur.execute("DELETE FROM assets WHERE id = %s AND user_id = %s", (item_id, user_id))
                flash("Asset deleted.", "info")
            elif form_type == 'liability':
                cur.execute("DELETE FROM liabilities WHERE id = %s AND user_id = %s", (item_id, user_id))
                flash("Liability deleted.", "info")
        
        conn.commit()
        cur.close()
        conn.close()
        return redirect(url_for('assetdebt'))
        
    # GET request logic
    cur.execute("SELECT * FROM assets WHERE user_id = %s ORDER BY id", (user_id,))
    assets = cur.fetchall()
    cur.execute("SELECT * FROM liabilities WHERE user_id = %s ORDER BY id", (user_id,))
    liabilities = cur.fetchall()

    total_assets = sum(a['value'] for a in assets)
    total_liabilities = sum(l['amount_owed'] for l in liabilities)
    net_worth = total_assets - total_liabilities

    cur.close()
    conn.close()
    
    # --- THIS IS THE CORRECTED LINE ---
    # We must pass the 'user' object to the template so it can be used.
    return render_template('assetdebt.html', assets=assets, liabilities=liabilities, net_worth=net_worth, user=user)
    # --- END OF CORRECTION ---

# ... (rest of the file) ...
@app.route('/reports')
@login_required
def reports():
    return render_template('reports.html')


# NEW: A powerful API endpoint to fetch all data needed for the reports page.
# ... (keep the rest of app.py as is) ...

# NEW: A powerful API endpoint to fetch all data needed for the reports page.
@app.route('/api/comprehensive-report')
@login_required
def comprehensive_report_api():
    user_id = session['user_id']
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    # 1. Income Data (for Pie Chart)
    cur.execute("SELECT monthly_income FROM incomes WHERE user_id = %s", (user_id,))
    main_income = cur.fetchone()
    cur.execute("SELECT source_name, amount FROM other_incomes WHERE user_id = %s", (user_id,))
    other_incomes = cur.fetchall()
    
    income_sources = {'Salary / Main': float(main_income['monthly_income']) if main_income else 0}
    for item in other_incomes:
        income_sources[item['source_name']] = float(item['amount'])

    # 2. Expenses by Month Data (for Bar Chart)
    cur.execute("""
        SELECT to_char(expense_date, 'YYYY-MM') as month, SUM(amount) as total 
        FROM expenses WHERE user_id = %s 
        GROUP BY month ORDER BY month DESC LIMIT 6
    """, (user_id,))
    expenses_by_month_rows = cur.fetchall()
    expenses_by_month = {row['month']: float(row['total']) for row in reversed(expenses_by_month_rows)}

    # 3. Budget vs Actuals Data
    cur.execute("SELECT category, SUM(amount) as total_spent FROM expenses WHERE user_id = %s AND date_part('month', expense_date) = date_part('month', CURRENT_DATE) GROUP BY category", (user_id,))
    actual_spending = {row['category']: float(row['total_spent']) for row in cur.fetchall()}
    cur.execute("SELECT category_name, amount FROM category_budgets WHERE user_id = %s", (user_id,))
    budgeted_amounts = {row['category_name']: float(row['amount']) for row in cur.fetchall()}
    
    all_budget_cats = sorted(list(set(actual_spending.keys()) | set(budgeted_amounts.keys())))
    budget_vs_actual = {
        "labels": all_budget_cats,
        "budgeted": [budgeted_amounts.get(cat, 0) for cat in all_budget_cats],
        "actual": [actual_spending.get(cat, 0) for cat in all_budget_cats]
    }

    # 4. Investments by Type Data
    cur.execute("SELECT investment_type, SUM(amount_invested) as total FROM investments WHERE user_id = %s GROUP BY investment_type", (user_id,))
    investments_by_type = {row['investment_type']: float(row['total']) for row in cur.fetchall()}

    # --- 5. NEW: Assets vs Liabilities Data ---
    cur.execute("SELECT SUM(value) as total_assets FROM assets WHERE user_id = %s", (user_id,))
    total_assets = cur.fetchone()['total_assets'] or 0
    cur.execute("SELECT SUM(amount_owed) as total_liabilities FROM liabilities WHERE user_id = %s", (user_id,))
    total_liabilities = cur.fetchone()['total_liabilities'] or 0
    assets_vs_liabilities = {
        'assets': float(total_assets),
        'liabilities': float(total_liabilities)
    }
    # --- END OF NEW DATA ---

    # 6. Summary Line Graph Data (Income vs Expense Over Time)
    summary_line_graph = {
        "labels": list(expenses_by_month.keys()),
        "expenses": list(expenses_by_month.values()),
        "income": [income_sources.get('Salary / Main', 0)] * len(expenses_by_month)
    }

    cur.close()
    conn.close()

    # Consolidate all data into a single JSON response
    return jsonify({
        'income_by_source': income_sources,
        'expenses_by_month': expenses_by_month,
        'budget_vs_actual': budget_vs_actual,
        'investments_by_type': investments_by_type,
        'assets_vs_liabilities': assets_vs_liabilities, # Add the new data here
        'summary_line_graph': summary_line_graph
    })

# ... (rest of app.py)

@app.route('/chatbot')
@login_required
def chatbot_page():
    return render_template('chatbot.html')

@app.route('/api/chat', methods=['POST'])
@login_required
def chat_api():
    if not model:
        return jsonify({"error": "Chatbot is not configured."}), 500
    data = request.get_json()
    user_message = data.get("message")
    if not user_message:
        return jsonify({"error": "No message provided."}), 400
    try:
        prompt = f"You are a friendly and helpful financial assistant for an app called Finance Tracker. A user has asked: '{user_message}'. Provide a concise and helpful answer."
        response = model.generate_content(prompt)
        return jsonify({"reply": response.text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.context_processor
def inject_user():
    if 'user_id' in session:
        return dict(current_user=get_user_data(session['user_id']))
    return dict(current_user=None)

if __name__ == '__main__':
    app.run(debug=True)