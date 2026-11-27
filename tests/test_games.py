import random

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
        assert game.throw("anna", 1) == ["anna, du hast die Kartoffel doch gar nicht!"]
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
            other = next(p for p in game.players if p != game.holder)
            assert game.throw(other, now) == [f"{other}, du hast die Kartoffel doch gar nicht!"]
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
