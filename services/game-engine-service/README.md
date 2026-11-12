# Mafia Game Engine Service ("God" Engine)

The **Game Engine Service** is the central, automated arbiter ("God") for a real-time multiplayer Mafia game. It serves as the single source of truth for all game session states, phase transitions, night action resolutions, day vote adjudications, and victory conditions.

---

## 1. Architectural Overview

```
                          ┌──────────────────────────┐
                          │   Room / Matchmaking     │
                          └────────────┬─────────────┘
                                       │ (ROOM_READY event or POST /games)
                                       ▼
┌─────────────────┐       ┌──────────────────────────┐       ┌─────────────────┐
│   Role Service  │◄─────►│   Game Engine Service    │◄─────►│ Voting Service  │
│  (HTTP / AMQP)  │       │      ("The God")         │       │  (HTTP / AMQP)  │
└─────────────────┘       └────────────┬─────────────┘       └─────────────────┘
                                       │
            ┌──────────────────────────┼──────────────────────────┐
            ▼                          ▼                          ▼
  ┌──────────────────┐       ┌──────────────────┐       ┌──────────────────┐
  │ PostgreSQL 16+   │       │     Redis 7+     │       │   RabbitMQ 3+    │
  │ (game_engine_db) │       │   (game:* keys)  │       │  (mafia.events)  │
  └──────────────────┘       └──────────────────┘       └──────────────────┘
```

### Core Design Principles
- **Strict Service Independence**: Lives entirely within `services/game-engine-service/`. Does not import external shared code or schemas. Maintains its own database (`game_engine_db`), Redis key prefix (`game:`), and RabbitMQ exchanges/queues.
- **Pure Game Engine Core (`app/engine/`)**: All game logic (state machine transitions, night resolution matrix, vote tie handling, win conditions) is 100% deterministic and free of I/O.
- **Optimistic Concurrency**: Every game session tracks an incrementing `version`. Concurrent updates or stale phase advances are rejected with HTTP 409 (`ConcurrencyConflictError`).
- **Transactional Outbox Pattern**: State mutations and outgoing event records are committed within the same database transaction (`outbox_events` table). An asynchronous worker publishes events reliably to RabbitMQ.
- **Idempotent Event Ingestion**: Processed message identifiers are recorded in `processed_events`. Re-delivered events are acknowledged without repeating side effects.
- **Durable Timers & Crash Recovery**: Deadlines are persisted in PostgreSQL (`phase_deadline`) and indexed in Redis sorted sets. On startup, `recovery.py` re-arms or triggers overdue deadlines.
- **Role Confidentiality**: Role allocations are stored strictly in internal tables. Public APIs and broadcast events never disclose secret roles of living participants.

---

## 2. Phase State Machine

```
              ┌─────────────────────────────────┐
              │         ROLE_ASSIGNMENT         │
              └────────────────┬────────────────┘
                               │ (Roles assigned)
                               ▼
            ┌─────────►┌────────────────┐
            │          │     NIGHT      │◄──────────┐
            │          └───────┬────────┘           │
            │                  │ (Mafia / Doctor)   │
            │                  ▼                    │
            │          ┌────────────────┐           │
            │          │NIGHT_RESOLUTION│           │
            │          └───────┬────────┘           │
            │                  │ (Elimination/Save) │
            │                  ▼                    │
            │          ┌────────────────┐           │
 (Win check │          │      DAY       │           │
  = False)  │          └───────┬────────┘           │ (Win check
            │                  │                    │  = False)
            │                  ▼                    │
            │          ┌────────────────┐           │
            │          │   DISCUSSION   │           │
            │          └───────┬────────┘           │
            │                  │                    │
            │                  ▼                    │
            │          ┌────────────────┐           │
            │          │     VOTING     │           │
            │          └───────┬────────┘           │
            │                  │ (Tally votes)      │
            │                  ▼                    │
            │          ┌────────────────┐           │
            └──────────┤  ELIMINATION   ├───────────┘
                       └───────┬────────┘
                               │ (Mafia eliminated OR
                               │  Mafia >= Non-Mafia)
                               ▼
                       ┌────────────────┐
                       │   GAME_OVER    │
                       └────────────────┘
```

---

## 3. Configuration & Game Rules

All settings can be customized via environment variables or a `.env` file. Rules live under the `GAME_RULES__` prefix.

### Game Rules Settings (`config/rules.py`)

| Setting Key | Default | Type | Description |
| :--- | :--- | :--- | :--- |
| `GAME_RULES__MIN_PLAYERS` | `5` | `int` | Minimum participants required to start a game (min 3). |
| `GAME_RULES__MAX_PLAYERS` | `12` | `int` | Maximum participants allowed in a single room. |
| `GAME_RULES__ROLE_ASSIGNMENT_TIMEOUT_SECONDS` | `30` | `int` | Maximum wait time for Role Service response. |
| `GAME_RULES__NIGHT_PHASE_DURATION_SECONDS` | `30` | `int` | Window for Mafia kill & Doctor protect actions. |
| `GAME_RULES__NIGHT_RESOLUTION_DURATION_SECONDS` | `5` | `int` | Display window for night outcomes. |
| `GAME_RULES__DAY_PHASE_DURATION_SECONDS` | `5` | `int` | Day start announcement duration. |
| `GAME_RULES__DISCUSSION_PHASE_DURATION_SECONDS`| `45` | `int` | Discussion period before voting begins. |
| `GAME_RULES__VOTING_PHASE_DURATION_SECONDS` | `30` | `int` | Ballot voting window. |
| `GAME_RULES__ELIMINATION_PHASE_DURATION_SECONDS`| `5` | `int` | Elimination announcement display duration. |
| `GAME_RULES__DOCTOR_CAN_SELF_PROTECT` | `true` | `bool` | Whether Doctor can protect themselves. |
| `GAME_RULES__TIE_RULE` | `REVOTE_AMONG_TIED` | `str` | `REVOTE_AMONG_TIED` or `NO_ELIMINATION`. |
| `GAME_RULES__MAX_REVOTES` | `1` | `int` | Maximum revotes before defaulting to no elimination. |
| `GAME_RULES__ABSTENTION_HANDLING` | `COUNT_AS_ABSTAIN`| `str` | `COUNT_AS_ABSTAIN` or `IGNORE`. |
| `GAME_RULES__REVEAL_ROLE_ON_DEATH` | `true` | `bool` | Whether an eliminated player's role is published. |
| `GAME_RULES__DISCONNECT_GRACE_PERIOD_SECONDS` | `60` | `int` | Reconnection window before forfeit/elimination. |

### Service & Infrastructure Settings (`app/config/__init__.py`)

| Setting Key | Default | Description |
| :--- | :--- | :--- |
| `DATABASE_URL` | `postgresql+asyncpg://...` | Async PostgreSQL connection string. |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL for state caching & timers. |
| `REDIS_KEY_PREFIX` | `game:` | Namespace prefix for Redis keys. |
| `RABBITMQ_URL` | `amqp://guest:guest@localhost:5672/` | RabbitMQ AMQP connection string. |
| `RABBITMQ_EXCHANGE` | `mafia.events` | Topic exchange name. |
| `INTERNAL_SERVICE_TOKEN` | `dev-secret-internal-service-token` | Token required on `/internal/*` routes. |
| `JWT_SECRET_KEY` | *(32+ chars)* | Secret used to verify player JWTs. |
| `JWT_ALGORITHM` | `HS256` | JWT signature algorithm. |
| `CLIENT_MODE` | `fake` | `fake` for local/tests, or `http` for live services. |
| `ROLE_SERVICE_URL` | `http://localhost:8001` | Role Service base URL when `CLIENT_MODE=http`. |
| `VOTING_SERVICE_URL` | `http://localhost:8002` | Voting Service base URL when `CLIENT_MODE=http`. |

---

## 4. API Endpoints

### Public Endpoints (Requires `Authorization: Bearer <player_jwt>`)

The authenticated player's identity is extracted from standard JWT claims (`sub`, `player_id`, or `user_id`).

- `POST /games`
  - **Description**: Creates and initializes a game session from a validated room.
  - **Body**:
    ```json
    {
      "room_id": "room-101",
      "player_ids": ["p1", "p2", "p3", "p4", "p5"],
      "player_names": {"p1": "Alice", "p2": "Bob", "p3": "Charlie", "p4": "Dave", "p5": "Eve"}
    }
    ```
  - **Response (201 Created)**: Caller-safe game session response.

- `GET /games/{game_id}`
  - **Description**: Retrieves public session state. Only accessible to participants of the room.
  - **Security Guarantee**: Secret roles of living players are excluded (`role: null`).

- `POST /games/{game_id}/actions`
  - **Description**: Submits a night action (Mafia attack or Doctor heal).
  - **Body**:
    ```json
    {
      "target_id": "p3"
    }
    ```
  - **Validation**:
    - Current phase must be `NIGHT`.
    - Caller must be an alive player.
    - Caller must have an active action role (`MAFIA` or `DOCTOR`).
    - Target must be a valid, living participant.
    - Doctor self-protection rules are enforced.
    - Exactly one action per role per night is permitted.

### Internal Endpoints (Requires `X-Internal-Service-Token: <token>`)

- `POST /internal/games/{game_id}/advance`
  - **Description**: Progresses the state machine to the next phase (triggered by timer daemon or scheduler).
  - **Body** (optional):
    ```json
    {
      "force": false,
      "expected_version": 2
    }
    ```

- `GET /internal/games/{game_id}`
  - **Description**: Diagnostic endpoint returning complete state including `role_assignments`.

### Diagnostic Endpoints (Unauthenticated)

- `GET /health` - Returns `{"status": "ok"}`.
- `GET /ready` - Returns dependency readiness status.

---

## 5. Event Catalog (AMQP)

All events use a uniform envelope structure:

```json
{
  "event_id": "evt-77b3112c-9a1b-4ec2-9e2e-13c5dd78e329",
  "event_type": "PHASE_CHANGED",
  "occurred_at": "2026-11-12T09:30:00Z",
  "game_id": "game-450",
  "version": 3,
  "payload": { ... }
}
```

Contract schemas for all events are maintained in `tests/contract/schemas/`.

### Produced Events (Published to RabbitMQ)

1. **`GAME_STARTED`**
   - Published after role assignment completes and the game session begins.
   - Routing Key: `game.started`
2. **`PHASE_CHANGED`**
   - Published on every phase transition (`NIGHT`, `DISCUSSION`, `VOTING`, etc.) with `phase_deadline`.
   - Routing Key: `game.phase_changed`
3. **`NIGHT_RESOLVED`**
   - Published after night actions resolve. Only states whether someone died; never reveals who protected whom.
   - Routing Key: `game.night_resolved`
4. **`PLAYER_ELIMINATION_CONFIRMED`**
   - Published when a player is eliminated (by vote, night kill, or disconnect). Reveals role if `reveal_role_on_death=True`.
   - Routing Key: `game.elimination_confirmed`
5. **`GAME_OVER`**
   - Published upon reaching a terminal win state (`MAFIA` or `VILLAGERS`).
   - Routing Key: `game.over`

### Consumed Events (Ingested by Engine)

1. **`ROOM_READY`** (`room.ready`): Triggers game creation and requests role assignment.
2. **`ROLE_ASSIGNMENT_COMPLETED`** (`role.assigned`): Stores role mappings internally and transitions game to `NIGHT`.
3. **`VOTE_RESULT_READY`** (`voting.result`): Ingests day voting results, executes elimination or revote.
4. **`PLAYER_DISCONNECTED`** (`player.disconnected`): Initiates disconnect grace timer or handles forfeit.

---

## 6. How to Run Tests

### Prerequisites
Create and activate the virtual environment:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

### Running Test Suites via Makefile

- **Run all unit tests** (pure in-memory, no I/O, fast):
  ```bash
  make test-unit
  ```
- **Run integration tests** (verifies PostgreSQL, Redis, RabbitMQ):
  ```bash
  make test-integration
  ```
- **Run the full test suite** (Unit, API, Contract, Concurrency, Recovery, Security, E2E, Integration):
  ```bash
  make test
  ```
- **Run coverage analysis** (enforces $\ge 90\%$ on `app/engine/` and $\ge 80\%$ overall):
  ```bash
  make coverage
  ```
- **Run linters and type checkers** (Ruff + Mypy strict):
  ```bash
  make lint
  ```

---

## 7. Running the Service

### A. Local Development Mode (with In-Memory Fakes)

Run directly with fake Role and Voting clients:
```bash
# 1. Start local dependencies (Postgres, Redis, RabbitMQ)
docker compose -f docker-compose.test.yml up -d

# 2. Run database migrations
alembic upgrade head

# 3. Start development server
make run
```

### B. Running via Docker Container

Build the multi-stage, non-root Docker image:
```bash
docker build -t game-engine-service:latest .
```

Run the container:
```bash
docker run -d \
  --name game-engine \
  -p 8000:8000 \
  -e DATABASE_URL="postgresql+asyncpg://game_engine_user:game_engine_pass@host.docker.internal:5434/game_engine_db" \
  -e REDIS_URL="redis://host.docker.internal:6379/0" \
  -e RABBITMQ_URL="amqp://guest:guest@host.docker.internal:5672/" \
  game-engine-service:latest
```

### C. Connecting to Real External Role & Voting Services

When deploying alongside the actual `role-service` and `voting-service`:

1. Update your environment variables:
   ```env
   CLIENT_MODE=http
   ROLE_SERVICE_URL=http://role-service:8001
   VOTING_SERVICE_URL=http://voting-service:8002
   ```

2. Role Service Contract:
   - `POST /internal/roles/assign`
   - Request: `{"game_id": "...", "player_ids": ["p1", "p2", ...]}`
   - Response: `{"assignments": [{"player_id": "p1", "role": "MAFIA"}, ...]}`

3. Voting Service Contract:
   - `POST /internal/voting/open`
     - Request: `{"game_id": "...", "round_number": 1, "eligible_voter_ids": [...], "candidate_ids": [...], "duration_seconds": 30}`
   - `POST /internal/voting/close`
     - Request: `{"game_id": "...", "round_number": 1}`
     - Response: `{"game_id": "...", "round_number": 1, "eliminated_player_id": "p3", "is_tie": false, "votes": {...}}`
