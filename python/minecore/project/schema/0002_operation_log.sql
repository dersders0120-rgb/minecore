-- MineCore project schema v2: operation log and undo/redo (ADR-0007).
--
-- Every change to a tracked table happens inside an operation. Triggers record
-- a before/after image of each changed row, so undo and redo are generic: undo
-- applies the before images in reverse order, redo the after images in order.
-- Bulk files are never overwritten (ADR-0007), so restoring a catalogue row
-- also restores the data it points to.

CREATE TABLE operations (
    seq             INTEGER PRIMARY KEY,                 -- execution order
    operation_id    TEXT    NOT NULL UNIQUE,
    kind            TEXT    NOT NULL CHECK (kind GLOB '[a-z]*' AND kind NOT GLOB '*[^a-z0-9_]*'),
    description     TEXT    NOT NULL,
    parameters_json TEXT    NOT NULL DEFAULT '{}' CHECK (json_valid(parameters_json)),
    state           TEXT    NOT NULL CHECK (state IN ('done', 'undone')),
    user_name       TEXT    NOT NULL,
    host            TEXT    NOT NULL,
    minecore_version TEXT   NOT NULL,
    utc             TEXT    NOT NULL
) STRICT;

CREATE TABLE operation_changes (
    seq             INTEGER NOT NULL REFERENCES operations(seq) ON DELETE CASCADE,
    ord             INTEGER NOT NULL,
    table_name      TEXT    NOT NULL,
    row_key         TEXT    NOT NULL,
    old_json        TEXT    CHECK (old_json IS NULL OR json_valid(old_json)),
    new_json        TEXT    CHECK (new_json IS NULL OR json_valid(new_json)),
    PRIMARY KEY (seq, ord)
) STRICT;

-- Single row. `seq` is the open operation (NULL when none is open); it is set
-- and cleared inside the operation's own transaction, so it is never left set
-- after a crash. `replaying` = 1 while undo/redo writes rows.
CREATE TABLE operation_context (
    singleton       INTEGER PRIMARY KEY CHECK (singleton = 1),
    seq             INTEGER,
    replaying       INTEGER NOT NULL DEFAULT 0 CHECK (replaying IN (0, 1))
) STRICT;
INSERT INTO operation_context (singleton, seq, replaying) VALUES (1, NULL, 0);

-- settings ----------------------------------------------------------------------
CREATE TRIGGER settings_insert AFTER INSERT ON settings BEGIN
    SELECT RAISE(ABORT, 'settings changed outside an operation')
    WHERE (SELECT seq FROM operation_context) IS NULL;
    INSERT INTO operation_changes (seq, ord, table_name, row_key, old_json, new_json)
    SELECT c.seq, (SELECT count(*) FROM operation_changes WHERE seq = c.seq), 'settings',
           NEW.key, NULL, json_object('key', NEW.key, 'value_json', NEW.value_json)
    FROM operation_context c WHERE c.replaying = 0;
END;

CREATE TRIGGER settings_update AFTER UPDATE ON settings BEGIN
    SELECT RAISE(ABORT, 'settings changed outside an operation')
    WHERE (SELECT seq FROM operation_context) IS NULL;
    INSERT INTO operation_changes (seq, ord, table_name, row_key, old_json, new_json)
    SELECT c.seq, (SELECT count(*) FROM operation_changes WHERE seq = c.seq), 'settings',
           OLD.key, json_object('key', OLD.key, 'value_json', OLD.value_json),
           json_object('key', NEW.key, 'value_json', NEW.value_json)
    FROM operation_context c WHERE c.replaying = 0;
END;

CREATE TRIGGER settings_delete AFTER DELETE ON settings BEGIN
    SELECT RAISE(ABORT, 'settings changed outside an operation')
    WHERE (SELECT seq FROM operation_context) IS NULL;
    INSERT INTO operation_changes (seq, ord, table_name, row_key, old_json, new_json)
    SELECT c.seq, (SELECT count(*) FROM operation_changes WHERE seq = c.seq), 'settings',
           OLD.key, json_object('key', OLD.key, 'value_json', OLD.value_json), NULL
    FROM operation_context c WHERE c.replaying = 0;
END;

-- objects -----------------------------------------------------------------------
CREATE TRIGGER objects_insert AFTER INSERT ON objects BEGIN
    SELECT RAISE(ABORT, 'objects changed outside an operation')
    WHERE (SELECT seq FROM operation_context) IS NULL;
    INSERT INTO operation_changes (seq, ord, table_name, row_key, old_json, new_json)
    SELECT c.seq, (SELECT count(*) FROM operation_changes WHERE seq = c.seq), 'objects',
           NEW.object_id, NULL,
           json_object('object_id', NEW.object_id, 'kind', NEW.kind, 'name', NEW.name,
                       'parent_id', NEW.parent_id, 'storage_path', NEW.storage_path,
                       'metadata_json', NEW.metadata_json, 'created_utc', NEW.created_utc,
                       'modified_utc', NEW.modified_utc)
    FROM operation_context c WHERE c.replaying = 0;
END;

CREATE TRIGGER objects_update AFTER UPDATE ON objects BEGIN
    SELECT RAISE(ABORT, 'objects changed outside an operation')
    WHERE (SELECT seq FROM operation_context) IS NULL;
    INSERT INTO operation_changes (seq, ord, table_name, row_key, old_json, new_json)
    SELECT c.seq, (SELECT count(*) FROM operation_changes WHERE seq = c.seq), 'objects',
           OLD.object_id,
           json_object('object_id', OLD.object_id, 'kind', OLD.kind, 'name', OLD.name,
                       'parent_id', OLD.parent_id, 'storage_path', OLD.storage_path,
                       'metadata_json', OLD.metadata_json, 'created_utc', OLD.created_utc,
                       'modified_utc', OLD.modified_utc),
           json_object('object_id', NEW.object_id, 'kind', NEW.kind, 'name', NEW.name,
                       'parent_id', NEW.parent_id, 'storage_path', NEW.storage_path,
                       'metadata_json', NEW.metadata_json, 'created_utc', NEW.created_utc,
                       'modified_utc', NEW.modified_utc)
    FROM operation_context c WHERE c.replaying = 0;
END;

CREATE TRIGGER objects_delete AFTER DELETE ON objects BEGIN
    SELECT RAISE(ABORT, 'objects changed outside an operation')
    WHERE (SELECT seq FROM operation_context) IS NULL;
    INSERT INTO operation_changes (seq, ord, table_name, row_key, old_json, new_json)
    SELECT c.seq, (SELECT count(*) FROM operation_changes WHERE seq = c.seq), 'objects',
           OLD.object_id,
           json_object('object_id', OLD.object_id, 'kind', OLD.kind, 'name', OLD.name,
                       'parent_id', OLD.parent_id, 'storage_path', OLD.storage_path,
                       'metadata_json', OLD.metadata_json, 'created_utc', OLD.created_utc,
                       'modified_utc', OLD.modified_utc),
           NULL
    FROM operation_context c WHERE c.replaying = 0;
END;
