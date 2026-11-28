import random
from collections.abc import Callable

import pytest

from wkqb.games import (
    HANGMAN_TIMEOUT,
    POTATO_SIGNUP,
    POTATO_THROW_TIME,
    QUIZ_QUESTION_TIME,
    WIRES,
    WORDMIX_TIME,
    Hangman,
    HotPotato,
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
        assert game.cut("bert", wrong) == [
            f"Das war leider das falsche Kabel! Richtig wäre <b>{game.wire}</b> gewesen."
        ]
        assert not game.running

    def test_unknown_wire(self) -> None:
        game = Timebomb("anna", "bert", now=0, rng=rng())
        assert game.cut("bert", "Lila")[0].startswith("Welches Kabel?")
        assert game.cut("bert", "")[0].startswith("Welches Kabel?")
        assert game.running

    def test_timeout(self) -> None:
        game = Timebomb("anna", "bert", now=0, rng=rng())
        assert game.tick(game.seconds) == [
            "bert hat es nicht rechtzeitig geschafft, die Bombe zu entschärfen :-( "
            f"Richtig wäre <b>{game.wire}</b> gewesen."
        ]


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
        assert game.answer("anna", "MIAU", 6)[1:] == [
            "Das waren alle Fragen, das Quiz ist daher beendet! Gewonnen haben bert, anna mit je 1 richtigen Antwort!"
        ]
        assert not game.running

    @pytest.mark.parametrize(("show", "solution"), [(True, ["Die korrekte Antwort war: Muh"]), (False, [])])
    def test_timeout(self, show: bool, solution: list[str]) -> None:
        game = Quiz(QUIZZES[0], show, now=0, rng=rng())
        assert game.tick(QUIZ_QUESTION_TIME) == [
            "Die Frage wurde nicht rechtzeitig beantwortet.",
            *solution,
            "Wie macht die Katze?",
        ]
        assert game.tick(2 * QUIZ_QUESTION_TIME)[-1] == (
            "Das waren alle Fragen, das Quiz ist daher beendet! Leider wurde keine Frage richtig beantwortet."
        )
        assert not game.running


def test_command_sign_in_the_texts() -> None:
    assert "<b>#hangman BUCHSTABE</b>" in Hangman(["Katze"], True, now=0, rng=rng(), sign="#").start()[1]
    assert "<b>#wordmix LÖSUNG</b>" in Wordmix(["Katze"], True, now=0, rng=rng(), sign="#").start()[1]
    assert "<b>#schneide FARBE</b>" in Timebomb("a", "b", now=0, rng=rng(), sign="#").start()[1]
    assert quiz_menu(QUIZZES, "&")[1] == "Sende &amp;quiz ID, um das gewünschte Quiz zu starten!"


def test_hangman_pictures() -> None:
    url = '<img src="https://wkqb.de/hangman/gerade/ff6600/hm{n}.gif">'
    game = Hangman(["Katze"], True, now=0, rng=rng(), picture=url)
    assert game.start()[0].endswith('(5 Zeichen) <img src="https://wkqb.de/hangman/gerade/ff6600/hm0.gif">')
    assert game.guess("anna", "k", 1) == ["anna, der Buchstabe K kommt vor: K _ _ _ _"]  # no new picture
    assert game.guess("anna", "x", 2)[1].endswith(
        'Verbleibende Versuche: 9 <img src="https://wkqb.de/hangman/gerade/ff6600/hm1.gif">'
    )


def test_hangman_smileys_and_the_last_picture() -> None:
    game = Hangman(["Katze"], False, now=0, rng=rng(), picture=":hangman{n}")
    assert game.start()[0].endswith("(5 Zeichen) :hangman0")
    for i, word in enumerate(["Hund", "Maus", "Igel"]):
        game.guess("anna", word, i)
    assert game.guess("anna", "Wurm", 4)[1] == "Leider sind alle Versuche aufgebraucht :-( :hangman10"


class TestWordmixHints:
    def test_hints(self) -> None:
        game = Wordmix(["Gemüse"], True, now=0, rng=rng(), hints=True)
        assert "<b>5 Minuten</b>" in game.start()[1]
        assert game.tick(149) == []
        assert game.tick(150) == ["Tipp: <b>g _ _ _ _ e</b>"]
        assert game.tick(151) == []
        [second] = game.tick(240)
        assert second.startswith("Tipp: <b>g ") and second.endswith(" e</b>")
        assert second.count("_") == 3  # one more letter
        assert game.tick(250) == []
        assert game.tick(WORDMIX_TIME)[0] == "Die Zeit für Wordmix ist abgelaufen!"

    def test_no_hints(self) -> None:
        game = Wordmix(["Gemüse"], True, now=0, rng=rng())
        assert game.tick(245) == []

    def test_no_hints_after_the_end(self) -> None:
        game = Wordmix(["Gemüse"], True, now=0, rng=rng(), hints=True)
        game.guess("anna", "gemüse", 10)
        assert game.tick(95) == []


class TestHotPotato:
    def game(self) -> HotPotato:
        game = HotPotato("anna", now=0, rng=rng())
        game.join("bert")
        game.join("cem")
        return game

    def test_signup(self) -> None:
        game = HotPotato("anna", now=0, rng=rng(), sign="#")
        assert "<b>#kartoffel</b>" in game.start()[0] and "<b>#wurf</b>" in game.start()[0]
        assert game.join("Bert") == ["Bert spielt mit! (2 Spieler)"]
        assert game.join("bert") == ["bert, du spielst doch schon mit!"]
        assert game.join("ANNA") == ["ANNA, du spielst doch schon mit!"]
        assert game.throw("anna", 1) == ["anna, das Spiel hat doch noch gar nicht angefangen!"]
        assert game.tick(POTATO_SIGNUP - 1) == []

    def test_too_few_players(self) -> None:
        game = HotPotato("anna", now=0, rng=rng())
        game.join("bert")
        assert game.tick(POTATO_SIGNUP) == ["Für Heiße Kartoffel braucht es mindestens 3 Spieler, das Spiel fällt aus."]
        assert not game.running

    def test_throws_until_cold(self) -> None:
        game = self.game()
        lines = game.tick(POTATO_SIGNUP)
        assert lines[0] == "Es spielen mit: anna, bert, cem"
        assert (
            game.holder in ("anna", "bert", "cem")
            and lines[1] == f"Die heiße Kartoffel fliegt zu <b>{game.holder}</b>! Schnell, <b>!wurf</b>!"
        )
        assert game.join("dora") == ["Das Spiel läuft schon, du musst bis zum nächsten warten."]
        now = POTATO_SIGNUP
        for _ in range(game.cold_after - 1):
            now += 1
            assert game.holder is not None
            assert game.throw("dora", now) == ["Du spielst nicht mit, dora!"]
            assert game.throw(game.holder.upper(), now)[0].startswith("Die heiße Kartoffel fliegt zu")
            assert game.deadline == now + POTATO_THROW_TIME
        assert game.holder is not None
        assert game.throw(game.holder, now + 1) == ["Die Kartoffel ist kalt! Gewonnen haben: anna, bert, cem"]
        assert not game.running

    def test_too_slow(self) -> None:
        game = self.game()
        game.tick(POTATO_SIGNUP)
        first = game.holder
        lines = game.tick(POTATO_SIGNUP + POTATO_THROW_TIME)
        assert lines[0] == f"{first} war zu langsam und hat sich die Finger verbrannt!"
        assert first not in game.players and len(game.players) == 2
        second = game.holder
        lines = game.tick(POTATO_SIGNUP + 2 * POTATO_THROW_TIME)
        [winner] = game.players
        assert lines == [f"{second} war zu langsam und hat sich die Finger verbrannt!", f"{winner} hat gewonnen!"]
        assert not game.running

    def test_throwing_without_the_potato_costs_the_game(self) -> None:
        game = self.game()
        game.tick(POTATO_SIGNUP)
        holder = game.holder
        assert holder is not None
        others = [p for p in game.players if p != holder]
        deadline = game.deadline
        assert game.throw(others[0], POTATO_SIGNUP + 1) == [f"{others[0]} hat sich verworfen und scheidet aus!"]
        assert game.holder == holder and game.deadline == deadline  # the potato stays where it is
        assert game.throw(others[1], POTATO_SIGNUP + 2) == [
            f"{others[1]} hat sich verworfen und scheidet aus!",
            f"{holder} hat gewonnen!",
        ]
        assert not game.running

    def test_cold_after_10_to_20_throws(self) -> None:
        assert {HotPotato("a", now=0, rng=random.Random(seed)).cold_after for seed in range(200)} == set(range(10, 21))

    def test_names_are_escaped(self) -> None:
        game = HotPotato("<b>", now=0, rng=rng())
        assert game.start()[0].startswith("&lt;b&gt; startet Heiße Kartoffel!")


def test_quiz_asks_at_most_15_questions_in_random_order() -> None:
    questions = [Question(f"Frage {i}?", str(i)) for i in range(20)]
    game = Quiz(QuizSet("Viele", questions), True, now=0, rng=rng())
    assert len(game.questions) == 15 and len(set(game.questions)) == 15
    assert game.questions != questions[:15]
    assert quiz_menu([QuizSet("Viele", questions)])[0] == "Ich habe folgende Quizze anzubieten: 1: Viele (15 Fragen)"
    assert game.deadline == QUIZ_QUESTION_TIME == 30


def test_quiz_plain_answers() -> None:
    game = Quiz(QUIZZES[0], True, now=0, rng=rng())
    first = game.current
    assert game.check("anna", "falsch", 1) == []
    assert game.check("anna", f"  {first.answer.upper()} ", 2)[0].endswith(f"anna, {first.answer} ist korrekt!")


def everybody(user: str) -> bool:
    return True


def only(*names: str) -> Callable[[str], bool]:
    return lambda user: user in names


class TestPointsHangman:
    def game(self, word: str = "Katze", scores: Callable[[str], bool] | None = everybody) -> Hangman:
        return Hangman([word], True, now=0, rng=rng(), scores=scores)

    def test_a_point_per_new_correct_letter_without_announcement(self) -> None:
        game = self.game("Banane")
        assert game.guess("anna", "a", 1) == ["anna, der Buchstabe A kommt vor: _ A _ A _ _"]
        game.guess("bert", "a", 2)  # already tried
        game.guess("bert", "x", 3)  # wrong
        game.guess("bert", "n", 4)
        assert game.awards == [("anna", 1), ("bert", 1)]

    def test_the_letter_that_completes_the_word_scores_too(self) -> None:
        game = self.game("ab")
        game.guess("anna", "a", 1)
        assert game.guess("bert", "b", 2) == ["Die Lösung wurde von bert gefunden: ab"]
        assert game.awards == [("anna", 1), ("bert", 1)]

    def test_the_solution_gives_the_remaining_tries(self) -> None:
        game = self.game()
        game.guess("anna", "x", 1)
        game.guess("anna", "Hund", 2)
        assert game.remaining == 6  # a wrong word costs 3 tries
        assert game.guess("bert", "katze", 3) == ["Volltreffer bert, Katze ist richtig! bert bekommt 6 Punkte!"]
        assert game.awards == [("bert", 6)]

    def test_one_point_is_singular(self) -> None:
        game = self.game()
        for i, wrong in enumerate(["Hund", "Maus", "Igel"]):
            game.guess("anna", wrong, i)
        assert game.remaining == 1
        assert game.guess("bert", "Katze", 10) == ["Volltreffer bert, Katze ist richtig! bert bekommt 1 Punkt!"]

    def test_nothing_for_a_lost_game_or_a_timeout(self) -> None:
        game = self.game()
        for i, wrong in enumerate(["Hund", "Maus", "Igel", "Wurm"]):
            game.guess("anna", wrong, i)
        assert not game.running and game.awards == []
        game = self.game()
        game.tick(HANGMAN_TIMEOUT)
        assert game.awards == []

    def test_players_without_points_get_nothing_silently(self) -> None:
        game = self.game(scores=only("anna"))
        game.guess("bert", "k", 1)
        assert game.guess("bert", "katze", 2) == ["Volltreffer bert, Katze ist richtig!"]
        assert game.awards == []

    def test_without_scores_nobody_gets_points(self) -> None:
        game = self.game(scores=None)
        game.guess("anna", "k", 1)
        assert game.guess("anna", "katze", 2) == ["Volltreffer anna, Katze ist richtig!"]
        assert game.awards == []

    def test_names_are_escaped_in_the_announcement(self) -> None:
        game = self.game()
        assert game.guess("<b>", "katze", 1) == [
            "Volltreffer &lt;b&gt;, Katze ist richtig! &lt;b&gt; bekommt 10 Punkte!"
        ]
        assert game.awards == [("<b>", 10)]


class TestPointsWordmix:
    @pytest.mark.parametrize(
        ("length", "points"),
        [(1, 0), (2, 1), (7, 3), (10, 5), (11, 7), (12, 7), (20, 7), (21, 15), (30, 15)],
    )
    def test_points_by_length_without_hints(self, length: int, points: int) -> None:
        game = Wordmix(["a" * length], True, now=0, rng=rng(), scores=everybody)
        assert game.points() == points

    @pytest.mark.parametrize(
        ("length", "hints", "points"),
        [(7, 1, 1), (7, 2, 1), (10, 1, 2), (10, 2, 1), (12, 1, 3), (12, 2, 2), (21, 1, 7), (21, 2, 5)],
    )
    def test_points_by_length_and_hints(self, length: int, hints: int, points: int) -> None:
        game = Wordmix(["a" * length], True, now=0, rng=rng(), hints=True, scores=everybody)
        for now in [150, 240][:hints]:
            game.tick(now)
        assert game.hints_shown == hints and game.points() == points

    def test_the_announcement_and_the_award(self) -> None:
        game = Wordmix(["Gemüse"], True, now=0, rng=rng(), scores=everybody)
        assert game.guess("anna", "Gemüse", 1) == ["Volltreffer anna, Gemüse ist richtig! anna bekommt 3 Punkte!"]
        assert game.awards == [("anna", 3)]

    def test_with_a_hint_the_announcement_shows_the_lower_points(self) -> None:
        game = Wordmix(["Gemüsesuppe"], True, now=0, rng=rng(), hints=True, scores=everybody)
        game.tick(150)
        assert game.guess("anna", "Gemüsesuppe", 151)[0].endswith("anna bekommt 3 Punkte!")

    def test_a_word_worth_one_point_says_punkt(self) -> None:
        game = Wordmix(["ab"], True, now=0, rng=rng(), scores=everybody)
        assert game.guess("anna", "ab", 1) == ["Volltreffer anna, ab ist richtig! anna bekommt 1 Punkt!"]

    def test_a_word_worth_nothing_gives_no_award(self) -> None:
        game = Wordmix(["a"], True, now=0, rng=rng(), scores=everybody)
        assert game.guess("anna", "a", 1) == ["Volltreffer anna, a ist richtig!"]
        assert game.awards == []

    def test_players_without_points_and_the_default(self) -> None:
        game = Wordmix(["Gemüse"], True, now=0, rng=rng(), scores=only("bert"))
        assert game.guess("anna", "Gemüse", 1) == ["Volltreffer anna, Gemüse ist richtig!"]
        game = Wordmix(["Gemüse"], True, now=0, rng=rng())
        assert game.guess("anna", "Gemüse", 1) == ["Volltreffer anna, Gemüse ist richtig!"]
        assert game.awards == []

    def test_nothing_after_the_time_is_up(self) -> None:
        game = Wordmix(["Gemüse"], True, now=0, rng=rng(), scores=everybody)
        game.tick(WORDMIX_TIME)
        assert game.awards == []


class TestPointsTimebomb:
    def test_defusing_gives_5_points_to_the_victim(self) -> None:
        game = Timebomb("anna", "bert", now=0, rng=rng(), scores=everybody)
        assert game.cut("bert", game.wire) == ["bert hat die Bombe entschärft. Glückwunsch! bert bekommt 5 Punkte!"]
        assert game.awards == [("bert", 5)]

    def test_nothing_for_a_wrong_wire_cheating_or_the_timeout(self) -> None:
        game = Timebomb("anna", "bert", now=0, rng=rng(), scores=everybody)
        game.cut("bert", next(w for w in game.wires if w != game.wire))
        assert game.awards == []
        game = Timebomb("anna", "bert", now=0, rng=rng(), scores=everybody)
        game.cheat("bert")
        assert game.awards == []
        game = Timebomb("anna", "bert", now=0, rng=rng(), scores=everybody)
        game.tick(game.seconds)
        assert game.awards == []

    def test_nothing_for_the_victim_without_points(self) -> None:
        game = Timebomb("anna", "bert", now=0, rng=rng(), scores=only("anna"))
        assert game.cut("bert", game.wire) == ["bert hat die Bombe entschärft. Glückwunsch!"]
        assert game.awards == []

    def test_without_scores(self) -> None:
        game = Timebomb("anna", "bert", now=0, rng=rng())
        assert game.cut("bert", game.wire) == ["bert hat die Bombe entschärft. Glückwunsch!"]
        assert game.awards == []

    def test_the_name_is_escaped(self) -> None:
        game = Timebomb("anna", "<i>", now=0, rng=rng(), scores=everybody)
        assert game.cut("<i>", game.wire)[0].endswith("Glückwunsch! &lt;i&gt; bekommt 5 Punkte!")


class TestPointsQuiz:
    def play(self, answers: list[str], scores: Callable[[str], bool] | None = everybody) -> tuple[Quiz, list[str]]:
        """A quiz of len(answers) questions; each answer is the name of the one who gets it right ("" for nobody)"""
        quiz = QuizSet("Zahlen", [Question(f"{i}?", str(i)) for i in range(len(answers))])
        game = Quiz(quiz, True, now=0, rng=rng(), scores=scores)
        lines: list[str] = []
        for i, user in enumerate(answers):
            lines = game.answer(user, game.current.answer, i) if user else game.tick(game.deadline or 0)
        return game, lines

    def test_two_points_per_right_answer(self) -> None:
        game = Quiz(QUIZZES[0], True, now=0, rng=rng(), scores=everybody)
        [praise, _] = game.answer("anna", "muh", 1)
        assert praise.endswith("anna, Muh ist korrekt! anna bekommt 2 Punkte!")
        assert game.awards == [("anna", 2)]

    def test_plain_message_answers_score_too(self) -> None:
        game = Quiz(QUIZZES[0], True, now=0, rng=rng(), scores=everybody)
        assert game.check("anna", "muh", 1)[0].endswith("anna bekommt 2 Punkte!")
        assert game.awards == [("anna", 2)]

    def test_the_winner_gets_10_bonus_points_in_the_end_line(self) -> None:
        game, lines = self.play(["anna", "anna", "bert"])
        assert lines[-1] == (
            "Das waren alle Fragen, das Quiz ist daher beendet! anna hat mit 2 richtigen Antworten gewonnen! "
            "anna bekommt 10 Punkte!"
        )
        assert game.awards == [("anna", 2), ("anna", 2), ("bert", 2), ("anna", 10)]

    def test_three_players_split_the_bonus(self) -> None:
        game, lines = self.play(["anna", "bert", "cem"])
        assert lines[-1].endswith(
            "Gewonnen haben anna, bert, cem mit je 1 richtigen Antwort! anna, bert, cem bekommen je 3 Punkte!"
        )
        assert game.awards[3:] == [("anna", 3), ("bert", 3), ("cem", 3)]

    def test_two_players_split_the_bonus_in_halves(self) -> None:
        game, lines = self.play(["anna", "bert"])
        assert lines[-1].endswith("anna, bert bekommen je 5 Punkte!")
        assert game.awards[2:] == [("anna", 5), ("bert", 5)]

    def test_case_variants_count_as_one_player(self) -> None:
        game, lines = self.play(["anna", "Anna"])
        assert "mit 2 richtigen Antworten" in lines[-1]
        assert game.awards[2:] == [("Anna", 10)] or game.awards[2:] == [("anna", 10)]

    def test_a_guest_winner_gets_no_bonus(self) -> None:
        game, lines = self.play(["gast", "gast", "bert"], scores=only("bert"))
        assert (
            lines[-1]
            == "Das waren alle Fragen, das Quiz ist daher beendet! gast hat mit 2 richtigen Antworten gewonnen!"
        )
        assert game.awards == [("bert", 2)]

    def test_only_the_players_with_points_are_announced_among_equals(self) -> None:
        game, lines = self.play(["gast", "bert"], scores=only("bert"))
        assert lines[-1].endswith("Gewonnen haben gast, bert mit je 1 richtigen Antwort! bert bekommt 5 Punkte!")
        assert game.awards == [("bert", 2), ("bert", 5)]

    def test_nobody_right_nobody_paid(self) -> None:
        game, lines = self.play(["", ""])
        assert lines[-1].endswith("Leider wurde keine Frage richtig beantwortet.")
        assert game.awards == []

    def test_without_scores_the_texts_stay_plain(self) -> None:
        game, lines = self.play(["anna"], scores=None)
        assert (
            lines[-1] == "Das waren alle Fragen, das Quiz ist daher beendet! anna hat mit 1 richtigen Antwort gewonnen!"
        )
        assert game.awards == []

    def test_more_than_ten_equal_players_get_nothing(self) -> None:
        names = [f"p{i}" for i in range(11)]
        game, lines = self.play(names)
        assert "bekommen" not in lines[-1] and game.awards[11:] == []


class TestPointsHotPotato:
    def game(self, scores: Callable[[str], bool] | None = everybody) -> HotPotato:
        game = HotPotato("anna", now=0, rng=rng(), scores=scores)
        game.join("bert")
        game.join("cem")
        return game

    def test_the_last_player_gets_5_points(self) -> None:
        game = self.game()
        game.tick(POTATO_SIGNUP)
        game.tick(POTATO_SIGNUP + POTATO_THROW_TIME)
        lines = game.tick(POTATO_SIGNUP + 2 * POTATO_THROW_TIME)
        [winner] = game.players
        assert lines[-1] == f"{winner} hat gewonnen! {winner} bekommt 5 Punkte!"
        assert game.awards == [(winner, 5)]

    def test_everybody_left_gets_3_points_when_the_potato_is_cold(self) -> None:
        game = self.game()
        game.tick(POTATO_SIGNUP)
        now = POTATO_SIGNUP
        for _ in range(game.cold_after - 1):
            now += 1
            assert game.holder is not None
            game.throw(game.holder, now)
        assert game.holder is not None
        assert game.throw(game.holder, now + 1) == [
            "Die Kartoffel ist kalt! Gewonnen haben: anna, bert, cem anna, bert, cem bekommen je 3 Punkte!"
        ]
        assert game.awards == [("anna", 3), ("bert", 3), ("cem", 3)]

    def test_only_players_with_points_are_paid_and_named(self) -> None:
        game = self.game(scores=only("bert"))
        game.tick(POTATO_SIGNUP)
        game.cold_after = 1
        assert game.holder is not None
        assert game.throw(game.holder, POTATO_SIGNUP + 1) == [
            "Die Kartoffel ist kalt! Gewonnen haben: anna, bert, cem bert bekommt 3 Punkte!"
        ]
        assert game.awards == [("bert", 3)]

    def test_a_lone_winner_without_points(self) -> None:
        game = self.game(scores=only())
        game.tick(POTATO_SIGNUP)
        game.tick(POTATO_SIGNUP + POTATO_THROW_TIME)
        lines = game.tick(POTATO_SIGNUP + 2 * POTATO_THROW_TIME)
        assert lines[-1] == f"{game.players[0]} hat gewonnen!" and game.awards == []

    def test_too_few_players_nothing(self) -> None:
        game = HotPotato("anna", now=0, rng=rng(), scores=everybody)
        game.join("bert")
        game.tick(POTATO_SIGNUP)
        assert game.awards == []

    def test_throwing_out_of_turn_scores_only_for_the_winner(self) -> None:
        game = self.game()
        game.tick(POTATO_SIGNUP)
        holder = game.holder
        others = [p for p in game.players if p != holder]
        game.throw(others[0], POTATO_SIGNUP + 1)
        assert game.awards == []
        assert game.throw(others[1], POTATO_SIGNUP + 2)[-1] == f"{holder} hat gewonnen! {holder} bekommt 5 Punkte!"
        assert game.awards == [(holder, 5)]
