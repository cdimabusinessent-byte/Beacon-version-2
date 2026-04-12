#!/usr/bin/env python3
"""Diagnose why trades aren't executing."""
import sqlite3
from datetime import UTC, datetime, timedelta

# Connect to the database
db_path = "trading_bot_v2.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

print("=" * 80)
print("TRADE EXECUTION DIAGNOSIS")
print("=" * 80)

# Check recent trades
print("\n1. RECENT TRADES (last 24 hours)")
print("-" * 80)
cursor.execute("""
    SELECT id, symbol, side, status, is_dry_run, execution_symbol,
           created_at, notes
    FROM trades
    WHERE created_at > datetime('now', '-24 hours')
    ORDER BY created_at DESC
    LIMIT 10
""")
trades = cursor.fetchall()
if trades:
    for trade in trades:
        print(f"\nID: {trade['id']}")
        print(f"  Symbol: {trade['symbol']} | Side: {trade['side']} | Status: {trade['status']}")
        print(f"  Is Dry Run: {trade['is_dry_run']}")
        print(f"  Created: {trade['created_at']}")
        print(f"  Notes: {trade['notes'][:100]}")
else:
    print("NO TRADES FOUND IN LAST 24 HOURS")

# Check execution requests
print("\n2. EXECUTION REQUESTS (last 24 hours)")
print("-" * 80)
cursor.execute("""
    SELECT id, idempotency_key, status, action, created_at, error
    FROM execution_requests
    WHERE created_at > datetime('now', '-24 hours')
    ORDER BY created_at DESC
    LIMIT 10
""")
reqs = cursor.fetchall()
if reqs:
    for req in reqs:
        print(f"\nID: {req['id']}")
        print(f"  Action: {req['action']} | Status: {req['status']}")
        print(f"  Created: {req['created_at']}")
        if req['error']:
            print(f"  Error: {req['error']}")
else:
    print("NO EXECUTION REQUESTS FOUND IN LAST 24 HOURS")

# Check all statuses
print("\n3. EXECUTION REQUEST STATUS SUMMARY")
print("-" * 80)
cursor.execute("""
    SELECT status, COUNT(*) as count
    FROM execution_requests
    GROUP BY status
    ORDER BY count DESC
""")
for row in cursor.fetchall():
    print(f"  {row['status']}: {row['count']}")

# Check trade status summary
print("\n4. TRADE STATUS SUMMARY")
print("-" * 80)
cursor.execute("""
    SELECT status, is_dry_run, COUNT(*) as count
    FROM trades
    GROUP BY status, is_dry_run
    ORDER BY count DESC
""")
for row in cursor.fetchall():
    mode = "DRY RUN" if row['is_dry_run'] else "LIVE"
    print(f"  {row['status']} ({mode}): {row['count']}")

# Check if strategy is being triggered
print("\n5. RECENT STRATEGY DECISIONS")
print("-" * 80)
cursor.execute("""
    SELECT symbol, side, status, created_at
    FROM trades
    WHERE created_at > datetime('now', '-7 days')
    ORDER BY created_at DESC
    LIMIT 5
""")
recent = cursor.fetchall()
if recent:
    for trade in recent:
        print(f"  {trade['created_at']}: {trade['symbol']} - {trade['suggested_action']} | Status: {trade['status']}")
else:
    print("  NO TRADES IN LAST 7 DAYS")

# Check for any HOLD or NO_ACTION decisions
print("\n6. SUGGESTED ACTIONS BREAKDOWN (last 7 days)")
print("-" * 80)
cursor.execute("""
    SELECT suggested_action, COUNT(*) as count
    FROM trades
    WHERE created_at > datetime('now', '-7 days')
    GROUP BY suggested_action
    ORDER BY count DESC
""")
for row in cursor.fetchall():
    print(f"  {row['suggested_action']}: {row['count']}")

conn.close()
print("\n" + "=" * 80)
