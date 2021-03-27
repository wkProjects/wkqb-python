import json
import logging
import os
import random
import re
import signal
import threading
import time

import schedule

from chatmessage import Level, Outgoing
from commands import Command
from config import Generic
from games import Hangman, Timebomb, Wordmix
from webkicks import Webkicks

logger = logging.getLogger(__name__)


class WKQB:

    def __init__(self):
        self.webkicks = Webkicks(os.environ.get("WK_CHATURL"), username=os.environ.get("WK_USERNAME"),
                                 password=os.environ.get("WK_PASSWORD"))
        self.config = self.load_settings()
        self.user_list = {}
        self.hangman = None
        self.wordmix = None
        self.timebomb = None

        signal.signal(signal.SIGHUP, self.handle_signal)
        signal.signal(signal.SIGINT, self.handle_signal)
        signal.signal(signal.SIGTERM, self.handle_signal)

    def run(self):

        self.webkicks.login()
        self.webkicks.send_delayed(Outgoing("Hallo! (wkQB 5.0, https://wkqb.de)"), 5)
        stream_url = self.webkicks.get_stream_url()

        chat_started = False

        stream = self.webkicks.http_client.get(stream_url, stream=True)
        if stream.encoding is None:
            stream.encoding = 'utf-8'

        # now we listen for chat messages, as long as the stream is open
        for line in stream.iter_lines(decode_unicode=True):
            if line:
                if self.webkicks.Pattern.UPDATE.match(line) and not chat_started:
                    logger.info("Chat initialized: " + time.strftime("%a, %d %b %Y %H:%M:%S"))

                    # here we can initialize some things, cause we got the first chat message
                    self.schedule_quotes()

                    # starting the event loop in a separate thread
                    ev_loop = threading.Thread(target=self.event_loop, daemon=True)
                    ev_loop.start()

                    chat_started = True
                if chat_started:
                    chat_message = self.webkicks.parse_message(line)
                    if chat_message and chat_message.user:
                        if self.is_ignored(chat_message.user):
                            chat_message.level = Level.IGNORED
                        elif self.is_master(chat_message.user):
                            chat_message.level = Level.MASTER
                        elif self.is_admin(chat_message.user):
                            chat_message.level = Level.ADMIN
                        elif self.is_mod(chat_message.user):
                            chat_message.level = Level.MOD

                    self.handle_message(chat_message)

        # stream is closed, time to cleanup
        pass

    def handle_message(self, chat_message):
        if chat_message:
            if chat_message.user == self.webkicks.username:
                # we dont want to react to our own messages
                return
            if chat_message.level == Level.IGNORED:
                # we also ignore ignored users, obviously
                return
            if chat_message.type == Webkicks.Type.LOGIN:
                logger.debug(chat_message.user + " logged in!")
                self.handle_user_login(chat_message.user)

            elif chat_message.type == Webkicks.Type.LOGOUT:
                # we dont handle logouts for now
                pass
            elif chat_message.has_command():
                # first we handle system commands, as they have to always work
                command = chat_message.get_command()
                if command.cmd == Command.Commands.PING:
                    self.webkicks.send_message(Outgoing("Pong!"))

                elif command.cmd == Command.Commands.SAY:
                    if chat_message.level >= Level.ADMIN:
                        self.webkicks.send_message(Outgoing(command.param_string))

                elif command.cmd == Command.Commands.RELOAD:
                    if chat_message.level >= Level.MOD:
                        self.config = self.load_settings()
                        schedule.clear("quotes")
                        self.schedule_quotes()
                        self.webkicks.send_message(Outgoing("Einstellungen neu geladen!"))

                elif command.cmd == Command.Commands.QUIT:
                    if chat_message.level >= Level.ADMIN:
                        self.webkicks.send_message(Outgoing("Oh je, ich muss wohl gehen :-("))
                        self.webkicks.send_message(Outgoing("/exit"))

                elif command.cmd == Command.Commands.MASTER:
                    self.webkicks.send_message(Outgoing("Master: " + self.config.users.master))

                elif command.cmd == Command.Commands.ADMINS:
                    if len(self.config.users.admins) > 0:
                        self.webkicks.send_message(Outgoing("Admins: " + str.join(", ", self.config.users.admins)))
                    else:
                        self.webkicks.send_message(Outgoing("Es gibt keine Admins :-("))

                elif command.cmd == Command.Commands.MODS:
                    if len(self.config.users.mods) > 0:
                        self.webkicks.send_message(Outgoing("Admins: " + str.join(", ", self.config.users.mods)))
                    else:
                        self.webkicks.send_message(Outgoing("Es gibt keine Mods :-("))

                elif command.cmd == Command.Commands.IGNORED:
                    if len(self.config.users.ignored) > 0:
                        self.webkicks.send_message(Outgoing("Ignoriert: " + str.join(", ", self.config.users.ignored)))
                    else:
                        self.webkicks.send_message(Outgoing("Niemand wird ignoriert :-)"))

                elif command.cmd == Command.Commands.LEVEL:
                    self.webkicks.send_message(
                        Outgoing("%s, dein Level ist: %s" % (
                            chat_message.user, Level.get_name(chat_message.level))))

                elif command.cmd == Command.Commands.QUOTE:
                    if command.param_string:
                        if command.param_string.isdigit():
                            index = max(1, int(command.param_string)) - 1
                            if index > len(self.config.quote.quotes):
                                self.webkicks.send_message(Outgoing("So viele Zitate habe ich nicht :-("))
                            else:
                                self.webkicks.send_message(
                                    Outgoing(self.config.quote.quotes[index]))
                        else:
                            filtered_quotes = [quote for quote in self.config.quote.quotes if
                                               quote.lower().find(command.param_string.lower()) != -1]
                            if len(filtered_quotes) == 0:
                                self.webkicks.send_message(
                                    Outgoing("Kein Zitat mit '" + command.param_string + "' gefunden :-("))
                            else:
                                self.webkicks.send_message(Outgoing(random.choice(filtered_quotes)))
                    else:
                        self.send_random_quote()

                elif command.cmd == Command.Commands.HANGMAN:
                    if not self.hangman:
                        self.hangman = Hangman(self.config.hangman)
                        schedule.every(1).minutes.do(self.hangman_timeout).tag("hangman")
                        self.webkicks.send_message(Outgoing(self.hangman.start()))
                    else:
                        self.webkicks.send_message(Outgoing(self.hangman.handle(chat_message)))
                        schedule.clear("hangman")
                        if not self.hangman.running:
                            self.hangman = None
                        else:
                            schedule.every(1).minutes.do(self.hangman_timeout).tag("hangman")

                elif command.cmd == Command.Commands.WORDMIX:
                    if not self.wordmix:
                        self.wordmix = Wordmix(self.config.wordmix)
                        schedule.every(2).minutes.do(self.wordmix_timeout).tag("wordmix")
                        self.webkicks.send_message(Outgoing(self.wordmix.start()))
                    else:
                        self.webkicks.send_message(Outgoing(self.wordmix.handle(chat_message)))
                        if not self.wordmix.running:
                            schedule.clear("wordmix")
                            self.wordmix = None

                elif command.cmd in [Command.Commands.TIMEBOMB, Command.Commands.CUT]:
                    if command.cmd == Command.Commands.TIMEBOMB:
                        if not self.timebomb:
                            if self.is_ignored(command.param_string):
                                self.webkicks.send_message(Outgoing("Mit ignorierten Usern spiele ich nicht!"))
                            else:
                                self.timebomb = Timebomb(chat_message.user, command.param_string)
                                schedule.every(self.timebomb.time).seconds.do(self.timebomb_timeout).tag("timebomb")
                                self.webkicks.send_message(Outgoing(self.timebomb.start(chat_message)))
                        else:
                            self.webkicks.send_message(Outgoing("Es läuft bereits ein Spiel!"))
                    elif command.cmd == Command.Commands.CUT:
                        if self.timebomb:
                            self.webkicks.send_message(
                                Outgoing(self.timebomb.handle(chat_message)))
                        else:
                            self.webkicks.send_message(Outgoing("Es läuft doch gar kein Spiel!"))
                    if self.timebomb and not self.timebomb.running:
                        schedule.clear("timebomb")
                        self.timebomb = None

                else:
                    # here we handle custom commands
                    for cmd in self.config.commands.list:
                        if cmd.command == command.cmd and chat_message.level >= cmd.min_level:
                            self.webkicks.send_message(Outgoing(cmd.reaction,
                                                                replacements={"user": chat_message.user,
                                                                              "param": command.param_string}))
                            break
            else:
                # no we do the pattern matching, based on the conditions provided
                for entry in self.config.pattern.list:
                    if entry.guest_reaction or chat_message.level >= 1:
                        if (entry.type == 'regex' and re.search(entry.pattern, chat_message.message)) or (
                                entry.type == 'plain' and entry.pattern in chat_message.message):
                            if not entry.needs_bot_name or re.search(r"\b" + self.webkicks.username.lower() + r"\b",
                                                                     chat_message.message.lower()):
                                self.webkicks.send_message(
                                    Outgoing(entry.reaction,
                                             replacements={"user": chat_message.user, "random": random.random()}))

    def handle_user_login(self, username):
        # is there a special greeting config for the user?
        if hasattr(self.config.greeting.custom, username):
            login_config = getattr(self.config.greeting.custom, username)
        else:
            login_config = self.config.greeting

        # if the user was here recently (in the last 30 minutes) we use the welcome back greeting
        if username in self.user_list and self.user_list[username] >= time.time() - (30 * 60):
            message = getattr(login_config, "wb")
        else:
            message = getattr(login_config, "hello")

        # regardless of the greeting we use we save the timestamp of the login
        self.user_list[username] = time.time()
        greeting = Outgoing(message, replacements={"user": username})
        self.webkicks.send_delayed(greeting, 5)

    def send_random_quote(self):
        self.webkicks.send_message(Outgoing(" ".join([self.config.quote.prefix, random.choice(self.config.quote.quotes), self.config.quote.suffix]).strip()))

    def schedule_quotes(self):
        schedule.every(self.config.quote.interval).minutes.do(self.send_random_quote).tag("quotes")

    def is_ignored(self, username):
        return username.lower() in map(str.lower, self.config.users.ignored) and not self.is_mod(username.lower())

    def is_mod(self, username):
        return self.is_admin(username) or username.lower() in map(str.lower, self.config.users.mods)

    def is_admin(self, username):
        return self.is_master(username) or username.lower() in map(str.lower, self.config.users.admins)

    def is_master(self, username):
        return username.lower() == self.config.users.master.lower()

    def handle_signal(self, received_signal, frame):
        if received_signal == signal.SIGHUP:
            self.load_settings()
        elif received_signal in [signal.SIGTERM, signal.SIGINT]:
            self.webkicks.logout()

    def hangman_timeout(self):
        self.webkicks.send_message(Outgoing(self.hangman.timeout()))
        self.hangman = None
        schedule.clear("hangman")

    def wordmix_timeout(self):
        self.webkicks.send_message(Outgoing(self.wordmix.timeout()))
        self.wordmix = None
        schedule.clear("wordmix")

    def timebomb_timeout(self):
        self.webkicks.send_message(Outgoing(self.timebomb.timeout()))
        self.timebomb = None
        schedule.clear("timebomb")

    @staticmethod
    def load_settings():
        return json.load(open("config.json", "r", encoding="utf-8"), object_hook=Generic.from_dict)

    @staticmethod
    def event_loop():
        while True:
            schedule.run_pending()
            time.sleep(1)
