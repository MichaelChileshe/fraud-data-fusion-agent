-- A second, empty database for the integration tests, so `pytest` never touches real run data.
-- Runs once, the first time the PostgreSQL volume is created (docker-entrypoint-initdb.d).
CREATE DATABASE fusion_test;
