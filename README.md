# vpgtp
GTP server for offline matches with broadcasting into KGS

Программа позволяет проводить матчи между программами го и транслировать их на KGS. Сервер использует протокол GTP.
Поддерживаются китайкие правила и канадское бееми (стандарт протокола GTP).
Сервер производит обработку несколько матчей одновременно и начинает раунд в указанное в настройках время. Для начала следующего раунда необходимо перенастроить сервер с новой жеребьевкой.
Игроки для пар идентифицируются по личному идентификатору.
Игрокам позволено подключаться к серверу в любой момент матча, кроме подсчета очков. Если игрок потерял соединение до или во время подсчета, то подсчет осуществляется без него.
В качестве судьи используется локальная программа с протоколом GTP (обычно GNU Go). Она проверяет правильность ходов, осуществляет их запись и перепроверяет результат партии.
