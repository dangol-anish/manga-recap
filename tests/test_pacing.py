import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
from app import alignment, audio, storage, video


class PacingTests(unittest.TestCase):
    def test_word_clock_accounts_for_inserted_pause(self):
        edits=[{'start':.5,'end':1.0},{'start':1.5,'end':1.5,'added':.08}]
        self.assertAlmostEqual(alignment.adjusted_time(1.4,edits),.9)
        self.assertAlmostEqual(alignment.adjusted_time(2,edits),1.58)

    def test_speed_changes_cached_trial_fingerprint(self):
        mapping={'anchors':[]}
        a=alignment.trial_token({},'source',mapping,{'rate':'+20%','speed':1.2,'sentence_pause':.18})
        b=alignment.trial_token({},'source',mapping,{'rate':'+30%','speed':1.3,'sentence_pause':.18})
        self.assertNotEqual(a,b)
        self.assertNotEqual(a,alignment.trial_token({},'source',mapping))

    def test_period_pause_is_180_ms_and_other_long_gap_stays_100_ms(self):
        # Two sentences separated by 80ms or 800ms quiet, plus an ordinary 800ms gap.
        for period_gap in (.08,.8):
            with self.subTest(period_gap=period_gap), tempfile.TemporaryDirectory(dir=storage.ROOT) as folder:
                target=Path(folder)/'input.wav'
                tone=b''.join(struct.pack('<h',round(10000*math.sin(2*math.pi*440*i/24000))) for i in range(9600))
                with wave.open(str(target),'wb') as track:
                    track.setparams((1,2,24000,0,'NONE','not compressed'))
                    track.writeframes(tone+b'\0\0'*round(period_gap*24000)+tone+b'\0\0'*19200+tone)
                edits=audio.shorten_pauses(target,[{'start':0,'end':.4+period_gap-.055}],.18)
                duration=len(video.pcm(target))/48000
                self.assertAlmostEqual(duration,1.2+.18+.1,delta=.015)
                self.assertTrue(any(e.get('added',0)>0 for e in edits) if period_gap<.18 else any(e['end']>e['start'] for e in edits))

    def test_individual_regeneration_keeps_saved_pacing(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory(dir=storage.ROOT) as folder, patch.object(storage,'OUTPUTS',Path(folder)):
            settings={'rate':'+20%','speed':1.2,'sentence_pause':.18}
            storage.write_json(audio.root('test')/'pacing.json',settings)
            beat={'number':1,'token':'a'*64,'narration':'One. Two.','panel_ids':['p001-1']}
            def speech(text,target,pacing):
                self.assertEqual(pacing,settings);target.write_bytes(b'audio')
            with patch.object(alignment,'speech',side_effect=speech) as render, patch.object(audio,'synthesize') as legacy, patch.object(audio,'beats',return_value=[beat]), patch('mutagen.mp3.MP3',return_value=SimpleNamespace(info=SimpleNamespace(length=2))):
                audio.generate_one('test','Chapter 1',beat)
                render.assert_called_once();legacy.assert_not_called()
            self.assertEqual(audio.manifest('test')['clips'][beat['token']]['pacing'],settings)

    def test_saved_pacing_invalidates_a_different_speed_clip(self):
        with tempfile.TemporaryDirectory(dir=storage.ROOT) as folder, patch.object(storage,'OUTPUTS',Path(folder)):
            beat={'number':1,'token':'a'*64,'narration':'One.','panel_ids':['p001-1']}
            settings={'rate':'+20%','speed':1.2,'sentence_pause':.18}
            storage.write_json(audio.root('test')/'pacing.json',settings)
            clip={'file':'old.mp3','duration':1,'pacing':{}}
            (audio.root('test')/'old.mp3').write_bytes(b'audio')
            storage.write_json(audio.root('test')/'audio.json',{'clips':{beat['token']:clip}})
            with patch.object(audio,'beats',return_value=[beat]):
                self.assertEqual(audio.status('test','Chapter 1')['ready'],0)
                clip['pacing']=settings
                storage.write_json(audio.root('test')/'audio.json',{'clips':{beat['token']:clip}})
                self.assertEqual(audio.status('test','Chapter 1')['ready'],1)
