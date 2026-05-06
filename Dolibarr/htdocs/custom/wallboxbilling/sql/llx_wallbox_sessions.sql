-- Tabelle für Wallbox-Ladevorgänge (DB-01, API-05)
-- Felder: rowid, fk_user, rfid_hash, wallbox_id, start_time, end_time, kwh, price_per_kwh, total_cost, status, date_creation, transmitted_at

CREATE TABLE llx_wallbox_sessions (
    rowid INTEGER AUTO_INCREMENT PRIMARY KEY NOT NULL,
    fk_user INTEGER NOT NULL DEFAULT 0,                      -- user_id (Dolibarr user ID)
    rfid_hash VARCHAR(64) NOT NULL,               -- SHA-256 Hash (64 Zeichen Hex)
    wallbox_id VARCHAR(50) NOT NULL DEFAULT 'alfen_eve',
    start_time DATETIME NOT NULL,
    end_time DATETIME NULL DEFAULT NULL,
    kwh REAL NOT NULL DEFAULT 0.0,
    price_per_kwh REAL NOT NULL DEFAULT 0.30,     -- Standard: 0.30 €/kWh
    total_cost REAL NOT NULL DEFAULT 0.0,
    status VARCHAR(20) NOT NULL DEFAULT 'active',   -- 'active', 'completed'
    date_creation DATETIME NOT NULL,
    transmitted_at DATETIME NULL DEFAULT NULL,    -- API-05: Zeitpunkt der Übertragung von HA-Addon
    tms TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

-- Indizes für performante Abfragen
CREATE INDEX idx_wallbox_sessions_rfid ON llx_wallbox_sessions(rfid_hash);
CREATE INDEX idx_wallbox_sessions_user ON llx_wallbox_sessions(fk_user);
CREATE INDEX idx_wallbox_sessions_status ON llx_wallbox_sessions(status);
CREATE INDEX idx_wallbox_sessions_transmitted ON llx_wallbox_sessions(transmitted_at);
