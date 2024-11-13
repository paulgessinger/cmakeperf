# cmakeperf

`cmakeperf` is a simple tool to measure compile time and memory consumption. It
can collect measurements from compilation of a C/C++ based project and report
results from these measurements.

## Use with a compilation database

`cmakeperf` can use the common compilation database format that CMake can write
via `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON` (and is not actually tied to CMake).
It executes all the processes from a `compile_commands.json` file and will
monitor the total execution time and the maximum memory consumption.

## Use as a compiler / linker launcher

An alternative way of running `cmakeperf` is to configure CMake to use it as a
compiler / linker launcher. This is achieved by calling CMake with the
following argument in addition to any other ones:

```console
$ cmake <OTHER OPTIONS> |
        -DCMAKE_CXX_COMPILER_LAUNCHER=cmakeperf-intercept \
        -DCMAKE_CXX_LINKER_LAUNCHER=cmakeperf-intercept-ld
```

Then just run a build of your project or any specific target using

```console
# cmake --build <BUILD DIR>
```

You can run with multiple `-j` jobs, but note that the measurement results
might be less reliable.
