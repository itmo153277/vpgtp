#!/usr/bin/python3
# -*- coding: utf-8 -*-

# Выполнение функции с таймаутом по времени (в секундах)
def timeout(func, time, timeoutVal = "timeout"):
  from threading import Thread
  # Внешний поток для выполнения функции
  class InterruptableThread(Thread):
    def __init__(self):
      Thread.__init__(self)
      self.result = None
    def run(self):
      try:
        self.result = func()
      except:
        self.result = None
  it = InterruptableThread()
  it.start()
  it.join(time)
  if it.isAlive():
    return timeoutVal
  else:
    return it.result
 
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

class KgsClient(object):
  def __init__(self, kgsApi, kgsName, kgsPassword):
    from requests import Session
    from threading import Lock, Event
    self.session = Session()
    self.api = kgsApi
    self.login = kgsName
    self.pwd = kgsPassword
    self.terminated = False
    self.rooms = {}
    self.channels = []
    self.msgQueueFilter = []
    self.msgQueue = []
    self.queueLock = Lock()
    self.queueFeed = Event()
    self.proc = None
    if not self.signIn():
      raise ValueError
  def terminate(self):
    self.sendRequest({"type":"LOGOUT"})
    self.proc.join()
  def processResponse(self):
    from requests import exceptions
    from time import sleep
    from json import loads
    while not self.terminated:
      try:
        req = self.session.get(self.api, timeout = 20)
      except exceptions.Timeout:
        continue
      if req.status_code == 200:
        msg = loads(req.content.decode('utf-8'))
        if "messages" in msg:
          for x in msg["messages"]:
            self.processMessage(x)
  def sendRequest(self, msg):
    from requests import exceptions
    from json import dumps
    print("U: %s" % str(msg))
    ret = None
    try:
      req = self.session.post(self.api, data = dumps(msg), timeout = 20)
    except exceptions.Timeout:
      return None
    ret = req.text
    return ret
  def waitForQueueMsg(self, msgFilter):
    ret = None
    hope = True
    while hope and ret is None:
      self.queueFeed.wait()
      self.queueLock.acquire()
      try:
        for x in self.msgQueue:
          if msgFilter(x):
            ret = x
            break
        if ret is not None:
          self.msgQueue.remove(ret)
        elif msgFilter not in self.msgQueueFilter:
          hope = False
      finally:
        self.queueLock.release()
    return ret
  def sendRequestAndWaitAnswer(self, msg, msgFilter):
    ret = None
    self.queueLock.acquire()
    try:
      self.msgQueueFilter.append(msgFilter)
      self.queueFeed.clear()
    finally:
      self.queueLock.release()
    if self.sendRequest(msg) != "OK":
      return None
    if not self.proc:
      self.proc = threadStart(self.processResponse)
    ret = timeout(lambda: self.waitForQueueMsg(msgFilter), 20, None)
    if ret is None:
      self.queueLock.acquire()
      try:
        if msgFilter in self.msgQueueFilter:
          self.msgQueueFilter.remove(msgFilter)
          self.queueFeed.set()
      finally:
        self.queueLock.release()
    return ret
  def signIn(self):
    return self.sendRequestAndWaitAnswer({"type": "LOGIN", "name": self.login, "password": self.pwd, "locale": "en_US"}, lambda x: x["type"] == "LOGIN_SUCCESS") is not None
  def processMessage(self, msg):
    print("D: %s" % str(msg))
    self.queueLock.acquire()
    try:
      for x in self.msgQueueFilter:
        if x(msg):
          self.msgQueue.append(msg)
          self.msgQueueFilter.remove(x)
          self.queueFeed.set()
    finally:
      self.queueLock.release()
    if msg["type"] == "LOGOUT":
      self.terminated = True
      self.queueLock.acquire()
      try:
        self.msgQueueFilter = []
        self.queueFeed.set()
      finally:
        self.queueLock.release()
    elif msg["type"] == "IDLE_WARNING":
      self.sendRequest({"type": "WAKE_UP"})
    elif msg["type"] == "ROOM_NAMES":
      for x in msg["rooms"]:
        self.rooms[x["channelId"]] = x["name"]
    elif msg["type"] == "JOIN_COMPLETE":
      self.channels.append(msg["channelId"])
    elif msg["type"] == "UNJOIN":
      self.channels.remvoe(msg["channelId"])
  def sendMessage(self, channelId, msg):
    self.sendRequest({"type": "CHAT", "channelId": channelId, "text": msg})
  def channelIdByRoomName(self, roomName):
    return list(self.rooms.keys())[list(self.rooms.values()).index(roomName)];
if __name__ == '__main__':
  from time import sleep
  client = KgsClient('http://127.0.0.1:8080/kgs/access', 'vpgtpdtest', 'vpgtpdtest')
  client.terminate()