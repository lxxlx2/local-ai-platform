"""Fresh workload-aware execution bridge.

This module deliberately does not accept WorkloadRoutingPlan
objects from callers as runtime authorization. Every execution
attempt obtains a new plan while holding the runtime selection
lock, then delegates physical lifecycle safety to
RuntimeProviderFactory.

Phase F1 supports only the already-qualified heavy-local
recommendations. Cloud and small-local execution remain
separate future integrations.
"""

from __future__ import annotations

from contextlib import contextmanager

from local_ai_control.services.qualification_evidence_store import (
    DeploymentMode,
)
from local_ai_control.services.runtime_providers import (
    RuntimeProviderFactory,
)
from local_ai_control.services.workload_planner import (
    WorkloadRoutingPlan,
    WorkloadRoutingPlanner,
)
from local_ai_control.services.workload_router import (
    DecisionAction,
)


class WorkloadExecutionError(RuntimeError):
    """Fail-closed execution integration error."""

    execution_authorized = False


class WorkloadExecutionDeferred(WorkloadExecutionError):
    """The fresh planner did not select an executable Phase F1 route."""

    def __init__(
        self,
        action: DecisionAction,
        reason: str,
    ):
        self.action = DecisionAction(action)
        self.reason = str(reason)
        super().__init__(
            f"{self.action.value}:{self.reason}"
        )


class WorkloadAwareExecutionCoordinator:
    """Fresh-plan bridge to exact heavy-runtime lifecycle control."""

    def __init__(
        self,
        *,
        planner: WorkloadRoutingPlanner | None = None,
        runtime: RuntimeProviderFactory | None = None,
    ):
        if planner is None and runtime is None:
            runtime = RuntimeProviderFactory()
            planner = WorkloadRoutingPlanner(
                registry=runtime.registry
            )
        elif runtime is None:
            runtime = RuntimeProviderFactory(
                registry=planner.registry
            )
        elif planner is None:
            planner = WorkloadRoutingPlanner(
                registry=runtime.registry
            )
        elif planner.registry is not runtime.registry:
            raise WorkloadExecutionError(
                "DEPENDENCY_REGISTRY_MISMATCH"
            )

        self.planner = planner
        self.runtime = runtime

    def _fresh_plan(
        self,
        *,
        task_type: str,
        deployment_mode: DeploymentMode | str,
        small_local_qualified_for_workload: bool,
        small_local_capability_ready: bool,
        cloud_egress_allowed: bool,
        cloud_provider_ready: bool,
    ) -> WorkloadRoutingPlan:
        try:
            plan = self.planner.plan(
                task_type=task_type,
                deployment_mode=deployment_mode,
                small_local_qualified_for_workload=(
                    small_local_qualified_for_workload
                ),
                small_local_capability_ready=(
                    small_local_capability_ready
                ),
                cloud_egress_allowed=cloud_egress_allowed,
                cloud_provider_ready=cloud_provider_ready,
            )
        except Exception as error:
            raise WorkloadExecutionError(
                "FRESH_PLANNING_FAILED:"
                f"{type(error).__name__}"
            ) from error

        if not isinstance(
            plan,
            WorkloadRoutingPlan,
        ):
            raise WorkloadExecutionError(
                "INVALID_FRESH_PLAN_TYPE"
            )

        if (
            plan.execution_authorized
            or not plan.requires_fresh_execution_revalidation
        ):
            raise WorkloadExecutionError(
                "INVALID_PLAN_AUTHORIZATION_CONTRACT"
            )

        if (
            plan.deployment_mode
            is not DeploymentMode.ON_DEMAND_COLD_START
        ):
            raise WorkloadExecutionDeferred(
                plan.routing_decision.action,
                "DEPLOYMENT_MODE_NOT_EXECUTABLE_IN_PHASE_F1",
            )

        return plan

    @contextmanager
    def session(
        self,
        *,
        task_type: str,
        deployment_mode: DeploymentMode | str = (
            DeploymentMode.ON_DEMAND_COLD_START
        ),
        small_local_qualified_for_workload: bool = False,
        small_local_capability_ready: bool = False,
        cloud_egress_allowed: bool = False,
        cloud_provider_ready: bool = False,
    ):
        # RuntimeProviderFactory uses an RLock. Holding the same
        # lock while obtaining the execution-time plan prevents
        # another platform runtime transition from interleaving
        # between fresh planning and exact target selection.
        with self.runtime.lock:
            plan = self._fresh_plan(
                task_type=task_type,
                deployment_mode=deployment_mode,
                small_local_qualified_for_workload=(
                    small_local_qualified_for_workload
                ),
                small_local_capability_ready=(
                    small_local_capability_ready
                ),
                cloud_egress_allowed=cloud_egress_allowed,
                cloud_provider_ready=cloud_provider_ready,
            )

            decision = plan.routing_decision

            expected = {
                DecisionAction.ALLOW_QWEN38:
                    "local-qwen38",
                DecisionAction.ALLOW_QWEN36:
                    "local-qwen36",
            }.get(decision.action)

            if expected is None:
                raise WorkloadExecutionDeferred(
                    decision.action,
                    decision.reason,
                )

            if decision.profile_id != expected:
                raise WorkloadExecutionError(
                    "PLANNER_PROFILE_ACTION_MISMATCH"
                )

            with self.runtime.exact_profile_session(
                expected,
                plan.task_type,
            ) as provider:
                yield provider

    def generate(
        self,
        *,
        task_type: str,
        prompt: str,
        max_output_tokens: int = 1024,
        deployment_mode: DeploymentMode | str = (
            DeploymentMode.ON_DEMAND_COLD_START
        ),
        small_local_qualified_for_workload: bool = False,
        small_local_capability_ready: bool = False,
        cloud_egress_allowed: bool = False,
        cloud_provider_ready: bool = False,
    ):
        # No implicit infrastructure failover here. A failed
        # generation must be retried through a new coordinator
        # call so workload/evidence/admission are freshly planned.
        with self.session(
            task_type=task_type,
            deployment_mode=deployment_mode,
            small_local_qualified_for_workload=(
                small_local_qualified_for_workload
            ),
            small_local_capability_ready=(
                small_local_capability_ready
            ),
            cloud_egress_allowed=cloud_egress_allowed,
            cloud_provider_ready=cloud_provider_ready,
        ) as provider:
            return provider.generate(
                prompt,
                max_output_tokens=max_output_tokens,
            )
