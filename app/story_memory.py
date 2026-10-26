"""User review overlays; never rewrite imported narration or generated media."""
import json
import re
from app import storage
from app.memory import digest, ordered

PIN_LIMIT = 3500


def path(mid):
    if not re.fullmatch(r'[a-f0-9]{32}', mid):
        raise ValueError('Invalid manga ID for story memory.')
    target = storage.DATA / 'manga' / mid / 'story-memory-review.json'
    if target.is_symlink() or target.parent.is_symlink() or not target.resolve().is_relative_to(storage.DATA.resolve()):
        raise ValueError('Story memory review cannot be a symbolic link.')
    return target


def load(mid):
    value = storage.read_json(path(mid), {'schema_version': 1, 'reviews': {}, 'history': []})
    if not isinstance(value, dict) or value.get('schema_version') != 1 or not isinstance(value.get('reviews'), dict) or not isinstance(value.get('history'), list):
        raise ValueError('Saved story memory reviews are damaged. Restore the review file from backup; narration and media are unchanged.')
    for review in value['reviews'].values():
        if not isinstance(review, dict) or set(review) != {'pinned','verified','resolved'} or any(type(v) is not bool for v in review.values()):
            raise ValueError('Saved story memory review has invalid settings. Restore it from backup; original imports are unchanged.')
    return value


def records(history):
    result = []; threads = {}; seen = set()
    for entry in history:
        source = {'chapter': entry['chapter'], 'batch_id': entry['batch_id'], 'fingerprint': entry.get('fingerprint', '')}
        for fact in entry.get('memory', []):
            row = dict(source, kind='fact', text=fact['text'], panel_ids=fact['panel_ids'], ai_status=fact['status'])
            row['id'] = digest(row)
            if row['id'] not in seen: result.append(row); seen.add(row['id'])
        for text in entry.get('continuity', {}).get('open_threads', []):
            # Repeated mentions retain the original question and its review state.
            key = ' '.join(text.casefold().split())
            if key not in threads:
                row = dict(source, kind='thread', text=text, panel_ids=[], ai_status='uncertain')
                row['id'] = digest(row); threads[key] = row; result.append(row)
    return result


def decorate(rows, reviews):
    return [dict(row, **reviews.get(row['id'], {'pinned':False,'verified':False,'resolved':False})) for row in rows]


def overview(mid):
    from app import narration_stage5 as story
    state = story.load_state(mid); history = []; current = set()
    prior = None; broken = False
    for chapter in ordered(state):
        saved_batches = state['chapters'][chapter].get('batches', {})
        history.extend(dict(entry, chapter=chapter, batch_id=bid) for bid,entry in sorted(saved_batches.items()))
        preceding = []
        try:
            for batch in story.batches(mid,chapter):
                token = digest({'earlier_chapters':prior,'batches':preceding}) if prior else digest(preceding)
                entry = saved_batches.get(batch['id'])
                if broken or not entry or entry.get('format')!='beats-v1' or entry.get('context_token')!=token or entry.get('fingerprint')!=batch['fingerprint']:
                    broken = True; break
                current.add((chapter,batch['id']))
                preceding.append(dict(entry,id=batch['id']))
            prior = digest([prior,{'chapter':chapter,'batches':preceding}])
        except (ValueError,OSError): broken = True
    saved = load(mid)
    rows = decorate(records(history), saved['reviews'])
    for row in rows: row['current'] = (row['chapter'],row['batch_id']) in current
    return {'revision': digest([state,saved]), 'items': rows, 'pin_limit': PIN_LIMIT}


def update(mid, revision, ident, pinned, verified, resolved):
    current = overview(mid)
    if revision != current['revision']:
        raise FileExistsError('Story memory changed in another tab. Reload before saving; no changes were made.')
    row = next((r for r in current['items'] if r['id'] == ident), None)
    if not row or not row['current']:
        raise FileExistsError('This fact or thread no longer has current source narration. Refresh its batch first; your saved work is unchanged.')
    if row['kind'] == 'fact' and resolved or row['kind'] == 'thread' and verified:
        raise ValueError('Only facts can be verified; only threads can be resolved.')
    settings = {'pinned':pinned,'verified':verified,'resolved':resolved}
    saved = load(mid)
    previous = saved['reviews'].get(ident, {'pinned':False,'verified':False,'resolved':False})
    if previous == settings: return current
    saved['reviews'][ident] = settings
    pinned_rows = [dict(r, **(settings if r['id'] == ident else {})) for r in current['items']]
    pinned_rows = [{k:v for k,v in r.items() if k != 'current'} for r in pinned_rows if r['pinned'] and not r['resolved'] and r['current']]
    if len(json.dumps(pinned_rows,ensure_ascii=False)) > PIN_LIMIT:
        raise ValueError('Pinned memory is full. Unpin an item before pinning another so every pin can fit future prompts. Nothing was changed.')
    from datetime import datetime, timezone
    saved['history'].append({'id':ident,'before':previous,'after':settings,'at':datetime.now(timezone.utc).isoformat()})
    storage.write_json(path(mid), saved)
    return overview(mid)


def context(mid, history):
    rows = decorate(records(history), load(mid)['reviews'])
    def clean(row):
        return {**row, 'verification':'user_verified' if row['verified'] else 'ai_claim'}
    return [clean(r) for r in rows]
