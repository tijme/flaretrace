-- Initialize evidence storage; JSON columns contain validated objects and timestamps use UTC ISO 8601.
PRAGMA journal_mode=WAL;
-- Installation settings: key identifies an option; value stores its validated text.
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- Reusable templates: id is private; name is its label; template_html is original authored HTML.
CREATE TABLE IF NOT EXISTS template_library (id TEXT PRIMARY KEY, name TEXT NOT NULL, template_html TEXT NOT NULL);
-- Links: id is the stable internal identifier; name labels the investigation.
-- destination is an optional final HTTP(S) URL; active controls public availability.
-- created_at/expires_at bound availability; template_html preserves the authored landing HTML.
-- Later migrations add public_slug, template_id, max_visits, and campaign_id without
-- rewriting existing records.
CREATE TABLE IF NOT EXISTS links (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, destination TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, expires_at TEXT, active INTEGER NOT NULL DEFAULT 1,
 template_html TEXT NOT NULL DEFAULT ''
);
-- Visits: id identifies one opening; link_id owns its evidence through cascade deletion.
-- created_at/ip/method/user_agent record request context; referrer strips sensitive URL parts.
-- headers/campaign/browser/location are JSON mappings; absent shared evidence remains NULL.
-- interacted records browser sharing; finished prevents further signed submissions.
-- Later migrations add optional ip_info JSON, a visit_groups foreign key (group_id), an
-- administrator-authored note, and an automated is_bot classification.
CREATE TABLE IF NOT EXISTS visits (
 id TEXT PRIMARY KEY, link_id TEXT NOT NULL REFERENCES links(id) ON DELETE CASCADE,
 created_at TEXT NOT NULL, ip TEXT NOT NULL, method TEXT NOT NULL,
 user_agent TEXT NOT NULL, referrer TEXT NOT NULL, headers TEXT NOT NULL,
 campaign TEXT NOT NULL, browser TEXT, location TEXT,
 interacted INTEGER NOT NULL DEFAULT 0, finished INTEGER NOT NULL DEFAULT 0
);
-- Click groups: id identifies the group; link_id and normalized ip define its scope.
-- started_at fixes its five-minute window; last_at tracks its most recent remaining click.
CREATE TABLE IF NOT EXISTS visit_groups (
 id TEXT PRIMARY KEY, link_id TEXT NOT NULL REFERENCES links(id) ON DELETE CASCADE,
 ip TEXT NOT NULL, started_at TEXT NOT NULL, last_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS groups_lookup ON visit_groups(link_id,ip,started_at DESC);
CREATE INDEX IF NOT EXISTS groups_recent ON visit_groups(last_at DESC);
CREATE INDEX IF NOT EXISTS groups_link_recent ON visit_groups(link_id,last_at DESC);
CREATE INDEX IF NOT EXISTS visits_link ON visits(link_id, created_at);
CREATE INDEX IF NOT EXISTS visits_created ON visits(created_at);
-- Events: monotonically increasing id orders evidence; visit_id owns it through cascade deletion.
-- created_at is server time; kind is an allowlisted category; payload is validated JSON.
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY, visit_id TEXT NOT NULL REFERENCES visits(id) ON DELETE CASCADE,
 created_at TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_visit ON events(visit_id, id);
-- Media: id identifies one file; visit_id owns it through cascade deletion; created_at is server time.
-- kind is photo/video; facing is front/rear; mime is an allowlisted content type.
-- filename is a generated private basename, never a visitor path; size is its byte count.
CREATE TABLE IF NOT EXISTS media (
 id TEXT PRIMARY KEY, visit_id TEXT NOT NULL REFERENCES visits(id) ON DELETE CASCADE,
 created_at TEXT NOT NULL, kind TEXT NOT NULL, facing TEXT NOT NULL,
 mime TEXT NOT NULL, filename TEXT NOT NULL, size INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS media_visit ON media(visit_id);
-- Campaigns: id is private; name labels a group of links for aggregate reporting.
-- A later migration adds the owning links.campaign_id column; see add_missing_columns.
CREATE TABLE IF NOT EXISTS campaigns (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL
);
