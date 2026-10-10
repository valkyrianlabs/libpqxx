# Independent consumer of the libpqxx SDK, configured exactly like Vaulthalla would: native Meson,
# no CMake, the SDK found only through its private pkg-config directory
# (-Dpkg_config_path=<sdk>/lib/pkgconfig), and Vaulthalla's strictness (warnings as errors, -O3,
# C++23 by default; tests/sdk-test also builds C++20 and unity variants).
project('libpqxx-sdk-consumer', 'cpp',
  meson_version: '>=1.3.0',
  default_options: [
    'cpp_std=c++23',
    'warning_level=3',
    'werror=true',
    'buildtype=release',
    'optimization=3',
    'b_pie=true',
  ],
)

pqxx_dep = dependency(
  'libpqxx-vh',
  method: 'pkg-config',
  static: true,
  required: true,
)

# The linker map records which archive members and shared libraries satisfied each reference;
# tests/sdk-test uses it to prove libpqxx came from the SDK archive and libpq from the system.
consumer = executable('consumer',
  'consumer.cpp', 'queries.cpp',
  dependencies: pqxx_dep,
  cpp_args: ['-D_GLIBCXX_USE_CXX11_ABI=1'],
  link_args: ['-Wl,-Map=' + meson.current_build_dir() / 'consumer.map'],
)

test('consumer', consumer)
