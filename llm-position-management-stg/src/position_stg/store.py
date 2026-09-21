"""CAS documents. Firestore transactions contain no network side effects."""
import json
import sqlite3
import time
import uuid
from copy import deepcopy
from pathlib import Path


class Conflict(RuntimeError):
    pass


class SQLiteStore:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS docs (id TEXT PRIMARY KEY, version INTEGER, body TEXT)")

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def read(self, key):
        with self.connect() as db:
            row = db.execute("SELECT version, body FROM docs WHERE id=?", (key,)).fetchone()
        return (json.loads(row[1]), row[0]) if row else (None, 0)

    def write(self, key, value, version):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT version FROM docs WHERE id=?", (key,)).fetchone()
            if (row[0] if row else 0) != version:
                raise Conflict(key)
            db.execute("INSERT OR REPLACE INTO docs VALUES (?,?,?)", (key, version + 1, json.dumps(value)))
        return version + 1

    def items(self, prefix):
        with self.connect() as db:
            rows = db.execute("SELECT id,version,body FROM docs ORDER BY id").fetchall()
        return [(key, json.loads(body), version) for key, version, body in rows if key.startswith(prefix)]

    def read_many(self, keys):
        return {key: self.read(key)[0] for key in keys}

    def delete(self, key, version):
        with self.connect() as db:
            db.execute("DELETE FROM docs WHERE id=? AND version=?", (key, version))


class FirestoreStore:
    def __init__(self, project, bot_id):
        from google.cloud import firestore
        self.fs = firestore
        self.client = firestore.Client(project=project)
        self.collection = self.client.collection("bots").document(bot_id).collection("documents")

    def read(self, key):
        snap = self.collection.document(key).get(timeout=10)
        data = snap.to_dict() if snap.exists else None
        return (json.loads(data["body"]), data["version"]) if data else (None, 0)

    def write(self, key, value, version):
        ref = self.collection.document(key)
        @self.fs.transactional
        def commit(tx):
            snap = ref.get(transaction=tx)
            current = snap.to_dict()["version"] if snap.exists else 0
            if current != version:
                raise Conflict(key)
            tx.set(ref, {"version": version + 1, "body": json.dumps(value)})
        commit(self.client.transaction(max_attempts=3))
        return version + 1

    def items(self, prefix):
        # Documents use bounded, separate histories, never an ever-growing runtime array.
        from google.cloud.firestore_v1.base_query import FieldFilter
        query = self.collection.where(filter=FieldFilter("__name__", ">=", self.collection.document(prefix))).where(
            filter=FieldFilter("__name__", "<", self.collection.document(prefix + "\uf8ff")))
        return [(s.id, json.loads(s.to_dict()["body"]), s.to_dict()["version"]) for s in query.stream(timeout=30)]

    def read_many(self, keys):
        result = {}
        for offset in range(0, len(keys), 100):
            refs = [self.collection.document(key) for key in keys[offset:offset+100]]
            for snap in self.client.get_all(refs, timeout=10):
                if snap.exists:
                    result[snap.id] = json.loads(snap.to_dict()["body"])
        return result

    def delete(self, key, version):
        ref = self.collection.document(key)
        @self.fs.transactional
        def remove(tx):
            snap = ref.get(transaction=tx)
            if snap.exists and snap.to_dict()["version"] == version:
                tx.delete(ref)
        remove(self.client.transaction(max_attempts=3))


class Lease:
    """A fenced runtime snapshot; every save checks owner and expiration via CAS."""
    def __init__(self, store, initial, *, clock=time.time, duration=300):
        self.store, self.clock = store, clock
        self.data, self.version = store.read("runtime")
        now = clock()
        if self.data and self.data.get("lease_until", 0) > now:
            raise Conflict("busy")
        self.data = self.data or deepcopy(initial)
        self.owner = uuid.uuid4().hex
        self.data.update(lease_owner=self.owner, lease_until=now + duration)
        self.version = store.write("runtime", self.data, self.version)

    def save(self):
        if self.clock() >= self.data["lease_until"]:
            raise Conflict("lease expired")
        self.version = self.store.write("runtime", self.data, self.version)

    def release(self):
        self.data["lease_until"] = 0
        self.version = self.store.write("runtime", self.data, self.version)
