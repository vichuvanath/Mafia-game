"""Night action handling and validation service."""

from fastapi import HTTPException, status

from app.config import Settings
from app.config.rules import Phase, Role
from app.models.game_sessions import GameSessionModel
from app.state.redis_state import RedisStateManager


class ActionService:
    """Validates and buffers night actions."""

    def __init__(self, redis_state: RedisStateManager, settings: Settings) -> None:
        self.redis_state = redis_state
        self.settings = settings

    async def validate_and_record_action(
        self,
        game: GameSessionModel,
        actor_id: str,
        target_id: str,
    ) -> Role:
        """Validate an action submission and buffer it in Redis."""
        # 1. Phase validation: must be NIGHT
        if game.phase != Phase.NIGHT.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Actions can only be performed in NIGHT phase, current: {game.phase}",
            )

        # 2. Player membership & alive check
        player_map = {p["player_id"]: p for p in game.players}
        if actor_id not in player_map:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Player is not a participant in this game",
            )

        actor_player = player_map[actor_id]
        if not actor_player.get("is_alive", True):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Dead players cannot perform actions",
            )

        # 3. Role check: only MAFIA and DOCTOR can act at night
        roles = game.role_assignments
        role_str = roles.get(actor_id)
        if not role_str or role_str not in (Role.MAFIA.value, Role.DOCTOR.value):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Player role cannot submit night actions",
            )
        role = Role(role_str)

        # 4. Target validity check: target must be alive
        if target_id not in player_map:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Target player is not in this game",
            )

        target_player = player_map[target_id]
        if not target_player.get("is_alive", True):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Target must be an alive player in the game",
            )

        # 5. Doctor self-protection toggle
        if (
            role == Role.DOCTOR
            and actor_id == target_id
            and not self.settings.rules.doctor_can_self_protect
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Doctor self-protection is disabled in this game",
            )

        # 6. Check duplicate action: one action per role per night
        existing_actions = await self.redis_state.get_night_actions(game.game_id)
        if role.value in existing_actions:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Action has already been submitted for role {role.value} this night",
            )

        # 7. Buffer in Redis
        await self.redis_state.record_night_action(
            game_id=game.game_id,
            role=role.value,
            actor_id=actor_id,
            target_id=target_id,
        )

        return role
