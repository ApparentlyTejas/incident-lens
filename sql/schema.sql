-- Incident Lens schema. {{BGL_PARQUET}} / {{OS_PARQUET}} are substituted by scripts/load.py.
-- Tables are materialised from the Parquet files produced by prep/parse_*.py.

DROP TABLE IF EXISTS bgl_logs;
DROP TABLE IF EXISTS openstack_logs;
DROP TABLE IF EXISTS ownership;

CREATE TABLE bgl_logs AS SELECT * FROM read_parquet('{{BGL_PARQUET}}');
CREATE TABLE openstack_logs AS SELECT * FROM read_parquet('{{OS_PARQUET}}');

-- Fictional ownership mapping (see sql/ownership.sql and README).
CREATE TABLE ownership (dataset VARCHAR, component VARCHAR, team VARCHAR);
