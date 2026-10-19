from app import storage


def processing_allowed(manga_id, chapter):
    policy = storage.read_json(storage.DATA / 'model-validation.json', {})
    return (policy.get('panel_only_validated') is True
            and policy.get('allowed_manga_id') == manga_id
            and chapter in policy.get('allowed_chapters', []))


def require_authorized(manga_id, chapter):
    if not processing_allowed(manga_id, chapter):
        raise PermissionError('Processing is approved only for this manga’s Chapter 1 and Chapter 12.')
