"""In-memory async Redis fake for testing without external services."""


class FakeAsyncRedis:
    """Simulates async Redis client for RedisStateManager."""

    def __init__(self) -> None:
        self._kv: dict[str, str] = {}
        self._hashes: dict[str, dict[str, str]] = {}
        self._zsets: dict[str, dict[str, float]] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self._kv[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self._kv.get(key)

    async def delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self._kv:
                del self._kv[k]
                count += 1
            if k in self._hashes:
                del self._hashes[k]
                count += 1
            if k in self._zsets:
                del self._zsets[k]
                count += 1
        return count

    async def hset(self, name: str, key: str, value: str) -> int:
        if name not in self._hashes:
            self._hashes[name] = {}
        self._hashes[name][key] = value
        return 1

    async def hgetall(self, name: str) -> dict[str, str]:
        return dict(self._hashes.get(name, {}))

    async def zadd(self, name: str, mapping: dict[str, float]) -> int:
        if name not in self._zsets:
            self._zsets[name] = {}
        count = 0
        for member, score in mapping.items():
            if member not in self._zsets[name]:
                count += 1
            self._zsets[name][member] = float(score)
        return count

    async def zrangebyscore(self, name: str, min: float | str, max: float | str) -> list[str]:
        zset = self._zsets.get(name, {})
        min_val = float("-inf") if min in ("-inf", "-infinity") else float(min)
        max_val = float("inf") if max in ("+inf", "+infinity") else float(max)

        matching = [
            (member, score) for member, score in zset.items() if min_val <= score <= max_val
        ]
        matching.sort(key=lambda x: x[1])
        return [m[0] for m in matching]

    async def zrem(self, name: str, *values: str) -> int:
        zset = self._zsets.get(name, {})
        count = 0
        for val in values:
            if val in zset:
                del zset[val]
                count += 1
        return count

    async def flushdb(self) -> bool:
        self._kv.clear()
        self._hashes.clear()
        self._zsets.clear()
        return True
