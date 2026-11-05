"""Isolated CPU Piper; no application/cloud imports or inherited secrets."""
import os
import sys
import wave


def main(model,output):
    os.umask(0o077)
    from piper import PiperVoice
    voice=PiperVoice.load(model,use_cuda=False)
    text=sys.stdin.read(20001)
    if not text.strip() or len(text)>20000:raise ValueError('Speech text exceeds limits')
    with wave.open(output,'wb') as file:voice.synthesize_wav(text,file)


if __name__=='__main__':main(sys.argv[1],sys.argv[2])
