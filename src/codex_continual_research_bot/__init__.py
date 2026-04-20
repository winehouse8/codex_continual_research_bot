"""Canonical contract models for Continual Research Bot."""

from .contracts import (
    FailureCode,
    FrontierSelectionInput,
    ProposalBundle,
    QueueJob,
    RunExecutionRequest,
    RunIntent,
    RunLifecycleState,
    RuntimeEvent,
    SessionInspectResult,
    TopicSnapshot,
)
from .graph_canonicalization import (
    CanonicalGraphService,
    CanonicalizationContext,
    HypothesisSnapshot,
    canonical_mapping_spec,
    neo4j_constraints,
)
from .orchestrator import (
    CompetitionValidationError,
    InvalidRunTransitionError,
    MissingTopicSnapshotError,
    RunOrchestrator,
    RunStateMachine,
    StaleTopicSnapshotError,
    STATE_TRANSITIONS,
)

__all__ = [
    "CanonicalGraphService",
    "CanonicalizationContext",
    "CompetitionValidationError",
    "FailureCode",
    "FrontierSelectionInput",
    "HypothesisSnapshot",
    "InvalidRunTransitionError",
    "MissingTopicSnapshotError",
    "ProposalBundle",
    "QueueJob",
    "RunExecutionRequest",
    "RunIntent",
    "RunLifecycleState",
    "RunOrchestrator",
    "RunStateMachine",
    "RuntimeEvent",
    "STATE_TRANSITIONS",
    "SessionInspectResult",
    "StaleTopicSnapshotError",
    "TopicSnapshot",
    "canonical_mapping_spec",
    "neo4j_constraints",
]
