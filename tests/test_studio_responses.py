"""Only verifiably complete output leaves the wait loop; no live calls."""
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
    return {'protocol':4,'url':'https://aistudio.google.com/prompts/fixture','status_text':'',
            'replies':[{'id':ident,'text':text,'completed':finished}],'finished':finished,'generating':generating}


class ResponseTests(unittest.TestCase):
    def test_every_completed_output_is_returned_for_validation(self):
        values=['I cannot assist with this request.', '[]','null','42','true','"hello"',
                '{"beats":"wrong"}', '{"error":"Images unreadable"}']
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
        old=snapshot('{"beats":[]}',ident='model-1');new=snapshot('{"beats":[]}',ident='model-2')
        browser=extension.ExtensionBrowser()
        with patch.object(browser,'snapshot',side_effect=[snapshot('{',False,True),new,new,new]):
            self.assertEqual(browser.receive(reply_tokens(old),Cancel(),lambda url:None,timeout=10),'{"beats":[]}')

    def test_missing_new_reply_pauses_instead_of_importing_prompt_or_old_reply(self):
        old=snapshot('{"beats":[{"narration":"old"}]}')
        browser=extension.ExtensionBrowser()
        with patch.object(browser,'snapshot',return_value=old),patch.object(extension.time,'monotonic',side_effect=[0,1,101]):
            with self.assertRaises(Attention) as error:browser.receive(reply_tokens(old),Cancel(),lambda url:None,timeout=100)
        self.assertEqual(error.exception.code,'timeout')

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

    def test_unrecognized_idle_page_waits_for_the_full_deadline(self):
        browser=extension.ExtensionBrowser()
        with patch.object(browser,'snapshot',return_value=snapshot('',False,False)),patch.object(extension.time,'monotonic',side_effect=[0,1,140,301]):
            with self.assertRaises(Attention) as error:browser.receive([],Cancel(),lambda url:None,timeout=300)
        self.assertEqual(error.exception.code,'timeout')

    def test_empty_and_incomplete_output_never_become_rejected_replies(self):
        for raw in ('', '{', '{"beats":[', '{"response":""}', 'ok', '{"beats":[NaN]}'):
            browser=extension.ExtensionBrowser()
            with self.subTest(raw=raw),patch.object(browser,'snapshot',return_value=snapshot(raw)),patch.object(extension.time,'monotonic',side_effect=[0,1,599,601]),patch.object(extension,'command') as command:
                with self.assertRaises(Attention) as error:browser.receive([],Cancel(),lambda url:None)
                self.assertEqual(error.exception.code,'timeout')
                self.assertFalse(hasattr(browser,'last_capture'));command.assert_not_called()

    def test_false_empty_completion_and_partial_json_wait_for_actual_reply(self):
        browser=extension.ExtensionBrowser();done=snapshot('{"beats":[1]}')
        frames=[snapshot('')]*5+[snapshot('{"beats":[')]*5+[snapshot('{"beats":[1]}',True,True)]*5+[done]*3
        with patch.object(browser,'snapshot',side_effect=frames),patch.object(extension.time,'monotonic',side_effect=range(0,600,20)):
            self.assertEqual(browser.receive([],Cancel(),lambda url:None),'{"beats":[1]}')

    def test_latest_turn_must_confirm_completion(self):
        browser=extension.ExtensionBrowser();unconfirmed=snapshot('{"beats":[1]}');unconfirmed['replies'][0]['completed']=False
        with patch.object(browser,'snapshot',return_value=unconfirmed),patch.object(extension.time,'monotonic',side_effect=[0,1,601]):
            with self.assertRaises(Attention):browser.receive([],Cancel(),lambda url:None)
        self.assertFalse(hasattr(browser,'last_capture'))

    def test_resume_does_not_reload_same_chat_while_generating(self):
        browser=extension.ExtensionBrowser();current=snapshot('{',False,True)
        with patch.object(browser,'snapshot',return_value=current),patch.object(extension,'command') as command:
            browser.recover_page(current['url'])
        command.assert_not_called()


if __name__=='__main__':unittest.main()
