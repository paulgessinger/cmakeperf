#!/usr/bin/env python3
import subprocess as sp
import sys
import time
from datetime import datetime
import re
import csv
import json
import os
import re
import io

import click
import psutil


@click.command()
@click.argument("compile_db")
@click.option("--output", "-o", default="-", help="Output CSV to file, or stdout (use -)")
@click.option("--filter", default=".*", help="Filter input files by regex")
@click.option("--interval", type=float, default=0.5, help="Sample interval")
def main(compile_db, output, filter, interval):

  regex = re.compile(filter)
  cwd = os.getcwd()

  with open(compile_db) as fh:
    commands = json.load(fh)

  outstr = io.StringIO()

  if output == "-":
    if not sys.stdout.isatty():
      outstr = sys.stdout
  else:
    outstr = open(output, "w")
    print("I will write output to", output)

  writer = csv.writer(outstr, delimiter=',')
  writer.writerow(["file", "max_rss", "time"])
  outstr.flush()

  try:
    for item in commands:
      command = item["command"]
      file = item["file"]
      directory = item["directory"]

      rp = os.path.relpath(file, cwd)
      
      if not regex.match(file):
        continue

      os.chdir(directory)

      p = psutil.Popen(command, shell=True, stdout=sp.PIPE, stderr=sp.STDOUT)

      max_mem = 0
      start = datetime.now()

      while p.status() == "running":
        #  print("{1:%H:%M:%S} - {0:6>.2f}s - {2:>8.2f}MB - {3:>6.2f}% CPU".format(t, *samples[-1]))
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
        sys.stderr.write(f"{rp}: {mem/1e6:8.2f}M, max: {max_mem/1e6:8.2f}M [{delta.total_seconds():8.2f}s]\r")
        time.sleep(interval)
      p.wait()
      sys.stderr.write("\n")

      delta = datetime.now() - start
      writer.writerow([file, max_mem, delta.total_seconds()])
      outstr.flush()
  finally:
    if outstr != sys.stdout:
      outstr.close()


if "__main__" == __name__:
  main()
