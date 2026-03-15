"""
jobs/store.py
In-memory job store for background calculations.
Good enough for single-user MVP.
Replace with Redis when scaling to multiple concurrent users.

To upgrade to Redis later:
  pip install redis
  Change _jobs dict to redis client calls
  That's it — interface stays the same
"""

import uuid
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("capgainsiq.jobs")

# In-memory store — replace with Redis for production
_jobs: dict[str, dict] = {}


def create_job() -> str:
    """Create a new job and return its ID."""
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "status":   "running",
        "result":   None,
        "error":    None,
        "progress": "Queued...",
        "created":  datetime.now().isoformat(),
        "tool":     None,
    }
    logger.info(f"Job created: {job_id[:8]}")
    return job_id


def set_progress(job_id: str, msg: str):
    if job_id in _jobs:
        _jobs[job_id]["progress"] = msg


def set_done(job_id: str, result: dict):
    if job_id in _jobs:
        _jobs[job_id].update({
            "status":   "done",
            "result":   result,
            "progress": "Complete",
        })
        logger.info(f"Job done: {job_id[:8]}")


def set_error(job_id: str, error: str):
    if job_id in _jobs:
        _jobs[job_id].update({
            "status": "error",
            "error":  error,
        })
        logger.error(f"Job failed: {job_id[:8]} — {error}")


def get_job(job_id: str) -> Optional[dict]:
    return _jobs.get(job_id)


def delete_job(job_id: str) -> bool:
    if job_id in _jobs:
        del _jobs[job_id]
        return True
    return False


def list_all() -> dict:
    return {
        jid: {
            "status":   j["status"],
            "progress": j.get("progress"),
            "created":  j.get("created"),
            "tool":     j.get("tool"),
        }
        for jid, j in _jobs.items()
    }
