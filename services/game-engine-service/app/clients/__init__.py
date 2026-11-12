"""Client interfaces and implementations."""

from app.clients.fakes import FakeRoleServiceClient, FakeVotingServiceClient
from app.clients.role_client import HttpRoleServiceClient, RoleServiceClient
from app.clients.voting_client import (
    HttpVotingServiceClient,
    VotingResultDto,
    VotingServiceClient,
)

__all__ = [
    "FakeRoleServiceClient",
    "FakeVotingServiceClient",
    "HttpRoleServiceClient",
    "HttpVotingServiceClient",
    "RoleServiceClient",
    "VotingResultDto",
    "VotingServiceClient",
]
