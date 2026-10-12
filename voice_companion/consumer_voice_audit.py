"""Synthetic speech -> production offline recognizer -> safe command fixtures.

Reports recognition errors rather than claiming microphone performance.
"""
import io,json,os,tempfile,wave
from pathlib import Path
from audio_resample import PCM16Resampler,stereo_to_mono
from vosk import Model,KaldiRecognizer,SetLogLevel
import companion as app

PHRASES=('next heading','previous link','list fields','main menu','go to sleep','wake up')

def dispatch(phrase):
    phrase=app.normalized_command(phrase)
    from test_consumer_workflows import ConsumerWorkflowTests
    from test_web_assistant import FakeBackend
    from web_assistant import WebSession
    case=ConsumerWorkflowTests('test_sleep_keeps_document_help_and_note_contexts');case.setUp()
    try:
        session=WebSession(case.root,FakeBackend());session.open('https://example.test/')
        app.web_session=session
        if phrase=='wake up':
            app.SLEEP_RETURN_MODE='web';result=app.resume_from_sleep();expected='web'
        else:
            result=app.handle(phrase,'web');expected='awake' if phrase=='main menu' else 'sleep' if phrase=='go to sleep' else 'web'
        return result,expected
    finally:case.doCleanups()

def audit():
    report={'version':app.APP_VERSION,'method':'Synthesized speech, with no microphone or personal accounts','samples':[]}
    if os.name!='nt':return report|{'blocked':'Windows SAPI and bundled eSpeak are required.'}
    import pythoncom,win32com.client
    from espeak_speech import synthesize
    pythoncom.CoInitialize()
    try:
        SetLogLevel(-1);model=Model(str(app.MODEL));speaker=win32com.client.Dispatch('SAPI.SpVoice');speaker.Rate=0
        from parakeet_recognition import ParakeetRecognition
        parakeet=ParakeetRecognition(app.PARAKEET_MODEL) if app.PARAKEET_MODEL.is_dir() else None
        with tempfile.TemporaryDirectory(prefix='VoiceCompanion-Voice-Audit-') as folder:
            for engine in ('SAPI','eSpeak US','eSpeak UK'):
                for phrase in PHRASES:
                    entry={'engine':engine,'expected':phrase,'status':'blocked'}
                    try:
                        if engine=='SAPI':
                            target=Path(folder)/'utterance.wav';stream=win32com.client.Dispatch('SAPI.SpFileStream');stream.Open(str(target),3,False)
                            try:speaker.AudioOutputStream=stream;speaker.Speak(phrase,0)
                            finally:stream.Close()
                            with wave.open(str(target),'rb') as audio:
                                if audio.getsampwidth()!=2:raise ValueError('SAPI did not return 16-bit PCM.')
                                raw=audio.readframes(audio.getnframes());rate=audio.getframerate();channels=audio.getnchannels()
                            if channels==2:raw=stereo_to_mono(raw)
                            elif channels!=1:raise ValueError('Unexpected channel count.')
                            raw=PCM16Resampler(rate,16000).convert(raw)
                        else:raw=PCM16Resampler(24000,16000).convert(synthesize(phrase,voice='en-us' if engine=='eSpeak US' else 'en-gb'))
                        raw+=b'\0\0'*8000
                        for recognizer_name in ('Vosk','Parakeet'):
                            sample=entry|{'recognizer':recognizer_name}
                            if recognizer_name=='Vosk':
                                recognizer=KaldiRecognizer(model,16000);parts=[]
                                for start in range(0,len(raw),4000):
                                    if recognizer.AcceptWaveform(raw[start:start+4000]):parts.append(json.loads(recognizer.Result()).get('text',''))
                                parts.append(json.loads(recognizer.FinalResult()).get('text',''));heard=' '.join(p for p in parts if p).strip()
                            elif parakeet is not None:heard=parakeet.recognize(raw)
                            else:
                                sample['reason']='Parakeet is not installed.';report['samples'].append(sample);print('SYNTHETIC VOICE AUDIT '+json.dumps(sample),flush=True);continue
                            sample['recognized']=heard
                            if app.normalized_command(heard)!=phrase:sample['status']='misrecognized'
                            else:
                                result,expected=dispatch(heard);sample['mode']=result;sample['status']='matched' if result==expected else 'dispatch_failed'
                            report['samples'].append(sample);print('SYNTHETIC VOICE AUDIT '+json.dumps(sample),flush=True)
                        continue
                    except Exception as exc:entry['reason']=type(exc).__name__+': '+str(exc)[:250]
                    report['samples'].append(entry);print('SYNTHETIC VOICE AUDIT '+json.dumps(entry),flush=True)
        report['matched']=sum(s['status']=='matched' for s in report['samples']);report['misrecognized']=sum(s['status']=='misrecognized' for s in report['samples']);report['blocked']=sum(s['status']=='blocked' for s in report['samples']);report['dispatch_failed']=sum(s['status']=='dispatch_failed' for s in report['samples'])
    finally:pythoncom.CoUninitialize()
    return report

if __name__=='__main__':
    report=audit();Path(__file__).with_name('VoiceCompanion-Consumer-Voice-Audit.txt').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('SYNTHETIC VOICE AUDIT SUMMARY '+json.dumps({k:v for k,v in report.items() if k!='samples'}),flush=True)
    raise SystemExit(bool(report.get('dispatch_failed')))
