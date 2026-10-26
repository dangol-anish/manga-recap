import json
import unittest
from unittest.mock import patch
from app import story_memory as review, memory, narration_stage5 as story
from tests import test_narration_stage5 as story_tests


class MemoryReviewTests(unittest.TestCase):
    setUp=story_tests.StoryTests.setUp
    tearDown=story_tests.StoryTests.tearDown
    detect=story_tests.StoryTests.detect
    prepare=story_tests.StoryTests.prepare
    ready=story_tests.StoryTests.ready
    status=story_tests.StoryTests.status
    payload=story_tests.StoryTests.payload
    submit=story_tests.StoryTests.submit

    def setup_memory(self):
        self.ready(21)
        payload=self.payload();payload['continuity']['open_threads']=['Where is the missing brother?']
        self.assertTrue(self.submit(payload).json()['ok'])
        self.url=f'/api/story/{self.mid}/memory'
        return self.client.get(self.url).json()

    def save(self, row, data, **settings):
        return self.client.post(self.url,json={'revision':data['revision'],'id':row['id'],'pinned':False,'verified':False,'resolved':False,**settings})

    def packet(self):return story.story_context(self.mid,self.chapter,'batch-002')

    def test_read_only_overview_and_review_preserve_imports_media_and_readiness(self):
        data=self.setup_memory()
        before={p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.client.get(self.url);self.packet()
        self.assertEqual(before,{p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        fact=next(r for r in data['items'] if r['kind']=='fact')
        token=self.packet()['token']
        result=self.save(fact,data,pinned=True,verified=True)
        self.assertEqual(result.status_code,200,result.text)
        self.assertTrue(self.status()['batches'][0]['imported'])
        self.assertEqual(token,self.packet()['token'])
        for p,content in before.items():self.assertEqual(p.read_bytes(),content,str(p))
        self.assertEqual(len(review.load(self.mid)['history']),1)
        # Old prompts' structural receipts and imported media stay compatible.
        packet=self.packet();self.assertEqual(packet['pinned_memory'][0]['verification'],'user_verified')
        self.assertEqual(packet['coverage']['pinned_included'],1)
        self.assertLessEqual(len(json.dumps(packet,ensure_ascii=False)),memory.CONTEXT_LIMIT)

    def test_pin_does_not_verify_ai_claim_and_no_future_source_leaks(self):
        data=self.setup_memory();fact=next(r for r in data['items'] if r['kind']=='fact')
        self.assertEqual(self.save(fact,data,pinned=True).status_code,200)
        self.assertEqual(self.packet()['pinned_memory'][0]['verification'],'ai_claim')
        self.assertEqual(story.story_context(self.mid,self.chapter,'batch-001')['pinned_memory'],[])
        self.assertIn('NOT human-verified',self.packet()['instructions'])

    def test_resolve_reopen_and_stale_tab_conflicts(self):
        data=self.setup_memory();thread=next(r for r in data['items'] if r['kind']=='thread')
        result=self.save(thread,data,resolved=True);self.assertEqual(result.status_code,200)
        self.assertEqual(self.packet()['continuity']['open_threads'],[])
        self.assertEqual(len(self.packet()['resolved_threads']),1)
        self.assertEqual(self.save(thread,data).status_code,409)
        self.assertEqual(self.save(thread,result.json()).status_code,200)
        self.assertEqual(self.packet()['continuity']['open_threads'],['Where is the missing brother?'])

    def test_invalid_setting_and_unknown_item_do_not_write(self):
        data=self.setup_memory();thread=next(r for r in data['items'] if r['kind']=='thread')
        self.assertEqual(self.save(thread,data,verified=True).status_code,400)
        self.assertEqual(self.save({'id':'a'*64},data).status_code,409)
        self.assertFalse(review.path(self.mid).exists())

    def test_corrupt_review_file_is_reported_not_reset(self):
        self.setup_memory();p=review.path(self.mid);p.write_text('{broken')
        response=self.client.get(self.url)
        self.assertGreaterEqual(response.status_code,400)
        self.assertEqual(p.read_text(),'{broken')

    def test_pin_budget_rejects_without_modifying_saved_review(self):
        data=self.setup_memory();fact=next(r for r in data['items'] if r['kind']=='fact')
        with patch.object(review,'PIN_LIMIT',1):
            self.assertEqual(self.save(fact,data,pinned=True).status_code,400)
        self.assertFalse(review.path(self.mid).exists())

    def test_early_thread_survives_ninety_nine_later_summaries(self):
        self.setup_memory()
        history=[]
        for n in range(1,101):
            history.append({'chapter':f'Chapter {n}','batch_id':'batch-001','fingerprint':'a'*64,
                'continuity':{'summary':f'Event {n}.','open_threads':['Where is the missing brother?'] if n==1 else []},
                'memory':[],'beats':[{'narration':'Action continues.','panel_ids':['p0000-1']}], 'cast':[]})
        state={'chapters':{},'seed_cast':[]}
        with patch.object(memory,'lineage',return_value=(state,history,'token',None)):
            packet=memory.packet(self.mid,'Chapter 101','batch-001',backend=story)
        self.assertIn('Where is the missing brother?',packet['continuity']['open_threads'])
        self.assertEqual(packet['unresolved_threads'][0]['chapter'],'Chapter 1')

    def test_changed_fact_cannot_inherit_old_verification(self):
        data=self.setup_memory();fact=next(r for r in data['items'] if r['kind']=='fact')
        self.save(fact,data,pinned=True,verified=True)
        state=story.load_state(self.mid);state['chapters'][self.chapter]['batches']['batch-001']['memory'][0]['text']='A different claim.'
        from app import storage
        storage.write_json(story.state_path(self.mid),state)
        packet=self.packet()
        self.assertEqual(packet['pinned_memory'],[])
        self.assertEqual(packet['user_verified_facts'],[])

    def test_large_history_stays_bounded_and_keeps_pins(self):
        data=self.setup_memory();fact=next(r for r in data['items'] if r['kind']=='fact')
        self.save(fact,data,pinned=True,verified=True)
        state,history,token,seed=memory.lineage(self.mid,self.chapter,'batch-002',backend=story)
        for n in range(2,102):
            history.append({'chapter':f'Chapter {n}','batch_id':'batch-001','fingerprint':'b'*64,
                'continuity':{'summary':'S'*1200,'open_threads':[f'Question {n} '+ 'q'*100]},
                'memory':[{'text':f'Claim {n}-{i} '+ 'x'*360,'status':'confirmed','panel_ids':['p0000-1']} for i in range(30)],
                'beats':[{'narration':'Action continues.','panel_ids':['p0000-1']}], 'cast':[]})
        with patch.object(memory,'lineage',return_value=(state,history,token,seed)):
            packet=memory.packet(self.mid,'Chapter 102','batch-001',backend=story)
        self.assertLessEqual(len(json.dumps(packet,ensure_ascii=False)),memory.CONTEXT_LIMIT)
        self.assertEqual(packet['pinned_memory'][0]['text'],fact['text'])
        self.assertGreater(packet['coverage']['unresolved_saved'],packet['coverage']['unresolved_included'])
