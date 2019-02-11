import random
import re

from chatmessage import Incoming


class Hangman:
    def __init__(self, config):
        self.config = config
        self.running = True
        self.remaining = 10

        self.word: str = random.choice(config.words)
        self.guessed = ["_"] * len(self.word)

    def start(self):
        answer = [
            "Hangman gestartet! Gesucht: %s (%i Zeichen)" % (" ".join(self.guessed), len(self.word)),
            "Ratet einen Buchstaben mit <b>!hangman BUCHSTABE</b>. Lösen könnt ihr mit <b>!hangman LÖSUNG</b>. "
            "Ihr habt <b>10</b> Versuche. Viel Erfolg!"
        ]
        return answer

    def timeout(self):
        answer = []
        self.running = False
        answer.append("Das Hangman-Spiel wurde beendet, weil es zu lange ignoriert wurde.")
        if self.config.show_solution:
            answer.append("Die richtige Lösung war: %s" % self.word)
        return answer

    def handle(self, param_string: str):
        answer = []
        if param_string.lower() == self.word.lower():
            self.running = False
            return "Volltreffer, %s ist richtig!" % self.word

        elif len(param_string) == 1:
            if param_string.lower() in self.word.lower():
                indices = [m.start() for m in re.finditer(param_string.lower(), self.word.lower())]
                for i in indices:
                    self.guessed[i] = self.word[i].upper()
                if "".join(self.guessed).lower() == self.word.lower():
                    self.running = False
                    return "Die Lösung wurde gefunden: %s" % self.word
                else:
                    return "Der Buchstabe %s kommt vor: %s" % (param_string.upper(), " ".join(self.guessed))
            else:
                self.remaining = self.remaining - 1
                answer.append("Der Buchstabe %s kommt nicht vor!" % param_string.upper())
        else:
            self.remaining = self.remaining - 3
            answer.append("%s war leider nicht die richtige Lösung." % param_string)

        if self.remaining <= 0:
            answer.append("Leider sind alle Versuche aufgebraucht :-(")
            if self.config.show_solution:
                answer.append("Die richtige Lösung war: %s" % self.word)
            self.running = False
        else:
            answer.append("Verbleibende Versuche: %i" % self.remaining)

        return answer


class Wordmix:
    def __init__(self, config):
        self.config = config
        self.running = True
        self.remaining = 10

        self.word: str = random.choice(config.words)
        self.shuffled: str = ''.join(random.sample(self.word, len(self.word)))

    def start(self):
        answer = [
            "Wordmix gestartet! Gesucht: <b>%s</b>" % self.shuffled,
            "Ratet die Lösung mit <b>!wordmix LÖSUNG</b>. Ihr habt <b>2 Minuten</b> Zeit. Viel Erfolg!"
        ]
        return answer

    def timeout(self):
        answer = []
        self.running = False
        answer.append("Die Zeit für Wordmix ist abgelaufen!")
        if self.config.show_solution:
            answer.append("Die richtige Lösung war: %s" % self.word)
        return answer

    def handle(self, param_string: str):
        if param_string.lower() == self.word.lower():
            self.running = False
            return "Volltreffer, %s ist richtig!" % self.word

        else:
            return "%s war leider nicht die richtige Lösung." % param_string


class Timebomb:
    def __init__(self, offender, victim):
        wires = ["Rot",
                 "Grün",
                 "Blau",
                 "Schwarz",
                 "Cyan",
                 "Braun",
                 "Weiß",
                 "Grau",
                 "Antikweiß",
                 "Hellgrün",
                 "Eisfarben",
                 "grün-gelb-gepunktet",
                 "rot-weiß-gestrichelt",
                 "grau-braun-gepunktet",
                 "grau-weiß-gepunktet mit Lila Streifen",
                 "Unsichtbar"]
        self.offender = offender
        self.victim = victim
        self.wires = random.sample(wires, k=3)
        self.wire = random.choice(self.wires)
        self.time = random.randint(15, 30)
        self.running = True

    def start(self, chat_message: Incoming):
        if not chat_message.get_command().param_string:
            self.victim = chat_message.user
        return [
            "%s schiebt %s eine Bombe zu! Der Timer sagt: <b>%i</b> Sekunden!" % (self.offender, self.victim, self.time),
            "Schneide ein Kabel mit <b>!schneide FARBE</b> durch. "
            "Die Bombe hat folgende Kabel: <b>%s</b>" % ', '.join(self.wires)
        ]

    def timeout(self):
        return "%s hat es nicht rechtzeitig geschafft, die Bombe zu entschärfen :-(" % self.victim

    def handle(self, chat_message: Incoming, param_string: str):
        if chat_message.user.lower() == self.victim.lower():
            self.running = False
            if param_string.lower() == self.wire.lower():
                return "%s hat die Bombe entschärft. Glückwunsch!" % self.victim
            else:
                return "Das war leider das falsche Kabel!"
        else:
            return "Du bist doch gar nicht dran!"
