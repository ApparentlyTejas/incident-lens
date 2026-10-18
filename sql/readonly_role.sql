-- Read-only role used by the MCP server (the agent can never write).
CREATE ROLE il_reader LOGIN PASSWORD 'il_reader';
GRANT SELECT ON bgl_logs TO il_reader;
GRANT SELECT ON openstack_logs TO il_reader;
GRANT SELECT ON zookeeper_logs TO il_reader;
GRANT SELECT ON ownership TO il_reader;
