class Command:
    class Commands:
        PING = "ping"
        WAIT = "wait"
        SAY = "say"
        QUOTE = "quote"
        RELOAD = "reload"
        QUIT = "quit"
        MASTER = "master"
        ADMINS = "admins"
        MODS = "mods"
        IGNORED = "ignored"
        LEVEL = "level"

        HANGMAN = "hangman"
        WORDMIX = "wordmix"
        TIMEBOMB = "zeitbombe"
        CUT = "schneide"
        QUIZ = "quiz"

    def __init__(self, cmd, param_string=""):
        self.cmd = cmd
        self.param_string = param_string
        self.param_list = param_string.split(" ")
