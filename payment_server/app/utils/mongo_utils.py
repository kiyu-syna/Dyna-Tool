from datetime import datetime
from typing import Any, Optional


def serialize_doc(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    out: dict[str, Any] = {}
    for key, value in doc.items():
        if key == "_id":
            out["id"] = str(value)
            continue
        if isinstance(value, datetime):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


def serialize_docs(docs: list[dict]) -> list[dict]:
    return [serialize_doc(d) for d in docs if d]
