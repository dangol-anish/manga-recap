import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import uuid
from PIL import Image
from fastapi.testclient import TestClient
from app import storage,review,narration,memory,audio,video,alignment,main

MASTER=(storage.ROOT/'prompts/narration_master.md').read_text(encoding='utf-8')

class GenericTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=storage.ROOT);self.root=Path(self.temp.name)
        self.patches=[patch.object(storage,k,self.root/v) for k,v in [('DATA','data'),('OUTPUTS','outputs')]]+[patch.object(storage,'ROOT',self.root)]
        self.patches += [patch.object(main,'DATA',self.root/'data'),patch.object(main,'OUTPUTS',self.root/'outputs')]
        for p in self.patches:p.start()
        (self.root/'prompts').mkdir();(self.root/'prompts/narration_master.md').write_text(MASTER, encoding='utf-8')
        self.manga=[]
        for mid in ['a','b']:
            m={'id':mid,'name':mid,'folder':'manga/'+mid,'direction':'ltr' if mid=='b' else 'rtl'};self.manga.append(m)
            for ch in ['Episode 2','Episode 10']:
                directory=self.root/m['folder']/ch;directory.mkdir(parents=True)
                for i in range(1,3):
                    p=directory/f'page_{i}.png';Image.new('RGB',(80,160),'red' if i==1 else 'blue').save(p)
                    review.generate_page(m,ch,p,[[0,0,80,160]])
        storage.write_json(storage.DATA/'library.json',self.manga)
        for module in [audio,video,alignment]:module.jobs.clear()
        alignment.chapter_jobs.clear()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
        for module in [audio,video,alignment]:module.jobs.clear()
        alignment.chapter_jobs.clear()
    def payload(self,mid='a',ch='Episode 2',text='First. Next.',facts=True):
        b=narration.batches(mid,ch)[0];ctx=narration.story_context(mid,ch,b['id'])
        return {'cast':[],'beats':[{'panel_ids':b['panel_ids'],'narration':text,'unsure':'','anchors':[{'panel_id':b['panel_ids'][0],'phrase':'First.'},{'panel_id':b['panel_ids'][1],'phrase':'Next.'}]}],
                'continuity':{'summary':text,'open_threads':[]},'chapter_summary':text,
                'memory':[{'text':text,'status':'confirmed','panel_ids':[b['panel_ids'][0]]}] if facts else [],
                'batch_receipt':{'manga_id':mid,'chapter':ch,'batch_id':b['id'],'fingerprint':b['fingerprint'],'context_token':ctx['token']}}
    def submit(self,payload=None,mid='a',ch='Episode 2'):
        b=narration.batches(mid,ch)[0]
        return narration.import_reply(mid,ch,b['id'],b['fingerprint'],json.dumps(payload or self.payload(mid,ch)),require_receipt=True)
    def test_fresh_chat_cross_chapter_and_manga_isolation(self):
        name=uuid.uuid4().hex;p=self.payload();p['cast']=[{'name':name}]
        self.assertTrue(self.submit(p)['ok'])
        prompt=narration.prompt_text('a','Episode 10')
        self.assertIn(name,prompt);self.assertIn('First. Next.',prompt);self.assertIn('Episode 2',prompt)
        self.assertNotIn(name,narration.prompt_text('b','Episode 2'))
        future=uuid.uuid4().hex;second=self.payload('a','Episode 10');second['cast']=[{'name':future}]
        self.assertTrue(self.submit(second,mid='a',ch='Episode 10')['ok'])
        self.assertNotIn(future,narration.prompt_text('a','Episode 10'))
        first=narration.prompt_text('a','Episode 2');self.assertNotIn(name,first)
        self.assertEqual(narration.status('a','Episode 10')['imported_beats'],1)

    def test_fractional_chapter_order_and_future_history_exclusion(self):
        from types import SimpleNamespace
        from app.chapter_names import chapter_sort_key
        names=['Chapter 1.5','Chapter 1.25','Chapter 1.05','Chapter 1','Chapter 2']
        self.assertEqual(sorted(names,key=chapter_sort_key),
                         ['Chapter 1','Chapter 1.05','Chapter 1.25','Chapter 1.5','Chapter 2'])
        entry={'format':'beats-v1','fingerprint':'fixture','context_token':memory.digest([]),
               'beats':[],'continuity':{'summary':'A later event.','open_threads':[]}}
        state={'chapters':{'Chapter 1.5':{'batches':{'batch-001':entry}}}}
        frozen=json.dumps(state,sort_keys=True)
        backend=SimpleNamespace(load_state=lambda _:state,
                                batches=lambda *_:[{'id':'batch-001','fingerprint':'fixture'}])
        _,history,token,_=memory.lineage('fixture','Chapter 1.25','batch-001',backend)
        self.assertEqual(history,[]);self.assertEqual(token,memory.digest([]))
        self.assertEqual(json.dumps(state,sort_keys=True),frozen)
        state['chapters']={'Chapter 1.25':{'batches':{'batch-001':entry}}}
        _,history,_,_=memory.lineage('fixture','Chapter 1.5','batch-001',backend)
        self.assertEqual([h['chapter'] for h in history],['Chapter 1.25'])

    def test_changed_decimal_lineage_is_stale_without_rewriting_saved_work(self):
        from types import SimpleNamespace
        # Previously 1.5 was imported first, then 1.25 using that later history.
        first={'format':'beats-v1','fingerprint':'fixture','context_token':memory.digest([]),'beats':[]}
        old_seed=memory.digest([None,{'chapter':'Chapter 1.5','batches':[dict(first,id='batch-001')]}])
        second={**first,'context_token':memory.digest({'earlier_chapters':old_seed,'batches':[]})}
        state={'chapters':{'Chapter 1.5':{'batches':{'batch-001':first}},
                           'Chapter 1.25':{'batches':{'batch-001':second}}}}
        frozen=json.dumps(state,sort_keys=True)
        backend=SimpleNamespace(load_state=lambda _:state,
                                batches=lambda *_:[{'id':'batch-001','fingerprint':'fixture'}])
        with self.assertRaises(FileExistsError):memory.lineage('fixture','Chapter 2','batch-001',backend)
        self.assertEqual(json.dumps(state,sort_keys=True),frozen)
    def test_stale_upstream_narration_and_memory_block_later_audio(self):
        self.assertTrue(self.submit()['ok']);self.assertTrue(self.submit(ch='Episode 10')['ok'])
        state=memory.overview('a');entry=state['entries'][0]
        memory.edit('a','Episode 2','batch-001',state['revision'],'Changed reviewed context.',entry['memory'])
        self.assertFalse(narration.status('a','Episode 10')['batches'][0]['imported'])
        with self.assertRaises(FileExistsError):audio.beats('a','Episode 10')
        with self.assertRaises(FileExistsError):memory.edit('a','Episode 2','batch-001',state['revision'],'Old revision.',[])
        self.assertEqual(memory.overview('a')['entries'][0]['prior_revisions'],1)
    def test_unknown_receipt_and_failed_memory_never_commit(self):
        p=self.payload();p['batch_receipt']['manga_id']='b'
        result=self.submit(p);self.assertFalse(result['ok']);self.assertFalse(result['can_correct']);self.assertFalse(narration.state_path('a').exists())
        p=self.payload();p['memory'][0]['panel_ids']=['p999-1']
        self.assertFalse(self.submit(p)['ok']);self.assertFalse(narration.state_path('a').exists())
        p=self.payload();p['memory'][0]['status']='maybe';self.assertFalse(self.submit(p)['ok'])
    def test_uncertainty_is_not_promoted_and_search_has_sources(self):
        p=self.payload();p['beats'][0]['unsure']='Controlled uncertain claim.'
        self.assertTrue(self.submit(p)['ok'])
        ctx=narration.story_context('a','Episode 10','batch-001')
        self.assertEqual(ctx['confirmed_facts'],[]);self.assertTrue(ctx['uncertain_claims'])
        matches=memory.overview('a','uncertain')['entries'];self.assertEqual(matches[0]['chapter'],'Episode 2')
    def test_paths_jobs_and_duplicate_ids_are_chapter_scoped(self):
        self.submit();self.submit(ch='Episode 10');self.submit(mid='b')
        roots=[audio.root('a','Episode 2'),audio.root('a','Episode 10'),audio.root('b','Episode 2')]
        self.assertEqual(len(set(roots)),3)
        for i,path in enumerate(roots):storage.write_json(path/'audio.json',{'clips':{'fixture':{'value':i}}})
        self.assertEqual(audio.manifest('a','Episode 10')['clips']['fixture']['value'],1)
        self.assertNotEqual(video.root('a','Episode 2'),video.root('a','Episode 10'))
        self.assertNotEqual(alignment.root('a','Episode 2'),alignment.root('b','Episode 2'))
        self.assertNotEqual(audio.job_key('a','Episode 2'),audio.job_key('a','Episode 10'))
        ids=narration.batches('a','Episode 2')[0]['panel_ids'];self.assertEqual(ids,['p001-1','p002-1'])
        self.assertTrue(narration.export_zip('a','Episode 2','batch-001',narration.batches('a','Episode 2')[0]['fingerprint']).is_file())
    def test_context_is_bounded_and_original_summaries_survive(self):
        # Many original chapter records, without any inference or large fixture images.
        template=self.payload();state={'schema_version':6,'cast':[],'legacy_cast_after':None,'legacy_cast':[],'chapters':{}}
        prior=None
        for n in range(80):
            ch=f'Part {n:03}';summary=f'Original chapter {n}. '+'context '*120
            entry={'format':'beats-v1','fingerprint':ch,'context_token':memory.digest({'earlier_chapters':prior,'batches':[]}) if prior else memory.digest([]),
                   'beats':template['beats'],'cast':[{'name':uuid.uuid4().hex,'look':'context '*40}],
                   'continuity':{'summary':summary,'open_threads':['context '*30]*10},'chapter_summary':summary,
                   'memory':[{'text':f'{n}-{i} '+'context '*35,'status':'confirmed','panel_ids':['p001-1']} for i in range(20)]}
            state['chapters'][ch]={'batches':{'batch-001':entry}}
            prior=memory.digest([prior,{'chapter':ch,'batches':[dict(entry,id='batch-001')]}])
        storage.write_json(narration.state_path('a'),state)
        def batches(mid,ch):return [{'id':'batch-001','fingerprint':ch}]
        with patch.object(narration,'batches',side_effect=batches):
            ctx=memory.packet('a','Part 080','batch-001')
        self.assertLessEqual(len(json.dumps(ctx,ensure_ascii=False)),memory.CONTEXT_LIMIT)
        self.assertEqual(ctx['coverage']['chapters_saved'],80)
        self.assertLess(ctx['coverage']['facts_included'],ctx['coverage']['facts_saved'])
        self.assertEqual(narration.load_state('a')['chapters']['Part 000']['batches']['batch-001']['chapter_summary'],state['chapters']['Part 000']['batches']['batch-001']['chapter_summary'])
    def test_real_generic_video_pipeline_with_imported_phrase_anchors(self):
        p=self.payload();p['beats'][0]['anchors']=[{'panel_id':'p001-1','phrase':'First.'},{'panel_id':'p002-1','phrase':'Next.'}]
        self.assertTrue(self.submit(p)['ok'])
        fixture=self.root/'fixture.mp3'
        subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-f','lavfi','-i','sine=frequency=440:sample_rate=24000:duration=1.2','-c:a','libmp3lame',str(fixture)],check=True)
        def speech(text,target,settings):
            target.write_bytes(fixture.read_bytes())
            return {'duration':1.2,'words':[{'char_start':0,'char_end':6,'start':0,'end':.4},{'char_start':7,'char_end':12,'start':.6,'end':1.1}]}
        with patch.object(alignment,'speech',side_effect=speech):audio.generate_one('a','Episode 2',audio.beats('a','Episode 2')[0])
        state=video.timeline('a','Episode 2');self.assertEqual(state['beats'][0]['switches'],[.6])
        video.jobs[video.job_key('a','Episode 2')]={'running':True,'duration':state['duration']}
        video.run('a','Episode 2',state)
        self.assertFalse(video.jobs[video.job_key('a','Episode 2')].get('error'),video.jobs)
        path=video.export_path('a',state);self.assertTrue(path.is_file());self.assertIn('Episode 2',str(path))
        self.assertFalse(audio.root('a','Episode 10').exists())
        client=TestClient(main.app)
        self.assertEqual(client.get('/api/video/a/Episode%202').status_code,200)
        response=client.get('/api/video/a/Episode%202/mp4?token='+state['render_token'],headers={'Range':'bytes=0-99'})
        self.assertEqual(response.status_code,206)

    def test_library_prepare_and_memory_api_for_any_registered_chapter(self):
        client=TestClient(main.app)
        response=client.post('/api/panels/a/Episode%2010/prepare')
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(client.get('/api/library').json()['manga'][0]['chapters'][1]['review_ready'])
        self.submit()
        data=client.get('/api/memory/a').json();self.assertEqual(len(data['entries']),1)
        self.assertEqual(client.get('/api/memory/unknown').status_code,404)
        entry=data['entries'][0]
        result=client.post('/api/memory/a/Episode%202/batch-001',json={'revision':data['revision'],'chapter_summary':'Reviewed chapter summary.','memory':entry['memory'],'cast':entry['cast']})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(client.post('/api/memory/a/Episode%202/batch-001',json={'revision':data['revision'],'chapter_summary':'Stale edit.','memory':[]}).status_code,409)
        with self.assertRaises(FileNotFoundError):review.review_context('a','../b')

    def test_heavy_jobs_do_not_overlap_between_chapters(self):
        from app.tasks import ensure_idle
        audio.jobs[audio.job_key('a','Episode 2')]={'running':True}
        with self.assertRaises(FileExistsError):ensure_idle()
        with self.assertRaises(FileExistsError):video.start('a','Episode 10','a'*64)
        audio.jobs.clear();ensure_idle()

    def test_detector_start_accepts_any_registered_manga_but_never_autostarts(self):
        from app.authorization import processing_allowed
        from unittest.mock import MagicMock
        storage.write_json(storage.DATA/'model-validation.json',{'panel_only_validated':True,'allowed_manga_id':'old-only','allowed_chapters':['Chapter 1']})
        self.assertTrue(processing_allowed('a','Episode 2'))
        self.assertTrue(processing_allowed('b','Episode 10'))
        self.assertFalse(processing_allowed('unknown','Episode 10'))
        process=MagicMock(pid=12345);process.poll.return_value=None
        try:
            with patch.object(main.subprocess,'Popen',return_value=process) as start:
                client=TestClient(main.app)
                self.assertEqual(client.get('/api/library').status_code,200)
                start.assert_not_called()
                response=client.post('/api/manga/b/chapters/Episode%2010/start')
                self.assertEqual(response.status_code,200,response.text)
                command=start.call_args.args[0]
                self.assertIn('app.worker',command)
                self.assertIn('Episode 10',command[-1]);self.assertIn('/b/',command[-1])
        finally:main.process=main.active_job=None

    def test_old_context_receipt_requires_fresh_reply_not_metadata_swap(self):
        self.submit();old=self.payload('a','Episode 10')
        view=memory.overview('a');entry=view['entries'][0]
        memory.edit('a','Episode 2','batch-001',view['revision'],'Reviewed changed context.',entry['memory'])
        with self.assertRaises(FileExistsError):self.submit(old,ch='Episode 10')
        self.assertNotIn('Episode 10',narration.load_state('a')['chapters'])

    def test_replacing_legacy_import_does_not_reintroduce_old_cast(self):
        self.submit();state=narration.load_state('a');name=uuid.uuid4().hex
        state['cast']=[{'name':name}]
        state.pop('legacy_cast_after',None);state.pop('legacy_cast',None)
        state['chapters']['Episode 2']['batches']['batch-001'].pop('cast',None)
        storage.write_json(narration.state_path('a'),state)
        self.assertIn(name,narration.prompt_text('a','Episode 10'))
        self.assertTrue(self.submit()['ok'])
        self.assertNotIn(name,narration.prompt_text('a','Episode 10'))
        archived=narration.load_state('a')['chapters']['Episode 2']['history']['batch-001'][-1]
        self.assertEqual(archived['cast'][0]['name'],name)
