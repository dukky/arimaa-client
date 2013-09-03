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

import sys, wx, math, os, socket, string
from threading import Thread, Event
from Queue import Queue, Empty

# This script is meant to be run by AEI.  The current directory should be
# the AEI directory, which we need in the path to find pyrimaa/board.py.
aei_directory = "."
sys.path.append(aei_directory)
from pyrimaa import board
from pyrimaa.board import Position, Color, Piece

from ConfigParser import SafeConfigParser, NoOptionError
import roundrobin

class ArimaaClient(wx.Frame):
    GOLD_COLOR   = wx.Color(200, 175, 100)
    SILV_COLOR   = wx.Color(125, 125, 140)
    # The same setup order is used in the official gameroom.
    SETUP_PIECES = [Piece.GELEPHANT, Piece.GCAMEL, Piece.GHORSE, Piece.GDOG, 
                    Piece.GCAT, Piece.GHORSE, Piece.GDOG, Piece.GCAT]
    RIM_SIZE     = 10
    RESOURCES    = os.path.dirname(__file__) + "/resources/"
    
    #### Initialization methods ####
    
    def __init__(self, proxiedBot=None):
        wx.Frame.__init__(self, None, -1, "Rabbits' Arimaa Client")
        icon = wx.Icon(ArimaaClient.RESOURCES + "icon.xpm", wx.BITMAP_TYPE_XPM)
        self.SetIcon(icon)
                
        wx.EVT_CLOSE(self, self.OnClose)
        self.Bind(wx.EVT_TIMER, self.OnTimer)
        
        self.controller = ComThread()
        self.controller.start()
        
        config = wx.Config()
        initialBoard  = config.Read("board", "Marble")
        initialPieces = config.Read("pieces", "Gameroom")
        self.loadBoard(initialBoard)
        self.loadPieces(initialPieces)
    
        gameMenu = wx.Menu()
        self.addMenuItem(gameMenu,"&Save\tCtrl-S","Save move list",self.OnSave)
        self.addMenuItem(gameMenu,"&Copy Position\tCtrl-C","Copy position",
        		self.OnCopyBoard)
        self.addMenuItem(gameMenu,"&Quit\tCtrl-Q","Quit",self.OnQuit)
        
        boardMenu = wx.Menu()
        for board in os.listdir(ArimaaClient.RESOURCES + "boards/"):
            boardId = self.addRadioItem(boardMenu, board, board + " board", 
                    lambda e, b=board: self.OnSelectBoard(b, e))
            if board == initialBoard:
                boardMenu.Check(boardId, True)
        
        piecesMenu = wx.Menu()
        for pieces in os.listdir(ArimaaClient.RESOURCES + "pieces/"):
            piecesId = self.addRadioItem(piecesMenu, pieces, pieces + " pieces", 
                    lambda e, p=pieces: self.OnSelectPieces(p, e))
            if pieces == initialPieces:
                piecesMenu.Check(piecesId, True)
                
        menuBar = wx.MenuBar()
        menuBar.Append(gameMenu, "&Game")
        menuBar.Append(boardMenu, "&Board")
        menuBar.Append(piecesMenu, "&Pieces")
        self.SetMenuBar(menuBar)
        self.CreateStatusBar()
        
        sizer = wx.BoxSizer(wx.HORIZONTAL)
        sizer.Add(self.controlPanel(), 0, wx.ALL, 0)
        sizer.Add(self.boardPanel(), 0, wx.ALL, 0)
        sizer.Add(self.historyPanel(), 0, wx.ALL, 0)
        self.SetSizer(sizer)
        sizer.SetSizeHints(self)
        
        self.setDefaultGuiValues()
        self.botName = proxiedBot
        self.proxiedEngine = None
        if self.botName:
            self.setupProxiedEngine()
        self.setupEngine()
        
        # On every event from this timer, we check for an AEI message.
        self.aeiTimer = wx.Timer(self)
        self.aeiTimer.Start(200)
        
        self.slideTimer = wx.Timer(self)
        self.slideDelay = 30
        self.slidePieces = True
        
        self.timeLeft = 0
        self.timeTimer = wx.CallLater(1000, self.updateTime)
        self.timeTimer.Stop()
        
    # Only called in __init__ above...
    def setDefaultGuiValues(self):
        self.sendBtn.Enable(False)
        self.undoBtn.Enable(False)
        self.selectedSquare = None
        self.glowingSquares = []
        self.stepsTaken = []
        self.pastPositions = []
        self.insetup = False
        self.stepsLeft = 0
        self.going = False
        self.turnPosition = Position(Color.GOLD, 4, board.BLANK_BOARD)
        self.position = self.turnPosition
        self.setBorderColor()
        self.gameHistory = []
        self.slidingNow = None
        self.slideCount = 0
        self.slideNextMove = True
        self.tcmove = 30
        self.greserve, self.sreserve = 0, 0
        self.isGold = "notset"
        
    def setupEngine(self):
        try:
            header = self.controller.messages.get(30)
        except Empty:
            raise Exception("Timed out waiting for AEI header")
        if header != "aei":
            raise Exception("Instead of AEI header, received (%s)" % header)
        self.controller.send("protocol-version 1")
        if self.proxiedEngine:
            self.controller.send("id name Arimaa Client as " + self.botName)
        else:
            self.controller.send("id name Arimaa Client")
        self.controller.send("id author Rabbits")
        self.controller.send("aeiok")
    
    def setupProxiedEngine(self):
        botConfig = SafeConfigParser()
        botConfig.read("roundrobin.cfg")
        globalOpts = []
        for name, value in botConfig.items("global"):
            if name.startswith("bot_"):
                globalOpts.append((name[4:], value))
        botConfigs = set(botConfig.sections())
        botConfigs.remove('global')
        bot = None
        for botSection in botConfigs:
            if self.botName.lower() == botSection.lower():
                botOpts = []
                for name, value in botConfig.items(botSection):
                    if name.startswith("bot_"):
                        botOpts.append((name[4:], value))
                bot = {'name': botSection, 'options': botOpts}
                break
        if bot:
            self.proxiedEngine = roundrobin.run_bot(bot, botConfig, globalOpts)
            self.SetStatusText("Proxying bot: " + self.botName)
        else:
            self.SetStatusText("Proxy error: " + self.botName + \
                    " not found in roundrobin.cfg")
    
    def loadBoard(self, board):
        directory = ArimaaClient.RESOURCES + "boards/" + board + "/"
        self.buffImage = wx.Bitmap(directory + "board.png")
        self.gameBoard = wx.Bitmap(directory + "board.png")
        self.selectGlo = wx.Bitmap(directory + "selection-glow.png")
        self.moveToGlo = wx.Bitmap(directory + "move-glow.png")
        
        ArimaaClient.newGameSound   = wx.Sound(directory + "new-game.wav")
        ArimaaClient.stepSound      = wx.Sound(directory + "step.wav")
        ArimaaClient.placementSound = wx.Sound(directory + "placement.wav")
        ArimaaClient.captureSound   = wx.Sound(directory + "capture.wav")
        ArimaaClient.victorySound   = wx.Sound(directory + "victory.wav")
        ArimaaClient.defeatSound    = wx.Sound(directory + "defeat.wav")
        ArimaaClient.lowTimeSound   = wx.Sound(directory + "low-time.wav")
    
    def loadPieces(self, pieces):
        directory = ArimaaClient.RESOURCES + "pieces/" + pieces + "/"
        ArimaaClient.images = (None, \
                wx.Bitmap(directory + "gold-rabbit.png"), \
                wx.Bitmap(directory + "gold-cat.png"), \
                wx.Bitmap(directory + "gold-dog.png"), \
                wx.Bitmap(directory + "gold-horse.png"), \
                wx.Bitmap(directory + "gold-camel.png"), \
                wx.Bitmap(directory + "gold-elephant.png"), \
                None, None, \
                wx.Bitmap(directory + "silver-rabbit.png"), \
                wx.Bitmap(directory + "silver-cat.png"), \
                wx.Bitmap(directory + "silver-dog.png"), \
                wx.Bitmap(directory + "silver-horse.png"), \
                wx.Bitmap(directory + "silver-camel.png"), \
                wx.Bitmap(directory + "silver-elephant.png"))
    
    def addMenuItem(self, menu, text, description, handler):
        menuId = wx.NewId()
        menu.Append(menuId, text, description)
        self.Bind(wx.EVT_MENU, handler, id=menuId)
        return menuId
    
    def addRadioItem(self, menu, text, description, handler):
        menuId = wx.NewId()
        menu.AppendRadioItem(menuId, text, description)
        self.Bind(wx.EVT_MENU, handler, id=menuId)
        return menuId
        
    def boardPanel(self):
        self.boardRim = wx.Panel(self)
        self.boardRim.SetBackgroundColour(ArimaaClient.GOLD_COLOR)
        self.boardP = wx.Panel(self.boardRim, size=(402, 402))
        self.boardP.Bind(wx.EVT_PAINT, self.OnPaint)
        self.boardP.Bind(wx.EVT_LEFT_DOWN, self.OnClick)
        self.boardP.Bind(wx.EVT_MOTION, self.OnMotion)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.boardP, 0, wx.ALL, ArimaaClient.RIM_SIZE)
        self.boardRim.SetSizer(sizer)
        sizer.SetSizeHints(self.boardRim)
        return self.boardRim
        
    def controlPanel(self):
        cp = wx.Panel(self)
        self.sendBtn = wx.Button(cp, -1, "Send")
        self.undoBtn = wx.Button(cp, -1, "Undo")
        self.sendBtn.SetFont(wx.Font(-1, -1, wx.NORMAL, wx.NORMAL))
        self.undoBtn.SetFont(wx.Font(-1, -1, wx.NORMAL, wx.NORMAL))
        self.stepsTxt  = wx.StaticText(cp, -1, "8")
        self.timeTxt   = wx.StaticText(cp, -1, "--")
        cp.Bind(wx.EVT_BUTTON, self.OnSendMove, self.sendBtn)
        cp.Bind(wx.EVT_BUTTON, self.OnUndoStep, self.undoBtn)
        sizer  = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.sendBtn,  0, wx.ALL, 4)
        sizer.Add(self.undoBtn,  0, wx.ALL, 4)
        sizer.Add(wx.StaticText(cp, -1, "Steps left:"), 0, wx.ALL, 4)
        sizer.Add(self.stepsTxt, 0, wx.ALL, 4)
        sizer.Add(wx.StaticText(cp, -1, "Time left:"), 0, wx.ALL, 4)
        sizer.Add(self.timeTxt, 0, wx.ALL, 4)
        cp.SetSizer(sizer)
        sizer.SetSizeHints(cp)
        return cp
        
    def historyPanel(self):
        self.historyP = wx.ListBox(self, size=(200,240))
        return self.historyP
    
    #### Event handlers ####
    
    def OnQuit(self, event):
        self.OnClose(None)
        
    def OnClose(self, event):
        self.aeiTimer.Stop()
        self.slideTimer.Stop()
        self.controller.stop.set()
        self.Destroy()
    
    def OnSave(self, event):
        self.SetStatusText("Saving is not implemented.") # Bocsi
        
    def OnCopyBoard(self, event):
        if not wx.TheClipboard.IsOpened():
            clipdata = wx.TextDataObject()
            if self.position.color == Color.GOLD: pos_str = "g"
            else:                                 pos_str = "s"
            if self.position.stepsLeft != 4:
                pos_str += " (" + str(4 - self.position.stepsLeft) + ")"
            pos_str += "\n" + self.position.board_to_str()
            clipdata.SetText(pos_str)
            wx.TheClipboard.Open()
            wx.TheClipboard.SetData(clipdata)
            wx.TheClipboard.Close()
            self.SetStatusText("The position is now in your clipboard.")
        else:
            self.SetStatusText("Failed to copy to your clipboard.")
    
    def OnTimer(self, event):
        if event.GetEventObject() == self.aeiTimer:     self.checkAEI()
        elif event.GetEventObject() == self.slideTimer: self.slideEvent()
    
    def OnSelectBoard(self, boardName, event):
        self.SetStatusText("Set board to " + boardName)
        self.loadBoard(boardName)
        config = wx.Config()
        config.Write("board", boardName)
        self.DoDrawing()
        
    def OnSelectPieces(self, piecesName, event):
        self.SetStatusText("Set pieces to " + piecesName)
        self.loadPieces(piecesName)
        config = wx.Config()
        config.Write("pieces", piecesName)
        self.DoDrawing()
        
    def OnSendMove(self, event):
        self.sendBtn.Enable(False)
        self.undoBtn.Enable(False)
        if self.insetup:
            setup_moves = self.position.to_placing_move()
            self.bestmove(setup_moves[self.position.color][2:])
        else:
            self.bestmove(self.turnPosition.steps_to_str(self.stepsTaken))
        
    def OnUndoStep(self, event):
        self.position = self.pastPositions.pop()
        self.stepsTaken.pop()
        self.selectedSquare = None
        self.glowingSquares = []
        self.stepsLeft += 1
        self.stepsTxt.SetLabel(str(self.stepsLeft))
        if self.insetup or self.position.inpush or len(self.pastPositions) == 0:
            self.sendBtn.Enable(False)
        else:
            self.sendBtn.Enable(True)
        if len(self.pastPositions) == 0:
            self.undoBtn.Enable(False)
        self.DoDrawing()
        
    def OnClick(self, event):
        row, col = self.coordinatesToIndices(event.GetX(), event.GetY())
        # Currently, clicks are ignored while pieces are sliding...
        if row < 0 or row > 7 or col < 0 or col > 7 \
                or not self.going or self.slidingNow:
            return
        if self.insetup:
            self.placeNextSetupPiece(row, col)
        elif self.pieceAt(row,col) != Piece.EMPTY:
            self.selectedSquare = (row, col)
        elif (row, col) in self.glowingSquares:
            self.applyStep(row, col)
        else:
            self.selectedSquare = None
        self.refreshGlowingSquares()
        self.DoDrawing()
    
    def OnMotion(self, event):
        if self.insetup or not self.going or self.slidingNow:
            return
        row, col = self.coordinatesToIndices(event.GetX(), event.GetY())
        if row < 0 or row > 7 or col < 0 or col > 7:
            return
        if (row, col) == self.selectedSquare or \
                (row, col) in self.glowingSquares:
            return
        elif self.pieceAt(row,col) != Piece.EMPTY:
            self.selectedSquare = (row, col)
            self.refreshGlowingSquares()
            self.DoDrawing()
    
    def OnPaint(self, event):
        dc = wx.BufferedPaintDC(event.GetEventObject(), self.buffImage)
        self.DoDrawing(dc)
    
    #### Various helper methods ####
    
    def DoDrawing(self, dc=None):
        if dc is None:
            dc = wx.BufferedDC(wx.ClientDC(self.boardP), self.buffImage)
        dc.DrawBitmap(self.gameBoard, 0, 0, False)
        if self.selectedSquare != None:
            x, y = self.indicesToCoordinates(self.selectedSquare)
            dc.DrawBitmap(self.selectGlo, x, y, True)
            for square in self.glowingSquares:
                x, y = self.indicesToCoordinates(square)
                dc.DrawBitmap(self.moveToGlo, x, y, True)
        elif self.insetup and self.going and not self.slidingNow:
            self.drawNextSetupPiece(dc)
        for row in xrange(8):
            for col in xrange(8):
                piece = self.pieceAt(row, col)
                if (not self.slidingNow or (row,col) != self.slidingNow.at()) \
                        and piece != Piece.EMPTY:
                    x, y = self.indicesToCoordinates((row, col))
                    dc.DrawBitmap(ArimaaClient.images[piece], x, y, True)
        if self.slidingNow:
            self.slidingNow.Draw(dc, self.slideCount, self)
    
    def drawNextSetupPiece(self, dc):
        if self.stepsLeft != 0:
            image = ArimaaClient.images[self.nextSetupPiece()]
            dc.DrawBitmap(image, 178, 178, True)
    
    def placeNextSetupPiece(self, row, col):
        if self.stepsLeft == 0:
            self.SetStatusText("Click the Send button to submit your move.")
            return
        elif (self.position.color == Color.GOLD and row > 1) or \
                (self.position.color == Color.SILVER and row < 6):
            self.SetStatusText("Setup in the bottom two rows.")
            return
        elif self.pieceAt(row, col) != Piece.EMPTY:
            self.SetStatusText("That spot is already taken.")
            return
        piece = self.nextSetupPiece()
        self.stepsLeft -= 1
        self.stepsTaken.append((8*row+col, None))
        self.pastPositions.append(self.position)
        self.position = self.position.place_piece(piece, 8*row+col)
        ArimaaClient.placementSound.Play()
        if self.stepsLeft == 0:
            if self.position.color == Color.GOLD:
                rabbit, rows = Piece.GRABBIT, (0,1)
            else:
                rabbit, rows = Piece.SRABBIT, (6,7)
            for r in rows:
                for c in xrange(8):
                    if self.pieceAt(r, c) == Piece.EMPTY:
                        self.position = self.position.place_piece(rabbit, 8*r+c)
            self.sendBtn.Enable(True)
        self.SetStatusText("")
        self.undoBtn.Enable(True)
        self.stepsTxt.SetLabel(str(self.stepsLeft))
    
    def nextSetupPiece(self):
        piece = ArimaaClient.SETUP_PIECES[8-self.stepsLeft]
        if self.position.color != Color.GOLD:
            piece = piece + Piece.COLOR
        return piece
    
    # This is for steps made by clicking on a glowing square.
    def applyStep(self, row, col):
        fromRow, fromCol = self.selectedSquare
        step = ((fromRow*8+fromCol), (row*8+col))
        # Remember these pieces for sliding.
        movedPiece = self.pieceAt(fromRow, fromCol)
        c3Trap, f3Trap = self.pieceAt(2, 2), self.pieceAt(2, 5)
        f6Trap, c6Trap = self.pieceAt(5, 5), self.pieceAt(5, 2)
        self.stepsTaken.append(step)
        self.pastPositions.append(self.position)
        self.position = self.position.do_step(step)
        self.undoBtn.Enable(True)
        if self.position.inpush: self.sendBtn.Enable(False)
        else:                    self.sendBtn.Enable(True)
        self.stepsLeft -= 1
        self.stepsTxt.SetLabel(str(self.stepsLeft))
        if self.pieceAt(row, col) != Piece.EMPTY:
            self.selectedSquare = (row, col)
            if not ((fromRow == 2 or fromRow == 5) and 
                    (fromCol == 2 or fromCol == 5)):
                if c3Trap != Piece.EMPTY and self.pieceAt(2, 2) == Piece.EMPTY:
                    self.startCaptureSlide(2, 2, c3Trap)
                if f3Trap != Piece.EMPTY and self.pieceAt(2, 5) == Piece.EMPTY:
                    self.startCaptureSlide(2, 5, f3Trap)
                if f6Trap != Piece.EMPTY and self.pieceAt(5, 5) == Piece.EMPTY:
                    self.startCaptureSlide(5, 5, f6Trap)
                if c6Trap != Piece.EMPTY and self.pieceAt(5, 2) == Piece.EMPTY:
                    self.startCaptureSlide(5, 2, c6Trap)
        else:
            self.startCaptureSlide(row, col, movedPiece)
        if not self.slidingNow: ArimaaClient.stepSound.Play()
    
    # This method shows the move described in a move string from AEI.
    def applyAEISlidingMove(self, move_str):
        pos = self.position
        self.pendingSlides = []     
        for step in move_str.split():
            piece = Piece.PCHARS.find(step[0])
            row, col = int(step[2])-1, ord(step[1])-97
            if len(step) < 4: direction = 'p' # 'p' for placement
            else:             direction = step[3]
            # A hack to prevent a visual glitch:
            if direction == 'x':
                self.pendingSlides[-1].position = \
                    self.pendingSlides[-1].position.place_piece(piece,8*row+col)
            slide = Slide(piece, row, col, direction, pos)
            pos = slide.position
            self.pendingSlides.append(slide)
        self.pendingSlides[-1].position = self.turnPosition
        self.slideNextStep()
        self.selectedSquare = None
    
    def slideNextStep(self):
        if len(self.pendingSlides) == 0:
            self.slideTimer.Stop()
            self.slidingNow = None
        else:
            self.slidingNow = self.pendingSlides.pop(0) # Pop from the front.
            self.slidingNow.playSound()
            self.position = self.slidingNow.position
            self.slideCount = self.slidingNow.startCount
            self.slideTimer.Start(self.slideDelay)
    
    def startCaptureSlide(self, row, col, piece):
        self.slidingNow = Slide(piece, row, col, 'x', None)
        self.slideCount = self.slidingNow.startCount
        self.slideTimer.Start(self.slideDelay)
        self.selectedSquare = None
        ArimaaClient.captureSound.Play()
    
    def pieceAt(self, row, col):
        return self.position.piece_at(1 << (8*row+col))
    
    def coordinatesToIndices(self, x, y):
        row, col = (y - 5) / 49, (x - 5) / 49
        if self.isGold: row = 7 - row
        else:           col = 7 - col
        return (row, col)
    
    def indicesToCoordinates(self, (row, col)):
        if self.isGold: return 5+49*col, 5+49*(7-row)
        else:           return 5+49*(7-col), 5+49*row
    
    def refreshGlowingSquares(self):
        self.glowingSquares = []
        if self.selectedSquare == None or self.stepsLeft == 0:
            return
        else:
            row, col = self.selectedSquare
            ix = (row*8+col)
            if col < 7 and self.position.check_step((ix, ix+1)):
                self.glowingSquares.append((row,col+1))
            if col > 0 and self.position.check_step((ix, ix-1)):
                self.glowingSquares.append((row,col-1))
            if row < 7 and self.position.check_step((ix, ix+8)):
                self.glowingSquares.append((row+1,col))
            if row > 0 and self.position.check_step((ix, ix-8)):
                self.glowingSquares.append((row-1,col))
    
    def setBorderColor(self):
        if self.turnPosition.color == Color.GOLD:
            self.boardRim.SetBackgroundColour(ArimaaClient.GOLD_COLOR)
        else:
            self.boardRim.SetBackgroundColour(ArimaaClient.SILV_COLOR)
    
    def updateTime(self):
        self.timeLeft -= 1
        self.timeTxt.SetLabel(str(self.timeLeft))
        self.timeTimer.Restart(1000)
        if self.timeLeft == 10:
            self.timeTxt.SetForegroundColour('red')
            ArimaaClient.lowTimeSound.Play()
    
    def resetTime(self):
        if self.turnPosition.color == Color.GOLD:
            self.timeLeft = self.tcmove + self.greserve
        else:
            self.timeLeft = self.tcmove + self.sreserve
        self.timeTxt.SetLabel(str(self.timeLeft))
        self.timeTimer.Restart(1000)
        self.timeTxt.SetForegroundColour('black')
    
    def slideEvent(self):
        self.slideCount -= 1
        if self.slideCount == 0:
            self.slideNextStep()
        self.DoDrawing()
       
    #### AEI engine methods ####

    def newgame(self):
        if self.proxiedEngine: self.proxiedEngine.newgame()
        self.turnPosition = Position(Color.GOLD, 4, board.BLANK_BOARD)
        self.position = self.turnPosition
        self.insetup = True
        self.isGold = "notset"
        self.stepsLeft = 8
        self.stepsTxt.SetLabel(str(self.stepsLeft))
        self.setBorderColor()
        ArimaaClient.newGameSound.Play()
        self.DoDrawing()

    def setposition(self, side_str, pos_str):
        side = "gswb".find(side_str) % 2
        self.position = board.parse_short_pos(side, 4, pos_str)
        if self.proxiedEngine: self.proxiedEngine.setposition(self.position)
        self.insetup = False
        self.setBoarderColor()
        self.DoDrawing()

    def setoption(self, name, value):
        if self.proxiedEngine: self.proxiedEngine.setoption(name, value)
        if   name == "tcmove":   self.tcmove   = int(value)
        elif name == "greserve": self.greserve = int(value)
        elif name == "sreserve": self.sreserve = int(value)
        elif name == "rated":    self.OnClose(None)
        std_opts = set(["tcmove", "tcreserve", "tcpercent", "tcmax", "tctotal",
                "tcturns", "tcturntime", "greserve", "sreserve", "gused",
                "sused", "lastmoveused", "moveused", "opponent",
                "opponent_rating"])
        if name not in std_opts:
            self.log("Warning: Received unrecognized option %s" % (name))
    
    def makemove(self, move_str):
        move_number = str(1 + (len(self.gameHistory) / 2))
        if self.turnPosition.color == Color.GOLD: move_number += "g "
        else:                                     move_number += "s "
        self.gameHistory.append(move_number + move_str)
        self.historyP.Set(self.gameHistory)
        self.historyP.Select(len(self.gameHistory)-1)
        self.turnPosition = self.turnPosition.do_move_str(move_str)
        self.selectedSquare = None
        self.glowingSquares = []
        self.stepsTaken = []
        self.pastPositions = []
        if self.insetup and self.turnPosition.color == Color.GOLD:
            self.insetup = False
        if self.insetup: self.stepsLeft = 8
        else:            self.stepsLeft = 4
        if self.isGold == "notset":
            self.isGold = False
        self.stepsTxt.SetLabel(str(self.stepsLeft))
        self.setBorderColor()
        if self.slideNextMove:
            self.applyAEISlidingMove(move_str)
        else:
            self.slideNextMove = self.slidePieces
            self.position = self.turnPosition
        self.DoDrawing()
        if self.proxiedEngine: self.proxiedEngine.makemove(move_str)

    def go(self):
        if self.isGold == "notset": # Uhg.
            self.isGold = True
        if self.proxiedEngine:
            self.proxiedEngine.go()
        else:
            self.going = True
            if self.insetup: self.SetStatusText("Click to place your pieces.")
            else:            self.SetStatusText("Your turn.")
        self.resetTime()
        self.DoDrawing()

    def log(self, msg):
        self.controller.send("log " + msg)

    def bestmove(self, move_str):
        self.going = False
        self.controller.send("bestmove " + move_str)
        if not self.proxiedEngine:
            self.slideNextMove = False
        self.SetStatusText("Move sent.")
        self.timeTimer.Stop()

    def checkAEI(self):
        # Don't communicate while sliding.
        if self.slidingNow:
            return
        ctl = self.controller
        if self.proxiedEngine:
            try:
                resp = self.proxiedEngine.engine.readline(timeout=0.1)
                resp = string.strip(resp, "\n")
                if resp == "":
                    raise socket.timeout()
                if resp.startswith("bestmove"):
                    self.timeTimer.Stop()
                self.SetStatusText(self.botName + ": " + resp)
                ctl.send(resp)
            except socket.timeout:
                pass
                
        if not ctl.stop.isSet() and not ctl.messages.empty():
            msg = ctl.messages.get()
            if msg == "isready":
                if self.proxiedEngine: self.proxiedEngine.isready()
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
                # Right now, proxied bots don't get to ponder
                if len(msg.split()) == 1:
                    self.go()
            elif msg == "stop":
                if self.proxiedEngine: self.proxiedEngine.stop()
                pass
            elif msg == "quit":
                if self.proxiedEngine: self.proxiedEngine.quit()
                self.SetStatusText("Game over.")
                endState = self.turnPosition.is_end_state()
                if (not self.isGold and endState == -1) or \
                        (self.isGold and endState == 1):
                    self.victorySound.Play()
                else:
                    self.defeatSound.Play()
                # AEI kills the program after a few seconds.

# I thought that factoring slides into a class would clean up the code,
# but it didn't really.
class Slide():
    def __init__(self, piece, row, col, direction, pos):
        self.image = ArimaaClient.images[piece]
        if direction == 'p': # 'p' for placement
            self.startCount = 5
            if pos.color == Color.GOLD:
                self.up, self.right, self.angle = -1, 0, 0
            else:
                self.up, self.right, self.angle =  1, 0, 0
            self.torow, self.tocol = row, col
            self.position = pos.place_piece(piece, 8*row+col)
            self.sound = None
        elif direction == 'x':
            self.startCount = 15
            self.up, self.right, self.angle = 0,  0, math.pi/2/self.startCount
            self.torow, self.tocol = row, col
            self.position = pos
            self.sound = ArimaaClient.captureSound
        else:
            self.startCount = 15
            if   direction == 'n':
                self.up, self.right, self.angle =  1,  0, 0
            elif direction == 's':
                self.up, self.right, self.angle = -1,  0, 0
            elif direction == 'e':
                self.up, self.right, self.angle =  0,  1, 0
            elif direction == 'w':
                self.up, self.right, self.angle =  0, -1, 0
            self.torow, self.tocol = row + self.up, col + self.right
            fromIndex = 8*row + col
            toIndex   = 8*(self.torow) + self.tocol
            self.position = pos.do_step((fromIndex, toIndex))
            self.sound = ArimaaClient.stepSound
        self.up *= 49.0 / self.startCount
        self.right *= 49.0 / self.startCount
    
    def at(self):
        return (self.torow, self.tocol)
        
    def playSound(self):
        if self.sound: self.sound.Play()
    
    def Draw(self, dc, count, frame):
        if self.angle == 0:
            pieceImage = self.image
        else:
            img = self.image.ConvertToImage()
            img = img.Rotate(self.angle*(self.startCount-count), (24, 24))
            pieceImage = img.ConvertToBitmap()
        x, y = frame.indicesToCoordinates((self.torow, self.tocol))
        if frame.isGold:
            y += count*self.up
            x -= count*self.right
        else:
            y -= count*self.up
            x += count*self.right
        dc.DrawBitmap(pieceImage, x, y, True)

class ComThread(Thread):
    def __init__(self):
        Thread.__init__(self)
        self.stop = Event()
        # Setting a max queue size seems to prevent this thread from eating the 
        # CPU when things go badly.
        self.messages = Queue(1000)
        self.setDaemon(True)

    def send(self, msg):
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()

    def run(self):
        while not self.stop.isSet():
            msg = sys.stdin.readline()
            self.messages.put(msg.strip())

class ArimaaApp(wx.App):
    def OnInit(self):
        argv = sys.argv[1:]
        proxyBotName = None
        if len(argv) > 0 and (argv[0] == "-p" or argv[0] == "--proxy"):
            proxyBotName = argv[1]
        self.SetAppName("rabbits-arimaa-client")
        self.SetVendorName("rabbits")
        self.frame = ArimaaClient(proxyBotName)
        self.frame.Show(True)
        self.SetTopWindow(self.frame)
        return True

if __name__ == "__main__":
    app = ArimaaApp(redirect=False)
    app.MainLoop()

