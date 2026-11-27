import random

import pytest

from wkqb.games import (
    HANGMAN_TIMEOUT,
    QUIZ_QUESTION_TIME,
    WIRES,
    WORDMIX_TIME,
    Hangman,
    Question,
    Quiz,
    QuizSet,
    Timebomb,
    Wordmix,
    quiz_menu,
)


def rng() -> random.Random:
    return random.Random(4)


class TestHangman:
    def game(self, word: str = "Katze", show: bool = True) -> Hangman:
        return Hangman([word], show, now=0, rng=rng())

    def test_start(self) -> None:
        assert self.game().start() == [
            "Hangman gestartet! Gesucht: _ _ _ _ _ (5 Zeichen)",
            "Ratet einen Buchstaben mit <b>!hangman BUCHSTABE</b>. Lösen könnt ihr mit <b>!hangman LÖSUNG</b>. "
            "Ihr habt <b>10</b> Versuche. Viel Erfolg!",
        ]

    def test_letters(self) -> None:
        game = self.game("Banane")
        assert game.guess("anna", "a", 1) == ["anna, der Buchstabe A kommt vor: _ A _ A _ _"]
        assert game.guess("bert", "A", 2) == ["bert, A wurde bereits probiert."]
        assert game.guess("bert", "x", 3) == ["bert, der Buchstabe X kommt nicht vor!", "Verbleibende Versuche: 9"]
        assert game.guess("bert", "x", 4) == ["bert, x wurde bereits probiert."]
        assert game.remaining == 9
        game.guess("anna", "n", 5)
        game.guess("anna", "b", 6)
        assert game.guess("anna", "e", 7) == ["Die Lösung wurde von anna gefunden: Banane"]
        assert not game.running

    def test_solution(self) -> None:
        game = self.game("Katze")
        assert game.guess("anna", "Hund", 1) == [
            "anna, Hund war leider nicht die richtige Lösung.",
            "Verbleibende Versuche: 7",
        ]
        assert game.guess("bert", "  katze ", 2) == ["Volltreffer bert, Katze ist richtig!"]
        assert not game.running

    def test_an_empty_guess_costs_nothing(self) -> None:
        game = self.game()
        assert game.guess("anna", "", 1) == ["Gesucht: _ _ _ _ _ (verbleibende Versuche: 10)"]
        assert game.remaining == 10

    def test_special_characters_are_no_regex(self) -> None:
        game = self.game("a.b")
        assert game.pattern() == "_ . _"  # not a letter: shown right away
        assert game.guess("anna", "(", 1)[0] == "anna, der Buchstabe ( kommt nicht vor!"

    def test_spaces_and_umlauts(self) -> None:
        game = self.game("Heiße Kartoffel")
        assert game.pattern() == "_ _ _ _ _ &nbsp; _ _ _ _ _ _ _ _ _"
        assert game.guess("anna", "ß", 1) == ["anna, der Buchstabe ß kommt vor: _ _ _ ß _ &nbsp; _ _ _ _ _ _ _ _ _"]

    def test_lost(self) -> None:
        game = self.game("Katze")
        for i, guess in enumerate(["Hund", "Maus", "Igel"]):
            game.guess("anna", guess, i)
        assert game.guess("anna", "Wurm", 4) == [
            "anna, Wurm war leider nicht die richtige Lösung.",
            "Leider sind alle Versuche aufgebraucht :-(",
            "Die richtige Lösung war: Katze",
        ]
        assert not game.running

    def test_timeout_counts_from_the_last_guess(self) -> None:
        game = self.game(show=False)
        game.guess("anna", "k", 30)
        assert game.tick(30 + HANGMAN_TIMEOUT - 1) == []
        assert game.tick(30 + HANGMAN_TIMEOUT) == ["Das Hangman-Spiel wurde beendet, weil es zu lange ignoriert wurde."]
        assert not game.running
        assert game.tick(1000) == []


class TestWordmix:
    def test_game(self) -> None:
        game = Wordmix(["Gemüse"], True, now=0, rng=rng())
        assert sorted(game.shuffled) == sorted("gemüse") and game.shuffled != "gemüse"
        assert game.start()[0] == f"Wordmix gestartet! Gesucht: <b>{game.shuffled}</b>"
        assert game.guess("anna", "", 1) == [f"Gesucht: <b>{game.shuffled}</b>"]
        assert game.guess("anna", "Gemuese", 1) == ["anna, Gemuese war leider nicht die richtige Lösung."]
        assert game.guess("bert", "GEMÜSE", 2) == ["Volltreffer bert, Gemüse ist richtig!"]
        assert not game.running

    def test_timeout(self) -> None:
        game = Wordmix(["ab"], True, now=0, rng=rng())
        assert game.shuffled == "ba"
        assert game.tick(WORDMIX_TIME - 0.5) == []
        assert game.tick(WORDMIX_TIME) == ["Die Zeit für Wordmix ist abgelaufen!", "Die richtige Lösung war: ab"]

    def test_one_letter(self) -> None:
        assert Wordmix(["a"], True, now=0, rng=rng()).shuffled == "a"


class TestTimebomb:
    def test_start(self) -> None:
        game = Timebomb("anna", "bert", now=0, rng=rng())
        assert len(game.wires) == 3 and set(game.wires) <= set(WIRES) and game.wire in game.wires
        assert 15 <= game.seconds <= 30 and game.deadline == game.seconds
        assert game.start() == [
            f"anna schiebt bert eine Bombe zu! Der Timer sagt: <b>{game.seconds}</b> Sekunden!",
            "Schneide ein Kabel mit <b>!schneide FARBE</b> durch. "
            f"Die Bombe hat folgende Kabel: <b>{', '.join(game.wires)}</b>",
        ]

    def test_without_victim_it_is_the_offender(self) -> None:
        assert Timebomb("anna", "", now=0, rng=rng()).victim == "anna"

    def test_right_wire(self) -> None:
        game = Timebomb("anna", "bert", now=0, rng=rng())
        assert game.cut("anna", game.wire) == ["Du bist doch gar nicht dran!"]
        assert game.running
        assert game.cut("BERT", f" {game.wire.upper()} ") == ["bert hat die Bombe entschärft. Glückwunsch!"]
        assert not game.running

    def test_wrong_wire(self) -> None:
        game = Timebomb("anna", "bert", now=0, rng=rng())
        wrong = next(w for w in game.wires if w != game.wire)
        assert game.cut("bert", wrong) == ["Das war leider das falsche Kabel!"]
        assert not game.running

    def test_unknown_wire(self) -> None:
        game = Timebomb("anna", "bert", now=0, rng=rng())
        assert game.cut("bert", "Lila")[0].startswith("Welches Kabel?")
        assert game.cut("bert", "")[0].startswith("Welches Kabel?")
        assert game.running

    def test_timeout(self) -> None:
        game = Timebomb("anna", "bert", now=0, rng=rng())
        assert game.tick(game.seconds) == ["bert hat es nicht rechtzeitig geschafft, die Bombe zu entschärfen :-("]


QUIZZES = [
    QuizSet("Tiere", [Question("Wie macht die Kuh?", "Muh"), Question("Wie macht die Katze?", "Miau")]),
    QuizSet("Leer?", [Question("1+1?", "2")]),
]


def test_quiz_menu() -> None:
    assert quiz_menu(QUIZZES) == [
        "Ich habe folgende Quizze anzubieten: 1: Tiere (2 Fragen), 2: Leer? (1 Fragen)",
        "Sende !quiz ID, um das gewünschte Quiz zu starten!",
    ]
    assert quiz_menu([]) == ["Ich kenne leider kein Quiz :-("]


class TestQuiz:
    def test_answers(self) -> None:
        game = Quiz(QUIZZES[0], True, now=0, rng=rng())
        assert game.start() == ["Starte Quiz: Tiere", "Wie macht die Kuh?"]
        assert game.answer("anna", "", 1) == ["Wie macht die Kuh?"]
        assert game.answer("anna", "Mäh", 1) == ["Mäh ist leider falsch!"]
        [praise, question] = game.answer("bert", " muh ", 5)
        assert praise.endswith(" bert, Muh ist korrekt!") and question == "Wie macht die Katze?"
        assert game.deadline == 5 + QUIZ_QUESTION_TIME
        assert game.answer("anna", "MIAU", 6)[1:] == ["Das waren alle Fragen, das Quiz ist daher beendet!"]
        assert not game.running

    @pytest.mark.parametrize(("show", "solution"), [(True, ["Die korrekte Antwort war: Muh"]), (False, [])])
    def test_timeout(self, show: bool, solution: list[str]) -> None:
        game = Quiz(QUIZZES[0], show, now=0, rng=rng())
        assert game.tick(QUIZ_QUESTION_TIME) == [
            "Die Frage wurde nicht rechtzeitig beantwortet.",
            *solution,
            "Wie macht die Katze?",
        ]
        assert game.tick(2 * QUIZ_QUESTION_TIME)[-1] == "Das waren alle Fragen, das Quiz ist daher beendet!"
        assert not game.running
