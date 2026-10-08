-- Выполняется один раз при первом старте контейнера (пустой том pgdata), в БД $POSTGRES_DB.
CREATE EXTENSION IF NOT EXISTS vector;

-- Отдельная БД для pytest
CREATE DATABASE corag_test;
\c corag_test
CREATE EXTENSION IF NOT EXISTS vector;
