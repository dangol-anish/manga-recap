from app import storage


def processing_allowed(manga_id, chapter):
    policy=storage.read_json(storage.DATA/'model-validation.json',{})
    if policy.get('panel_only_validated') is not True:return False
    manga=next((m for m in storage.read_json(storage.DATA/'library.json',[]) if m['id']==manga_id),None)
    if manga is None:return False
    return any(p.name==chapter for p in storage.chapters(manga))


def require_authorized(manga_id, chapter):
    if not processing_allowed(manga_id, chapter):
        raise PermissionError('Select a registered chapter with validated panel detection.')
