import time
import sys
import threading
import numpy
import pyaudio
import fluidsynth
import midi
import wx

EVT_TICK = wx.NewId()
MAXDELTA = 5

class Player(threading.Thread):
    '''Class for playing MIDI files'''
    def __init__(self, notify_window):
        '''Setup player thread'''
        threading.Thread.__init__(self)
        self._notify_window = notify_window
        self._abort = False
        self._playing = False
        self.fs = fluidsynth.Synth()
        self.pa = pyaudio.PyAudio()
        self.strm = self.pa.open(
            format = pyaudio.paInt16,
            channels = 2, 
            rate = 44100, 
            output = True)
        self.sfid = self.fs.sfload("gm32MB.sf2")
        self.time = 0
        self.timeSig = [4, 4]
        self.metronome = 32
        self.tempo = 120.0
        self.resolution = 220
        self.qpm = 3 # quarter notes per measure (midi is 1/4 based)
        self.events = []
        self.remainingEvents = []
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
        self.events = events
        self.remainingEvents = events[:]
        self.tempo = 120.0
        self.time = 0
        self._playing = False

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
            1 + self.time / self.resolution / self.qpm,
            1 + int(self.time * self.timeSig[1] / self.resolution / 4) % self.timeSig[0],
            self.time / self.metronome,
        ]
        wx.PostEvent(self._notify_window, evt)

    def getTimeRange(self):
        return [self.events[0].tick, self.events[-1].tick]

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
        self.remainingEvents = [e for e in self.events if e.tick >= self.time]
        self.sendUpdate()
        self.softReset() # more reliably than fs.system_reset()

    def skip(self, dtime):
        self.gotoTime(self.time + dtime * self.metronome)

    def main(self):
        while True:
            if len(self.remainingEvents) > 0 and self._playing:
                event = self.remainingEvents.pop(0)
                delta = event.tick - self.time
                while delta > 0:
                    bdelta = delta
                    if delta > MAXDELTA:
                        bdelta = MAXDELTA
                    self.time += bdelta
                    n = int(44100 * bdelta / self.resolution * 60 / self.tempo)
                    s = self.fs.get_samples(n)
                    samps = fluidsynth.raw_audio_string(s)
                    self.strm.write(samps)
                    delta -= bdelta
                    self.sendUpdate()
                    if not self._playing:
                        break
                self.do_event(event)
                self.sendUpdate()
            if self._abort:
                return
    
    def pause(self):
        self._playing = not self._playing

    def close(self):
        self.fs.delete()

    def run(self):
        self.main()
        self.close()

class CueApp(wx.Frame):
    '''Main CueMIDI application class'''

    def __init__(self, *args, **kwargs):
        '''Setup app'''
        super(CueApp, self).__init__(*args, **kwargs)
        self.player = Player(self)
        self.markTime = 0
        self.InitUI()
        if len(sys.argv) > 1:
            self.player.load(sys.argv[1])

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
        self.Bind(wx.EVT_TOOL, self.GotoMark, rrTool)
        markTool = toolbar.AddLabelTool(wx.ID_ANY, '', wx.Bitmap('gfx/mark64.png'))
        self.Bind(wx.EVT_TOOL, self.Mark, markTool)
        toolbar.Realize()

        vbox.Add(curTime, 0, wx.TOP)
        vbox.Add(toolbar, 0, wx.TOP)
        self.SetSizer(vbox)
        self.SetSize((400, 400))
        self.SetTitle('CueMIDI')
        self.Show(True)

    def OnClose(self, e):
        '''Stop threads and close app'''
        if self.player:
            self.player.abort()
        self.Destroy()

    def OnOpen(self, e):
        dialog = wx.FileDialog(self, 'Choose a file to open')
        if dialog.ShowModal() == wx.ID_OK:
            filename = dialog.GetPath()
            self.player.load(filename)
            self.markTime = 0

    def OnQuit(self, e):
        '''Quit menu item action'''
        self.Close()

    def OnPause(self, e):
        self.player.pause()

    def MetaSkip(self, v):
        def f(_):
            self.player.skip(v)
        return f

    def GotoMark(self, e):
        if self.markTime is not None:
            self.player.gotoTime(self.markTime)

    def Mark(self, e):
        self.markTime = self.player.getTime()

    def Tick(self, e):
        '''Update with info from worker thread'''
        self.curTime.SetLabel('Time: {} {} {}'.format(e.data[0], e.data[1], e.data[2]))

if __name__ == '__main__':
    app = wx.App()
    CueApp(None)
    app.MainLoop()


sys.exit(0)
