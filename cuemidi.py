import time
import sys
import threading
import argparse
import numpy
import pyaudio
import fluidsynth
import midi
import wx

EVT_TICK = wx.NewId()
MAXDELTA = 5
GRACE = 100
TRASH_DELTA = 1000
FREQ = 44100

class Player(threading.Thread):
    '''Class for playing MIDI files'''
    def __init__(self, notify_window):
        '''Setup player thread'''
        threading.Thread.__init__(self)
        self._notify_window = notify_window
        self._abort = False
        self._playing = False
        self.fs = fluidsynth.Synth(samplerate=FREQ)
        self.pa = pyaudio.PyAudio()
        self.strm = self.pa.open(
            format = pyaudio.paInt16,
            channels = 2, 
            rate = FREQ,
            output = True)
        self.sfid = self.fs.sfload("gm32MB.sf2")
        self.time = 0
        self.timeSig = [4, 4]
        self.metronome = 32
        self.tempo = 120.0
        self.resolution = 220
        self.qpm = 3 # quarter notes per measure (midi is 1/4 based)
        self.events = []
        self.eventnum = 0
        self.start()

    def abort(self):
        self._abort = True

    def load(self, filename):
        '''Load MIDI file'''
        pattern = midi.read_midifile(filename)        
        self.resolution = pattern.resolution
        pattern.make_ticks_abs()
        events = []
        for track in pattern:
            for event in track:
                events.append(event)
        events.sort()
        cues = []
        for e in events:
            if type(e) == midi.events.TextMetaEvent:
                if e.text.startswith('cue'):
                    cues.append(e.tick)
        self.cues = cues
        self.events = events
        self.eventnum = 0
        self.tempo = 120.0
        self.time = 0
        self._playing = False
        self.softReset()
        while self.eventnum < len(self.events) and self.events[self.eventnum].tick < 2:
                event = self.events[self.eventnum]
                self.eventnum += 1
                self.do_event(event)

    def do_event(self, evt):
        if type(evt) == midi.events.TimeSignatureEvent:
            # MIDI stores log_2 of denom
            self.timeSig = [evt.data[0], 2 ** evt.data[1]]
            # Metronome is just for displaying subbeats
            self.metronome = evt.data[2]
        if type(evt) == midi.events.NoteOnEvent:
            self.fs.noteon(evt.channel, evt.data[0], evt.data[1])
        if type(evt) == midi.events.NoteOffEvent:
            self.fs.noteoff(evt.channel, evt.data[0])
        if type(evt) == midi.events.ProgramChangeEvent:
            self.fs.program_select(evt.channel, self.sfid, 0, evt.data[0])
        if type(evt) == midi.events.ControlChangeEvent:
            self.fs.cc(evt.channel, evt.data[0], evt.data[1])
        if type(evt) == midi.events.SetTempoEvent:
            self.tempo = evt.get_bpm()

    def sendUpdate(self):
        evt = wx.PyEvent()
        evt.SetEventType(EVT_TICK)
        evt.data = [
            self.time,
            1 + self.time / self.resolution / self.qpm,
            1 + int(self.time * self.timeSig[1] / self.resolution / 4) % self.timeSig[0],
            self.time / self.metronome,
        ]
        wx.PostEvent(self._notify_window, evt)

    def getTimeRange(self):
        if len(self.events) > 0:
            return [self.events[0].tick, self.events[-1].tick]
        else:
            return [0, 1]

    def getTime(self):
        return self.time

    def softReset(self):
        for chan in range(16):
            for note in range(128):
                self.fs.noteoff(chan, note)

    def gotoTime(self, time):
        self.time = time
        if self.time < 0:
            self.time = 0
        # Pick out events to do
        # Always include start events (setup)
        self.eventnum = 0
        while self.events[self.eventnum].tick < self.time:
            self.eventnum += 1
        self.sendUpdate()
        self.softReset() # more reliably than fs.system_reset()

    def skip(self, dtime):
        self.gotoTime(self.time + dtime * self.metronome)

    def main(self):
        while True:
            if self.eventnum < len(self.events) and self._playing:
                event = self.events[self.eventnum]
                self.eventnum += 1
                delta = event.tick - self.time
                while delta > 0:
                    bdelta = delta
                    if delta > MAXDELTA:
                        bdelta = MAXDELTA
                    self.time += bdelta
                    n = int(FREQ * bdelta / self.resolution * 60 / self.tempo)
                    s = self.fs.get_samples(n)
                    samps = fluidsynth.raw_audio_string(s)
                    self.strm.write(samps)
                    delta -= bdelta
                    self.sendUpdate()
                    if not self._playing:
                        break
                self.do_event(event)
                self.sendUpdate()
            else:
                time.sleep(0.01)
            if self._abort:
                return
    
    def pause(self):
        self._playing = not self._playing

    def close(self):
        self.fs.delete()

    def run(self):
        self.main()
        self.close()

class Cues(wx.Panel):
    def __init__(self, *args, **kwargs):
        super(Cues, self).__init__(*args, **kwargs)
        self.Bind(wx.EVT_PAINT, self.OnPaint)
        self.Show(True)
        self.cues = []

    def SetCues(self, cues):
        self.cues = cues
        self.Refresh()
        self.Update()

    def OnPaint(self, e):
        dc = wx.PaintDC(self)
        dc.SetPen(wx.Pen('WHITE'))
        dc.DrawRectangle(0, 0, 390, 50)
        dc.SetPen(wx.Pen('RED'))
        for cue in self.cues:
            x = cue * 390 / 1000
            dc.DrawRectangle(x, 0, 2, 50)

class CueApp(wx.Frame):
    '''Main CueMIDI application class'''

    def __init__(self, *args, **kwargs):
        '''Setup app'''
        super(CueApp, self).__init__(*args, **kwargs)
        parser = argparse.ArgumentParser(description='Play MIDI file with cues.')
        parser.add_argument('filename', nargs='?')
        parser.add_argument('--samplerate', default=44100, type=int,
            help='samplerate to use (44100, 22050)')
        cmdargs = parser.parse_args()
        global FREQ
        FREQ = cmdargs.samplerate
        self.player = Player(self)
        self.markTimes = [0]
        self.InitUI()
        if cmdargs.filename:
            self.Open(cmdargs.filename)

    def InitUI(self):
        '''Setup UI for application'''
        menubar = wx.MenuBar()

        fileMenu = wx.Menu()
        openItem = fileMenu.Append(wx.ID_OPEN, '&Open')
        self.Bind(wx.EVT_MENU, self.OnOpen, openItem)
        #~ fileMenu.Append(wx.ID_ANY, '&Set SoundFont')
        quitItem = fileMenu.Append(wx.ID_EXIT, '&Quit', 'Quit application')

        menubar.Append(fileMenu, '&File')
        self.SetMenuBar(menubar)

        self.Bind(wx.EVT_MENU, self.OnQuit, quitItem)
        self.Bind(wx.EVT_CLOSE, self.OnClose)
        self.Connect(-1, -1, EVT_TICK, self.Tick)

        vbox = wx.BoxSizer(wx.VERTICAL)

        curTime = wx.StaticText(self)
        curTime.SetLabel("")
        self.curTime = curTime

        toolbar = wx.ToolBar(self)
        pauseTool = toolbar.AddLabelTool(wx.ID_ANY, '', wx.Bitmap('gfx/pause64.png'))
        self.Bind(wx.EVT_TOOL, self.OnPause, pauseTool)
        rrTool = toolbar.AddLabelTool(wx.ID_ANY, '', wx.Bitmap('gfx/rr64.png'))
        self.Bind(wx.EVT_TOOL, self.MetaSkip(-1), rrTool)
        ffTool = toolbar.AddLabelTool(wx.ID_ANY, '', wx.Bitmap('gfx/ff64.png'))
        self.Bind(wx.EVT_TOOL, self.MetaSkip(1), ffTool)
        markTool = toolbar.AddLabelTool(wx.ID_ANY, '', wx.Bitmap('gfx/mark64.png'))
        self.Bind(wx.EVT_TOOL, self.Mark, markTool)
        trashTool = toolbar.AddLabelTool(wx.ID_ANY, '', wx.Bitmap('gfx/trash64.png'))
        self.Bind(wx.EVT_TOOL, self.Trash, trashTool)
        toolbar.Realize()

        slider = wx.Slider(self, value=0, minValue=0, maxValue=1000, size=(390, 50))
        self.slider = slider
        self.Bind(wx.EVT_SCROLL_CHANGED, self.Slider, slider)

        canvas = Cues(self, size=(390, 50))
        self.canvas = canvas
        self.SetCues()

        vbox.Add(toolbar, 0, wx.TOP)
        vbox.Add(slider, 0, wx.TOP)
        vbox.Add(curTime, 0, wx.TOP)
        vbox.Add(canvas, 0, wx.TOP)
        self.SetSizer(vbox)
        self.SetSize((400, 400))
        self.SetTitle('CueMIDI')
        self.Show(True)

    def OnClose(self, e):
        '''Stop threads and close app'''
        if self.player:
            self.player.abort()
        self.Destroy()

    def Open(self, filename):
        self.player.load(filename)
        if len(self.player.cues) > 0:
            self.markTimes = list(set(self.player.cues))
            self.markTimes.sort()
        else:
            self.markTimes = [0]
        self.SetCues()

    def OnOpen(self, e):
        dialog = wx.FileDialog(self, 'Choose a file to open')
        if dialog.ShowModal() == wx.ID_OK:
            filename = dialog.GetPath()
            self.Open(filename)

    def OnQuit(self, e):
        '''Quit menu item action'''
        self.Close()

    def OnPause(self, e):
        self.player.pause()

    def Trash(self, e):
        if len(self.markTimes) == 0:
            return
        t = self.player.getTime()
        def close(x):
            return abs(x - t)
        closest = sorted(self.markTimes, key=close)[0]
        if abs(closest - t) < TRASH_DELTA:
            self.markTimes.remove(closest)
        self.SetCues()

    def MetaSkip(self, v):
        def f(_):
            t = self.player.getTime()
            m = 0
            if self.player._playing and v < 0:
                t -= GRACE
            while m < len(self.markTimes) and self.markTimes[m] < t:
                m += 1
            if v == -1:
                self.player.gotoTime(self.markTimes[m - 1])
            if v == 1:
                if m < len(self.markTimes) and t == self.markTimes[m]:
                    m += 1
                if m >= len(self.markTimes):
                    m = 0
                self.player.gotoTime(self.markTimes[m])
        return f

    def SetCues(self):
        r = self.player.getTimeRange()
        self.canvas.SetCues([t * 1000 / r[1] for t in self.markTimes])
    
    def GotoMark(self, e):
        if self.markTime is not None:
            self.player.gotoTime(self.markTime)

    def Mark(self, e):
        self.markTimes.append(self.player.getTime())
        # remove dups
        self.markTimes = list(set(self.markTimes))
        self.markTimes.sort()
        self.SetCues()

    def Tick(self, e):
        '''Update with info from worker thread'''
        r = self.player.getTimeRange()
        self.curTime.SetLabel('Time: {} {} {}'.format(e.data[1], e.data[2], e.data[3]))
        v = int(1000 * e.data[0] / r[1])
        self.slider.SetValue(v)

    def Slider(self, e):
        r = self.player.getTimeRange()
        o = e.GetEventObject()
        v = int(o.GetValue() / 1000.0 * r[1])
        self.player.gotoTime(v)

if __name__ == '__main__':
    app = wx.App()
    CueApp(None)
    app.MainLoop()


sys.exit(0)
