from io import BytesIO
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
from PIL import Image
from app import narration_stage5 as story, panel_preparation as panels, storage, detection
from tests import test_panel_preparation as preparation_tests


class StoryTests(unittest.TestCase):
    setUp=preparation_tests.PreparationTests.setUp
    tearDown=preparation_tests.PreparationTests.tearDown
    detect=preparation_tests.PreparationTests.detect
    prepare=preparation_tests.PreparationTests.prepare

    def ready(self,count=2):
        for i in range(2,count):shutil.copyfile(self.source/'0000.png',self.source/f'{i:04d}.png')
        if count>2:self.detect()
        info,records=self.prepare();self.mid=info['manga_id'];self.chapter=info['name']
        self.api=f'/api/story/{self.mid}/{self.chapter}'
        prompts=self.root/'prompts';prompts.mkdir(exist_ok=True)
        master=Path(__file__).resolve().parents[1]/'prompts/narration_master.md'
        (prompts/master.name).write_text(master.read_text(encoding='utf-8'), encoding='utf-8')
        return records

    def status(self):return story.status(self.mid,self.chapter)

    def payload(self,batch_index=0):
        state=self.status();b=state['batches'][batch_index];previous=b['previous_context'];pid=b['panel_ids'][0]
        return {'batch_receipt':story.receipt(self.mid,self.chapter,b,previous),'cast':[],
                'beats':[{'panel_ids':[pid],'narration':'A new moment begins.','unsure':'','anchors':[{'panel_id':pid,'phrase':'A new moment'}]}],
                'continuity':{'summary':'The action continues.','open_threads':[]},'chapter_summary':'The action so far.',
                'memory':[{'text':'A moment occurred.','status':'confirmed','panel_ids':[pid]}]}

    def submit(self,payload=None,batch_index=0,**kwargs):
        state=self.status();b=state['batches'][batch_index]
        body={'reply':json.dumps(payload if payload is not None else self.payload(batch_index)),
              'fingerprint':b['fingerprint'],'context_token':b['context_token'],'revision':state['revision'],**kwargs}
        return self.client.post(self.api+'/'+b['id']+'/import',json=body)

    def export(self,batch):
        with patch('app.runtime.open_system_folder') as opened:
            response=self.client.post(self.api+'/'+batch['id']+'/export',json={'fingerprint':batch['fingerprint']})
            if response.status_code==200:opened.assert_called_once()
            else:opened.assert_not_called()
            return response

    def test_export_has_twenty_then_remainder_in_page_order(self):
        self.ready(21);state=self.status();self.assertEqual([len(b['pages']) for b in state['batches']],[20,1])
        for i,b in enumerate(state['batches']):
            response=self.export(b);self.assertEqual(response.status_code,200,response.text)
            folder=storage.ROOT/response.json()['folder']
            expected=[f'p{n:04d}.png' for n in range(i*20,min(i*20+20,21))]
            self.assertEqual(sorted(p.name for p in folder.iterdir()),expected)
            self.assertEqual(response.json()['photos'],len(expected))
            with Image.open(folder/expected[0]) as image:self.assertEqual(image.size,(100,150))
            photo=self.client.get(self.api+'/'+b['id']+'/photos/'+Path(expected[0]).stem,params={'fingerprint':b['fingerprint']})
            self.assertEqual(photo.status_code,200)
            self.assertEqual(photo.content,(folder/expected[0]).read_bytes())
            self.assertEqual(self.export(b).status_code,200)
        self.assertFalse(story.state_path(self.mid).exists())
        self.assertEqual(list(story.root(self.mid,self.chapter).rglob('*.zip')),[])

    def test_prompt_and_export_do_not_run_models_or_modify_prepared_assets(self):
        records=self.ready();before=[r['revision'] for r in records]
        with patch('app.detector.Detector',side_effect=AssertionError('No inference')),patch('app.panel_preparation.generate',side_effect=AssertionError('No preparation')):
            b=self.status()['batches'][0];response=self.client.get(self.api+'/batch-001/prompt')
            self.assertEqual(response.status_code,200);text=response.text
            self.assertIn('VALID_PANEL_IDS',text);self.assertIn('p0000-1',text);self.assertIn('FINAL_RECAP: false',text)
            self.assertNotIn('Content-Disposition',response.headers);self.assertIn('BATCH_RECEIPT',text);self.assertEqual(self.export(b).status_code,200)
        info,files,_=panels.context(self.ident)
        self.assertEqual([panels.safe_record(panels.record_path(info,panels.page_ids(files)[p.name]))['revision'] for p in files],before)

    def test_later_prompt_locked_until_previous_import(self):
        self.ready(21);self.assertFalse(self.status()['batches'][1]['ready'])
        self.assertEqual(self.client.get(self.api+'/batch-002/prompt').status_code,409)
        payload=self.payload();payload['cast']=[{'name':'A recurring figure','gender':'','age_group':'','role':'','look':''}]
        self.assertEqual(self.submit(payload).status_code,200)
        b=self.status()['batches'][1];self.assertTrue(b['ready']);context=b['previous_context']
        self.assertEqual(context['previous_narration_ending'],['A new moment begins.'])
        self.assertEqual(context['cast'][0]['name'],'A recurring figure')
        self.assertEqual(context['confirmed_facts'][0]['panel_ids'],['p0000-1'])
        self.assertEqual(context['confirmed_facts'][0]['batch_id'],'batch-001')
        self.assertIn('A recurring figure',self.client.get(self.api+'/batch-002/prompt').text)
        self.assertEqual(self.submit(batch_index=1).status_code,200)
        self.assertEqual(sum(b['imported'] for b in self.status()['batches']),2)

    def test_status_hashes_each_page_once_and_import_still_rechecks_sources(self):
        from app import read_cache
        self.ready(21)
        payload=self.payload()
        read_cache._cached_hash.cache_clear()
        with patch.object(read_cache,'_hash_file',wraps=read_cache._hash_file) as hashes:
            first=self.client.get(self.api)
            self.assertEqual(first.status_code,200)
            self.assertEqual(hashes.call_count,21)
            self.assertEqual(self.client.get(self.api).json(),first.json())
            self.assertEqual(hashes.call_count,21)
            batch=first.json()['batches'][0]
            response=self.client.post(self.api+'/batch-001/import',json={
                'reply':json.dumps(payload),'fingerprint':batch['fingerprint'],
                'context_token':batch['context_token'],'revision':first.json()['revision']})
            self.assertEqual(response.status_code,200,response.text)
            self.assertGreater(hashes.call_count,21)
        # A changed upstream page is caught by the next GET, not hidden by cache.
        Image.new('RGB',(100,150),'black').save(self.source/'0000.png')
        self.assertNotEqual(self.client.get(self.api).status_code,200)

    def test_unknown_repeated_order_and_word_budget_errors_are_exact(self):
        self.ready();b=self.status()['batches'][0]
        for ids,text,expected in [(['p9999-1'],'One event.','unknown ID'),([b['panel_ids'][0]]*2,'One event.','Repeated ID'),(list(reversed(b['panel_ids'][:2])),'One event.','reading order'),([b['panel_ids'][0]],'word '*41,'word budget')]:
            payload=self.payload();payload['beats']=[{'panel_ids':ids,'narration':text,'unsure':''}]
            result=self.submit(payload);self.assertEqual(result.status_code,422)
            errors=json.dumps(result.json());self.assertIn(expected,errors);self.assertTrue(result.json()['issues'])
            self.assertFalse(story.state_path(self.mid).exists())

    def test_invalid_reply_never_changes_saved_narration(self):
        self.ready();self.assertEqual(self.submit().status_code,200);before=story.state_path(self.mid).read_bytes()
        for reply in ['{broken','{"cast":[],"cast":[]}','{"beats":NaN}']:
            result=self.submit(reply=reply);self.assertEqual(result.status_code,422)
            self.assertEqual(story.state_path(self.mid).read_bytes(),before)

    def test_wrong_receipt_and_stale_concurrent_state_rejected(self):
        self.ready();payload=self.payload();payload['batch_receipt']['chapter']='Another chapter'
        result=self.submit(payload);self.assertEqual(result.status_code,422);self.assertFalse(result.json()['can_correct'])
        payload=self.payload();old=self.status();self.assertEqual(self.submit(payload).status_code,200)
        result=self.submit(payload,revision=old['revision']);self.assertEqual(result.status_code,409)

    def test_missing_anchors_and_memory_metadata_fail(self):
        self.ready();payload=self.payload();del payload['beats'][0]['anchors'];del payload['memory'];payload['chapter_summary']='x'*1201
        result=self.submit(payload);self.assertEqual(result.status_code,422)
        self.assertIn('anchors',str(result.json()));self.assertIn('chapter_summary',str(result.json()));self.assertIn('memory',str(result.json()))

    def test_targeted_corrections_keep_unaffected_beats(self):
        self.ready();payload=self.payload();b=self.status()['batches'][0]
        payload['beats'].append({'panel_ids':['not-an-id'],'narration':'Another moment.','unsure':'','anchors':[{'panel_id':'not-an-id','phrase':'Another moment'}]})
        result=self.submit(payload);self.assertEqual(result.status_code,422);self.assertEqual(result.json()['fix_beats'],[2])
        correction={'beats':[{'replace_beat':2,'panel_ids':[b['panel_ids'][1]],'narration':'Another moment.','unsure':'','anchors':[{'panel_id':b['panel_ids'][1],'phrase':'Another moment'}]}]}
        result=self.submit(correction,corrections_only=True);self.assertEqual(result.status_code,200,result.text)
        saved=story.load_state(self.mid)['chapters'][self.chapter]['batches']['batch-001']
        self.assertEqual(saved['beats'][0]['narration'],payload['beats'][0]['narration']);self.assertEqual(len(saved['beats']),2)

    def test_reimport_invalidates_later_context_and_preserves_history(self):
        self.ready(21);self.assertEqual(self.submit().status_code,200);later_payload=self.payload(1)
        self.assertEqual(self.submit(later_payload,1).status_code,200)
        earlier=self.payload();earlier['continuity']['summary']='An updated established fact.'
        self.assertEqual(self.submit(earlier).status_code,200)
        state=self.status();self.assertTrue(state['batches'][1]['stale']);self.assertFalse(state['batches'][1]['imported'])
        result=self.submit(later_payload,1);self.assertEqual(result.status_code,409)
        saved=story.load_state(self.mid);self.assertEqual(len(saved['chapters'][self.chapter]['history']['batch-001']),1)
        self.assertEqual(len(saved['chapters'][self.chapter]['beats']),1)
        # Earlier prompts must never contain later chapter/batch facts.
        self.assertEqual(self.status()['batches'][0]['previous_context']['coverage']['saved_batches'],0)

    def test_panel_revision_invalidates_exports_and_imports(self):
        records=self.ready();b=self.status()['batches'][0];self.assertEqual(self.submit().status_code,200)
        response=self.client.post(f'/api/preparation/chapters/{self.ident}/pages/p0000',json={'revision':records[0]['revision'],'boxes':[[0,0,100,150]]})
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.export(b).status_code,409)
        self.assertTrue(self.status()['batches'][0]['stale'])

    def test_changed_source_and_missing_assets_block_export(self):
        records=self.ready();b=self.status()['batches'][0];info,_,_=panels.context(self.ident)
        (panels.record_path(info,'p0000').parent/records[0]['revision']/'labeled.png').unlink()
        self.assertEqual(self.export(b).status_code,400)
        self.prepare();Image.new('RGB',(100,150),'black').save(self.source/'0000.png')
        self.assertEqual(self.client.get(self.api).status_code,400)

    def test_cross_chapter_memory_is_preceding_only(self):
        self.ready();first_api=self.api;mid=self.mid;self.assertEqual(self.submit().status_code,200)
        old=self.source;self.source=old.parent/'Chapter 2';shutil.copytree(old,self.source)
        self.ident=next(c['id'] for c in detection.catalog()['manga'][0]['chapters'] if c['name']=='Chapter 2')
        self.detect();self.ready();self.assertEqual(self.mid,mid)
        previous=self.status()['batches'][0]['previous_context'];self.assertEqual(previous['preceding_batches'],['Chapter 1 / batch-001'])
        payload=self.payload();payload['continuity']['summary']='Later chapter fact';self.assertEqual(self.submit(payload).status_code,200)
        earlier=self.client.get(first_api).json()['batches'][0]['previous_context']
        self.assertNotIn('Later chapter fact',json.dumps(earlier));self.assertEqual(earlier['coverage']['saved_batches'],0)

    def test_saved_seed_cast_is_included_but_legacy_future_cast_is_not(self):
        self.ready();storage.write_json(storage.DATA/'manga'/self.mid/'cast.json',[{'name':'Seed figure'}])
        storage.write_json(storage.DATA/'manga'/self.mid/'narration.json',{'cast':[{'name':'Future identity'}],'chapters':{}})
        context=self.status()['batches'][0]['previous_context'];self.assertEqual(context['cast'],[{'name':'Seed figure'}]);self.assertNotIn('Future identity',json.dumps(context))
        self.assertEqual(self.submit().status_code,200)
        self.assertEqual(self.status()['batches'][0]['previous_context']['cast'],[{'name':'Seed figure'}])

    def test_uncertainty_and_context_limit_preserved(self):
        self.ready(21);payload=self.payload();payload['beats'][0]['unsure']='Identity uncertain.'
        self.assertEqual(self.submit(payload).status_code,200)
        previous=self.status()['batches'][1]['previous_context']
        self.assertEqual(previous['confirmed_facts'],[]);self.assertTrue(previous['uncertain_claims'])
        self.assertLessEqual(len(json.dumps(previous,ensure_ascii=False)),18000)

    def test_failed_photo_export_is_not_published(self):
        self.ready();b=self.status()['batches'][0]
        with patch.object(story.shutil,'copyfile',side_effect=OSError('disk full')):
            self.assertEqual(self.export(b).status_code,400)
        self.assertEqual(list((story.root(self.mid,self.chapter)/'exports').iterdir()),[])

    def test_final_choice_and_path_validation(self):
        self.ready();self.assertIn('FINAL_RECAP: true',self.client.get(self.api+'/batch-001/prompt?final_recap=true').text)
        self.assertEqual(self.submit(final_recap=True).status_code,200)
        self.assertTrue(self.status()['batches'][0]['final_recap'])
        self.assertEqual(self.client.get('/api/story/not-a-manga/Chapter 1').status_code,400)

    def test_active_processing_blocks_export_import(self):
        self.ready();b=self.status()['batches'][0];panels.active={'id':'fixture'}
        try:
            self.assertEqual(self.export(b).status_code,409)
            self.assertEqual(self.submit().status_code,409)
        finally:panels.active=None

    def test_modified_export_is_not_silently_reused_or_overwritten(self):
        self.ready();b=self.status()['batches'][0];result=self.export(b)
        folder=storage.ROOT/result.json()['folder'];photo=folder/'p0000.png';photo.write_bytes(b'changed')
        self.assertEqual(self.export(b).status_code,400)
        self.assertEqual(photo.read_bytes(),b'changed')
        self.assertEqual(self.client.get(self.api+'/batch-001/photos/p9999',params={'fingerprint':b['fingerprint']}).status_code,400)

    def test_master_schema_example_passes_anchor_and_memory_validation(self):
        from app.narration import validate_payload
        from app.memory import normalize
        master=(Path(__file__).resolve().parents[1]/'prompts/narration_master.md').read_text(encoding='utf-8')
        example=json.loads(next(line for line in master.splitlines() if line.startswith('{"batch_receipt"')))
        expected=['p0000-1','p0000-2','p0000-3']
        validation,beats,_,_=validate_payload(example,expected)
        self.assertTrue(validation['ok'],validation)
        self.assertEqual([a['panel_id'] for a in beats[0]['anchors']],beats[0]['panel_ids'])
        self.assertEqual(len(normalize(example['memory'],expected)),1)
        self.assertNotIn('Return this format, with one or more beats:',master)

    def test_correction_template_prefills_original_numbers_and_frozen_neighbors(self):
        from app.narration import redo_message
        payload={'beats':[{'panel_ids':[f'p000{i}-1'],'narration':'A moment.','unsure':'','anchors':[]} for i in range(4)]}
        message=redo_message({'issues':{},'errors':['missing anchors']*2,'fix_beats':[2,4]},payload,['p0000-1','p0001-1','p0002-1','p0003-1'])
        lines=message.splitlines()
        template=json.loads(lines[next(i for i,line in enumerate(lines) if line.startswith('REPLACEMENT_TEMPLATE'))+1])
        self.assertEqual([b['replace_beat'] for b in template['beats']],[2,4])
        self.assertEqual(template['beats'][0]['narration'],'A moment.')
        frozen=json.loads(lines[next(i for i,line in enumerate(lines) if line.startswith('FROZEN_UNAFFECTED_BEATS'))+1])
        self.assertEqual([b['original_beat'] for b in frozen],[1,3])
        self.assertEqual(message.count('missing anchors'),1)
        self.assertIn('JSON INTEGER',message)
        self.assertIn('Do not return the entire original beats array',message)

    def test_generated_correction_template_can_be_repaired_and_imported(self):
        self.ready();payload=self.payload();pid=self.status()['batches'][0]['panel_ids'][1]
        payload['beats'].append({'panel_ids':[pid],'narration':'Another moment follows.','unsure':'','anchors':[]})
        response=self.submit(payload);self.assertEqual(response.status_code,422)
        lines=response.json()['redo_message'].splitlines()
        template=json.loads(lines[next(i for i,line in enumerate(lines) if line.startswith('REPLACEMENT_TEMPLATE'))+1])
        self.assertEqual([b['replace_beat'] for b in template['beats']],[2])
        template['beats'][0]['anchors']=[{'panel_id':pid,'phrase':'Another moment follows'}]
        fixed=self.submit(template,corrections_only=True)
        self.assertEqual(fixed.status_code,200,fixed.text)
        saved=story.load_state(self.mid)['chapters'][self.chapter]['batches']['batch-001']
        self.assertEqual(saved['beats'][0]['narration'],payload['beats'][0]['narration'])
        self.assertEqual(len(saved['beats']),2)

    def test_full_reply_with_corrections_checked_validates_the_new_reply(self):
        self.ready();bad=self.payload();bad['beats'][0]['panel_ids']=['not-an-id']
        self.assertEqual(self.submit(bad).status_code,422)
        fixed=self.payload();response=self.submit(fixed,corrections_only=True)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['reply_mode'],'full')
        saved=story.load_state(self.mid)['chapters'][self.chapter]['batches']['batch-001']
        self.assertEqual(saved['beats'][0]['panel_ids'],fixed['beats'][0]['panel_ids'])

    def test_malformed_patch_shows_one_format_error_without_old_panel_errors(self):
        self.ready();bad=self.payload();bad['beats'][0]['panel_ids']=['not-an-id']
        self.assertEqual(self.submit(bad).status_code,422)
        path=story.pending_path(self.mid,'batch-001',self.chapter);before=path.read_bytes()
        response=self.submit({'beats':[{'panel_ids':['p0000-1'],'narration':'A moment.'}]*12},corrections_only=True)
        self.assertEqual(response.status_code,422)
        result=response.json();self.assertEqual(len(result['errors']),1);self.assertEqual(result['issues'],{})
        self.assertIn('replace_beat',result['errors'][0]);self.assertEqual(path.read_bytes(),before)
        self.assertFalse(story.state_path(self.mid).exists())

    def test_full_reply_detection_keeps_receipt_and_order_validation(self):
        self.ready();self.assertEqual(self.submit({'beats':[]}).status_code,422)
        payload=self.payload();payload['batch_receipt']['context_token']='f'*64
        self.assertEqual(self.submit(payload,corrections_only=True).status_code,409)
        self.assertFalse(story.state_path(self.mid).exists())
        payload=self.payload();ids=self.status()['batches'][0]['panel_ids']
        payload['beats'][0]['panel_ids']=[ids[1],ids[0]]
        response=self.submit(payload,corrections_only=True)
        self.assertEqual(response.status_code,422)
        self.assertTrue(response.json()['issues']);self.assertIn('reading order',str(response.json()))
        self.assertEqual(response.json()['reply_mode'],'full')
        self.assertFalse(story.state_path(self.mid).exists())
