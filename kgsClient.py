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
    self.session.keep_alive = False
    self.api = kgsApi
    self.login = kgsName
    self.pwd = kgsPassword
    self.terminated = False
    self.rooms = {}
    self.games = {}
    self.channels = []
    self.msgQueueFilter = []
    self.msgQueue = []
    self.queueLock = Lock()
    self.queueFeed = Event()
    self.proc = None
    self.logMessages = 0
    self.logLock = Lock()
    self.msgLog = []
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
        req = self.session.get(self.api)
      except exceptions.Timeout:
        continue
      if req.status_code == 200:
        msg = loads(req.content.decode('utf-8'))
        if "messages" in msg:
          for x in msg["messages"]:
            self.processMessage(x)
      else:
        self.processMessage({"type": "LOGOUT"})
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
    self.startWaitMsg()
    if self.sendRequest(msg) != "OK":
      self.cancelWaitMsg()
      return None
    return self.endWaitMsg(msgFilter)
  def signIn(self):
    return self.sendRequestAndWaitAnswer({"type": "LOGIN", "name": self.login, "password": self.pwd, "locale": "en_US"}, lambda x: x["type"] == "LOGIN_SUCCESS") is not None
  def processMessage(self, msg):
    print("D: %s" % str(msg))
    self.logLock.acquire()
    try:
      if self.logMessages > 0:
        self.msgLog.append(msg)
    finally:
      self.logLock.release()
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
    elif msg["type"] == "GAME_JOIN":
      game = {"nodes":{0:{"nodeId": 0, "parentNode": -1, "position": 0, "props": []}}, "activeNode": 0}
      for x in msg["sgfEvents"]:
        self.parseSgfEvent(game, x)
      self.games[msg["channelId"]] = game
    elif msg["type"] == "GAME_UPDATE":
      game = self.games[msg["channelId"]]
      for x in msg["sgfEvents"]:
        self.parseSgfEvent(game, x)
    elif msg["type"] == "JOIN_COMPLETE":
      self.channels.append(msg["channelId"])
    elif msg["type"] == "UNJOIN":
      self.channels.remove(msg["channelId"])
  def findProp(self, props, prop):
    foundProp = None
    for x in props:
      if x["name"] == prop["name"] and ("color" not in prop or x["color"] == prop["color"]):
        foundProp = x
        break
    return foundProp
  def parseSgfEvent(self, game, event):
    node = game["nodes"][event["nodeId"]]
    if event["type"] == "CHILD_ADDED":
      newNode = {"nodeId": event["childNodeId"], "parentNode": node["nodeId"], "position": 0, "props": []}
      if "position" in event:
        newNode["position"] = event["position"]
      game["nodes"][newNode["nodeId"]] = newNode
    elif event["type"] == "CHILDREN_REORDERED":
      for pos in range(0, len(event["children"])):
        game["nodes"][event["children"][pos]]["position"] = pos
    elif event["type"] == "ACTIVATED":
      game["activeNode"] = node["nodeId"]
    elif event["type"] in {"PROP_ADDED", "PROP_CHANGED"}:
      prop = event["prop"]
      oldProp = self.findProp(node["props"], prop)
      if oldProp:
        node["props"].remove(oldProp)
      node["props"].append(prop)
    elif event["type"] == "PROP_REMOVED":
      prop = event["prop"]
      node["props"].remove(self.findProp(node["props"], prop))
    elif event["type"] == "PROP_GROUP_ADDED":
      for prop in event["props"]:
        oldProp = self.findProp(node["props"], prop)
        if oldProp:
          node["props"].remove(oldProp)
        node["props"].append(prop)
    elif event["type"] == "PROP_GROUP_REMOVED":
      for prop in event["props"]:
        node["props"].remove(self.findProp(node["props"], prop))
  def sendMessage(self, channelId, msg):
    self.sendRequest({"type": "CHAT", "channelId": channelId, "text": msg})
  def channelIdByRoomName(self, roomName):
    return list(self.rooms.keys())[list(self.rooms.values()).index(roomName)];
  def createDemo(self, channelId, boardSize, komi):
    self.startWaitMsg()
    game = self.sendRequestAndWaitAnswer({
      "type": "CHALLENGE_CREATE",
      "channelId": channelId,
      "callbackKey": 0,
      "global": False,
      "text": "",
      "proposal": {
        "gameType": "demonstration",
        "nigiri": False,
        "rules": {
          "rules": "chinese",
          "size": boardSize,
          "komi": komi,
          "timeSystem": "none"
        },
        "players": [{
          "role": "owner",
          "name": self.login
        }]
    }}, lambda x: x["type"] == "GAME_NOTIFY")
    gameId = None
    if game:
      gameId = game["game"]["channelId"]
    else:
      self.cancelWaitMsg()
      return None
    self.endWaitMsg(lambda x: x["type"] == "GAME_JOIN" and x["channelId"] == gameId)
    return gameId
  def demoSetInfo(self, channelId, playerWhite, playerBlack, place, gameName):
    self.sendRequest({
      "type": "KGS_SGF_CHANGE",
      "channelId": channelId,
      "sgfEvents": [
        {
          "type": "PROP_GROUP_ADDED",
          "nodeId": 0,
          "props": [
            {
              "name": "PLAYERNAME",
              "color": "white",
              "text": playerWhite
            }, {
              "name": "PLAYERNAME",
              "color": "black",
              "text": playerBlack
            }, {
              "name": "PLACE",
              "text": place
            }, {
              "name": "GAMENAME",
              "text": gameName
            }
          ]
        }
      ]
    })
  def demoPlayMove(self, channelId, colour, place):
    game = self.games[channelId]
    newNode = max(game["nodes"].keys()) + 1
    placeSgf = None
    if place.lower() == "pass":
      placeSgf = "PASS"
    else:
      x = ord(place[:1].lower()) - ord('a')
      y = 19 - int(place[1:])
      if x > 8:
        x -= 1
      placeSgf = {"x": x, "y": y}
    def findEvent(events, nodeId):
      for event in events:
        if event["type"] == "ACTIVATED" and event["nodeId"] == nodeId:
          return True
      return False
    self.sendRequestAndWaitAnswer({
      "type": "KGS_SGF_CHANGE",
      "channelId": channelId,
      "sgfEvents": [
        {
          "type": "CHILD_ADDED",
          "nodeId": game["activeNode"],
          "childNodeID": newNode
        },
        {
          "type": "PROP_ADDED",
          "nodeId": newNode,
          "prop": {
            "name": "MOVE",
            "loc": placeSgf,
            "color": colour
          }
        },
        {
          "type": "ACTIVATED",
          "nodeId": newNode,
          "prevNodeId": -1
        }
      ]
    }, lambda x: x["type"] == "GAME_UPDATE" and x["channelId"] == channelId and findEvent(x["sgfEvents"], newNode))
  def demoJumpToMove(self, channelId, moveNum):
    moveCur = moveNum
    nodes = self.games[channelId]["nodes"]
    curNode = nodes[0]
    while moveCur > 0:
      moveCur -= 1
      newCur = None
      lastPos = 0
      for node in nodes:
        if nodes[node]["parentNode"] == curNode["nodeId"] and nodes[node]["position"] == lastPos:
          newCur = nodes[node]
          lastPos += 1
      if not newCur:
        break
      else:
        curNode = newCur
    newNode = curNode["nodeId"]
    if newNode == self.games[channelId]["activeNode"]:
      return()
    def findEvent(events, nodeId):
      for event in events:
        if event["type"] == "ACTIVATED" and event["nodeId"] == nodeId:
          return True
      return False
    self.sendRequestAndWaitAnswer({
      "type": "KGS_SGF_CHANGE",
      "channelId": channelId,
      "sgfEvents": [
        {
          "type": "ACTIVATED",
          "nodeId": newNode,
          "prevNodeId": -1
        }
      ]
    }, lambda x: x["type"] == "GAME_UPDATE" and x["channelId"] == channelId and findEvent(x["sgfEvents"], newNode))
  def startWaitMsg(self):
    self.logLock.acquire()
    try:
      self.logMessages += 1
    finally:
      self.logLock.release()
  def cancelWaitMsg(self):
    self.logLock.acquire()
    try:
      self.logMessages -= 1
      if self.logMessages == 0:
        self.logMessages = []
    finally:
      self.logLock.release()
  def endWaitMsg(self, msgFilter):
    retMsg = None
    self.queueLock.acquire()
    try:
      self.msgQueueFilter.append(msgFilter)
      self.queueFeed.clear()
    finally:
      self.queueLock.release()
    self.logLock.acquire()
    try:
      self.logMessages -= 1
      for msg in self.msgLog:
        if msgFilter(msg):
          retMsg = msg
          break
      if self.logMessages == 0:
        self.msgLog = []
    finally:
      self.logLock.release()
    if retMsg is not None:
      self.queueLock.acquire()
      try:
        if msgFilter in self.msgQueueFilter:
          self.msgQueueFilter.remove(msgFilter)
          self.queueFeed.set()
      finally:
        self.queueLock.release()
      return retMsg
    if not self.proc:
      self.proc = threadStart(self.processResponse)
    retMsg = timeout(lambda: self.waitForQueueMsg(msgFilter), 20, None)
    if retMsg is None:
      self.queueLock.acquire()
      try:
        if msgFilter in self.msgQueueFilter:
          self.msgQueueFilter.remove(msgFilter)
          self.queueFeed.set()
      finally:
        self.queueLock.release()
    return retMsg
if __name__ == '__main__':
  from time import sleep
  kgsLogin = "vpgtpdtest"
  kgsPwd = "vpgtpdtest"
  client = KgsClient('http://localhost:8080/kgs/access', kgsLogin, kgsPwd)
  game = client.createDemo(client.channelIdByRoomName("Клуб Го Университета ИТМО"), 19, 7.5)
  if game:
    client.demoSetInfo(game, "Player White", "Player Black", "test client", "Test Game")
    client.demoPlayMove(game, "black", "k10")
    client.demoPlayMove(game, "white", "c17")
    client.demoJumpToMove(game, 1)
    client.demoPlayMove(game, "white", "d16")
    client.demoJumpToMove(game, 2)
    client.demoPlayMove(game, "black", "f17")
    sleep(20)
    print(str(client.games))
  client.terminate()