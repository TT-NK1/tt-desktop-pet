BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS tarot_daily_draws (
    draw_date TEXT PRIMARY KEY,
    card_id TEXT NOT NULL,
    reversed INTEGER NOT NULL CHECK (reversed IN (0, 1)),
    theme TEXT NOT NULL,
    revealed INTEGER NOT NULL DEFAULT 0,
    notified INTEGER NOT NULL DEFAULT 0,
    snooze_until REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tarot_readings (
    draw_date TEXT NOT NULL,
    theme TEXT NOT NULL,
    content TEXT NOT NULL,
    PRIMARY KEY (draw_date, theme)
);
CREATE TABLE IF NOT EXISTS tarot_favorites (card_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS tarot_settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
COMMIT;
