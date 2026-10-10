// Consumer smoke test for the libpqxx static SDK: the application-facing API is unchanged
// (#include <pqxx/pqxx>), and the program talks to a real (disposable) PostgreSQL server.
#include <pqxx/pqxx>

#include <cstdlib>
#include <iostream>

#include "queries.hxx"

int main(int argc, char **argv)
{
  std::cout << "libpqxx headers " << PQXX_VERSION << ", C++ " << __cplusplus << '\n';
  if (argc > 1 and std::string{argv[1]} == "--version-only")
    return EXIT_SUCCESS;
  try
  {
    int const failures{run_queries("consumer")};
    if (failures != 0)
    {
      std::cerr << failures << " check(s) failed\n";
      return EXIT_FAILURE;
    }
    std::cout << "all checks passed\n";
    return EXIT_SUCCESS;
  }
  catch (std::exception const &e)
  {
    std::cerr << "error: " << e.what() << '\n';
    return 2;
  }
}
