"""The chat games, without any I/O: every call returns the chat lines to send.

Time is passed in (`now`, in seconds, e.g. time.monotonic()), and the bot calls tick(now) about
once a second, which ends a game or moves on when its time is up. Randomness comes from an
injected random.Random. Texts are those of the old bot, except where it misbehaved.

Names and guesses from the chat are HTML-escaped where they are repeated (the bot may be a chat
admin, whose messages can contain HTML); words, questions and answers from the settings are not.

Points: a game asks `scores(user)` whether a player gets points (the points system is on and they
aren't a guest), announces them in its lines and notes them in `awards`, which the bot collects and
reports to wkqb-web. The rules are those of the wkQB docs (2011).
"""

import random
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from html import escape as _e

#: Seconds without a guess until Hangman ends
HANGMAN_TIMEOUT = 60.0
HANGMAN_TRIES = 10
#: A wrong solution costs this many tries
HANGMAN_WRONG_SOLUTION = 3
#: As documented: 5 minutes, as the game can be hard
WORDMIX_TIME = 300.0
#: Seconds after the start for the hints: first and last letter, then one more
WORDMIX_HINTS = (150.0, 240.0)
TIMEBOMB_MIN, TIMEBOMB_MAX = 15, 30
QUIZ_QUESTION_TIME = 30.0
#: Questions of a quiz are asked in random order, at most this many
QUIZ_MAX_QUESTIONS = 15
POTATO_SIGNUP = 60.0
POTATO_MIN_PLAYERS = 3
#: Seconds to throw the potato back: 10 as in the rules, plus the bot's sending delay
POTATO_THROW_TIME = 12.0
#: The potato is cold after this many throws
POTATO_THROWS = (10, 20)

TIMEBOMB_POINTS = 5
#: For a single winner, and for each of several winners when the potato got cold
POTATO_POINTS = (5, 3)
QUIZ_POINTS = 2
#: For the most right answers of a quiz, split between equal players (rounded down)
QUIZ_BONUS = 10

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


#: Does this player get points?
Scores = Callable[[str], bool]


def _nobody(user: str) -> bool:
    return False


class Game:
    running = True
    deadline: float | None = None

    def __init__(self, scores: Scores | None) -> None:
        self.scores = scores or _nobody
        self.awards: list[tuple[str, int]] = []
        """Points won since the bot last collected them"""

    def award(self, user: str, points: int) -> bool:
        """Notes the points if the player gets them; True if so."""
        if points > 0 and self.scores(user):
            self.awards.append((user, points))
            return True
        return False

    def awarded(self, users: Iterable[str], points: int) -> str:
        """Awards each player, and the announcement after a space (empty if nobody got points)."""
        winners = [user for user in users if self.award(user, points)]
        if not winners:
            return ""
        if len(winners) == 1:
            return f" {_e(winners[0])} bekommt {_points(points)}!"
        return f" {_e(', '.join(winners))} bekommen je {_points(points)}!"

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
        scores: Scores | None = None,
    ) -> None:
        """picture: shown with the used tries, "{n}" is their number (0-10), e.g. ':hangman{n}' (a chat smiley)
        or an <img> tag (only works if the bot is a chat admin); None for text only."""
        super().__init__(scores)
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
            # as many points as tries are left
            return [f"Volltreffer {_e(user)}, {self.word} ist richtig!{self.awarded([user], self.remaining)}"]

        self.tried.add(key)
        if len(guess) == 1:
            hits = [i for i, ch in enumerate(self.word) if ch.casefold() == key]
            if hits:
                # a point per letter, without announcing it (as the old bot)
                self.award(user, 1)
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
        scores: Scores | None = None,
    ) -> None:
        super().__init__(scores)
        self.word = rng.choice(words).strip()
        self.show_solution = show_solution
        self.sign = sign
        self.hint_times = [now + after for after in WORDMIX_HINTS] if hints else []
        self.hints_shown = 0
        letters = self.word.lower()
        # the hints show the first and the last letter, then one more in between
        self.hinted = {0, len(letters) - 1}
        middle = list(range(1, len(letters) - 1))
        self.second_hint = rng.choice(middle) if middle else None
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
            f"Ratet die Lösung mit <b>{_e(self.sign)}wordmix LÖSUNG</b>. Ihr habt <b>5 Minuten</b> Zeit. Viel Erfolg!",
        ]

    def guess(self, user: str, text: str, now: float) -> list[str]:
        guess = " ".join(text.split())
        if not guess:
            return [f"Gesucht: <b>{self.shuffled}</b>"]
        if _same(guess, self.word):
            self.running = False
            return [f"Volltreffer {_e(user)}, {self.word} ist richtig!{self.awarded([user], self.points())}"]
        return [f"{_e(user)}, {_e(guess)} war leider nicht die richtige Lösung."]

    def points(self) -> int:
        """Half the length for words up to 10 letters, 7 up to 20, else 15; halved after the first hint,
        a third after the second, rounded down."""
        length = len(self.word)
        base = Fraction(length, 2) if length <= 10 else 7 if length <= 20 else 15
        return int(base / (self.hints_shown + 1))

    def tick(self, now: float) -> list[str]:
        answer = super().tick(now)
        if self.running and self.hint_times and now >= self.hint_times[0]:
            self.hint_times.pop(0)
            self.hints_shown += 1
            if not self.hint_times and self.second_hint is not None:
                self.hinted.add(self.second_hint)
            answer.append(f"Tipp: <b>{self.hint()}</b>")
        return answer

    def hint(self) -> str:
        """The word with the hinted letters, e.g. "k _ _ _ e"."""
        return " ".join(ch if i in self.hinted else "_" for i, ch in enumerate(self.word.lower()))

    def timeout(self, now: float) -> list[str]:
        self.running = False
        answer = ["Die Zeit für Wordmix ist abgelaufen!"]
        if self.show_solution:
            answer.append(f"Die richtige Lösung war: {self.word}")
        return answer


class Timebomb(Game):
    def __init__(
        self,
        offender: str,
        victim: str,
        now: float,
        rng: random.Random,
        *,
        sign: str = "!",
        scores: Scores | None = None,
    ) -> None:
        super().__init__(scores)
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
            return [
                f"{_e(self.victim)} hat die Bombe entschärft. Glückwunsch!"
                f"{self.awarded([self.victim], TIMEBOMB_POINTS)}"
            ]
        return [f"Das war leider das falsche Kabel! Richtig wäre <b>{self.wire}</b> gewesen."]

    def cheat(self, user: str) -> list[str]:
        """!cheat: a trap. The docs promise the right wire, but whoever has the bomb loses."""
        if not _same(user, self.victim):
            return ["Du hast doch gar keine Bombe!"]
        self.running = False
        return [f"{_e(self.victim)} versuchte zu mogeln!"]

    def timeout(self, now: float) -> list[str]:
        self.running = False
        return [
            f"{_e(self.victim)} hat es nicht rechtzeitig geschafft, die Bombe zu entschärfen :-( "
            f"Richtig wäre <b>{self.wire}</b> gewesen."
        ]


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
    entries = ", ".join(
        f"{i}: {quiz.name} ({min(len(quiz.questions), QUIZ_MAX_QUESTIONS)} Fragen)" for i, quiz in enumerate(quizzes, 1)
    )
    return [
        f"Ich habe folgende Quizze anzubieten: {entries}",
        f"Sende {_e(sign)}quiz ID, um das gewünschte Quiz zu starten!",
    ]


class Quiz(Game):
    def __init__(
        self, quiz: QuizSet, show_solution: bool, now: float, rng: random.Random, *, scores: Scores | None = None
    ) -> None:
        super().__init__(scores)
        self.quiz = quiz
        self.questions = rng.sample(list(quiz.questions), min(len(quiz.questions), QUIZ_MAX_QUESTIONS))
        self.show_solution = show_solution
        self.rng = rng
        self.index = 0
        self.right: dict[str, tuple[str, int]] = {}
        """Right answers by lower-case name: (name, number)"""
        self.deadline = now + QUIZ_QUESTION_TIME

    @property
    def current(self) -> Question:
        return self.questions[self.index]

    def start(self) -> list[str]:
        return [f"Starte Quiz: {self.quiz.name}", self.current.question]

    def answer(self, user: str, text: str, now: float) -> list[str]:
        answer = " ".join(text.split())
        if not answer:
            return [self.current.question]
        if not _same(answer, self.current.answer):
            return [f"{_e(answer)} ist leider falsch!"]
        return self._correct(user, now)

    def check(self, user: str, text: str, now: float) -> list[str]:
        """An answer sent as a normal chat message: only right ones get a reaction."""
        return self._correct(user, now) if _same(text, self.current.answer) else []

    def _correct(self, user: str, now: float) -> list[str]:
        praise = self.rng.choice(["Super", "Toll", "Sehr gut"])
        _, number = self.right.get(user.casefold(), (user, 0))
        self.right[user.casefold()] = (user, number + 1)
        points = self.awarded([user], QUIZ_POINTS)
        return [f"{praise} {_e(user)}, {self.current.answer} ist korrekt!{points}", *self._next(now)]

    def timeout(self, now: float) -> list[str]:
        answer = ["Die Frage wurde nicht rechtzeitig beantwortet."]
        if self.show_solution:
            answer.append(f"Die korrekte Antwort war: {self.current.answer}")
        return answer + self._next(now)

    def _winners(self) -> str:
        """Who answered the most questions, and their extra points."""
        if not self.right:
            return "Leider wurde keine Frage richtig beantwortet."
        most = max(number for _, number in self.right.values())
        winners = [name for name, number in self.right.values() if number == most]
        answers = "richtigen Antwort" if most == 1 else "richtigen Antworten"
        bonus = self.awarded(winners, QUIZ_BONUS // len(winners))
        if len(winners) == 1:
            return f"{_e(winners[0])} hat mit {most} {answers} gewonnen!{bonus}"
        return f"Gewonnen haben {_e(', '.join(winners))} mit je {most} {answers}!{bonus}"

    def _next(self, now: float) -> list[str]:
        self.index += 1
        if self.index >= len(self.questions):
            self.running = False
            return [f"Das waren alle Fragen, das Quiz ist daher beendet! {self._winners()}"]
        self.deadline = now + QUIZ_QUESTION_TIME
        return [self.current.question]


class HotPotato(Game):
    """Heiße Kartoffel: players sign up, then the bot throws the potato to a random player, who must
    throw it back in time or is out. The last player left wins; after POTATO_THROWS throws the potato
    is cold and all remaining players win.
    """

    def __init__(
        self, starter: str, now: float, rng: random.Random, *, sign: str = "!", scores: Scores | None = None
    ) -> None:
        super().__init__(scores)
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
        if self.holder is None:
            return [f"{_e(user)}, das Spiel hat doch noch gar nicht angefangen!"]
        if not any(_same(user, player) for player in self.players):
            return [f"Du spielst nicht mit, {_e(user)}!"]
        if not _same(user, self.holder):
            # as in the Perl bot: throwing without the potato costs the game
            return self._out(user, f"{_e(user)} hat sich verworfen und scheidet aus!", now)
        self.throws += 1
        if self.throws >= self.cold_after:
            self.running = False
            return [
                f"Die Kartoffel ist kalt! Gewonnen haben: {_e(', '.join(self.players))}"
                f"{self.awarded(self.players, POTATO_POINTS[1])}"
            ]
        return self._throw_to_someone(now)

    def timeout(self, now: float) -> list[str]:
        if self.holder is None:
            if len(self.players) < POTATO_MIN_PLAYERS:
                self.running = False
                return [f"Für Heiße Kartoffel braucht es mindestens {POTATO_MIN_PLAYERS} Spieler, das Spiel fällt aus."]
            return [f"Es spielen mit: {_e(', '.join(self.players))}", *self._throw_to_someone(now)]
        loser = self.holder
        return self._out(loser, f"{_e(loser)} war zu langsam und hat sich die Finger verbrannt!", now)

    def _out(self, loser: str, why: str, now: float) -> list[str]:
        self.players = [player for player in self.players if not _same(player, loser)]
        if len(self.players) == 1:
            self.running = False
            return [why, f"{_e(self.players[0])} hat gewonnen!{self.awarded(self.players, POTATO_POINTS[0])}"]
        if self.holder is not None and not _same(self.holder, loser):
            # the potato stays where it is
            return [why]
        return [why, *self._throw_to_someone(now)]

    def _throw_to_someone(self, now: float) -> list[str]:
        self.holder = self.rng.choice(self.players)
        self.deadline = now + POTATO_THROW_TIME
        return [f"Die heiße Kartoffel fliegt zu <b>{_e(self.holder)}</b>! Schnell, <b>{_e(self.sign)}wurf</b>!"]


def _points(number: int) -> str:
    return "1 Punkt" if number == 1 else f"{number} Punkte"
