import sqlite3
conn = sqlite3.connect('data/customers.db')
result = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='customers'").fetchone()
print(result[0])
