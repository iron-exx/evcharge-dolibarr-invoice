-- Wallbox Billing Sessions Tabelle (D-07, DB-03, DB-01 Vorbereitung)
-- Erstellt die llx_wallboxbilling_sessions Tabelle für Ladevorgänge

CREATE TABLE IF NOT EXISTS `llx_wallboxbilling_sessions` (
  `rowid` INTEGER PRIMARY KEY AUTO_INCREMENT,
  `user_id` INTEGER NOT NULL DEFAULT 0,
  `rfid_hash` VARCHAR(128) NOT NULL DEFAULT '', -- SHA-256 Hash (64 Zeichen), Vorbereitung SEC-02
  `wallbox_id` VARCHAR(50) NOT NULL DEFAULT '', -- Vorbereitung EXT-01 (Multi-Wallbox)
  `start_time` DATETIME NULL DEFAULT NULL,
  `end_time` DATETIME NULL DEFAULT NULL,
  `kwh` DECIMAL(10,3) NOT NULL DEFAULT 0.000, -- Verbrauchte Energie in kWh
  `price_per_kwh` DECIMAL(10,4) NOT NULL DEFAULT 0.0000, -- Nutzerspezifischer Preis
  `total_cost` DECIMAL(10,2) NOT NULL DEFAULT 0.00, -- Gesamtkosten
  `status` VARCHAR(20) NOT NULL DEFAULT 'active', -- 'active', 'completed', 'cancelled'
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NULL DEFAULT NULL
);

-- Indizes für performante Abfragen (DB-02 Vorbereitung)
CREATE INDEX IF NOT EXISTS `idx_wallbox_sessions_rfid` ON `llx_wallboxbilling_sessions` (`rfid_hash`);
CREATE INDEX IF NOT EXISTS `idx_wallbox_sessions_user` ON `llx_wallboxbilling_sessions` (`user_id`);
CREATE INDEX IF NOT EXISTS `idx_wallbox_sessions_start` ON `llx_wallboxbilling_sessions` (`start_time`);
CREATE INDEX IF NOT EXISTS `idx_wallbox_sessions_status` ON `llx_wallboxbilling_sessions` (`status`);

-- Kommentare
ALTER TABLE `llx_wallboxbilling_sessions` COMMENT 'Wallbox Ladevorgänge (Sessions) für Abrechnung';
