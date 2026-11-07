"""Read-only bounded category weights from latest real publication analytics."""
from pathlib import Path
import sqlite3
from app.services.feedback_service import FeedbackService


def category_weights(db_path):
    path=Path(db_path).absolute()
    if not path.exists():return {}
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as c:
        c.row_factory=sqlite3.Row
        names={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'analytics_snapshots','publications','daily_plans'}<=names:return {}
        rows=[dict(r) for r in c.execute('''SELECT json_extract(d.recipe,'$.topic.category') AS category,a.views,a.likes,a.comments
            FROM analytics_snapshots a JOIN publications p ON p.publication_id=a.publication_id JOIN daily_plans d ON d.job_id=p.job_id
            WHERE p.status='published' AND a.snapshot_id=(SELECT MAX(b.snapshot_id) FROM analytics_snapshots b WHERE b.publication_id=a.publication_id)''')]
    ranked=[r for r in FeedbackService().rank_categories(rows) if r['video_count']>=3]
    if not ranked:return {}
    average=sum(r['average_score'] for r in ranked)/len(ranked)
    return {r['category']:round(max(.5,min(2,r['average_score']/max(average,.01))),4) for r in ranked}
