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
from typing import TextIO

import click
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
        mem = p.memory_info().rss
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


@click.group()
def main():
    pass


@main.command()
@click.argument("compile_db", type=click.Path(dir_okay=False, exists=True))
@click.option("--output", "-o", help="Output CSV to file, or stdout (use -)")
@click.option("--filter", default=".*", help="Filter input files by regex")
@click.option("--interval", type=float, default=0.5, help="Sample interval")
@click.option("--jobs", "-j", type=int, default=1)
@click.option("--post-clean/--no-post-clean")
def collect(compile_db, output, filter, interval, jobs, post_clean):
    regex = re.compile(filter)

    with open(compile_db) as fh:
        commands = json.load(fh)

    if not output:
        outstr = io.StringIO()
    else:
        outstr = open(output, "w")
        print("I will write output to", output)

    progout: TextIO = sys.stdout

    writer = csv.writer(outstr, delimiter=",")
    writer.writerow(["file", "max_rss", "time", "type"])
    outstr.flush()

    try:
        with ThreadPoolExecutor(jobs) as ex:
            futures = []
            try:
                for item in commands:
                    command = item["command"]
                    file = item["file"]
                    directory = item["directory"]

                    if not regex.match(file):
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
                    outstr.flush()
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
    finally:
        if outstr != sys.stdout:
            outstr.close()


@main.command("print")
@click.argument("data_file", type=click.Path(dir_okay=False, exists=True))
@click.option("--number", "--n", "-n", default=10)
@click.option("--filter", default=".*", help="Filter input files by regex")
def fn_print(data_file, number, filter):
    df = pd.read_csv(data_file)
    df.max_rss /= 1e6

    ex = re.compile(filter)
    mask = [not ex.match(f) is not None for f in df.file]
    df.drop(df[mask].index.tolist(), inplace=True)
    filenames = df.file[df.type == "compile"]

    prefix = os.path.commonprefix(list(filenames))
    filenames = [f[len(prefix) :] for f in filenames]

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
    # print(max_mem, delta)

    lock = FileLock(lock_path)

    with lock:
        exists = output_csv.exists()
        with output_csv.open("a+") as fh:
            writer = csv.writer(fh, delimiter=",")
            if not exists:
                writer.writerow(["file", "max_rss", "time", "type"])
            writer.writerow([rp, max_mem, delta.total_seconds(), type])


@main.command("intercept", context_settings=dict(ignore_unknown_options=True))
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def intercept(args):
    command = shlex.join(args)
    # print(command)

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


@main.command("intercept-ld", context_settings=dict(ignore_unknown_options=True))
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def intercept_ld(args):
    command = shlex.join(args)

    index = args.index("-o")
    file = args[index + 1]
    # print(command)
    # print(file)

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


if "__main__" == __name__:
    main()
