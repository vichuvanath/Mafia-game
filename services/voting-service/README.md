# Voting & Elimination Service

A hardened, standalone microservice that collects, validates, tallies, and proposes elimination results for daytime voting phases in a real-time multiplayer Mafia game.

---

## 1. Role in the Game Architecture

In Mafia, during the day phase, living players debate and cast ballots for the player they suspect is part of the Mafia.

### Key Invariants & Boundaries
- **Proposals Only**: This service collects votes, validates eligibility, executes the configured tie rule, and publishes a **PROPOSED** result. The **Game Engine** is the sole authority that confirms player eliminations, advances game phases, or declares winners. This service **never** eliminates players directly.
- **Strict Independence**: Lives entirely in `services/voting-service/` and contains its own self-contained JWT verifier, internal service token authenticator, event envelope schemas, and models. It never imports from any other service.
- **Data Isolation**: Owns its database schema (`voting_db`) and Redis key prefix (`voting:`). It never accesses another service's storage.
- **Eligibility Provided at Open**: Does not call the Lobby, Auth, or Game Engine to verify players. All eligibility lists (`eligible_voters`, `eligible_targets`) are provided in the `open` command sent by the Game Engine.

---

## 2. API Contract

### Internal REST Endpoints
Requires the `X-Service-Token` header matching `INTERNAL_SERVICE_TOKEN`.

#### `POST /internal/games/{game_id}/voting/open`
Opens a new voting session for a day round.
- **Idempotency**: Calling with an existing `voting_round_id` returns the existing session without resetting cast ballots.
- **Conflict**: Calling with a new `voting_round_id` while another round is still `OPEN` for the same `game_id` returns `409 Conflict`.
- **Request Body**:
  ```json
  {
    "round_number": 1,
    "voting_round_id": "b3f6a27e-851c-4b71-b0db-6a4a0c8b3211",
    "eligible_voters": ["player-uuid-1", "player-uuid-2", "player-uuid-3"],
    "eligible_targets": ["player-uuid-1", "player-uuid-2", "player-uuid-3"],
    "deadline": "2026-11-12T15:30:00Z",
    "tie_rule": "NO_ELIMINATION",
    "allow_vote_change": true,
    "reveal_ballots_after_close": false
  }
  ```

#### `POST /internal/games/{game_id}/voting/close`
Closes an active voting session manually (e.g. forced early by the Game Engine).
- **Idempotency**: Closing an already-closed round returns the stored result immediately and does not republish duplicate events.
- **Request Body**:
  ```json
  {
    "voting_round_id": "b3f6a27e-851c-4b71-b0db-6a4a0c8b3211"
  }
  ```

---

### Public REST Endpoints
Requires `Authorization: Bearer <JWT>`. The JWT `sub` claim is extracted as the calling player's `voter_id`.

#### `POST /games/{game_id}/votes`
Submit or revise a vote.
- **Self-Vote Check**: Rejects `target_id == caller_id` with `400 SELF_VOTE`.
- **Eligibility**: Voter must be in `eligible_voters`; target must be in `eligible_targets`.
- **Revisions**: Allowed only if `allow_vote_change: true`. If `false`, subsequent votes return `400 VOTE_CHANGE_NOT_ALLOWED`.
- **Auto-Close**: When all eligible voters have cast their ballots, voting closes automatically.
- **Request Body**:
  ```json
  {
    "target_id": "player-uuid-2"
  }
  ```

#### `GET /games/{game_id}/voting/status`
Returns public round progress and the caller's private ballot.
- **Ballot Secrecy**: Individual ballots stay strictly private until `reveal_ballots_after_close` is reached. Public output shows progress only (`votes_cast` vs `votes_expected`).
- **Response Body**:
  ```json
  {
    "round_number": 1,
    "voting_round_id": "b3f6a27e-851c-4b71-b0db-6a4a0c8b3211",
    "status": "OPEN",
    "votes_cast": 3,
    "votes_expected": 6,
    "deadline": "2026-11-12T15:30:00Z",
    "my_vote": "player-uuid-2"
  }
  ```

---

### Standard Error Envelope
All error responses adhere to the standard JSON schema:
```json
{
  "code": "SELF_VOTE",
  "message": "Self-voting is not permitted.",
  "request_id": "67243c7b-327e-4078-a0bc-9e28fa03299c"
}
```
Standard Error Codes:
- `NOT_ELIGIBLE`
- `INVALID_TARGET`
- `SELF_VOTE`
- `VOTING_CLOSED`
- `VOTE_CHANGE_NOT_ALLOWED`
- `ROUND_NOT_FOUND`
- `ROUND_CONFLICT`
- `RATE_LIMIT_EXCEEDED`
- `UNAUTHORIZED` / `FORBIDDEN`

---

## 3. Events Published to RabbitMQ

Events are queued into an transactional **Outbox Table** and published by a background worker to exchange `game.events` using topic routing keys `voting.<event_type>`.

### Envelope Structure
```json
{
  "event_id": "c1f7a0b3-469b-4e8c-8f24-2c0b6f95b712",
  "event_type": "VOTE_SUBMITTED",
  "occurred_at": "2026-11-12T15:25:00Z",
  "game_id": "game-001",
  "version": 1,
  "payload": {}
}
```

### Event Types
1. **`VOTE_SUBMITTED`**
   - Emitted whenever a ballot is cast or revised.
   - **Sanitized**: Excludes voter identity and target identity to prevent real-time leaking of votes.
   - Payload:
     ```json
     {
       "voting_round_id": "b3f6a27e-851c-4b71-b0db-6a4a0c8b3211",
       "votes_cast": 3,
       "votes_expected": 6
     }
     ```

2. **`VOTING_CLOSED`**
   - Emitted when voting closes via `ALL_VOTED`, `DEADLINE`, or `ENGINE_REQUEST`.
   - Payload:
     ```json
     {
       "voting_round_id": "b3f6a27e-851c-4b71-b0db-6a4a0c8b3211",
       "round_number": 1,
       "reason": "ALL_VOTED"
     }
     ```

3. **`VOTE_RESULT_READY`**
   - Emitted exactly once per round when the final outcome is tallied.
   - Payload:
     ```json
     {
       "voting_round_id": "b3f6a27e-851c-4b71-b0db-6a4a0c8b3211",
       "round_number": 1,
       "outcome": "ELIMINATE",
       "target_id": "player-uuid-2",
       "tied_player_ids": [],
       "tally": {
         "player-uuid-2": 4,
         "player-uuid-3": 1
       },
       "abstentions": 1
     }
     ```

---

## 4. Engineering Details & Guarantees

- **Pure Logic Core (`app/core/`)**: Ballot verification (`ballot_rules.py`), tie resolution (`tie_rules.py`), and vote tallying (`tally.py`) have **no I/O** or database dependencies and are verified with 100% test coverage and Property-Based testing (Hypothesis).
- **Outbox Pattern**: Avoids dual-write distributed transaction bugs. Outbox messages are committed in the same database transaction as the vote/close mutation.
- **Race Condition Prevention**:
  - Unique DB index `(voting_round_id, voter_id)` ensures exactly one active ballot exists per voter.
  - Vote revisions are logged in `vote_revisions` for complete auditing.
  - Atomic conditional update `UPDATE voting_sessions SET status = 'CLOSED' WHERE id = :id AND status = 'OPEN'` guarantees only one winner among racing close triggers (last voter vs. deadline timer vs. engine close).
- **Redis Scheduler & Recovery**:
  - Deadlines are tracked via Redis Sorted Sets (`voting:deadlines`).
  - On service startup, a recovery routine closes overdue sessions that expired during downtime and rearms future deadlines.
- **Rate Limiting**: Sliding-window rate limiter per player UUID backed by Redis sorted sets.

---

## 5. Development & Testing

### Running with Docker (Isolated Test Environment)
```bash
# Start Postgres, Redis, RabbitMQ
docker compose -f docker-compose.test.yml up -d

# Run database migrations
alembic upgrade head

# Run tests
pytest -v
```

### Running Tests Locally
```bash
# Install dependencies
pip install -r requirements-dev.txt

# Run all tests
pytest -v

# Run unit tests only (pure core logic, no I/O)
pytest tests/unit -v

# Run with test coverage
pytest --cov=app --cov-report=term-missing --cov-fail-under=80

# Run linter and type checker
ruff check .
mypy app
mypy app/core --strict
```

---

## 6. Integrating with the Game Engine

1. **Opening a Phase**:
   - The Game Engine issues `POST /internal/games/{game_id}/voting/open` at the beginning of the Day voting phase with the list of currently alive players.
2. **Monitoring Progress**:
   - The Game Engine listens to the `VOTE_SUBMITTED` event to display vote progress bar on the game board.
3. **Handling Results**:
   - On receiving `VOTE_RESULT_READY`:
     - If `outcome == "ELIMINATE"`: The Game Engine updates the eliminated player's state to dead and transitions to the Night phase.
     - If `outcome == "TIE"` (with `REVOTE_AMONG_TIED`): The Game Engine initiates a revote round by calling `/open` with `eligible_targets` set to `tied_player_ids`.
     - If `outcome == "NO_ELIMINATION"`: The Game Engine announces no player was eliminated and transitions directly to Night.

