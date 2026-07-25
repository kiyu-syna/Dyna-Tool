from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from app.services.payment_service import _apply_webhook_payment


class FakeCollection:
    def __init__(self, documents=None):
        self.documents = documents or []
        self.find_one_and_update_filters = []
        self.update_count = 0

    async def find_one(self, query, *, session=None):
        return next(
            (deepcopy(doc) for doc in self.documents if _matches(doc, query)),
            None,
        )

    async def find_one_and_update(
        self, query, update, *, return_document=None, session=None
    ):
        self.find_one_and_update_filters.append(query)
        for doc in self.documents:
            if _matches(doc, query):
                doc.update(update["$set"])
                return deepcopy(doc)
        return None

    async def update_one(self, query, update, *, upsert=False, session=None):
        self.update_count += 1
        for doc in self.documents:
            if _matches(doc, query):
                if "$set" in update:
                    doc.update(update["$set"])
                return SimpleNamespace(upserted_id=None)
        if upsert:
            doc = deepcopy(query)
            doc.update(update.get("$setOnInsert", {}))
            doc.update(update.get("$set", {}))
            self.documents.append(doc)
            return SimpleNamespace(upserted_id=object())
        return SimpleNamespace(upserted_id=None)


def _matches(document, query):
    return all(document.get(key) == value for key, value in query.items())


class PaymentAtomicityTests(IsolatedAsyncioTestCase):
    async def test_different_references_can_extend_an_order_only_once(self):
        now = datetime.now(timezone.utc)
        order = {
            "order_id": "DT123456",
            "username": "alice",
            "status": "pending",
            "expires_at": now + timedelta(minutes=10),
            "amount": 249_000,
            "days": 30,
            "plan_name": "Premium 30 days",
        }
        db = SimpleNamespace(
            orders=FakeCollection([order]),
            subscriptions=FakeCollection(),
            webhook_logs=FakeCollection(),
            users=FakeCollection(),
        )
        payload = {"content": "DT123456", "transferAmount": 249_000}
        session = object()

        first = await _apply_webhook_payment(
            db, payload, "bank-ref-1", "DT123456", now, session
        )
        second = await _apply_webhook_payment(
            db, payload, "bank-ref-2", "DT123456", now, session
        )

        self.assertTrue(first["success"])
        self.assertEqual(second, {"success": False, "reason": "already_paid"})
        self.assertEqual(db.subscriptions.update_count, 1)
        self.assertEqual(
            db.orders.find_one_and_update_filters,
            [{"order_id": "DT123456", "status": "pending"}],
        )
        self.assertEqual(
            db.subscriptions.documents[0]["expires_at"],
            now + timedelta(days=30),
        )
