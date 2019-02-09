import random
import re

class Hangman:
    def __init__(self, config):
        self.config = config
        self.running = True
        self.remaining = 10

        self.word: str = random.choice(config.words)
        self.guessed = ["_"] * len(self.word)

    def start(self):
        return "Hangman gestartet: %s (%i Zeichen)" % (" ".join(self.guessed), len(self.word))

    def timeout(self):
        self.running = False
        return "Timeout!"

    def handle(self, param_string: str):
        answer = []
        if param_string.lower() == self.word.lower():
            self.running = False
            answer.append("Volltreffer, %s ist richtig!" % self.word)
        elif len(param_string) == 1:
            if param_string.lower() in self.word.lower():
                indices = [m.start() for m in re.finditer(param_string.lower(), self.word.lower())]
                for i in indices:
                    self.guessed[i] = self.word[i].upper()
                if "".join(self.guessed).lower() == self.word.lower():
                    self.running = False
                    answer.append("Die Lösung wurde gefunden: %s" % self.word)
                else:
                    answer.append("Der Buchstabe %s kommt vor: %s" % (param_string.upper(), " ".join(self.guessed)))
            else:
                self.remaining = self.remaining - 1
                answer.append("Der Buchstabe %s kommt nicht vor! Verbleibende Versuche: %i" % (param_string.upper(), self.remaining))
        else:
            self.remaining = self.remaining - 3
            answer.append("%s war leider nicht die richtige Lösung. Verbleibende Versuche: %i" % (param_string, self.remaining))

        if self.remaining <= 0:
            answer.append("Leider sind alle Versuche aufgebraucht :-(")
            self.running = False

        return answer
