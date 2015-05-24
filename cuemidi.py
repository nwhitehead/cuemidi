import time
import numpy
import pyaudio
import fluidsynth
import midi
import sys

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

tick_length = 500

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
