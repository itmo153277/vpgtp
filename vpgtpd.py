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

# Позволяет подключаться к KGS и транслировать партию
class KgsClient(object):
  # Принимает адрес API, логин и пароль
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
  # Отключается от KGS
  def terminate(self):
    self.sendRequest({"type":"LOGOUT"})
    self.proc.join()
  # Обрабатывает ответ
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
  # Отправляет запрос на сервер
  def sendRequest(self, msg):
    from requests import exceptions
    from json import dumps
    if self.terminated:
      return None
    print("U: %s" % str(msg))
    ret = None
    try:
      req = self.session.post(self.api, data = dumps(msg), timeout = 20)
    except exceptions.Timeout:
      return None
    ret = req.text
    return ret
  # Ожидает прихода сообщения по фильтру
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
        else:
          self.queueFeed.clear()
      finally:
        self.queueLock.release()
    return ret
  # Отправляет запрос и ждет ответа
  def sendRequestAndWaitAnswer(self, msg, msgFilter):
    if self.terminated:
      return None
    self.startWaitMsg()
    if self.sendRequest(msg) != "OK":
      self.cancelWaitMsg()
      return None
    return self.endWaitMsg(msgFilter)
  # Логинится
  def signIn(self):
    return self.sendRequestAndWaitAnswer({"type": "LOGIN", "name": self.login, "password": self.pwd, "locale": "en_US"}, lambda x: x["type"] == "LOGIN_SUCCESS") is not None
  # Обрабатывает сообщение
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
  # Ищет определенное SGF свойство
  def findProp(self, props, prop):
    foundProp = None
    for x in props:
      if x["name"] == prop["name"] and ("color" not in prop or x["color"] == prop["color"]) and ("loc" not in prop or x["loc"] == prop["loc"]):
        foundProp = x
        break
    return foundProp
  # Обрабатывает SGF событие
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
  # Отправляет событие в чат
  def sendMessage(self, channelId, msg):
    self.sendRequest({"type": "CHAT", "channelId": channelId, "text": msg})
  # Ищет комнату
  def channelIdByRoomName(self, roomName):
    return list(self.rooms.keys())[list(self.rooms.values()).index(roomName)];
  # Создает партию для демонстрации
  def createDemo(self, channelId, boardSize, komi, timeSystem, mainTime, byoyomiTime, byoyomiStones):
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
          "timeSystem": timeSystem,
          "mainTime": mainTime,
          "byoYomiTime": byoyomiTime,
          "byoYomiStones": byoyomiStones
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
  # Обновляет информацию
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
  # Делает ход в демонстрации
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
          "childNodeId": newNode
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
  # Перемещается по демонстрационной партии
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
  # Обновляет информацию о времени
  def demoTimeLeft(self, channelId, colour, mainTime, byoyomiStones):
    self.sendRequest({
      "type": "KGS_SGF_CHANGE",
      "channelId": channelId,
      "sgfEvents": [
        {
          "type": "PROP_ADDED",
          "nodeId": self.games[channelId]["activeNode"],
          "prop":
            {
              "name": "TIMELEFT",
              "color": colour,
              "float": mainTime,
              "int": byoyomiStones
            }
        }
      ]
    })
  # Обновляет информацию о результате
  def demoSetResult(self, channelId, result):
    self.sendRequest({
      "type": "KGS_SGF_CHANGE",
      "channelId": channelId,
      "sgfEvents": [
        {
          "type": "PROP_ADDED",
          "nodeId": 0,
          "prop": {
            "name": "RESULT",
            "text": result
          }
        }
      ]
    })
  # Сохраняет игру на сервере
  def saveGame(self, channelId):
    self.sendRequest({
      "type": "GAME_LIST_ENTRY_SET_FLAGS",
      "channelId": channelId,
      "saved": True
    })
  # Начало ожидания сообщения
  def startWaitMsg(self):
    self.logLock.acquire()
    try:
      self.logMessages += 1
    finally:
      self.logLock.release()
  # Отмена ожидания сообщения
  def cancelWaitMsg(self):
    self.logLock.acquire()
    try:
      self.logMessages -= 1
      if self.logMessages == 0:
        self.msgLog = []
    finally:
      self.logLock.release()
  # Конец ожидания сообщения
  def endWaitMsg(self, msgFilter):
    if self.terminated:
      return None
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

# Класс для управления временем игрока
class Timer(object):
  from math import ceil
  ceil = staticmethod(ceil)
  from time import time
  time = staticmethod(time)
  # Основное время, бееми и количество ходов за бееми
  def __init__(self, mainTime, byoyomiTime, byoyomiMoves):
    self.mainTime = mainTime
    self.byoyomiTime = byoyomiTime
    self.byoyomiTimeCurrent = byoyomiTime
    self.byoyomiMoves = byoyomiMoves
    self.byoyomiMovesCurrent = byoyomiMoves
    self.localTime = self.time()
  # Наинает отсет времени хода и возвращает время ожидания в секундах
  def startMove(self):
    if self.byoyomiMoves > 0 and self.mainTime == 0 and self.byoyomiTime == 0:
      return None
    self.localTime = self.time()
    return self.ceil(self.mainTime + self.byoyomiTimeCurrent)
  # Возвращает время ожидания в секундах для текущего отсчета
  def sameMove(self):
    if self.byoyomiMoves > 0 and self.mainTime == 0 and self.byoyomiTime == 0:
      return None
    diffTime = self.time() - self.localTime
    return self.ceil(self.mainTime + self.byoyomiTimeCurrent - diffTime)
  # Пересчитывает оставшееся время и возвращает пару (Время, Число оставшихся ходов)
  def endMove(self):
    if self.byoyomiMoves > 0 and self.mainTime == 0 and self.byoyomiTime == 0:
      return 0, self.byoyomiMoves;
    diffTime = self.time() - self.localTime
    self.mainTime -= diffTime
    if self.mainTime <= 0:
      self.byoyomiMovesCurrent -= 1
      self.byoyomiTimeCurrent += self.mainTime
      self.mainTime = 0
      if self.byoyomiMovesCurrent == 0 and self.byoyomiTimeCurrent > 0:
        self.byoyomiTimeCurrent = self.byoyomiTime
        self.byoyomiMovesCurrent = self.byoyomiMoves
      return int(self.byoyomiTimeCurrent), self.byoyomiMovesCurrent
    else:
      return int(self.mainTime), 0
  # Проверяет есть ли время у игрока
  def lostOnTime(self):
    if self.byoyomiMoves > 0 and self.mainTime == 0 and self.byoyomiTime == 0:
      return False
    return self.byoyomiTimeCurrent + self.mainTime <= 0
  # Возвращает пару (Время, Число оставшихся ходов) для текущего отсчета без пересчета оставшегося времени
  def currentTime(self):
    if self.byoyomiMoves > 0 and self.mainTime == 0 and self.byoyomiTime == 0:
      return 0, self.byoyomiMoves
    diffTime = self.time() - self.localTime
    mainTime = self.mainTime - diffTime
    if mainTime <= 0:
      byoyomiTimeCurrent = self.byoyomiTimeCurrent + mainTime
      return int(byoyomiTimeCurrent), self.byoyomiMovesCurrent
    else:
      return int(mainTime), 0
  # Возвращает пару (Время, Число оставшихся ходов) для прошлого отсчета
  def lastTime(self):
    if self.byoyomiMoves > 0 and self.mainTime == 0 and self.byoyomiTime == 0:
      return 0, self.byoyomiMoves
    if self.mainTime > 0:
      return int(self.mainTime), 0
    else:
      return int(self.byoyomiTimeCurrent), self.byoyomiMovesCurrent

# Класс игрока для управления удаленным игроком
class Player(object):
  # Принимает класс socket в качестве параметра
  def __init__(self, session):
    from threading import Lock, Event
    self.session = session
    self.data = ""
    self.dead = False
    self.lock = Lock()
    self.feedEvent = Event()
    self.bufLock = Lock()
    threadStart(self.process)
    self.id = self.readLine()
    reqCommands = ["known_command", "name", "quit", "boardsize", "komi", "clear_board", "final_score", "final_status_list", "play", "genmove"]
    for x in reqCommands:
      if self.sendCommandWithTimeout("known_command %s" % x)[0].lower() != "= true":
        raise ValueError
    self.canCleanup = self.sendCommandWithTimeout("known_command kgs-genmove_cleanup")[0].lower() == "= true"
    self.name = "%s %s" % (self.sendCommandWithTimeout("name")[0][2:], self.sendCommandWithTimeout("version")[0][2:])
  # Осуществляет обработку
  def process(self):
    while not self.dead:
      try:
        data = self.session.recv(4096)
        if not data:
          self.dead = True
          self.session.close()
          self.feedEvent.set()
          break;
        self.bufLock.acquire()
        try:
          self.feedEvent.set()
          self.data = (self.data.encode("utf-8") + data).decode("utf-8")
        finally:
          self.bufLock.release()
      except:
        self.feedEvent.set()
        self.session.close()
        self.dead = True
  # Отправляет строку
  def sendLine(self, str):
    if not self.dead:
      self.session.send(("%s\n" % str).encode('utf-8'))
  # Получает строку
  def readLine(self):
    line = ""
    self.bufLock.acquire()
    try:
      line = self.data
      offset = 0
      while True:
        pos = line.find('\n', offset)
        if pos >= 0:
          self.data = line[pos + 1:]
          line = line[:pos]
          break
        offset = len(line)
        self.feedEvent.clear()
        self.bufLock.release()
        try:
          self.feedEvent.wait()
        finally:
          self.bufLock.acquire()
        if offset == len(self.data):
          self.data = ""
          break
        line = self.data
    finally:
      self.bufLock.release()
    if line and line[-1] == '\r':
      line = line[:-1]
    return line
  # Отправяет команду и возвращает список строк из ответа
  def sendCommand(self, command):
    from re import sub
    lines = []
    if not self.dead:
      self.lock.acquire()
      try:
        try:
          self.sendLine(command)
          while True:
            lastLine = self.readLine()
            if not lastLine:
              break
            lines.append(lastLine)
        except:
          self.dead = True
          self.session.close()
      finally:
        self.lock.release()
      if len(lines) > 0:
        lines[0] = sub(r"^=\d+ ", "= ", lines[0])
    return lines
  # Отправляет команду и возвращает список строк из ответа с таймаутом
  def sendCommandWithTimeout(self, command):
    res = timeout(lambda: self.sendCommand(command), 10)
    if res == "timeout":
      self.dead = True
      self.session.close()
      return []
    else:
      return res

# Класс судьи для проверки ходов и регистрации партии
class Referee(object):
  # Принимает командную строку и список команд GTP для настройки судьи
  def __init__(self, command, setupCommands):
    from subprocess import Popen, PIPE
    from threading import Lock
    import shlex
    self.lock = Lock()
    self.proc = Popen(shlex.split(command), stdin = PIPE, stdout = PIPE)
    reqCommands = ["known_command", "name", "version", "quit", "boardsize", "komi", "clear_board", "final_score", "play", "move_history"]
    for x in reqCommands:
      if self.sendCommand("known_command %s" % x)[0].lower() != "= true":
        raise ValueError
    self.name = "%s %s" % (self.sendCommand("name")[0][2:], self.sendCommand("version")[0][2:])
    for x in setupCommands:
      self.sendCommand(x)
  # Отправляет команду и возвращает список строк из ответа
  def sendCommand(self, command):
    from re import sub
    lines = []
    self.lock.acquire()
    try:
      self.proc.stdin.write(('%s\n' % command).encode('utf-8'))
      self.proc.stdin.flush()
      while True:
        line = self.proc.stdout.readline().decode('utf-8')[:-1]
        if line and line[-1] == '\r':
          line = line[:-1]
        if not line:
          break
        else:
          lines.append(line)
    finally:
      self.lock.release()
    if len(lines) > 0:
      lines[0] = sub(r"^=\d+ ", "= ", lines[0])
    return lines
  # Вводит игрока в курс партии
  def preparePlayer(self, player):
    moves = self.sendCommand("move_history")
    moves[0] = moves[0][2:]
    if moves[0]:
      for x in reversed(moves):
        player.sendCommand("play %s" % x)
  # Проверяет не закончилась ли партия (нужно ли переходить к подсчету)
  def gameEnded(self):
    moves = self.sendCommand("move_history")[:2]
    moves[0] = moves[0][2:]
    return set(x.lower() for x in moves) == set(['black pass', 'white pass'])

# Класс игры
class Game(object):
  # Принимает командную строку судью, команды для его настройки, API-адрес, комнату, логин и пароль KGS, заголовок игры, имена ботов, основное время, байоми и число ходов за байоми
  def __init__(self, referee, setupCommands, kgsApi, kgsRoom, kgsNick, kgsPwd, kgsTitle, names, ids, mainTime, byoyomiTime, byoyomiMoves):
    from threading import Lock, Event
    from random import randint
    self.name = kgsTitle
    self.colour = None
    self.colours = ['black', 'white']
    self.timers = []
    self.players = {}
    self.playerColours = {}
    self.playerEvents = {'black': Event(), 'white': Event()}
    self.playerBusy = Lock()
    self.result = ""
    self.cleanupMode = False
    self.referee = Referee(referee, setupCommands)
    self.kgsClient = KgsClient(kgsApi, kgsNick, kgsPwd)
    timeMode = "absolute"
    if byoyomiMoves > 0:
      timeMode = "canadian"
    self.kgsGame = self.kgsClient.createDemo(self.kgsClient.channelIdByRoomName(kgsRoom), 19, 7.5, timeMode, mainTime, byoyomiTime, byoyomiMoves)
    colour = randint(0,1)
    playerWhite = ""
    playerBlack = ""
    for x in range(0, len(ids)):
      print("%s: %s - %s" % (self.name, names[x], self.colours[colour]))
      playerName = names[x]
      if colour == 0:
        playerBlack = playerName[:10]
      else:
        playerWhite = playerName[:10]
      self.playerColours[ids[x]] = self.colours[colour]
      self.timers.append(Timer(mainTime, byoyomiTime, byoyomiMoves))
      colour ^= 1
    self.kgsClient.demoSetInfo(self.kgsGame, playerWhite, playerBlack, "vpgtpd server", self.name)
    for x in range(0, len(ids)):
      self.kgsClient.sendMessage(self.kgsGame, "Player: %s - %s" % (names[x], self.playerColours[ids[x]]))
    self.kgsClient.sendMessage(self.kgsGame, "Referee: %s" % self.referee.name)
  # Пытается сделать ход, судья его проверяет и записывает
  def attemptMove(self, move):
    r = self.referee.sendCommand("play %s %s" % (self.colours[self.colour], move))
    if r[0][:2] == "= ":
      self.removeDeadPlayers()
      for x in self.players:
        if x != self.colours[self.colour]:
          self.players[x].sendCommandWithTimeout("play %s %s" % (self.colours[self.colour], move))
      return True
    else:
      return False
  # Ждет хода от игрока
  def waitMove(self):
    if self.cleanupMode and self.players[self.colours[self.colour]].canCleanup:
      return self.players[self.colours[self.colour]].sendCommand("kgs-genmove_cleanup %s" % self.colours[self.colour])[0][2:].lower()
    else:
      return self.players[self.colours[self.colour]].sendCommand("genmove %s" % self.colours[self.colour])[0][2:].lower()
  # Ждет когда игрок подключится
  def waitConnect(self):
    self.playerEvents[self.colours[self.colour]].clear()
    self.playerBusy.release()
    try:
      self.playerEvents[self.colours[self.colour]].wait()
    finally:
      self.playerBusy.acquire()
    self.removeDeadPlayers()
    if self.colours[self.colour] not in self.players:
      return None
    return None
  # Удаляет отвалившихся игроков
  def removeDeadPlayers(self):
    newPlayers = dict(self.players)
    for x in self.players:
      if self.players[x].dead:
        del(newPlayers[x])
    self.players = newPlayers
  # Начинает игру
  def startGame(self):
    self.playerBusy.acquire()
    self.colour = 0
    try:
      self.removeDeadPlayers()
      for x in self.players:
        for t in range(0,2):
          time, periods = self.timers[t].lastTime()
          self.players[x].sendCommandWithTimeout("time_left %s %d %d" % (self.colours[t], time, periods))
      while True:
        time = self.timers[self.colour].startMove()
        move = ""
        while not move:
          self.removeDeadPlayers()
          if self.colours[self.colour] not in self.players:
            print("%s: connection wait %s" % (self.name, self.colours[self.colour]))
            move = timeout(self.waitConnect, time)
            if move == "timeout":
              self.playerEvents[self.colours[self.colour]].set()
          else:
            print("%s: move wait %s" % (self.name, self.colours[self.colour]))
            move = timeout(self.waitMove, time)
          time = self.timers[self.colour].sameMove()
        time, periods = self.timers[self.colour].endMove()
        for x in self.players:
          self.players[x].sendCommandWithTimeout("time_left %s %d %d" % (self.colours[self.colour], time, periods))
        if move == "resign":
          self.result = "%s+Resign" % self.colours[self.colour ^ 1][0].upper()
          self.kgsClient.demoTimeLeft(self.kgsGame, self.colours[self.colour], time, periods)
          self.kgsClient.demoSetResult(self.kgsGame, "%s+RESIGN" % self.colours[self.colour ^ 1][0].upper())
          break
        elif self.timers[self.colour].lostOnTime():
          self.result = "%s+Time" % self.colours[self.colour ^ 1][0].upper()
          self.kgsClient.demoTimeLeft(self.kgsGame, self.colours[self.colour], time, periods)
          self.kgsClient.demoSetResult(self.kgsGame, "%s+TIME" % self.colours[self.colour ^ 1][0].upper())
          break
        elif not self.attemptMove(move):
          self.kgsClient.sendMessage(self.kgsGame, "Attempted move: %s %s" % (self.colours[self.colour], move))
          self.result = "%s+Forfeit" % self.colours[self.colour ^ 1][0].upper()
          self.kgsClient.demoTimeLeft(self.kgsGame, self.colours[self.colour], time, periods)
          self.kgsClient.demoSetResult(self.kgsGame, "%s+FORFEIT" % self.colours[self.colour ^ 1][0].upper())
          break
        else:
          self.kgsClient.demoPlayMove(self.kgsGame, self.colours[self.colour], move)
          self.kgsClient.demoTimeLeft(self.kgsGame, self.colours[self.colour], time, periods)
          if self.referee.gameEnded() and self.finishGame():
            break
        print("%s: move %s %s" % (self.name, self.colours[self.colour], move))
        self.colour ^= 1
      print("%s: result %s" % (self.name, self.result))
      self.kgsClient.sendMessage(self.kgsGame, "Game result: %s" % self.result)
      self.kgsClient.saveGame(self.kgsGame)
      self.kgsClient.terminate()
      self.removeDeadPlayers()
      for x in self.players:
        self.players[x].session.close()
      self.referee.sendCommand("quit")
    finally:
      self.playerBusy.release()
  # Производит подсчет
  def finishGame(self):
    self.removeDeadPlayers()
    if len(self.players) == 2:
      deadStones = []
      for x in self.players:
        deadStones.append(set(stone.lower() for stone in " ".join(self.players[x].sendCommandWithTimeout("final_status_list dead"))[2:].split()))
      if deadStones[0] != deadStones[1]:
        self.cleanupMode = True
        self.kgsClient.sendMessage(self.kgsGame, "Players do not agree on dead stones status")
        return False
    results = []
    self.removeDeadPlayers()
    for x in self.players:
      results.append(self.players[x].sendCommandWithTimeout("final_score")[0][2:].upper())
    results.append(self.referee.sendCommand("final_score")[0][2:].upper())
    if results[1:] == results[:-1]:
      self.result = results[0]
      self.kgsClient.demoSetResult(self.kgsGame, self.result)
    elif results[1:-1] == results[:-2]:
      self.result = "players: %s, referee: %s" % (results[0], results[-1])
    else:
      self.result = "players do not agree, referee: %s" % results[-1]
    return True

# Класс для управления сервером
class Server(object):
  # Принимает адрес, порт, командную строку судьи, команды настройки судьи, команды настройки игроков, ники и пароли KGS, участников и настройки времени
  def __init__(self, host, port, referee, refereeSetup, playerSetup, kgsApi, kgsRooms, kgsNames, kgsPwds, kgsTitles, participants, participantIds, mainTime, byoyomiTime, byoyomiMoves):
    self.host = host
    self.port = port
    self.playerSetup = playerSetup
    self.participants = participants
    self.participantIds = participantIds
    numGames = len(participants)
    self.games = []
    self.sock = None
    self.threads = []
    for i in range(0, numGames):
      self.games.append(Game(referee, refereeSetup, kgsApi, kgsRooms[i], kgsNames[i], kgsPwds[i], kgsTitles[i], participants[i], participantIds[i], mainTime, byoyomiTime, byoyomiMoves))
  # Настраивает игрока
  def setupParticipant(self, socket):
    try:
      player = Player(socket)
    except:
      socket.close()
      return
    game = None
    for i in range(0, len(self.participantIds)):
      for x in self.participantIds[i]:
        if x == player.id:
          game = i
          break
      if game is not None:
        break
    if game is None or self.games[game].result:
      socket.close()
      return
    colour = self.games[game].playerColours[player.id]
    self.games[game].playerBusy.acquire()
    try:
      self.games[game].removeDeadPlayers()
      if colour not in self.games[game].players:
        print("Player joined: %s as %s in %s" % (player.name, colour, self.games[game].name))
        self.games[game].kgsClient.sendMessage(self.games[game].kgsGame, "Joined: %s" % (player.name))
        self.games[game].players[colour] = player
        for x in self.playerSetup:
           self.games[game].players[colour].sendCommandWithTimeout(x)
        self.games[game].referee.preparePlayer(self.games[game].players[colour])
        playColour = self.games[game].colour
        if playColour is not None:
          time, periods = self.games[game].timers[playColour].currentTime()
          self.games[game].players[colour].sendCommandWithTimeout("time_left %s %d %d" % (self.games[game].colours[playColour], time, periods))
          time, periods = self.games[game].timers[playColour ^ 1].lastTime()
          self.games[game].players[colour].sendCommandWithTimeout("time_left %s %d %d" % (self.games[game].colours[playColour ^ 1], time, periods))
        self.games[game].playerEvents[colour].set()
      else:
        socket.close()
    finally:
      self.games[game].playerBusy.release()
  # Запускает сервер
  def startServer(self):
    import socket
    try:
      self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
      self.sock.bind((self.host, self.port))
      self.sock.listen(1)
      print("Server started")
      while True:
        conn, (cl_addr, cl_port) = self.sock.accept()
        print("Client was accepted: %s" % cl_addr)
        threadStart(lambda: self.setupParticipant(conn))
    except:
      pass
  def stopServer(self):
    if self.sock:
      self.sock.close()
      print("Server has stopped")
  # Запускает игры
  def startGames(self):
    for x in self.games:
      self.threads.append(threadStart(x.startGame))
    for x in self.threads:
      x.join()

if __name__ == '__main__':
  from configparser import ConfigParser
  from time import sleep
  from datetime import datetime
  import sys
  config = ConfigParser()
  config.read(sys.argv[1])
  host = config["Server"]["Host"]
  port = int(config["Server"]["Port"])
  referee = config["Server"]["RefereeCmd"]
  kgsApi = config["Server"]["KgsApi"]
  refereeSetup = list(config["RefereeSetupCommands"].values())
  playerSetup = list(config["PlayerSetupCommands"].values())
  gameIds = []
  kgsRooms = []
  kgsNames = []
  kgsPwds = []
  participants = []
  participantIds = []
  mainTime = int(config["Server"]["MainTime"])
  byoyomiTime = int(config["Server"]["ByoyomiTime"])
  byoyomiMoves = int(config["Server"]["ByoyomiMoves"])
  playerSetup.append("time_settings %d %d %d" % (mainTime, byoyomiTime, byoyomiMoves))
  roundStart = datetime.strptime(config["Server"]["RoundStart"], "%d.%m.%Y %H:%M")
  for x in config.sections():
    v = x.split("=")
    if v[0] != "Game" or len(v) != 2:
      continue
    kgsRoom = config[x]["KGSRoom"]
    kgsName = config[x]["KGSName"]
    kgsPwd = config[x]["KGSPassword"]
    botNames = [config[x]["Player1"], config[x]["Player2"]]
    botIds = [config[x]["Player1ID"], config[x]["Player2ID"]]
    if v[1] in gameIds:
      ind = gameIds.index(v[1])
      kgsRooms[ind] = kgsRoom
      kgsNames[ind] = kgsName
      kgsPwds[ind] = kgsPwd
      participants[ind] = botNames
      participantIds[ind] = botIds
    else:
      gameIds.append(v[1])
      kgsRooms.append(kgsRoom)
      kgsNames.append(kgsName)
      kgsPwds.append(kgsPwd)
      participants.append(botNames)
      participantIds.append(botIds)
  server = Server(host, port, referee, refereeSetup, playerSetup, kgsApi, kgsRooms, kgsNames, kgsPwds, gameIds, participants, participantIds, mainTime, byoyomiTime, byoyomiMoves)
  threadStart(server.startServer)
  diff = (roundStart - datetime.now()).total_seconds()
  if diff > 0:
    print("Waiting for games to start")
    sleep(diff)
  print("Starting games")
  server.startGames()
  server.stopServer()
