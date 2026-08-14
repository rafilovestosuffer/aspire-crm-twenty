-- Twenty and n8n each need their own database on the shared Postgres instance.
-- Runs once, on first start, when the data volume is empty.
CREATE DATABASE twenty;
CREATE DATABASE n8n;
