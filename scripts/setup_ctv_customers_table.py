"""Idempotent setup for dbo.CTV_Customers — the shared customer-defaults store on
the Etere SQL Server, replacing the per-checkout SQLite `data/customers.db`.

Mirrors the SQLite `customers` schema so CustomerRepository can be repointed at it
with no data-shape change. Composite PK (customer_name, order_type), same as SQLite.
Safe to re-run — only creates the table if missing; never touches existing rows.

    uv run python scripts/setup_ctv_customers_table.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from browser_automation.etere_direct_client import connect

_DDL = [
    """IF OBJECT_ID('dbo.CTV_Customers', 'U') IS NULL
       CREATE TABLE dbo.CTV_Customers (
           customer_name          NVARCHAR(200) NOT NULL,
           order_type             NVARCHAR(50)  NOT NULL,
           customer_id            NVARCHAR(50)  NOT NULL,
           abbreviation           NVARCHAR(50)  NOT NULL CONSTRAINT DF_CTVCust_abbr    DEFAULT '',
           default_market         NVARCHAR(10)  NULL,
           billing_type           NVARCHAR(20)  NOT NULL CONSTRAINT DF_CTVCust_bill    DEFAULT 'agency',
           separation_customer    INT           NOT NULL CONSTRAINT DF_CTVCust_sepc    DEFAULT 15,
           separation_event       INT           NOT NULL CONSTRAINT DF_CTVCust_sepe    DEFAULT 0,
           separation_order       INT           NOT NULL CONSTRAINT DF_CTVCust_sepo    DEFAULT 0,
           code_name              NVARCHAR(100) NOT NULL CONSTRAINT DF_CTVCust_code    DEFAULT '',
           description_name       NVARCHAR(200) NOT NULL CONSTRAINT DF_CTVCust_desc    DEFAULT '',
           include_market_in_code INT           NOT NULL CONSTRAINT DF_CTVCust_incmkt  DEFAULT 0,
           auto_aircheck          INT           NOT NULL CONSTRAINT DF_CTVCust_airchk  DEFAULT 0,
           owner                  NVARCHAR(100) NOT NULL CONSTRAINT DF_CTVCust_owner   DEFAULT '',
           default_code_template  NVARCHAR(200) NULL,
           default_desc_template  NVARCHAR(200) NULL,
           created_at             DATETIME      NOT NULL CONSTRAINT DF_CTVCust_created DEFAULT GETDATE(),
           updated_at             DATETIME      NOT NULL CONSTRAINT DF_CTVCust_updated DEFAULT GETDATE(),
           CONSTRAINT PK_CTV_Customers PRIMARY KEY (customer_name, order_type)
       )""",
    # Idempotent migrations for tables created before these columns existed.
    "IF COL_LENGTH('dbo.CTV_Customers','default_code_template') IS NULL "
    "ALTER TABLE dbo.CTV_Customers ADD default_code_template NVARCHAR(200) NULL",
    "IF COL_LENGTH('dbo.CTV_Customers','default_desc_template') IS NULL "
    "ALTER TABLE dbo.CTV_Customers ADD default_desc_template NVARCHAR(200) NULL",
    "IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_CTVCust_name') "
    "CREATE INDEX IX_CTVCust_name ON dbo.CTV_Customers(customer_name)",
    "IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_CTVCust_type') "
    "CREATE INDEX IX_CTVCust_type ON dbo.CTV_Customers(order_type)",
]


def main():
    with connect() as conn:
        cur = conn.cursor()
        for stmt in _DDL:
            cur.execute(stmt)
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM dbo.CTV_Customers")
        print(f"dbo.CTV_Customers ready — {cur.fetchone()[0]} customer row(s).")


if __name__ == "__main__":
    main()
