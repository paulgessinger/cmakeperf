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
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

import click
import psutil


def run(command, file, directory, interval, progress):
  rp = os.path.relpath(file, os.getcwd())
  

  p = psutil.Popen(command, shell=True, cwd=directory, stdout=sp.PIPE, stderr=sp.STDOUT)

  max_mem = 0
  start = datetime.now()

  while p.status() in (psutil.STATUS_RUNNING, psutil.STATUS_SLEEPING):
    mem = 0
    children = list(p.children(recursive=True))
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
      sys.stderr.write(f"{rp}: {mem/1e6:8.2f}M, max: {max_mem/1e6:8.2f}M [{delta.total_seconds():8.2f}s]\r")
    time.sleep(interval)

  p.wait()
  if progress:
    sys.stderr.write("\n")

  delta = datetime.now() - start
  return file, max_mem, delta



@click.command()
@click.argument("compile_db")
@click.option("--output", "-o", default="-", help="Output CSV to file, or stdout (use -)")
@click.option("--filter", default=".*", help="Filter input files by regex")
@click.option("--interval", type=float, default=0.5, help="Sample interval")
@click.option("--jobs", "-j", type=int, default=1)
def main(compile_db, output, filter, interval, jobs):

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
    
  with ThreadPoolExecutor(jobs) as ex:
    futures = []
    try:
      for item in commands:
        command = item["command"]
        file = item["file"]
        directory = item["directory"]

        if not regex.match(file):
          continue
  
        futures.append(ex.submit(run, command, file, directory, interval, jobs==1))
  
      try:
        for f in as_completed(futures):
            file, max_mem, delta = f.result()
            writer.writerow([file, max_mem, delta.total_seconds()])
            outstr.flush()
            if jobs > 1:
              rp = os.path.relpath(file, os.getcwd())
              sys.stderr.write(f"{rp}: max: {max_mem/1e6:8.2f}M [{delta.total_seconds():8.2f}s]\n")
      finally:
        if outstr != sys.stdout:
          outstr.close()
    except KeyboardInterrupt:
      print("Ctrl+C")
      for f in futures:
          f.cancel()
      ex.shutdown()


if "__main__" == __name__:
  main()
