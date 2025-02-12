#!/usr/bin/env python3
import subprocess as sp
import sys
import time
from datetime import datetime, timedelta
import csv
import json
import os
import re
import io
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
import shlex
from pathlib import Path
import contextlib
from typing import TextIO, Annotated, Callable
import functools

import typer
import psutil
import pandas as pd
from tabulate import tabulate
from filelock import FileLock


def run(
    command: str,
    file: str,
    *,
    directory: str,
    interval: float,
    progress: bool,
    progout: TextIO,
    post_clean: bool,
    dry_run: bool = False,
) -> tuple[str, float, timedelta]:
    if dry_run:
        return file, 0, timedelta(seconds=0)

    rp = os.path.relpath(file, os.getcwd())

    p = psutil.Popen(
        command, shell=True, cwd=directory, stdout=sp.PIPE, stderr=sp.STDOUT
    )

    max_mem = 0
    start = datetime.now()

    while p.status() in (psutil.STATUS_RUNNING, psutil.STATUS_SLEEPING):
        mem = 0
        try:
            mem += p.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

        for subp in p.children(recursive=True):
            try:
                mem += subp.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        # in KB, go to MB
        mem = mem
        max_mem = max(mem, max_mem)

        delta = datetime.now() - start
        if progress and progout.isatty():
            progout.write(
                f"[{mem/1e6:8.2f}M, max: {max_mem/1e6:8.2f}M] [{delta.total_seconds():8.2f}s] - {rp}\r"
            )
            progout.flush()
        time.sleep(interval)

    p.wait()

    if progress and progout.isatty():
        progout.write("\n")

    if post_clean:
        m = re.search(r"-o ?([\w\/\.]*)", command)
        assert m is not None, "Could not extract output"
        output = os.path.join(directory, m.group(1))
        os.remove(output)

    delta = datetime.now() - start
    return file, max_mem, delta


app = typer.Typer(no_args_is_help=True)


@app.command()
def collect(
    compile_db: Annotated[
        typer.FileText, typer.Argument(help="Path to compile_commands.json")
    ],
    output: Annotated[
        str | None,
        typer.Option("--output", "-o", help="Output CSV file, use - for stdout"),
    ] = None,
    filter: Annotated[
        str, typer.Option(help="Regular expression to select compilation units to run")
    ] = ".*",
    exclude: Annotated[
        str, typer.Option(help="Regular expression to remove compilation units to run")
    ] = "$^",
    interval: Annotated[
        float, typer.Option(help="Sampling interval to collect memory usage at")
    ] = 0.5,
    jobs: Annotated[int, typer.Option(help="Number of concurrent jobs to run")] = 1,
    post_clean: Annotated[
        bool, typer.Option(help="Clean up after the compilation")
    ] = False,
):
    filter_ex = re.compile(filter)
    exclude_ex = re.compile(exclude)

    commands = json.load(compile_db)

    with contextlib.ExitStack() as stack:
        out_fh = io.StringIO()  # we will throw this away
        progout: TextIO = sys.stdout  # default to stderr so we can pipe it
        if output is not None:
            if output == "-":
                out_fh = sys.stdout
                progout = sys.stderr
            else:
                p = Path(output)
                if p.is_dir():
                    raise ValueError(f"Output {output} is a directory")
                out_fh = stack.enter_context(p.open("w"))

        writer = csv.writer(out_fh, delimiter=",")

        writer.writerow(["file", "max_rss", "time", "type"])

        with ThreadPoolExecutor(jobs) as ex:
            futures = []
            try:
                for item in commands:
                    command = item["command"]
                    file = item["file"]
                    directory = item["directory"]

                    if not filter_ex.match(file):
                        continue

                    if exclude_ex.match(file):
                        continue

                    futures.append(
                        ex.submit(
                            run,
                            command,
                            file,
                            directory=directory,
                            interval=interval,
                            progress=jobs == 1,
                            progout=progout,
                            post_clean=post_clean,
                        )
                    )

                for idx, f in enumerate(as_completed(futures)):
                    file, max_mem, delta = f.result()
                    rp = os.path.relpath(file, os.getcwd())
                    writer.writerow([rp, max_mem, delta.total_seconds(), "compile"])
                    if jobs > 1 or not progout.isatty():
                        perc = (idx + 1) / len(futures) * 100
                        cur = str(idx + 1).rjust(math.ceil(math.log10(len(futures))))
                        progout.write(
                            f"[{cur}/{len(futures)}, {perc:5.1f}%] [{max_mem/1e6:8.2f}M] [{delta.total_seconds():8.2f}s] - {rp}\n"
                        )
                        progout.flush()

            except KeyboardInterrupt:
                print("Ctrl+C")
                for f in futures:
                    f.cancel()
                ex.shutdown()


@app.command("print")
def fn_print(
    data_file: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            help="Input CSV file that was written by cmakeperf",
        ),
    ],
    number: Annotated[
        int, typer.Option("--number", "-n", help="Number of entries to show")
    ] = 10,
    filter: Annotated[str, typer.Option(help="Filter input files by regex")] = ".*",
    exclude: Annotated[str, typer.Option(help="Exclude input files by regex")] = "$^",
):
    df = pd.read_csv(data_file)
    df.max_rss /= 1e6

    filter_ex = re.compile(filter)
    exclude_ex = re.compile(exclude)
    mask = [
        filter_ex.match(f) is None or exclude_ex.match(f) is not None for f in df.file
    ]
    df.drop(df[mask].index.tolist(), inplace=True)
    filenames = df.file[df.type == "compile"]

    if len(filenames) == 1:
        filenames = [os.path.basename(filenames.iloc[0])]
    else:
        prefix = os.path.commonprefix(list(filenames))
        filenames = [f[len(prefix) :] if f != prefix else f for f in filenames]

    df.loc[df.type == "compile", "file"] = filenames

    df.drop(columns="type", inplace=True)

    mem = df.sort_values(by="max_rss", ascending=False)
    time = df.sort_values(by="time", ascending=False)

    print(
        tabulate(
            [list(r) for _, r in mem.head(number).iterrows()],
            headers=("file", "max_rss [M]", "time [s]"),
            floatfmt=("", ".2f", ".2f"),
        )
    )
    print()
    print(
        tabulate(
            [list(r) for _, r in time.head(number).iterrows()],
            headers=("file", "max_rss [M]", "time [s]"),
            floatfmt=("", ".2f", ".2f"),
        )
    )


def _run_intercept(*args, type: str, **kwargs):
    output_csv = Path(
        os.environ.get("CMAKEPERF_OUTPUT_CSV", Path.cwd() / "cmakeperf.csv")
    )
    lock_path = output_csv.with_suffix(".lock")

    interval = float(os.environ.get("CMAKEPERF_INTERVAL", kwargs.pop("interval", 0.5)))

    rp, max_mem, delta = run(*args, interval=interval, **kwargs)

    lock = FileLock(lock_path)

    with lock:
        exists = output_csv.exists()
        with output_csv.open("a+") as fh:
            writer = csv.writer(fh, delimiter=",")
            if not exists:
                writer.writerow(["file", "max_rss", "time", "type"])
            writer.writerow([rp, max_mem, delta.total_seconds(), type])


def with_args(func: Callable[[list[str]], None]):
    @functools.wraps(func)
    def wrapper():
        args = sys.argv[1:]
        if len(args) == 1 and args[0] in ("-h", "--help"):
            print(
                f"Usage: {os.path.basename(sys.argv[0])} <compiler or linker command>"
            )
            return
        return func(args)

    return wrapper


@with_args
def intercept(args: list[str]):
    command = shlex.join(args)

    file = args[-1]

    _run_intercept(
        command,
        file,
        directory=os.getcwd(),
        progress=False,
        progout=sys.stdout,
        post_clean=False,
        dry_run=False,
        type="compile",
    )


@with_args
def intercept_ld(args: list[str]):
    command = shlex.join(args)

    index = args.index("-o")
    file = args[index + 1]

    assert file is not None

    _run_intercept(
        command,
        file,
        directory=os.getcwd(),
        progress=False,
        progout=sys.stdout,
        post_clean=False,
        dry_run=False,
        type="link",
    )
