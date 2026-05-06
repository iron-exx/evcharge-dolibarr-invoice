-- Upgrade-Script für Wallboxbilling Modul Version 3.0.0
-- Fügt transmitted_at Feld hinzu für API-Übertragungs-Tracking (D-03, API-05)

-- transmitted_at Spalte hinzufügen falls nicht vorhanden
ALTER TABLE llx_wallbox_sessions ADD COLUMN transmitted_at DATETIME NULL AFTER date_creation;

-- Index für effiziente Abfragen (API-Status-Abfragen)
CREATE INDEX idx_wallbox_sessions_transmitted ON llx_wallbox_sessions(transmitted_at);

-- Kommentar hinzufügen
ALTER TABLE llx_wallbox_sessions COMMENT 'Wallbox Ladevorgänge mit API-Übertragungsstatus';