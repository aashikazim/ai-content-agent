"""CPU Whisper recognition and short-form forced transcript alignment."""
import json
import os
from pathlib import Path
import sys


def main(request_path):
    os.umask(0o077)
    value=json.loads(Path(request_path).read_text())
    import numpy as np
    import whisper
    model=whisper.load_model(value['checkpoint'],device='cpu')
    audio=whisper.load_audio(value['audio'])
    duration=len(audio)/16000
    silent=not len(audio) or float(np.sqrt(np.mean(audio**2)))<1e-4
    result={'text':'','segments':[],'language':'en'} if silent else model.transcribe(audio,fp16=False,verbose=False,word_timestamps=True)
    words=[{'word':w['word'],'start':float(w['start']),'end':float(w['end']),'probability':float(w.get('probability',0))}
        for segment in result['segments'] for w in segment.get('words',[])]
    alignment=[]
    if value.get('forced'):
        if duration>30.01:raise ValueError('Forced alignment requires a clip up to30seconds; choose ASR timestamps for longer audio')
        from whisper.tokenizer import get_tokenizer
        from whisper.timing import find_alignment
        tokenizer=get_tokenizer(model.is_multilingual,language=result.get('language','en'),task='transcribe')
        tokens=tokenizer.encode(value['expected'])
        if len(tokens)>400:raise ValueError('Transcript exceeds alignment context')
        mel=whisper.log_mel_spectrogram(whisper.pad_or_trim(audio),n_mels=model.dims.n_mels)
        alignment=[{'word':w.word,'start':float(w.start),'end':float(w.end),'probability':float(w.probability)}
            for w in find_alignment(model,tokenizer,tokens,mel,len(audio)//160)]
    Path(value['output']).write_text(json.dumps({'text':result['text'],'words':words,'aligned_words':alignment,
        'duration_seconds':duration,'silent':silent,'method':'whisper-dtw-forced' if value.get('forced') else 'whisper-asr-word-timestamps'}))


if __name__=='__main__':main(sys.argv[1])
