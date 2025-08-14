import psycopg2
import os
from dotenv import load_dotenv

# --- THIS IS THE FIX ---
# Call the function to load the variables from the .env file
load_dotenv()
# --- END OF FIX ---

def get_db_connection():
    # Now, os.getenv() will be able to find your database credentials
    conn = psycopg2.connect(
        dbname=os.getenv('DB_NAME'), 
        user=os.getenv('DB_USER'), 
        password=os.getenv('DB_PASSWORD'), 
        host=os.getenv('DB_HOST'), 
        port=os.getenv('DB_PORT')
    )
    return conn

def update_database_schema():
    """Adds new tables and columns without dropping existing ones."""
    conn = get_db_connection()
    cur = conn.cursor()

    # Add profile_picture_url to users table if it doesn't exist
    cur.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='profile_picture_url') THEN
                ALTER TABLE users ADD COLUMN profile_picture_url VARCHAR(255);
            END IF;
        END $$;
    """)

    # Create financial_goals table if it doesn't exist
    cur.execute('''
        CREATE TABLE IF NOT EXISTS financial_goals (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            goal_name VARCHAR(100) NOT NULL,
            target_amount NUMERIC(15, 2) NOT NULL,
            goal_type VARCHAR(50) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
    ''')

    # Create wishlist_items table if it doesn't exist
    cur.execute('''
        CREATE TABLE IF NOT EXISTS wishlist_items (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            item_name VARCHAR(100) NOT NULL,
            target_price NUMERIC(15, 2) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    
    # Create recurring_transactions table if it doesn't exist
    cur.execute('''
        CREATE TABLE IF NOT EXISTS recurring_transactions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            transaction_name VARCHAR(100) NOT NULL,
            amount NUMERIC(12, 2) NOT NULL,
            category VARCHAR(50) NOT NULL,
            recurrence_pattern VARCHAR(50) NOT NULL,
            next_due_date DATE NOT NULL
        );
    ''')

    conn.commit()
    cur.close()
    conn.close()
    print("Database schema updated successfully!")

if __name__ == '__main__':
    update_database_schema()