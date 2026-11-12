"""Game session lifecycle orchestrator service."""

import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.role_client import RoleServiceClient
from app.clients.voting_client import VotingServiceClient
from app.config import Settings
from app.config.rules import Phase, Role
from app.engine.night_resolution import get_public_night_announcement, resolve_night
from app.engine.state_machine import validate_transition
from app.engine.timers import calculate_phase_deadline
from app.engine.voting_rules import evaluate_votes
from app.engine.win_conditions import evaluate_winner
from app.models.base import utc_now
from app.models.game_sessions import GameSessionModel
from app.models.outbox import OutboxEventModel
from app.repositories.game_repository import GameRepository
from app.schemas.games import GameSessionResponse, PlayerPublicState
from app.state.checkpoints import CheckpointManager, ConcurrencyConflictError
from app.state.redis_state import RedisStateManager


def to_public_session_response(
    game: GameSessionModel,
    reveal_role_on_death: bool = True,
) -> GameSessionResponse:
    """Project internal game model to safe public schema.

    Strict invariant: Role data for living players is NEVER revealed.
    Only dead players have revealed_role if reveal_role_on_death is enabled.
    """
    public_players: list[PlayerPublicState] = []
    for p in game.players:
        is_alive = p.get("is_alive", True)
        revealed_role: str | None = None
        if not is_alive and reveal_role_on_death:
            revealed_role = game.role_assignments.get(p["player_id"])

        public_players.append(
            PlayerPublicState(
                player_id=p["player_id"],
                username=p.get("username", p["player_id"]),
                is_alive=is_alive,
                is_connected=p.get("is_connected", True),
                revealed_role=revealed_role,
            )
        )

    return GameSessionResponse(
        game_id=game.game_id,
        room_id=game.room_id,
        phase=Phase(game.phase),
        version=game.version,
        round_number=game.round_number,
        phase_deadline=game.phase_deadline,
        players=public_players,
        winner=game.winner,
        created_at=game.created_at,
        updated_at=game.updated_at,
    )


class GameService:
    """Coordinates game lifecycle, state transitions, and client calls."""

    def __init__(
        self,
        session: AsyncSession,
        redis_state: RedisStateManager,
        role_client: RoleServiceClient,
        voting_client: VotingServiceClient,
        settings: Settings,
    ) -> None:
        self.session = session
        self.redis_state = redis_state
        self.role_client = role_client
        self.voting_client = voting_client
        self.settings = settings
        self.repository = GameRepository(session)
        self.checkpoint_manager = CheckpointManager(session)

    async def get_game_or_404(self, game_id: str, for_update: bool = False) -> GameSessionModel:
        """Fetch game session from database or raise 404."""
        model = await self.repository.get_session(game_id, for_update=for_update)
        if not model:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Game session {game_id} not found",
            )
        return model

    async def get_public_game(self, game_id: str) -> GameSessionResponse:
        """Retrieve public representation of game session."""
        game = await self.get_game_or_404(game_id)
        return to_public_session_response(
            game, reveal_role_on_death=self.settings.rules.reveal_role_on_death
        )

    async def create_game(
        self,
        room_id: str,
        player_ids: list[str],
        player_names: dict[str, str] | None = None,
        custom_game_id: str | None = None,
    ) -> GameSessionResponse:
        """Initiate game session, request role assignments, and advance to NIGHT."""
        rules = self.settings.rules
        if len(player_ids) < rules.min_players or len(player_ids) > rules.max_players:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Player count ({len(player_ids)}) must be between "
                    f"{rules.min_players} and {rules.max_players}"
                ),
            )

        names = player_names or {}
        players_data = [
            {
                "player_id": pid,
                "username": names.get(pid, pid),
                "is_alive": True,
                "is_connected": True,
                "revealed_role": None,
            }
            for pid in player_ids
        ]

        game_id = custom_game_id or str(uuid.uuid4())

        # 1. Request role assignment from Role Service
        try:
            assigned_roles = await self.role_client.request_role_assignment(game_id, player_ids)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to obtain role assignments from Role Service: {exc}",
            ) from exc

        role_assignments_internal = {pid: role.value for pid, role in assigned_roles.items()}

        # 2. Compute first night deadline
        deadline = calculate_phase_deadline(rules.night_phase_duration_seconds)

        # 3. Create initial durable model starting directly in NIGHT after assignments
        game_model = GameSessionModel(
            game_id=game_id,
            room_id=room_id,
            phase=Phase.NIGHT.value,
            version=1,
            round_number=1,
            phase_deadline=deadline,
            players=players_data,
            role_assignments=role_assignments_internal,
            winner=None,
        )

        # 4. Outbox events: GAME_STARTED, PHASE_CHANGED
        now = utc_now()
        started_event = OutboxEventModel(
            event_id=str(uuid.uuid4()),
            event_type="GAME_STARTED",
            game_id=game_id,
            occurred_at=now,
            version=1,
            payload={
                "game_id": game_id,
                "room_id": room_id,
                "player_count": len(player_ids),
                "round_number": 1,
            },
        )
        phase_event = OutboxEventModel(
            event_id=str(uuid.uuid4()),
            event_type="PHASE_CHANGED",
            game_id=game_id,
            occurred_at=now,
            version=1,
            payload={
                "game_id": game_id,
                "previous_phase": Phase.ROLE_ASSIGNMENT.value,
                "new_phase": Phase.NIGHT.value,
                "round_number": 1,
                "deadline": deadline.isoformat(),
            },
        )

        await self.repository.create_session(game_model, outbox_events=[started_event, phase_event])
        await self.session.commit()

        # 5. Schedule deadline in Redis
        await self.redis_state.schedule_deadline(
            game_id=game_id,
            phase=Phase.NIGHT.value,
            round_number=1,
            version=1,
            deadline_epoch=deadline.timestamp(),
        )

        # 6. Cache live session in Redis
        await self.redis_state.set_live_session(
            game_id,
            to_public_session_response(
                game_model, reveal_role_on_death=rules.reveal_role_on_death
            ).model_dump(mode="json"),
        )

        return to_public_session_response(
            game_model, reveal_role_on_death=rules.reveal_role_on_death
        )

    async def advance_phase(
        self,
        game_id: str,
        force: bool = False,
        expected_version: int | None = None,
    ) -> GameSessionResponse:
        """Advance game to next phase according to state machine."""
        game = await self.get_game_or_404(game_id, for_update=True)
        current_phase = Phase(game.phase)
        rules = self.settings.rules

        if expected_version is not None and game.version != expected_version:
            raise ConcurrencyConflictError(
                game_id=game_id,
                expected_version=expected_version,
                actual_version=game.version,
            )

        current_version = game.version

        if current_phase == Phase.GAME_OVER:
            return to_public_session_response(game, reveal_role_on_death=rules.reveal_role_on_death)

        outbox_events: list[OutboxEventModel] = []
        phase_details: dict[str, Any] = {}
        now = utc_now()

        if current_phase == Phase.NIGHT:
            # 1. Resolve Night
            actions = await self.redis_state.get_night_actions(game_id)
            mafia_action = actions.get(Role.MAFIA.value)
            doctor_action = actions.get(Role.DOCTOR.value)

            mafia_target = mafia_action.get("target_id") if mafia_action else None
            doctor_target = doctor_action.get("target_id") if doctor_action else None

            # Find Doctor player ID
            doctor_id = next(
                (pid for pid, r in game.role_assignments.items() if r == Role.DOCTOR.value), None
            )

            resolution = resolve_night(
                mafia_target_id=mafia_target,
                doctor_target_id=doctor_target,
                doctor_id=doctor_id,
                doctor_can_self_protect=rules.doctor_can_self_protect,
            )

            # Clear night action buffer
            await self.redis_state.clear_night_actions(game_id)

            killed_id = resolution.killed_player_id
            if killed_id:
                for p in game.players:
                    if p["player_id"] == killed_id:
                        p["is_alive"] = False
                        if rules.reveal_role_on_death:
                            p["revealed_role"] = game.role_assignments.get(killed_id)

            phase_details = {
                "night_resolution": get_public_night_announcement(resolution),
                "someone_died": resolution.someone_died,
                "is_saved": resolution.is_saved,
            }

            outbox_events.append(
                OutboxEventModel(
                    event_id=str(uuid.uuid4()),
                    event_type="NIGHT_RESOLVED",
                    game_id=game_id,
                    occurred_at=now,
                    version=current_version + 1,
                    payload=get_public_night_announcement(resolution),
                )
            )

            # Check win condition
            living_roles = [
                Role(game.role_assignments[p["player_id"]])
                for p in game.players
                if p.get("is_alive", True)
            ]
            winner = evaluate_winner(living_roles)
            if winner:
                game.phase = Phase.GAME_OVER.value
                game.winner = winner.value
                game.phase_deadline = None
                outbox_events.append(
                    OutboxEventModel(
                        event_id=str(uuid.uuid4()),
                        event_type="GAME_OVER",
                        game_id=game_id,
                        occurred_at=now,
                        version=current_version + 1,
                        payload={"winner": winner.value, "round_number": game.round_number},
                    )
                )
            else:
                # Next phase: DAY
                game.phase = Phase.DAY.value
                game.phase_deadline = calculate_phase_deadline(rules.day_phase_duration_seconds)

        elif current_phase == Phase.DAY:
            # DAY -> DISCUSSION
            validate_transition(current_phase, Phase.DISCUSSION)
            game.phase = Phase.DISCUSSION.value
            game.phase_deadline = calculate_phase_deadline(rules.discussion_phase_duration_seconds)

        elif current_phase == Phase.DISCUSSION:
            # DISCUSSION -> VOTING
            validate_transition(current_phase, Phase.VOTING)
            game.phase = Phase.VOTING.value
            game.phase_deadline = calculate_phase_deadline(rules.voting_phase_duration_seconds)
            living_pids = [p["player_id"] for p in game.players if p.get("is_alive", True)]

            # Open voting in Voting Service
            try:
                await self.voting_client.open_voting(
                    game_id=game_id,
                    round_number=game.round_number,
                    eligible_voter_ids=living_pids,
                    candidate_ids=living_pids,
                    duration_seconds=rules.voting_phase_duration_seconds,
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Failed to open voting with Voting Service: {exc}",
                ) from exc

        elif current_phase == Phase.VOTING:
            # VOTING -> ELIMINATION (or Revote)
            try:
                voting_dto = await self.voting_client.close_voting(
                    game_id=game_id, round_number=game.round_number
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Failed to close voting with Voting Service: {exc}",
                ) from exc

            living_pids_set = {p["player_id"] for p in game.players if p.get("is_alive", True)}
            vote_result = evaluate_votes(
                votes=voting_dto.votes,
                living_player_ids=living_pids_set,
                tie_rule=rules.tie_rule,
                max_revotes=rules.max_revotes,
                abstention_handling=rules.abstention_handling,
            )

            if vote_result.requires_revote:
                # Re-enter VOTING for revote
                game.phase = Phase.VOTING.value
                game.phase_deadline = calculate_phase_deadline(rules.voting_phase_duration_seconds)
                try:
                    await self.voting_client.open_voting(
                        game_id=game_id,
                        round_number=game.round_number,
                        eligible_voter_ids=list(living_pids_set),
                        candidate_ids=vote_result.revote_candidates,
                        duration_seconds=rules.voting_phase_duration_seconds,
                    )
                except Exception as exc:
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=f"Failed to open revote with Voting Service: {exc}",
                    ) from exc
            else:
                # Transition to ELIMINATION
                game.phase = Phase.ELIMINATION.value
                game.phase_deadline = calculate_phase_deadline(
                    rules.elimination_phase_duration_seconds
                )
                eliminated_id = vote_result.eliminated_player_id
                if eliminated_id:
                    for p in game.players:
                        if p["player_id"] == eliminated_id:
                            p["is_alive"] = False
                            if rules.reveal_role_on_death:
                                p["revealed_role"] = game.role_assignments.get(eliminated_id)

                    outbox_events.append(
                        OutboxEventModel(
                            event_id=str(uuid.uuid4()),
                            event_type="PLAYER_ELIMINATION_CONFIRMED",
                            game_id=game_id,
                            occurred_at=now,
                            version=current_version + 1,
                            payload={
                                "player_id": eliminated_id,
                                "round_number": game.round_number,
                                "revealed_role": game.role_assignments.get(eliminated_id)
                                if rules.reveal_role_on_death
                                else None,
                            },
                        )
                    )

                # Check win condition
                living_roles = [
                    Role(game.role_assignments[p["player_id"]])
                    for p in game.players
                    if p.get("is_alive", True)
                ]
                winner = evaluate_winner(living_roles)
                if winner:
                    game.phase = Phase.GAME_OVER.value
                    game.winner = winner.value
                    game.phase_deadline = None
                    outbox_events.append(
                        OutboxEventModel(
                            event_id=str(uuid.uuid4()),
                            event_type="GAME_OVER",
                            game_id=game_id,
                            occurred_at=now,
                            version=current_version + 1,
                            payload={"winner": winner.value, "round_number": game.round_number},
                        )
                    )

        elif current_phase == Phase.ELIMINATION:
            # ELIMINATION -> next round NIGHT
            validate_transition(current_phase, Phase.NIGHT)
            game.phase = Phase.NIGHT.value
            game.round_number += 1
            game.phase_deadline = calculate_phase_deadline(rules.night_phase_duration_seconds)

        # Standard PHASE_CHANGED outbox event
        outbox_events.append(
            OutboxEventModel(
                event_id=str(uuid.uuid4()),
                event_type="PHASE_CHANGED",
                game_id=game_id,
                occurred_at=now,
                version=current_version + 1,
                payload={
                    "game_id": game_id,
                    "previous_phase": current_phase.value,
                    "new_phase": game.phase,
                    "round_number": game.round_number,
                    "deadline": game.phase_deadline.isoformat() if game.phase_deadline else None,
                },
            )
        )

        saved = await self.checkpoint_manager.save_checkpoint(
            session_model=game,
            expected_version=current_version,
            phase_details=phase_details,
            outbox_events=outbox_events,
        )
        # Reschedule deadline in Redis
        if saved.phase_deadline:
            await self.redis_state.schedule_deadline(
                game_id=game_id,
                phase=saved.phase,
                round_number=saved.round_number,
                version=saved.version,
                deadline_epoch=saved.phase_deadline.timestamp(),
            )
        else:
            await self.redis_state.cancel_deadlines_for_game(game_id)

        # Update Redis live state
        public_resp = to_public_session_response(
            saved, reveal_role_on_death=rules.reveal_role_on_death
        )
        await self.redis_state.set_live_session(game_id, public_resp.model_dump(mode="json"))

        await self.session.commit()

        return public_resp
