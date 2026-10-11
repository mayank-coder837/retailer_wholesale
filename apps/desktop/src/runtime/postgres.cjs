// @ts-check
"use strict";

// PostgreSQL on the main PC: configuration and commands. The database listens on 127.0.0.1 only: office PCs
// talk to the API, never to PostgreSQL.
const path = require("node:path");

const OWNER = "smartbiz"; // superuser: migrations, backups, restores
const API_LOGIN = "smartbiz_api"; // the API's own login: no superuser, member of smartbiz_app (RLS always applies)
const DATABASE = "smartbiz";

/** @param {number} port */
function postgresqlConf(port) {
  return [
    "# Managed by SmartBiz OS (M0.6). Changes here are overwritten by setup.",
    `port = ${port}`,
    "listen_addresses = '127.0.0.1'",
    "password_encryption = 'scram-sha-256'",
    "max_connections = 50",
    "shared_buffers = 128MB",
    "logging_collector = on",
    "log_directory = 'log'",
    "log_filename = 'postgresql-%a.log'",
    "log_truncate_on_rotation = on",
    "log_rotation_age = 1d",
    "timezone = 'UTC'",
    "",
  ].join("\n");
}

function pgHbaConf() {
  return [
    "# Managed by SmartBiz OS (M0.6): local TCP connections only, always with a password.",
    "# TYPE  DATABASE  USER  ADDRESS        METHOD",
    "host    all       all   127.0.0.1/32   scram-sha-256",
    "host    all       all   ::1/128        scram-sha-256",
    "",
  ].join("\n");
}

/** @param {string} pgBin @param {string} pgdata @param {string} pwfile */
function initdbArgs(pgBin, pgdata, pwfile) {
  return {
    command: path.join(pgBin, "initdb.exe"),
    args: [
      `--pgdata=${pgdata}`,
      `--username=${OWNER}`,
      `--pwfile=${pwfile}`,
      "--auth=scram-sha-256",
      "--encoding=UTF8",
      "--locale=C",
      "--data-checksums",
    ],
  };
}

/**
 * SQL run as the owner after migrations: the API's login, a member of the app role (ADR 0002).
 * Passwords never appear in SQL: psql reads them from a variable.
 */
function apiLoginSql() {
  return [
    `DO $$ BEGIN`,
    `  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${API_LOGIN}') THEN`,
    `    CREATE ROLE ${API_LOGIN} LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;`,
    `  END IF;`,
    `END $$;`,
    `ALTER ROLE ${API_LOGIN} PASSWORD :'api_password';`,
    `GRANT smartbiz_app TO ${API_LOGIN};`,
    `GRANT CONNECT ON DATABASE ${DATABASE} TO ${API_LOGIN};`,
    "",
  ].join("\n");
}

/** @param {{ port: number, user: string, password: string }} c */
function connection(c) {
  return {
    args: ["--host=127.0.0.1", `--port=${c.port}`, `--username=${c.user}`, "--no-password"],
    env: { PGPASSWORD: c.password },
  };
}

/** asyncpg URL without the password (the API takes it from the DPAPI secrets file). @param {string} user @param {number} port */
function databaseUrl(user, port, password = "") {
  const auth = password ? `${user}:${encodeURIComponent(password)}` : user;
  return `postgresql+asyncpg://${auth}@127.0.0.1:${port}/${DATABASE}`;
}

module.exports = {
  OWNER,
  API_LOGIN,
  DATABASE,
  postgresqlConf,
  pgHbaConf,
  initdbArgs,
  apiLoginSql,
  connection,
  databaseUrl,
};
