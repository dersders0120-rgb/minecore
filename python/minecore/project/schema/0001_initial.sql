-- MineCore project schema v1.
-- Applied inside a transaction by minecore.project.migrations; never edit an
-- applied migration. Change the schema only by adding a new numbered file.

CREATE TABLE project_info (
    singleton       INTEGER PRIMARY KEY CHECK (singleton = 1),
    project_id      TEXT    NOT NULL,
    name            TEXT    NOT NULL CHECK (length(trim(name)) > 0),
    created_utc     TEXT    NOT NULL,
    created_by      TEXT    NOT NULL    -- MineCore version that created the project
) STRICT;

CREATE TABLE settings (
    key             TEXT    PRIMARY KEY,
    value_json      TEXT    NOT NULL CHECK (json_valid(value_json))
) STRICT;

-- Catalogue of every project object (drillhole sets, surfaces, solids,
-- block models, tables). Bulk data lives in files under the package; the
-- catalogue records where.
CREATE TABLE objects (
    object_id       TEXT    PRIMARY KEY,
    kind            TEXT    NOT NULL CHECK (kind GLOB '[a-z]*' AND kind NOT GLOB '*[^a-z0-9_]*'),
    name            TEXT    NOT NULL CHECK (length(trim(name)) > 0),
    parent_id       TEXT    REFERENCES objects(object_id) ON DELETE RESTRICT,
    storage_path    TEXT,               -- relative to the package root, POSIX separators
    metadata_json   TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(metadata_json)),
    created_utc     TEXT    NOT NULL,
    modified_utc    TEXT    NOT NULL,
    UNIQUE (kind, name)
) STRICT;

CREATE INDEX objects_parent ON objects(parent_id);

-- One row per applied migration, including this one.
CREATE TABLE schema_migrations (
    version         INTEGER PRIMARY KEY,
    name            TEXT    NOT NULL,
    applied_utc     TEXT    NOT NULL,
    applied_by      TEXT    NOT NULL    -- MineCore version that applied it
) STRICT;
