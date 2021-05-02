import random

import schedule

from chatmessage import Incoming, Outgoing
from webkicks import Webkicks

class Quiz:
    def __init__(self, webkicks: Webkicks, config):
        self.webkicks = webkicks
        self.config = config
        self.running = False
        self.quiz = None
        self.current_question = None
        self.current_answer = None

    def handle(self, chat_message: Incoming):
        param_string = chat_message.get_command().param_string
        if len(self.config.quizzes) == 0:
            self.webkicks.send_message(Outgoing("Ich kenne leider kein Quiz :-("))
            return

        if not self.running:
            if not param_string:
                quizzes = [f"ID: {id} => {quiz.name} ({len(quiz.questions)} Fragen)" for id, quiz in enumerate(self.config.quizzes, 1)]
                self.webkicks.send_message(Outgoing([
                    "Ich habe folgende Quizze anzubieten:",
                    *quizzes,
                    "Sende !quiz [ID], um das gewünschte Quiz zu starten!"

                ]))
                return

            try:
                quiz_id = int(param_string)
                if quiz_id <= 0:
                    raise IndexError()
                if self.config.quizzes[quiz_id-1] is not None:
                    self.quiz = self.config.quizzes[quiz_id-1]
                    self.running = True
                    self.webkicks.send_message(Outgoing(f"Starte Quiz: {self.quiz.name}"))
                    self.next_question()
            except (ValueError, IndexError):
                self.webkicks.send_message(Outgoing(f"{param_string} ist keine gültige ID."))

        else:
            if param_string.lower() == self.current_answer.lower():
                self.webkicks.send_message(Outgoing(f"{random.choice(['Super', 'Toll', 'Sehr gut'])} {chat_message.user}, {self.current_answer} ist korrekt!"))
                self.next_question()
            else:
                self.webkicks.send_message(Outgoing(f"{param_string} ist leider falsch!"))

    def next_question(self):
        schedule.clear("quiz")
        if self.current_question is None:
            self.current_question = 0
        try:
            question = self.quiz.questions[self.current_question].question
            answer = self.quiz.questions[self.current_question].answer
            self.current_question += 1
            self.current_answer = answer
            schedule.every(20).seconds.do(self.timeout).tag("quiz")
            self.webkicks.send_message(Outgoing(question))
        except IndexError:
            self.stop_quiz()

    def timeout(self):
        self.webkicks.send_message(Outgoing([
            "Die Frage wurde nicht rechtzeitig beantwortet.",
            f"Die korrekte Antwort war: {self.current_answer}" if self.config.show_solution else None,
        ]))
        self.next_question()

    def stop_quiz(self):
        self.running = False
        self.quiz = None
        self.current_question = None
        self.current_answer = None
        self.webkicks.send_message(Outgoing("Das waren alle Fragen, das Quiz ist daher beendet!"))
