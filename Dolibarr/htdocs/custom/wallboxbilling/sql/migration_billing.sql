-- WallboxBilling Migration: Billing History Table
-- Version: 1.0.0
-- Date: 2026-05-06
-- Description: Adds billing history table for monthly billing
--
-- This table stores completed monthly billing runs grouped by user.
-- Used by WallboxBilling class cron job for automatic monthly invoicing.
--
-- Requirements: BIL-01, BIL-02, BIL-03

-- Table for billing history
-- Stores completed monthly billing runs
CREATE TABLE IF NOT EXISTS llx_wallbox_billing_history (
    rowid INTEGER AUTO_INCREMENT PRIMARY KEY NOT NULL,
    fk_user INTEGER NOT NULL,
    billing_month INTEGER NOT NULL,
    billing_year INTEGER NOT NULL,
    total_kwh DECIMAL(10,2) NOT NULL DEFAULT 0,
    price_per_kwh DECIMAL(10,4) NOT NULL,
    total_cost DECIMAL(10,2) NOT NULL DEFAULT 0,
    session_count INTEGER NOT NULL DEFAULT 0,
    session_details LONGTEXT,
    fk_user_creator INTEGER NOT NULL,
    date_creation DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status INTEGER NOT NULL DEFAULT 1,
    UNIQUE KEY uk_user_month_year (fk_user, billing_month, billing_year)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Notes:
-- - session_details stores JSON encoded session list
-- - status field: 1=created (open), 2=sent, 3=paid
-- - UNIQUE KEY prevents double billing per month/user
-- - price_per_kwh is stored per billing run for historical accuracy