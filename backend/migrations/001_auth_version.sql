-- Apply explicitly to an identified business database only, never on API startup.
ALTER TABLE accounts ADD COLUMN auth_version BIGINT UNSIGNED NOT NULL DEFAULT 0;
