"""The chat games, without any I/O: every call returns the chat lines to send.

Time is passed in (`now`, in seconds, e.g. time.monotonic()), and the bot calls tick(now) about
once a second, which ends a game or moves on when its time is up. Randomness comes from an
injected random.Random. Texts are those of the old bot, except where it misbehaved.

Names and guesses from the chat are HTML-escaped where they are repeated (the bot may be a chat
admin, whose messages can contain HTML); words, questions and answers from the settings are not.
"""

import random
from collections.abc import Sequence
from dataclasses import dataclass
from html import escape as _e

#: Seconds without a guess until Hangman ends
HANGMAN_TIMEOUT = 60.0
HANGMAN_TRIES = 10
#: A wrong solution costs this many tries
HANGMAN_WRONG_SOLUTION = 3
WORDMIX_TIME = 120.0
#: Seconds after the start for the hints (first letter, then also the last one)
WORDMIX_HINTS = (60.0, 90.0)
TIMEBOMB_MIN, TIMEBOMB_MAX = 15, 30
QUIZ_QUESTION_TIME = 20.0
POTATO_SIGNUP = 60.0
POTATO_MIN_PLAYERS = 3
#: Seconds to throw the potato back: 10 as in the rules, plus the bot's sending delay
POTATO_THROW_TIME = 12.0
#: The potato is cold after this many throws
POTATO_THROWS = (10, 20)

WIRES = (
    "Rot",
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
    "Unsichtbar",
)


def _same(a: str, b: str) -> bool:
    """Equal, ignoring case and surrounding/repeated whitespace."""
    return " ".join(a.casefold().split()) == " ".join(b.casefold().split())


class Game:
    running = True
    deadline: float | None = None

    def tick(self, now: float) -> list[str]:
        """The lines to send when the time is up, else nothing."""
        if self.running and self.deadline is not None and now >= self.deadline:
            return self.timeout(now)
        return []

    def timeout(self, now: float) -> list[str]:
        raise NotImplementedError


class Hangman(Game):
    def __init__(
        self,
        words: Sequence[str],
        show_solution: bool,
        now: float,
        rng: random.Random,
        *,
        sign: str = "!",
        picture: str | None = None,
    ) -> None:
        """picture: shown with the used tries, "{n}" is their number (0-10), e.g. ':hangman{n}' (a chat smiley)
        or an <img> tag (only works if the bot is a chat admin); None for text only."""
        self.word = rng.choice(words).strip()
        self.sign = sign
        self.picture = picture
        self.show_solution = show_solution
        self.remaining = HANGMAN_TRIES
        self.tried: set[str] = set()
        # spaces, hyphens etc. are shown right away, they can't be guessed
        self.revealed = [not ch.isalpha() for ch in self.word]
        self.deadline = now + HANGMAN_TIMEOUT

    def pattern(self) -> str:
        """E.g. "_ A _ A _ _"; a space as &nbsp;, as the chat would collapse several spaces into one."""
        return " ".join(
            ("&nbsp;" if ch == " " else _display(ch)) if shown else "_"
            for ch, shown in zip(self.word, self.revealed, strict=True)
        )

    def start(self) -> list[str]:
        return [
            f"Hangman gestartet! Gesucht: {self.pattern()} ({len(self.word)} Zeichen){self._picture()}",
            f"Ratet einen Buchstaben mit <b>{_e(self.sign)}hangman BUCHSTABE</b>. "
            f"Lösen könnt ihr mit <b>{_e(self.sign)}hangman LÖSUNG</b>. "
            f"Ihr habt <b>{HANGMAN_TRIES}</b> Versuche. Viel Erfolg!",
        ]

    def guess(self, user: str, text: str, now: float) -> list[str]:
        guess = " ".join(text.split())
        if not guess:
            return [f"Gesucht: {self.pattern()} (verbleibende Versuche: {self.remaining})"]
        self.deadline = now + HANGMAN_TIMEOUT
        key = guess.casefold()
        if key in self.tried:
            return [f"{_e(user)}, {_e(guess)} wurde bereits probiert."]
        if _same(guess, self.word):
            self.running = False
            return [f"Volltreffer {_e(user)}, {self.word} ist richtig!"]

        self.tried.add(key)
        if len(guess) == 1:
            hits = [i for i, ch in enumerate(self.word) if ch.casefold() == key]
            if hits:
                for i in hits:
                    self.revealed[i] = True
                if all(self.revealed):
                    self.running = False
                    return [f"Die Lösung wurde von {_e(user)} gefunden: {self.word}"]
                return [f"{_e(user)}, der Buchstabe {_e(_display(guess))} kommt vor: {self.pattern()}"]
            self.remaining -= 1
            answer = [f"{_e(user)}, der Buchstabe {_e(_display(guess))} kommt nicht vor!"]
        else:
            self.remaining -= HANGMAN_WRONG_SOLUTION
            answer = [f"{_e(user)}, {_e(guess)} war leider nicht die richtige Lösung."]

        if self.remaining <= 0:
            self.running = False
            answer.append(f"Leider sind alle Versuche aufgebraucht :-({self._picture()}")
            answer += self._solution()
        else:
            answer.append(f"Verbleibende Versuche: {self.remaining}{self._picture()}")
        return answer

    def timeout(self, now: float) -> list[str]:
        self.running = False
        return ["Das Hangman-Spiel wurde beendet, weil es zu lange ignoriert wurde.", *self._solution()]

    def _solution(self) -> list[str]:
        return [f"Die richtige Lösung war: {self.word}"] if self.show_solution else []

    def _picture(self) -> str:
        """The gallows for the used tries, after a space."""
        if self.picture is None:
            return ""
        used = min(HANGMAN_TRIES, HANGMAN_TRIES - self.remaining)
        return " " + self.picture.replace("{n}", str(used))


def _display(ch: str) -> str:
    """A letter in upper case, unless that changes it (ß would become SS)."""
    upper = ch.upper()
    return upper if len(upper) == len(ch) else ch


class Wordmix(Game):
    def __init__(
        self,
        words: Sequence[str],
        show_solution: bool,
        now: float,
        rng: random.Random,
        *,
        sign: str = "!",
        hints: bool = False,
    ) -> None:
        self.word = rng.choice(words).strip()
        self.show_solution = show_solution
        self.sign = sign
        self.hint_times = [now + after for after in WORDMIX_HINTS] if hints else []
        letters = self.word.lower()
        shuffled = letters
        # a short word could come out unchanged
        for _ in range(10):
            shuffled = "".join(rng.sample(letters, len(letters)))
            if shuffled != letters:
                break
        self.shuffled = shuffled
        self.deadline = now + WORDMIX_TIME

    def start(self) -> list[str]:
        return [
            f"Wordmix gestartet! Gesucht: <b>{self.shuffled}</b>",
            f"Ratet die Lösung mit <b>{_e(self.sign)}wordmix LÖSUNG</b>. Ihr habt <b>2 Minuten</b> Zeit. Viel Erfolg!",
        ]

    def guess(self, user: str, text: str, now: float) -> list[str]:
        guess = " ".join(text.split())
        if not guess:
            return [f"Gesucht: <b>{self.shuffled}</b>"]
        if _same(guess, self.word):
            self.running = False
            return [f"Volltreffer {_e(user)}, {self.word} ist richtig!"]
        return [f"{_e(user)}, {_e(guess)} war leider nicht die richtige Lösung."]

    def tick(self, now: float) -> list[str]:
        answer = super().tick(now)
        if self.running and self.hint_times and now >= self.hint_times[0]:
            self.hint_times.pop(0)
            first, last = self.word[0].upper(), self.word[-1].lower()
            if self.hint_times:
                answer.append(f"Tipp: Das Wort beginnt mit <b>{first}</b>.")
            else:
                answer.append(f"Tipp: Das Wort beginnt mit <b>{first}</b> und endet mit <b>{last}</b>.")
        return answer

    def timeout(self, now: float) -> list[str]:
        self.running = False
        answer = ["Die Zeit für Wordmix ist abgelaufen!"]
        if self.show_solution:
            answer.append(f"Die richtige Lösung war: {self.word}")
        return answer


class Timebomb(Game):
    def __init__(self, offender: str, victim: str, now: float, rng: random.Random, *, sign: str = "!") -> None:
        self.sign = sign
        self.offender = offender
        self.victim = victim or offender
        self.wires = rng.sample(WIRES, k=3)
        self.wire = rng.choice(self.wires)
        self.seconds = rng.randint(TIMEBOMB_MIN, TIMEBOMB_MAX)
        self.deadline = now + self.seconds

    def start(self) -> list[str]:
        return [
            f"{_e(self.offender)} schiebt {_e(self.victim)} eine Bombe zu! "
            f"Der Timer sagt: <b>{self.seconds}</b> Sekunden!",
            f"Schneide ein Kabel mit <b>{_e(self.sign)}schneide FARBE</b> durch. "
            f"Die Bombe hat folgende Kabel: <b>{', '.join(self.wires)}</b>",
        ]

    def cut(self, user: str, text: str) -> list[str]:
        if not _same(user, self.victim):
            return ["Du bist doch gar nicht dran!"]
        wire = " ".join(text.split())
        if not any(_same(wire, w) for w in self.wires):
            # a typo must not blow it up
            return [f"Welches Kabel? Die Bombe hat folgende Kabel: <b>{', '.join(self.wires)}</b>"]
        self.running = False
        if _same(wire, self.wire):
            return [f"{_e(self.victim)} hat die Bombe entschärft. Glückwunsch!"]
        return ["Das war leider das falsche Kabel!"]

    def timeout(self, now: float) -> list[str]:
        self.running = False
        return [f"{_e(self.victim)} hat es nicht rechtzeitig geschafft, die Bombe zu entschärfen :-("]


@dataclass(frozen=True, slots=True)
class Question:
    question: str
    answer: str


@dataclass(frozen=True, slots=True)
class QuizSet:
    name: str
    questions: Sequence[Question]


def quiz_menu(quizzes: Sequence[QuizSet], sign: str = "!") -> list[str]:
    """The answer to !quiz without a running quiz and without an id."""
    if not quizzes:
        return ["Ich kenne leider kein Quiz :-("]
    # one line for all: every line costs 2.5 s (flood protection)
    entries = ", ".join(f"{i}: {quiz.name} ({len(quiz.questions)} Fragen)" for i, quiz in enumerate(quizzes, 1))
    return [
        f"Ich habe folgende Quizze anzubieten: {entries}",
        f"Sende {_e(sign)}quiz ID, um das gewünschte Quiz zu starten!",
    ]


class Quiz(Game):
    def __init__(self, quiz: QuizSet, show_solution: bool, now: float, rng: random.Random) -> None:
        self.quiz = quiz
        self.show_solution = show_solution
        self.rng = rng
        self.index = 0
        self.deadline = now + QUIZ_QUESTION_TIME

    @property
    def current(self) -> Question:
        return self.quiz.questions[self.index]

    def start(self) -> list[str]:
        return [f"Starte Quiz: {self.quiz.name}", self.current.question]

    def answer(self, user: str, text: str, now: float) -> list[str]:
        answer = " ".join(text.split())
        if not answer:
            return [self.current.question]
        if not _same(answer, self.current.answer):
            return [f"{_e(answer)} ist leider falsch!"]
        praise = self.rng.choice(["Super", "Toll", "Sehr gut"])
        return [f"{praise} {_e(user)}, {self.current.answer} ist korrekt!", *self._next(now)]

    def timeout(self, now: float) -> list[str]:
        answer = ["Die Frage wurde nicht rechtzeitig beantwortet."]
        if self.show_solution:
            answer.append(f"Die korrekte Antwort war: {self.current.answer}")
        return answer + self._next(now)

    def _next(self, now: float) -> list[str]:
        self.index += 1
        if self.index >= len(self.quiz.questions):
            self.running = False
            return ["Das waren alle Fragen, das Quiz ist daher beendet!"]
        self.deadline = now + QUIZ_QUESTION_TIME
        return [self.current.question]


class HotPotato(Game):
    """Heiße Kartoffel: players sign up, then the bot throws the potato to a random player, who must
    throw it back in time or is out. The last player left wins; after POTATO_THROWS throws the potato
    is cold and all remaining players win.
    """

    def __init__(self, starter: str, now: float, rng: random.Random, *, sign: str = "!") -> None:
        self.sign = sign
        self.rng = rng
        self.players = [starter]
        self.holder: str | None = None
        """None while players sign up"""
        self.throws = 0
        self.cold_after = rng.randint(*POTATO_THROWS)
        self.deadline = now + POTATO_SIGNUP

    def start(self) -> list[str]:
        return [
            f"{_e(self.players[0])} startet Heiße Kartoffel! Wer mitspielen will, sendet innerhalb von "
            f"<b>{POTATO_SIGNUP:.0f} Sekunden</b> <b>{_e(self.sign)}kartoffel</b>. "
            f"Wer die Kartoffel bekommt, muss sie schnell mit <b>{_e(self.sign)}wurf</b> zurückwerfen!"
        ]

    def join(self, user: str) -> list[str]:
        if self.holder is not None:
            return ["Das Spiel läuft schon, du musst bis zum nächsten warten."]
        if any(_same(user, player) for player in self.players):
            return [f"{_e(user)}, du spielst doch schon mit!"]
        self.players.append(user)
        return [f"{_e(user)} spielt mit! ({len(self.players)} Spieler)"]

    def throw(self, user: str, now: float) -> list[str]:
        if self.holder is None or not _same(user, self.holder):
            return [f"{_e(user)}, du hast die Kartoffel doch gar nicht!"]
        self.throws += 1
        if self.throws >= self.cold_after:
            self.running = False
            return [f"Die Kartoffel ist kalt! Gewonnen haben: {_e(', '.join(self.players))}"]
        return self._throw_to_someone(now)

    def timeout(self, now: float) -> list[str]:
        if self.holder is None:
            if len(self.players) < POTATO_MIN_PLAYERS:
                self.running = False
                return [f"Für Heiße Kartoffel braucht es mindestens {POTATO_MIN_PLAYERS} Spieler, das Spiel fällt aus."]
            return [f"Es spielen mit: {_e(', '.join(self.players))}", *self._throw_to_someone(now)]
        loser = self.holder
        self.players = [player for player in self.players if not _same(player, loser)]
        answer = [f"{_e(loser)} war zu langsam und hat sich die Finger verbrannt!"]
        if len(self.players) == 1:
            self.running = False
            return [*answer, f"{_e(self.players[0])} hat gewonnen!"]
        return [*answer, *self._throw_to_someone(now)]

    def _throw_to_someone(self, now: float) -> list[str]:
        self.holder = self.rng.choice(self.players)
        self.deadline = now + POTATO_THROW_TIME
        return [f"Die heiße Kartoffel fliegt zu <b>{_e(self.holder)}</b>! Schnell, <b>{_e(self.sign)}wurf</b>!"]
