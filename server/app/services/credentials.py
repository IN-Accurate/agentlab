"""Durable encrypted credentials, with synchronous per-operation evaluator access."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, insert, select

from app.models import SavedCredential
from app.security import create_credential, credential_metadata, credential_record
from app.store import CredentialStore


class CredentialScope(set):
    def __init__(self, identifiers, redactor):
        super().__init__(identifiers)
        self.store = CredentialStore()
        self.redactor = redactor
        self.records = []

    def put(self, identifier, value, remaining):
        self.redactor.restore(value)
        self.records.append(value)
        self.store.put(identifier, value, remaining)

    def release(self):
        for value in self.records:
            self.redactor.discard(value)
        self.records.clear()


def release(identifiers):
    if isinstance(identifiers, CredentialScope):
        identifiers.release()


class SavedCredentials:
    def __init__(self, state):
        # Resolve state.sessions at call time, including isolated test sessions.
        self.state = state

    def cache(self, value):
        identifier = value["credential_id"]
        store = self.state.credential_store
        remaining = (
            datetime.fromisoformat(value["expires_at"]) - datetime.now(UTC)
        ).total_seconds()
        store.delete(identifier)
        if remaining > 0:
            self.state.redactor.restore(value)
            store.put(identifier, value, remaining)
        return store.get(identifier)

    @staticmethod
    def value(row):
        return {
            "credential_id": str(row["id"]),
            **{
                key: row[key]
                for key in (
                    "provider",
                    "base_url",
                    "model",
                    "judge_model",
                    "ciphertext",
                    "key_hint",
                    "key_fingerprint",
                )
            },
            "expires_at": row["expires_at"].isoformat(),
        }

    async def purge_expired(self, db, cutoff):
        identifiers = await db.scalars(
            delete(SavedCredential.__table__)
            .where(SavedCredential.expires_at <= cutoff)
            .returning(SavedCredential.id)
        )
        return list(identifiers)

    async def restore(self):
        cutoff = datetime.now(UTC)
        async with self.state.sessions() as db, db.begin():
            expired = await self.purge_expired(db, cutoff)
            rows = await db.execute(
                select(SavedCredential.__table__).where(
                    SavedCredential.expires_at > cutoff
                )
            )
            values = [self.value(row) for row in rows.mappings()]
        for identifier in expired:
            self.state.credential_store.delete(str(identifier))
        for value in values:
            self.cache(value)

    async def create(self, data):
        # The public helper validates/encrypts the key and registers its fingerprint.
        # A temporary store keeps failed writes out of the process cache.
        temporary = CredentialStore(on_evict=self.state.redactor.discard)
        metadata = create_credential(
            data, self.state.settings, temporary, self.state.redactor
        )
        identifier = metadata["credential_id"]
        value = temporary.get(identifier)
        columns = {key: item for key, item in value.items() if key != "credential_id"}
        columns["id"] = UUID(identifier)
        columns["expires_at"] = datetime.fromisoformat(columns["expires_at"])
        try:
            async with self.state.sessions() as db, db.begin():
                expired = await self.purge_expired(db, datetime.now(UTC))
                # Core insert bypasses redaction of encrypted storage fields.
                await db.execute(insert(SavedCredential.__table__).values(**columns))
        finally:
            temporary.delete(identifier)
        for expired_id in expired:
            self.state.credential_store.delete(str(expired_id))
        self.cache(value)
        return credential_metadata(identifier, self.state.credential_store)

    async def get(self, identifier):
        identifier = str(identifier)
        async with self.state.sessions() as db:
            result = await db.execute(
                select(SavedCredential.__table__).where(
                    SavedCredential.id == UUID(identifier),
                    SavedCredential.expires_at > datetime.now(UTC),
                )
            )
            row = result.mappings().one_or_none()
        if row is None:
            self.state.credential_store.delete(identifier)
            return credential_record(identifier, self.state.credential_store)
        self.cache(self.value(row))
        return credential_record(identifier, self.state.credential_store)

    async def revoke(self, identifier):
        async with self.state.sessions() as db, db.begin():
            await db.execute(
                delete(SavedCredential.__table__).where(
                    SavedCredential.id == UUID(str(identifier))
                )
            )
        self.state.credential_store.delete(str(identifier))


async def scope(state, identifiers):
    if state.saved_credentials is None:
        for identifier in identifiers:
            credential_record(identifier, state.credential_store)
        return identifiers
    hydrated = CredentialScope(identifiers, state.redactor)
    try:
        for identifier in identifiers:
            value = await state.saved_credentials.get(identifier)
            remaining = (
                datetime.fromisoformat(value["expires_at"]) - datetime.now(UTC)
            ).total_seconds()
            hydrated.put(identifier, value, remaining)
        return hydrated
    except BaseException:
        hydrated.release()
        raise
    finally:
        release(identifiers)


async def create(state, data):
    if state.saved_credentials is not None:
        return await state.saved_credentials.create(data)
    return create_credential(
        data, state.settings, state.credential_store, state.redactor
    )


async def metadata(state, identifier):
    if state.saved_credentials is not None:
        await state.saved_credentials.get(identifier)
    return credential_metadata(str(identifier), state.credential_store)


async def revoke(state, identifier):
    if state.saved_credentials is not None:
        await state.saved_credentials.revoke(identifier)
    else:
        state.credential_store.delete(str(identifier))
