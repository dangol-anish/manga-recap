"""Every completed output must leave the wait loop; no browser or model calls."""
import json
import unittest
from unittest.mock import patch
from app import narration_extension as extension
from app.studio_responses import decode_reply,reply_problem,reply_tokens
from app.studio_browser import Attention


class Cancel:
    def is_set(self):return False
    def wait(self,*args):pass


def snapshot(text,finished=True,generating=False,ident='model-1'):
    return {'protocol':3,'url':'https://aistudio.google.com/prompts/fixture','status_text':'',
            'replies':[{'id':ident,'text':text}],'finished':finished,'generating':generating}


class ResponseTests(unittest.TestCase):
    def test_every_completed_output_is_returned_for_validation(self):
        values=['','ok','Here is your answer.','I cannot assist with this request.',
                '{broken','{"beats":[','[]','null','42','true','"hello"',
                '{"response":""}','{"response":"ok"}','{"beats":"wrong"}',
                '{"error":"Images unreadable"}','{"beats":[],"beats":[1]}','{"beats":[NaN]}']
        for raw in values:
            with self.subTest(raw=raw):
                browser=extension.ExtensionBrowser()
                with patch.object(browser,'snapshot',return_value=snapshot(raw)):
                    value=browser.receive([],Cancel(),lambda url:None,timeout=10)
                self.assertEqual(value,decode_reply(raw));self.assertEqual(browser.last_capture['raw'],raw)
                self.assertIsNotNone(reply_problem(value))

    def test_only_exact_valid_wrapper_is_unwrapped(self):
        raw='{"beats":[{"narration":"He says \\"hello\\"."}]}'
        raw=json.dumps({'beats':[{'narration':'He says "hello".'}]})
        self.assertEqual(decode_reply(json.dumps({'response':raw})),raw)
        duplicate='{"response":"first","response":'+json.dumps(raw)+'}'
        self.assertEqual(decode_reply(duplicate),duplicate)
        self.assertEqual(decode_reply('```json\n'+raw+'\n```'),'```json\n'+raw+'\n```')

    def test_streaming_output_is_not_returned_and_identical_new_turn_is_detected(self):
        old=snapshot('ok',ident='model-1');new=snapshot('ok',ident='model-2')
        browser=extension.ExtensionBrowser()
        with patch.object(browser,'snapshot',side_effect=[snapshot('{',False,True),new,new,new]):
            self.assertEqual(browser.receive(reply_tokens(old),Cancel(),lambda url:None,timeout=10),'ok')

    def test_missing_new_reply_pauses_instead_of_importing_prompt_or_old_reply(self):
        old=snapshot('{"beats":[{"narration":"old"}]}')
        browser=extension.ExtensionBrowser()
        with patch.object(browser,'snapshot',return_value=old),patch.object(extension.time,'monotonic',side_effect=[0,1,2,20,21]):
            with self.assertRaises(Attention) as error:browser.receive(reply_tokens(old),Cancel(),lambda url:None,timeout=100)
        self.assertEqual(error.exception.code,'response_read')

    def test_unsupported_extension_protocol_never_uses_whole_page_text(self):
        browser=extension.ExtensionBrowser()
        with patch.object(extension,'command',return_value={'text':'{"beats":[]}', 'finished':True}):
            with self.assertRaises(Attention) as error:browser.snapshot()
        self.assertEqual(error.exception.code,'extension_update')

    def test_delayed_page_indicators_get_time_to_show_completed_response(self):
        browser=extension.ExtensionBrowser();done=snapshot('{"beats":[]}')
        with patch.object(browser,'snapshot',side_effect=[snapshot('',False,False)]*2+[done]*3),patch.object(extension.time,'monotonic',side_effect=range(0,1000,30)):
            self.assertEqual(browser.receive([],Cancel(),lambda url:None,timeout=500),'{"beats":[]}')

    def test_first_chat_recovery_reads_existing_tab_without_navigation_or_send(self):
        browser=extension.ExtensionBrowser();done=snapshot('{"beats":[]}');done['url']='https://aistudio.google.com/prompts/saved-conversation'
        with patch.object(browser,'snapshot',return_value=done),patch.object(extension,'command',side_effect=AssertionError('Do not navigate or send')):
            browser.recover_page('https://aistudio.google.com/prompts/new_chat')
        with patch.object(browser,'snapshot',return_value={**done,'url':'https://evil.invalid/prompts/x'}):
            with self.assertRaises(Attention):browser.recover_page('https://aistudio.google.com/prompts/new_chat')

    def test_unrecognized_idle_page_does_not_wait_for_the_generation_timeout(self):
        browser=extension.ExtensionBrowser()
        with patch.object(browser,'snapshot',return_value=snapshot('',False,False)),patch.object(extension.time,'monotonic',side_effect=[0,1,2,3,140,141,142]):
            with self.assertRaises(Attention) as error:browser.receive([],Cancel(),lambda url:None,timeout=300)
        self.assertEqual(error.exception.code,'response_read')


if __name__=='__main__':unittest.main()
