-- Indizes für performante Abfragen (DB-02)
-- Index auf rfid_hash, fk_user, start_time, status

CREATE INDEX IF NOT EXISTS `idx_wallbox_sessions_rfid` ON `llx_wallbox_sessions` (`rfid_hash`);
CREATE INDEX IF NOT EXISTS `idx_wallbox_sessions_user` ON `llx_wallbox_sessions` (`fk_user`);
CREATE INDEX IF NOT EXISTS `idx_wallbox_sessions_start` ON `llx_wallbox_sessions` (`start_time`);
CREATE INDEX IF NOT EXISTS `idx_wallbox_sessions_status` ON `llx_wallbox_sessions` (`status`);
