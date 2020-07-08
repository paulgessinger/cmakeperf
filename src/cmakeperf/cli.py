#!/usr/bin/env python3
import subprocess as sp
import sys
import time
from datetime import datetime
import csv
import json
import os
import re
import io
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

import click
import psutil
import pandas as pd
from tabulate import tabulate


def run(command, file, directory, interval, progress, progout):
  rp = os.path.relpath(file, os.getcwd())
  

  p = psutil.Popen(command, shell=True, cwd=directory, stdout=sp.PIPE, stderr=sp.STDOUT)

  max_mem = 0
  start = datetime.now()

  while p.status() in (psutil.STATUS_RUNNING, psutil.STATUS_SLEEPING):
    mem = 0
    for subp in p.children(recursive=True):
      try:
        mem += subp.memory_info().rss
      except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

    # in KB, go to MB
    mem = mem
    max_mem = max(mem, max_mem)

    delta = datetime.now() - start
    if progress:
      progout.write(f"[{mem/1e6:8.2f}M, max: {max_mem/1e6:8.2f}M] [{delta.total_seconds():8.2f}s] - {rp}\r")
      progout.flush()
    time.sleep(interval)

  p.wait()
  if progress:
    progout.write("\n")

  delta = datetime.now() - start
  return file, max_mem, delta


@click.group()
def main():
    pass


@main.command()
@click.argument("compile_db", type=click.Path(dir_okay=False, exists=True))
@click.option("--output", "-o", default="-", help="Output CSV to file, or stdout (use -)")
@click.option("--filter", default=".*", help="Filter input files by regex")
@click.option("--interval", type=float, default=0.5, help="Sample interval")
@click.option("--jobs", "-j", type=int, default=1)
def collect(compile_db, output, filter, interval, jobs):

  regex = re.compile(filter)
  cwd = os.getcwd()

  with open(compile_db) as fh:
    commands = json.load(fh)

  outstr = io.StringIO()
  progout = sys.stdout

  if output == "-":
    if not sys.stdout.isatty():
      outstr = sys.stdout
      progout = sys.stderr
  else:
    outstr = open(output, "w")
    print("I will write output to", output)

  writer = csv.writer(outstr, delimiter=',')
  writer.writerow(["file", "max_rss", "time"])
  outstr.flush()
    
  with ThreadPoolExecutor(jobs) as ex:
    futures = []
    try:
      for item in commands:
        command = item["command"]
        file = item["file"]
        directory = item["directory"]

        if not regex.match(file):
          continue
  
        futures.append(ex.submit(run, command, file, directory, interval, jobs==1, progout))
  
      try:
        for idx, f in enumerate(as_completed(futures)):
            file, max_mem, delta = f.result()
            rp = os.path.relpath(file, os.getcwd())
            writer.writerow([rp, max_mem, delta.total_seconds()])
            outstr.flush()
            if jobs > 1:
              perc = (idx+1) / len(futures) * 100
              cur = str(idx+1).rjust(math.ceil(math.log10(len(futures))))
              progout.write(f"[{cur}/{len(futures)}, {perc:5.1f}%] [{max_mem/1e6:8.2f}M] [{delta.total_seconds():8.2f}s] - {rp}\n")
              progout.flush()
      finally:
        if outstr != sys.stdout:
          outstr.close()
    except KeyboardInterrupt:
      print("Ctrl+C")
      for f in futures:
          f.cancel()
      ex.shutdown()

@main.command("print")
@click.argument("data_file", type=click.Path(dir_okay=False, exists=True))
def fn_print(data_file):
  df = pd.read_csv(data_file)
  df.max_rss /= 1e6
  mem = df.sort_values(by="max_rss", ascending=False)
  time = df.sort_values(by="time", ascending=False)

  print(tabulate([r for _, r in mem.head(10).iterrows()], headers=("file", "max_rss [M]", "time [s]"), floatfmt=("", ".2f", ".2f")))
  print()
  print(tabulate([r for _, r in time.head(10).iterrows()], headers=("file", "max_rss [M]", "time [s]"), floatfmt=("", ".2f", ".2f")))

if "__main__" == __name__:
  main()
