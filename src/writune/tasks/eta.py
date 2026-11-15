"""Historical median ETA with queue, batch, provider, and model pressure."""
import statistics


def _next_model(row):
    if row['status'] == 'running' and row['provider'] and row['actual_model']:
        return row['provider'] + '/' + row['actual_model']
    if row['attempt_count']:
        return row['secondary_model'] or row['primary_model']
    return row['primary_model'] or (
        row['provider'] + '/' + row['actual_model']
        if row['provider'] and row['actual_model'] else None
    )


def estimate(
    db,
    provider,
    model,
    language,
    scene,
    text,
    concurrency=1,
    load=0,
    *,
    settings=None,
    connection=None,
    task_id=None,
    batch_id=None,
    batch_concurrency=None,
    retry_count=None,
    attempt_count=0,
    attempt_in_progress=False,
):
    def observations(c):
        rows = c.execute(
            'SELECT * FROM attempts WHERE provider=? AND model=? AND language=? '
            'AND scene=? AND finished_at IS NOT NULL ORDER BY id DESC LIMIT 50',
            (provider, model, language, scene),
        ).fetchall()
        pending = c.execute(
            "SELECT t.task_id,t.batch_id,t.status,t.primary_model,t.secondary_model,"
            "t.provider,t.actual_model,t.attempt_count,b.max_concurrency "
            "FROM tasks t LEFT JOIN batches b ON b.batch_id=t.batch_id "
            "WHERE t.status IN ('queued','running')"
        ).fetchall()
        current = None
        if task_id:
            current = c.execute(
                'SELECT t.batch_id,b.max_concurrency FROM tasks t '
                'LEFT JOIN batches b ON b.batch_id=t.batch_id WHERE t.task_id=?',
                (task_id,),
            ).fetchone()
        return rows, pending, current

    if connection is not None:
        rows, pending, current = observations(connection)
    else:
        with db.connect() as c:
            rows, pending, current = observations(c)

    if current:
        batch_id = batch_id or current['batch_id']
        if batch_concurrency is None:
            batch_concurrency = current['max_concurrency']
    elif batch_id and batch_concurrency is None:
        if connection is not None:
            batch = connection.execute(
                'SELECT max_concurrency FROM batches WHERE batch_id=?', (batch_id,)
            ).fetchone()
        else:
            with db.connect() as c:
                batch = c.execute(
                    'SELECT max_concurrency FROM batches WHERE batch_id=?', (batch_id,)
                ).fetchone()
        if batch:
            batch_concurrency = batch[0]

    competitors = [r for r in pending if not task_id or r['task_id'] != task_id]
    good = [r for r in rows if r['success'] and r['generation_tps'] and r['generation_tps'] > 0]
    if not good:
        return None, 'low'

    # Character approximation is explicitly an estimate, never a byte count.
    tokens = max(1, len(text) / (1.5 if language == 'zh' else 4))
    base = statistics.median(r['first_token_latency'] or 0 for r in good) + tokens / statistics.median(
        r['generation_tps'] for r in good
    )
    failure = sum(not bool(r['success']) for r in rows) / len(rows)
    global_limit = max(1, concurrency)
    other_count = max(len(competitors), load)
    pressures = [other_count / global_limit]

    refs = [_next_model(r) for r in competitors]
    refs = [ref for ref in refs if ref]
    if settings and provider in settings.providers:
        config = settings.providers[provider]
        provider_limit = max(1, min(global_limit, config.max_concurrency))
        model_limit = max(1, min(provider_limit, config.model_concurrency.get(model, provider_limit)))
        pressures.extend((
            sum(ref.startswith(provider + '/') for ref in refs) / provider_limit,
            sum(ref == provider + '/' + model for ref in refs) / model_limit,
        ))
    if batch_id and batch_concurrency:
        batch_others = sum(r['batch_id'] == batch_id for r in competitors)
        pressures.append(batch_others / max(1, batch_concurrency))

    configured_retries = retry_count if retry_count is not None else (settings.retry_count if settings else 1)
    retries_after_this = max(0, configured_retries - attempt_count + int(attempt_in_progress))
    retry_factor = sum(failure ** attempt for attempt in range(retries_after_this + 1))
    return round(base * retry_factor * (1 + max(pressures)), 2), 'high' if len(good) >= 10 else 'medium'
