from investment.database.migration import ALL_TABLES, MigrationReport, validate_schema_name
from investment.database.postgres import translate_sqlite_statement


def test_postgres_migration_table_plan_has_no_duplicates() -> None:
    assert len(ALL_TABLES) == len(set(ALL_TABLES))
    assert "paper_portfolio" in ALL_TABLES
    assert "decision_snapshot" in ALL_TABLES
    assert "derivatives_snapshot" in ALL_TABLES


def test_postgres_schema_name_rejects_sql_fragments() -> None:
    assert validate_schema_name("investment_v2") == "investment_v2"

    for invalid in ("Investment", "investment;drop schema public", "two schemas", ""):
        try:
            validate_schema_name(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe schema accepted: {invalid}")


def test_migration_report_requires_counts_and_integrity_to_match() -> None:
    valid = MigrationReport(
        "start",
        "finish",
        "investment",
        {"paper": "/paper"},
        {"paper_portfolio": 2},
        {"paper_portfolio": 2},
        {"paper": "ok"},
    )
    mismatch = MigrationReport(
        "start",
        "finish",
        "investment",
        {"paper": "/paper"},
        {"paper_portfolio": 2},
        {"paper_portfolio": 1},
        {"paper": "ok"},
    )

    assert valid.verified is True
    assert '"verified": true' in valid.to_json()
    assert mismatch.verified is False


def test_postgres_adapter_translates_repository_sql_subset() -> None:
    assert translate_sqlite_statement("BEGIN IMMEDIATE") == "BEGIN"
    assert (
        translate_sqlite_statement("INSERT OR IGNORE INTO sample VALUES (?, ?)")
        == "INSERT INTO sample VALUES (%s, %s) ON CONFLICT DO NOTHING"
    )
    assert (
        translate_sqlite_statement("decision_time <= julianday(?)")
        == "decision_time <= %s"
    )
