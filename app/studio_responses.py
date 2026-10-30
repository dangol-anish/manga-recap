"""Strict transport decoding and bounded handling of every completed model reply."""
import json
import re
from hashlib import sha256


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError('Duplicate JSON key: ' + key)
            result[key] = value
        return result
    def constant(value): raise ValueError('Invalid JSON constant: ' + value)
    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def decode_reply(raw):
    """Unwrap only a complete, exact transport envelope; never repair narration."""
    text = raw
    try:
        value = strict_json(text)
        if isinstance(value, dict) and set(value) == {'response'} and isinstance(value['response'], str):
            inner = value['response']
            try: value = strict_json(inner)
            except ValueError: return text
            text = inner
    except ValueError: pass
    return text


def reply_problem(text):
    """Structure only. Existing narration validation remains authoritative."""
    if not text.strip(): return {'kind':'empty', 'message':'Gemini finished without narration.'}
    try: value = strict_json(text)
    except ValueError as exc:
        if re.search(r"(?:i (?:cannot|can.t|am unable)|unable to comply|content.{0,20}(?:blocked|policy)|safety (?:policy|restriction))",text,re.I):
            return {'kind':'refusal', 'message':'Gemini returned a refusal or blocked-content message. Review the saved reply; it will not be retried automatically.'}
        return {'kind':'format', 'message':'The completed reply is not valid JSON: '+str(exc)[:240]}
    if isinstance(value, dict) and value.get('error'):
        return {'kind':'provider_error', 'message':'Gemini returned an error instead of narration. Review the saved reply; it will not be retried automatically.'}
    if not isinstance(value, dict): return {'kind':'format', 'message':'Gemini returned the wrong JSON root; narration must be an object.'}
    if not isinstance(value.get('beats'),list) or not value.get('beats'):
        return {'kind':'format', 'message':'Gemini finished but did not return a nonempty narration beats array.'}
    return None


def reply_tokens(snapshot):
    """Turn identity distinguishes identical replies in consecutive repairs."""
    return [sha256((item['id']+'\0'+item['text']).encode('utf-8')).hexdigest() for item in snapshot['replies']]


def full_repair_prompt(original, problem):
    return ('TASK: REPAIR COMPLETED RESPONSE FORMAT\n'
            'Your preceding reply did not satisfy the narration contract. '+problem+'\n'
            'Use the already attached pages and their labels. Return the complete requested narration object; '
            'do not acknowledge the instructions, return an example, add a response wrapper, or return a partial patch. '
            'Do not invent a story or change source identities to hide a mismatch. If the pages cannot be read or the request is blocked, '
            'return an explicit error explaining that; do not guess.\n\n'
            'ORIGINAL BATCH INSTRUCTIONS AND EXACT SOURCE RECEIPT:\n'+original)
