import time
import numpy
import pyaudio
import fluidsynth
import midi
import sys
import threading

import wx

EVT_TICK = wx.NewId()

class ResultEvent(wx.PyEvent):
    def __init__(self, data):
        wx.PyEvent.__init__(self)
        self.SetEventType(EVT_TICK)
        self.data = data

class Worker(threading.Thread):
    '''Worker thread class'''
    def __init__(self, notify_window):
        '''Create worker thread'''
        threading.Thread.__init__(self)
        self._notify_window = notify_window
        self._abort = 0
        self.i = 0
        self.start()

    def run(self):
        '''Run worker thread'''
        while True:
            time.sleep(1)
            self.i += 1
            if self._abort:
                break
            else:
                evt = wx.PyEvent()
                evt.SetEventType(EVT_TICK)
                evt.data = self.i
                wx.PostEvent(self._notify_window, evt)

    def abort(self):
        self._abort = True

class CueApp(wx.Frame):
    '''Main CueMIDI application class'''

    def __init__(self, *args, **kwargs):
        '''Setup app'''
        super(CueApp, self).__init__(*args, **kwargs)
        self.worker = Worker(self)
        self.InitUI()

    def InitUI(self):
        '''Setup UI for application'''
        menubar = wx.MenuBar()

        fileMenu = wx.Menu()
        fileMenu.Append(wx.ID_OPEN, '&Open')
        #~ fileMenu.Append(wx.ID_ANY, '&Set SoundFont')
        quitItem = fileMenu.Append(wx.ID_EXIT, '&Quit', 'Quit application')

        menubar.Append(fileMenu, '&File')
        self.SetMenuBar(menubar)

        self.Bind(wx.EVT_MENU, self.OnQuit, quitItem)
        self.Bind(wx.EVT_CLOSE, self.OnClose)
        self.Connect(-1, -1, EVT_TICK, self.Tick)

        vbox = wx.BoxSizer(wx.VERTICAL)

        curTime = wx.StaticText(self)
        curTime.SetLabel("Hello")
        self.curTime = curTime

        toolbar = wx.ToolBar(self)
        toolbar.AddLabelTool(wx.ID_ANY, '', wx.Bitmap('gfx/play64.png'))
        toolbar.AddLabelTool(wx.ID_ANY, '', wx.Bitmap('gfx/pause64.png'))
        toolbar.AddLabelTool(wx.ID_ANY, '', wx.Bitmap('gfx/rr64.png'))
        toolbar.AddLabelTool(wx.ID_ANY, '', wx.Bitmap('gfx/ff64.png'))
        toolbar.Realize()

        vbox.Add(curTime, 0, wx.TOP)
        vbox.Add(toolbar, 0, wx.TOP)
        self.SetSizer(vbox)
        self.SetSize((400, 400))
        self.SetTitle('CueMIDI')
        self.Show(True)

    def OnClose(self, e):
        '''Stop threads and close app'''
        if self.worker:
            self.worker.abort()
        self.Destroy()

    def OnQuit(self, e):
        '''Quit menu item action'''
        self.Close()
    
    def Tick(self, e):
        '''Update with info from worker thread'''
        print("TICK", e.data)
        self.curTime.SetLabel('Time: {}'.format(e.data))

if __name__ == '__main__':
    app = wx.App()
    CueApp(None)
    app.MainLoop()


sys.exit(0)
################

fs = fluidsynth.Synth()

pa = pyaudio.PyAudio()
strm = pa.open(
    format = pyaudio.paInt16,
    channels = 2, 
    rate = 44100, 
    output = True)
s = []

sfid = fs.sfload("gm32MB.sf2")
fs.program_select(0, sfid, 0, 68)

pattern = midi.read_midifile(sys.argv[1])

resolution = pattern.resolution

pattern.make_ticks_abs()

events = []
for track in pattern:
    for event in track:
        events.append(event)
events.sort()

t = 0

tempo = 120

def do_event(evt):
    print(evt)
    if type(evt) == midi.events.NoteOnEvent:
        fs.noteon(evt.channel, evt.data[0], evt.data[1])
    if type(evt) == midi.events.NoteOffEvent:
        fs.noteoff(evt.channel, evt.data[0])
    if type(evt) == midi.events.ProgramChangeEvent:
        fs.program_select(evt.channel, sfid, 0, evt.data[0])
    if type(evt) == midi.events.ControlChangeEvent:
        fs.cc(evt.channel, evt.data[0], evt.data[1])
    if type(evt) == midi.events.SetTempoEvent:
        global tempo
        tempo = evt.get_bpm()
        print("TEMPO", tempo)

while len(events) > 0:
    event = events.pop(0)
    delta = event.tick - t
    if delta > 0:
        t = event.tick
        s = fs.get_samples(int(44100 * delta / resolution * 120 / 2 / tempo))
        samps = fluidsynth.raw_audio_string(s)
        strm.write(samps)
    do_event(event)

fs.delete()
