#!/usr/bin/env python

# Copyright (c) 2010 Greg Clark
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import sys, os, string, random, commands

from threading import Thread, Event
from Queue import Queue, Empty

# This script is meant to be run by AEI.  The current directory should be
# the AEI directory, which we need in the path to find pyrimaa/board.py.
aei_directory = "."
sys.path.append(aei_directory)
from pyrimaa import board
from pyrimaa.board import Position, Color

class AEIAdapter(object):
    def __init__(self, command, controller):
        pid = str(os.getpid())
        self.posFileName       = "running/matchPos"       + pid
        self.moveFileName      = "running/matchMove"      + pid
        self.gamestateFileName = "running/matchGamestate" + pid
        self.command = command + " " + self.posFileName + " " + \
                self.moveFileName + " " + self.gamestateFileName
        if not os.path.exists(os.getcwd() + "/running"):
            os.mkdir(os.getcwd() + "/running")
        self.controller = controller
        try:
            header = controller.messages.get(30)
        except Empty:
            raise Exception("Timed out waiting for AEI header")
        if header != "aei":
            raise Exception("No AEI header received, instead (%s)" % header)
        controller.send("protocol-version 1")
        controller.send("id name Adapter")
        controller.send("id author Rabbits")
        controller.send("aeiok")
        self.options = {}
        self.turnnumber = 1
        self.color = Color.GOLD
        self.newgame()
        self.movesSoFar = ""

    def newgame(self):
        self.position = Position(Color.GOLD, 4, board.BLANK_BOARD)
        self.insetup = True

    def setposition(self, side_str, pos_str):
        side = "gswb".find(side_str) % 2
        self.position = board.parse_short_pos(side, 4, pos_str)
        self.insetup = False

    def setoption(self, name, value):
        if   name == "wreserve": name = "greserve"
        elif name == "breserve": name = "sreserve"
        elif name == "moveused": name = "tcmoveused"
        std_opts = set(["tcmove", "tcreserve", "tcpercent", "tcmax", "tctotal",
                "tcturns", "tcturntime", "greserve", "sreserve", "gused",
                "sused", "lastmoveused", "tcmoveused", "opponent",
                "opponent_rating"])
        if name in std_opts:
            self.options[name] = int(value)
        else:
            self.log("Warning: Received unrecognized option, %s" % (name))

    def makemove(self, move_str):
        self.position = self.position.do_move_str(move_str)
        movetag = str(self.turnnumber)
        if self.insetup and self.position.color == Color.GOLD:
            self.insetup = False
        if self.color == Color.SILVER:
            self.turnnumber += 1
            self.color = Color.GOLD
            movetag += "b " # s
        else:
            self.color = Color.SILVER
            movetag += "w " # g
        self.movesSoFar += movetag + move_str + "\n"

    def go(self):
        self.writeRunningFiles()
        (status, move_str) = commands.getstatusoutput(self.command)
        # This assumes that the last (or only) line of output is the move choice
        move_lines = move_str.split("\n")
        self.bestmove(move_lines.pop())

    def log(self, msg):
        self.controller.send("log " + msg)

    def bestmove(self, move_str):
        self.controller.send("bestmove " + move_str)

    def main(self):
        ctl = self.controller
        while not ctl.stop.isSet():
            msg = ctl.messages.get()
            if msg == "isready":
                ctl.send("readyok")
            elif msg == "newgame":
                self.newgame()
            elif msg.startswith("setposition"):
                side, pos_str = msg.split(None, 2)[1:]
                self.setposition(side, pos_str)
            elif msg.startswith("setoption"):
                words = msg.split()
                name = words[2]
                v_ix = msg.find(name) + len(name)
                v_ix = msg.find("value", v_ix)
                if v_ix != -1:
                    value = msg[v_ix + 5:]
                else:
                    value = None
                self.setoption(name, value)
            elif msg.startswith("makemove"):
                move_str = msg.split(None, 1)[1]
                self.makemove(move_str)
            elif msg.startswith("go"):
                if len(msg.split()) == 1:
                    self.go()
            elif msg == "stop":
                pass
            elif msg == "quit":
                self.removeRunningFiles()
                return

    def writeRunningFiles(self):
        posFile = open(self.posFileName, "w")
        posFile.write(self.positionString())
        posFile.close()
        moveFile = open(self.moveFileName, "w")
        moveFile.write(self.moveListString())
        moveFile.close()
        gamestateFile = open(self.gamestateFileName, "w")
        gamestateFile.write(self.gamestateString())
        gamestateFile.close()
    
    def positionString(self):
        turn = str(self.turnnumber)
        if self.color == Color.GOLD: turn += "w" # g
        else:                        turn += "b" # s
        board_str = self.position.board_to_str(dots=False)
        # Fairy needs big X's.
        board_str = board_str.replace("x", "X")
        return turn + "\n" + board_str
    
    def moveListString(self):
        string = self.movesSoFar
        string  += str(self.turnnumber)
        if self.color == Color.GOLD: string += "w" # g
        else:                        string += "b" # s
        return string + "\n"
        
    def gamestateString(self):
        gamestate = ""
        for k, v in self.options.iteritems():
            if   k == "greserve": key = "tcwreserve"
            elif k == "sreserve": key = "tcbreserve"
            else:                 key = k
            gamestate += k + "=" + str(v) + "\n"
        if self.color == Color.GOLD:
            gamestate += "turn=w\n"
        else:
            gamestate += "turn=b\n"
        gamestate += "--END--"
        return gamestate

    def removeRunningFiles(self):
        os.remove(self.posFileName)
        os.remove(self.moveFileName)
        os.remove(self.gamestateFileName)
        

class ComThread(Thread):
    def __init__(self):
        Thread.__init__(self)
        self.stop = Event()
        self.messages = Queue(1000)
        self.setDaemon(True)

    def send(self, msg):
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()

    def run(self):
        while not self.stop.isSet():
            msg = sys.stdin.readline()
            self.messages.put(msg.strip())

if __name__ == "__main__":
    argv = sys.argv[1:]
    seed = None
    for i in range(len(argv)):
        if argv[i] == "-s" or argv[i] == "--seed":
            seed = int(argv[i + 1])
            del argv[i + 1]
            del argv[i]
    if seed == None:
        seed = random.randint(1,1000000)
    if len(argv) < 2:
        print "log Not enough arguments"
        exit(0)
    directory = argv[0]
    os.chdir(directory)
    command = "./" + string.join(argv[1:], " ")
    command = command.replace("[seed]", str(seed))
    command = command.replace("[_]", " ")
    
    ctl = ComThread()
    ctl.start()
    try:
        eng = AEIAdapter(command, ctl)
        eng.main()
    finally:
        ctl.stop.set()
    sys.exit()

