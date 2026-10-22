import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import alignment, audio, main, storage


class AlignmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=storage.ROOT)
        self.out=patch.object(storage,'OUTPUTS',Path(self.tmp.name));self.out.start()
        self.beat={'number':6,'narration':'A truck approaches. The driver is asleep. He runs.',
                   'panel_ids':['p006-1','p006-2','p006-6'],'panels':[{'id':'p006-1'}, {'id':'p006-2'}, {'id':'p006-6'}], 'token':'b'*64}
        self.payload={'beat':6,'anchors':[{'panel_id':'p006-1','phrase':'A truck approaches.'},
                       {'panel_id':'p006-2','phrase':'The driver is asleep.'}, {'panel_id':'p006-6','phrase':'He runs.'}]}
        self.ctx=patch.object(alignment,'context',side_effect=lambda *a:(dict(copy.deepcopy(self.beat),number=a[2]),'a'*64));self.ctx.start()
        alignment.jobs.clear()

    def tearDown(self):
        self.ctx.stop();self.out.stop();self.tmp.cleanup();alignment.jobs.clear()

    def test_exact_map_and_failure_is_atomic(self):
        self.assertTrue(alignment.save_map('test','Chapter 1',6,'a'*64,json.dumps(self.payload))['ok'])
        path=alignment.root('test')/'beat-006-map.json';before=path.read_bytes()
        bad=copy.deepcopy(self.payload);bad['anchors'][1]['phrase']='Invented words'
        result=alignment.save_map('test','Chapter 1',6,'a'*64,json.dumps(bad))
        self.assertFalse(result['ok']);self.assertIn('p006-2',str(result['errors']));self.assertEqual(path.read_bytes(),before)
        with self.assertRaises(FileExistsError):alignment.save_map('test','Chapter 1',6,'c'*64,json.dumps(self.payload))

    def test_invalid_ids_order_script_rewrites_and_overlapping_phrases(self):
        for change in ('id','rewrite','same_start','substring'):
            bad=copy.deepcopy(self.payload)
            if change=='id':bad['anchors'][1]['panel_id']='p999-1'
            if change=='order':bad['anchors'].reverse()
            if change=='rewrite':bad['narration']='Changed script'
            if change=='same_start':bad['anchors'][1]['phrase']=bad['anchors'][0]['phrase']
            if change=='first':bad['anchors'][0]['phrase']='truck approaches.'
            if change=='substring':bad['anchors'][1]['phrase']='driver is as'
            self.assertTrue(alignment.validate_map(self.beat,bad)[0],change)

    def test_lead_in_and_shuffled_objects_keep_pairings_and_start_at_zero(self):
        p=copy.deepcopy(self.payload)
        p['anchors'][0]['phrase']='truck approaches.'
        p['anchors'].reverse()
        errors,anchors=alignment.validate_map(self.beat,p)
        self.assertEqual(errors,[])
        self.assertEqual([a['panel_id'] for a in anchors],self.beat['panel_ids'])
        words=[{'char_start':a['char_start'],'start':i+.2} for i,a in enumerate(anchors)]
        rows=alignment.anchor_times(self.beat,anchors,words,4)
        self.assertEqual(rows[0]['start'],0)
        self.assertEqual(rows[1]['start'],1.2)

    def test_real_phrase_order_conflict_is_not_silently_relabelled(self):
        p=copy.deepcopy(self.payload)
        p['anchors'][1]['phrase']='He runs.'
        p['anchors'][2]['phrase']='The driver is asleep.'
        errors,_=alignment.validate_map(self.beat,p)
        self.assertTrue(errors)
        self.assertIn('p006-2 → p006-6',str(errors))
        self.assertIn('Do not swap IDs',str(errors))

    def test_nested_anchor_prefix_keeps_switch_starts(self):
        beat={'number':3,'panel_ids':['p0025-1','p0025-2'],
              'narration':'Left alone, leaving Banaza with only a bottomless bag from the king.'}
        payload={'beat':3,'anchors':[
            {'panel_id':'p0025-1','phrase':'leaving Banaza with only a bottomless bag from the king'},
            {'panel_id':'p0025-2','phrase':'bag from the king'}]}
        errors,anchors=alignment.validate_map(beat,payload)
        self.assertEqual(errors,[])
        self.assertEqual(anchors[0]['phrase'],'leaving Banaza with only a bottomless')
        self.assertEqual(anchors[0]['char_start'],beat['narration'].index('leaving'))
        self.assertEqual(anchors[1]['char_start'],beat['narration'].index('bag'))
        self.assertEqual(payload['anchors'][0]['phrase'],'leaving Banaza with only a bottomless bag from the king')

    def test_overlap_with_nonunique_prefix_stays_rejected(self):
        beat={'number':3,'panel_ids':['p0025-1','p0025-2'],'narration':'Go now. Go later.'}
        payload={'beat':3,'anchors':[{'panel_id':'p0025-1','phrase':'Go now. Go later.'},
                                  {'panel_id':'p0025-2','phrase':'now.'}]}
        self.assertTrue(alignment.validate_map(beat,payload)[0])

    def test_duplicate_phrase_is_not_guessed(self):
        b=copy.deepcopy(self.beat);b['narration']='Go. Go. He runs.'
        p=copy.deepcopy(self.payload);p['anchors'][0]['phrase']='Go.'
        self.assertIn('found 2 matches',str(alignment.validate_map(b,p)[0]))
        self.assertFalse(alignment.save_map('test','Chapter 1',6,'a'*64,'{"beat":6,"beat":6,"anchors":[]}')['ok'])

    def test_silence_removal_changes_word_offsets(self):
        self.assertAlmostEqual(alignment.adjusted_time(2,[{'start':.5,'end':1.0}]),1.5)
        self.assertAlmostEqual(alignment.adjusted_time(.75,[{'start':.5,'end':1.0}]),.5)
        events=[{'text':'One','offset':1_000_000,'duration':2_000_000},{'text':'Two','offset':15_000_000,'duration':2_000_000}]
        words=alignment.aligned_words('One. Two.',events,[{'start':.5,'end':1.0}],2)
        self.assertAlmostEqual(words[1]['start'],1)
        self.assertEqual(words[1]['char_start'],5)
        b={'panels':[{},{}]};a=[{'panel_id':'p001-1','phrase':'One.','char_start':0,'char_end':4},
                             {'panel_id':'p001-2','phrase':'Two.','char_start':5,'char_end':9}]
        rows=alignment.anchor_times(b,a,words,2)
        self.assertEqual([r['start'] for r in rows],[0,1])

    def test_incorrect_word_metadata_rejected(self):
        with self.assertRaises(ValueError):alignment.aligned_words('One.',[{'text':'Other','offset':1,'duration':1}],[],1)
        with self.assertRaises(ValueError):alignment.aligned_words('One.',[{'text':'One','offset':20_000_000,'duration':1}],[],1)
        with self.assertRaises(ValueError):alignment.aligned_words('One.',[],[],1)

    def test_only_one_approved_beat_can_generate(self):
        with self.assertRaises(ValueError):alignment.start('test','Chapter 1',5,'a'*64)
        alignment.save_map('test','Chapter 1',6,'a'*64,json.dumps(self.payload))
        with patch('threading.Thread') as thread:
            alignment.start('test','Chapter 1',6,'a'*64)
            args=thread.call_args.kwargs['args'];self.assertEqual(args[2]['number'],6)
        with self.assertRaises(FileExistsError):alignment.start('test','Chapter 1',6,'a'*64)

    def test_failed_trial_keeps_previous_audio_and_reports_failure(self):
        alignment.jobs['test']={'running':True}
        with patch.object(alignment,'capture',side_effect=RuntimeError('Controlled service failure')):
            alignment.run('test','Chapter 1',self.beat,'a'*64,{'anchors':[]})
        self.assertFalse(alignment.jobs['test']['running']);self.assertTrue(alignment.jobs['test']['error'])
        self.assertFalse((alignment.root('test')/'beat-006-trial.json').exists())
        self.assertFalse((audio.root('test')/'audio.json').exists())

    def test_prompt_freezes_script_and_scoped_api(self):
        text=alignment.prompt_text('test','Chapter 1',6)
        self.assertIn(self.beat['narration'],text);self.assertIn('not a rewrite',text)
        c=TestClient(main.app)
        result=c.post('/api/alignment/test/Chapter%201/6/map',json={'source_token':'a'*64,'reply':'{}'})
        self.assertEqual(result.status_code,422)
        self.ctx.stop()
        try:
            self.assertEqual(c.get('/api/alignment/test/Chapter%2012/6').status_code,404)
            self.assertEqual(c.get('/api/alignment/test/Chapter%2012/6/prompt').status_code,404)
            self.assertEqual(c.post('/api/alignment/test/Chapter%2012/6/trial',json={'source_token':'a'*64}).status_code,404)
        finally:self.ctx.start()

    def test_additional_examples_are_explicitly_scoped(self):
        for number in (1,5,7,10,19,25,30):
            payload=copy.deepcopy(self.payload);payload['beat']=number
            self.assertTrue(alignment.save_map('test','Chapter 1',number,'a'*64,json.dumps(payload))['ok'])
            alignment.jobs.clear()
            with patch('threading.Thread') as thread:
                alignment.start('test','Chapter 1',number,'a'*64)
                self.assertEqual(thread.call_args.kwargs['args'][2]['number'],number)
        alignment.jobs.clear()
        self.ctx.stop()
        try:
            c=TestClient(main.app)
            self.assertEqual(c.get('/alignment-examples/test/Chapter%2012').status_code,404)
            self.assertEqual(c.get('/api/alignment-examples/test/Chapter%2012').status_code,404)
        finally:self.ctx.start()


class ChapterAlignmentTests(unittest.TestCase):
    def test_other_chapters_rejected(self):
        with self.assertRaises(FileNotFoundError):
            alignment.apply_chapter('test', 'Chapter 12')

    def test_cached_audio_published_only_after_render_and_rolls_back_on_failure(self):
        from contextlib import ExitStack
        from app import video
        for failure in ('render', 'publish', 'pacing', None):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory(dir=storage.ROOT) as folder, ExitStack() as stack:
                base=Path(folder)
                stack.enter_context(patch.object(storage,'OUTPUTS',base/'outputs'))
                stack.enter_context(patch.object(storage,'DATA',base/'data'))
                mid='test';chapter='Chapter 1'
                b={'number':1,'token':'b'*64,'revision':'old.mp3','narration':'One. Two.',
                   'panel_ids':['p001-1','p001-2'],'panels':[{'id':'p001-1','revision':'r'},{'id':'p001-2','revision':'r'}]}
                token='a'*64;mapping={'source_token':token,'anchors':[]}
                storage.write_json(alignment.root(mid)/'beat-001-map.json',mapping)
                trial_dir=alignment.root(mid)/'cached';trial_dir.mkdir();(trial_dir/'trial.mp3').write_bytes(b'cached audio')
                old={'clips':{'old':{'file':'old.mp3'}}};storage.write_json(audio.root(mid)/'audio.json',old)
                old_timing={'source_token':'original','switches':{'1':[1]}}
                storage.write_json(video.root(mid)/'timing.json',old_timing)
                script_path=storage.DATA/'manga'/mid/'narration.json';storage.write_json(script_path,{'immutable':'script'})
                script_bytes=script_path.read_bytes()
                stack.enter_context(patch.object(video,'source',return_value=([b],'original')))
                stack.enter_context(patch.object(alignment,'context',return_value=(b,token)))
                stack.enter_context(patch.object(alignment,'status',return_value={'ready':True,'trial':{'revision':'cached','anchors':[{'start':0},{'start':1}]}}))
                capture=stack.enter_context(patch.object(alignment,'capture'))
                stack.enter_context(patch.object(video,'pcm',return_value=b'\0'*96000))
                def render(state,work):
                    self.assertEqual(audio.manifest(mid),old)
                    self.assertEqual(storage.read_json(video.root(mid)/'timing.json'),old_timing)
                    if failure=='render':raise RuntimeError('render failed')
                    (work/'chapter.mp4').write_bytes(b'video')
                stack.enter_context(patch.object(video,'render_to',side_effect=render))
                settings={'rate':'+20%','speed':1.2,'sentence_pause':.18}
                real_write=storage.write_json;failed=False
                def write(path,value):
                    nonlocal failed
                    if not failed and ((failure=='publish' and path==video.root(mid)/'timing.json') or (failure=='pacing' and path==audio.root(mid)/'pacing.json')):
                        failed=True;raise OSError('disk failure')
                    real_write(path,value)
                stack.enter_context(patch.object(storage,'write_json',side_effect=write))
                if failure:
                    with self.assertRaises((RuntimeError,OSError)):alignment.apply_chapter(mid,chapter,settings)
                    self.assertEqual(audio.manifest(mid),old)
                    self.assertEqual(audio.pacing(mid),{})
                    self.assertEqual(storage.read_json(video.root(mid)/'timing.json'),old_timing)
                else:
                    report=alignment.apply_chapter(mid,chapter,settings)
                    timing=storage.read_json(video.root(mid)/'timing.json')
                    self.assertEqual(timing['switches'],{'1':[1]})
                    self.assertEqual(timing['mode'],'phrase-aligned')
                    self.assertEqual(audio.pacing(mid),settings)
                    self.assertTrue(Path(report['file']).is_file())
                    self.assertIn('aligned-cached',audio.manifest(mid)['clips'][b['token']]['file'])
                self.assertEqual(script_bytes,script_path.read_bytes());capture.assert_not_called()


class PreviewRestyleTests(unittest.TestCase):
    def test_restyle_preserves_audio_words_and_cuts_without_tts(self):
        from app import video
        with tempfile.TemporaryDirectory(dir=storage.ROOT) as folder, patch.object(storage,'OUTPUTS',Path(folder)):
            mid='test';saved={'revision':'old','duration':3,'words':[{'text':'Frozen'}],
                              'anchors':[{'start':0},{'start':1},{'start':2}],'script':'Frozen script.'}
            old=alignment.root(mid)/'old';old.mkdir(parents=True);(old/'trial.mp3').write_bytes(b'approved audio')
            state={'ready':True,'trial':saved,'source_token':'a'*64}
            beat={'number':1,'panels':[{}, {}, {}]}
            def render(snapshot,work):
                self.assertEqual(snapshot['beats'][0]['switches'],[1,2])
                (work/'chapter.mp4').write_bytes(b'restyled video')
            with patch.object(alignment,'status',return_value=state), patch.object(alignment,'context',return_value=(beat,'a'*64)), patch.object(video,'render_to',side_effect=render), patch.object(alignment,'capture') as capture:
                result=alignment.refresh_preview_visuals(mid,'Chapter 1',1)
                capture.assert_not_called()
            self.assertEqual(result['words'],saved['words']);self.assertEqual(result['anchors'],saved['anchors'])
            self.assertEqual(result['script'],saved['script'])
            self.assertEqual((alignment.root(mid)/result['revision']/'trial.mp3').read_bytes(),b'approved audio')
            self.assertEqual(result['visual_style'],video.VISUAL_STYLE)
