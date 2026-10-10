"""Instance identity: a UUID generated on first start (04-DATA-MODEL section 2.7)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.orm import Session as DbSession

from omnisync.db.models import Instance


def ensure_instance(db: DbSession, name: str, now: datetime) -> Instance:
    instance = db.get(Instance, 1)
    if instance is None:
        instance = Instance(id=1, instance_id=str(uuid.uuid4()), instance_name=name, created_at=now)
        db.add(instance)
    elif instance.instance_name != name:
        instance.instance_name = name
    return instance
