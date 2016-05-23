#!/usr/bin/python3
# -*- coding: utf-8 -*-

import socket

# Запускает функцию в новом потоке
def threadStart(func):
  from threading import Thread
  class NewThread(Thread):
    def __init__(self):
      Thread.__init__(self)
    def run(self):
      func()    
  nt = NewThread()
  nt.start()
  return nt

# Отправляет данные из сети программе
def sockToApp(sock, proc):
  import sys
  try:
    while True:
      data = sock.recv(4096)
      sys.stdout.write(data.decode('utf-8'))
      sys.stdout.flush()
      if not data:
        break
      proc.stdin.write(data)
      proc.stdin.flush()
  finally:
    sock.close()
    print("Lost connection")
    try:
      proc.stdin.write("quit\n".encode('utf-8'))
      proc.stdin.flush()
    except:
      proc.kill()

# Отправляет данные программы в сеть
def appToSock(sock, proc):
  import sys
  try:
    try:
      while True:
        data = proc.stdout.readline()
        sys.stdout.write(data.decode('utf-8'))
        sys.stdout.flush()
        if not data:
          break
        sock.send(data)
    finally:
      sock.close()
  except:
    pass
    
# Поделючается к серверу
def clientStart(host, port, cmdLine, setup):
  import shlex
  from subprocess import Popen, PIPE
  
  proc = Popen(shlex.split(cmdLine), stdin = PIPE, stdout = PIPE, stderr=PIPE)
  for x in setup:
    proc.stdin.write(("%s\n" % x).encode('utf-8'))
    proc.stdin.flush()
    while True:
      line = proc.stdout.readline().decode('utf-8')[:-1]
      if line and line[-1] == '\r':
        line = line[:-1]
      if not line:
        break
  sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  sock.connect((host, port))
  t1 = threadStart(lambda: sockToApp(sock, proc))
  t2 = threadStart(lambda: appToSock(sock, proc))
  t1.join()
  t2.join()

if __name__ == '__main__':
  from configparser import ConfigParser
  import sys
  config = ConfigParser()
  config.read(sys.argv[1])
  host = config["Client"]["Host"]
  port = int(config["Client"]["Port"])
  cmd = config["Client"]["Cmd"]
  playerSetup = list(config["Commands"].values())
  clientStart(host, port, cmd, playerSetup)