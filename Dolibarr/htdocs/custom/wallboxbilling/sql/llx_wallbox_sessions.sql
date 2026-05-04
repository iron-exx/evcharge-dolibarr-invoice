-- Tabelle für Wallbox-Ladevorgänge (DB-01)
-- Felder: rowid, fk_user, rfid_hash, wallbox_id, start_time, end_time, kwh, price_per_kwh, total_cost, date_creation

CREATE TABLE llx_wallbox_sessions (
    rowid INTEGER AUTO_INCREMENT PRIMARY KEY NOT NULL,
    fk_user INTEGER NOT NULL,                      -- user_id (Dolibarr user ID)
    rfid_hash VARCHAR(64) NOT NULL,               -- SHA-256 Hash (64 Zeichen Hex)
    wallbox_id VARCHAR(50) NOT NULL DEFAULT 'alfen_eve',
    start_time DATETIME NOT NULL,
    end_time DATETIME NULL DEFAULT NULL,
    kwh REAL NOT NULL DEFAULT 0.0,
    price_per_kwh REAL NOT NULL DEFAULT 0.30,     -- Standard: 0.30 €/kWh
    total_cost REAL NOT NULL DEFAULT 0.0,
    status VARCHAR(20) NOT NULL DEFAULT 'active',   -- 'active', 'completed'
    date_creation DATETIME NOT NULL,
    tms TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8;
