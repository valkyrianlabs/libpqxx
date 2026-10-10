#include "queries.hxx"

#include <pqxx/pqxx>

#include <cstdint>
#include <iostream>
#include <optional>
#include <string>
#include <tuple>
#include <vector>

namespace
{
int failures{0};

template<typename T> void expect(std::string_view what, T const &got, T const &want)
{
  if (got == want)
  {
    std::cout << "  ok   " << what << '\n';
  }
  else
  {
    ++failures;
    std::cout << "  FAIL " << what << ": got " << pqxx::to_string(got) << ", want "
              << pqxx::to_string(want) << '\n';
  }
}
} // namespace

int run_queries(std::string const &label)
{
  pqxx::connection conn; // PGHOST/PGDATABASE/... from the environment (tests/pg-sandbox)
  std::cout << label << ": connected to PostgreSQL server " << conn.server_version()
            << ", libpq protocol " << conn.protocol_version() << '\n';

  {
    pqxx::work tx{conn};
    expect("select 1", tx.query_value<int>("SELECT 1"), 1);
    expect(
      "server-side arithmetic",
      tx.query_value<std::int64_t>("SELECT 6::bigint * 7"), std::int64_t{42});

    tx.exec(
      "CREATE TEMP TABLE sdk_items (id serial PRIMARY KEY, name text NOT NULL, "
      "qty integer, note text)");
    tx.exec(
      "INSERT INTO sdk_items (name, qty, note) VALUES ($1, $2, $3)",
      pqxx::params{"alpha", 3, std::optional<std::string>{}});
    conn.prepare("add_item", "INSERT INTO sdk_items (name, qty) VALUES ($1, $2)");
    for (int i{1}; i <= 4; ++i)
      tx.exec(pqxx::prepped{"add_item"}, pqxx::params{"item " + pqxx::to_string(i), i * 10});

    expect("row count", tx.query_value<int>("SELECT count(*) FROM sdk_items"), 5);
    expect("sum", tx.query_value<int>("SELECT sum(qty) FROM sdk_items"), 103);

    std::vector<std::string> names;
    for (auto [name, qty] :
         tx.query<std::string, int>("SELECT name, qty FROM sdk_items ORDER BY id"))
      names.push_back(name + "=" + pqxx::to_string(qty));
    expect("first row", names.front(), std::string{"alpha=3"});
    expect("last row", names.back(), std::string{"item 4=40"});

    auto const null_note{
      tx.query_value<std::optional<std::string>>("SELECT note FROM sdk_items WHERE id = 1")};
    expect("null maps to nullopt", null_note.has_value(), false);

    expect(
      "quoting", tx.quote("it's"), std::string{"'it''s'"});
    tx.commit();
  }

  {
    pqxx::work tx{conn};
    tx.exec("CREATE TEMP TABLE sdk_rollback (v integer)");
    tx.exec("INSERT INTO sdk_rollback VALUES (1)");
    tx.abort();
    pqxx::nontransaction check{conn};
    expect(
      "rolled back table is gone",
      check.query_value<bool>("SELECT to_regclass('pg_temp.sdk_rollback') IS NULL"), true);
  }

  {
    pqxx::work tx{conn};
    bool caught{false};
    try
    {
      tx.exec("SELECT * FROM no_such_table_for_sdk_test");
    }
    catch (pqxx::sql_error const &e)
    {
      caught = (e.sqlstate() == "42P01");
    }
    expect("sql_error with sqlstate 42P01", caught, true);
  }

  {
    pqxx::work tx{conn};
    tx.exec("CREATE TEMP TABLE sdk_stream (k integer, v text)");
    {
      auto stream{pqxx::stream_to::table(tx, {"sdk_stream"}, {"k", "v"})};
      for (int i{0}; i < 1000; ++i)
        stream.write_values(i, "value " + pqxx::to_string(i));
      stream.complete();
    }
    std::int64_t total{0};
    for (auto [k, v] : tx.stream<int, std::string_view>("SELECT k, v FROM sdk_stream"))
      total += k + static_cast<std::int64_t>(v.size() > 0);
    expect("COPY round trip", total, std::int64_t{499500 + 1000});
    tx.commit();
  }

  return failures;
}
