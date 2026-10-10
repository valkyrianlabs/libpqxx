#pragma once

#include <string>

// Runs the PostgreSQL round trips; returns the number of failed checks.
int run_queries(std::string const &label);
