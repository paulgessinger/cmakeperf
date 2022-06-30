# cmakeperf

`cmakeperf` is a simple tool to measure compile time and memory consumption. 
It uses the common compilation database format that cmake can write via `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON` 
(and is not actually tied to cmake). It executes all the processes from a `compile_commands.json` file
and will monitor the total execution time and the maximum memory consumption.
