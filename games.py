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
        return "Das Hangman-Spiel wurde beendet, weil es zu lange ignoriert wurde."

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
