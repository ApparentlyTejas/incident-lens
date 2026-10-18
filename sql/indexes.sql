-- Keep identifiers and error codes intact: lower-case, no stemming.
DROP INDEX IF EXISTS bgl_idx;
DROP INDEX IF EXISTS os_idx;
DROP INDEX IF EXISTS zk_idx;
DROP TEXT SEARCH DICTIONARY IF EXISTS logdict;

CREATE TEXT SEARCH DICTIONARY logdict AS
  split_text(case := 'lower') WITH (frequency, position, offset);

-- Filter columns are included in the index so time/level/component filters run inside the scan.
CREATE INDEX bgl_idx ON bgl_logs
  USING inverted (id, ts, node, component, level, alert, message logdict);
CREATE INDEX os_idx ON openstack_logs
  USING inverted (id, ts, service, level, component, request_id, http_status, message logdict);
CREATE INDEX zk_idx ON zookeeper_logs
  USING inverted (id, ts, thread, component, level, message logdict);

-- Freshly loaded rows become searchable now (the index is otherwise eventually consistent).
VACUUM (REFRESH_TABLE) bgl_logs;
VACUUM (REFRESH_TABLE) openstack_logs;
VACUUM (REFRESH_TABLE) zookeeper_logs;
