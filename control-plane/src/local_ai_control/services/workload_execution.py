"""Fresh workload-aware execution bridge.

A planner result is always recommendation-only.

Every heavy execution attempt obtains an initial fresh plan and
then repeats workload/admission/evidence routing validation at
the physical runtime reuse/start boundary.

Phase F1 executes only qualified heavy-local decisions.
Other routing actions remain deferred.
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
    execution_authorized = False


class WorkloadExecutionDeferred(WorkloadExecutionError):
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

    _HEAVY_TARGETS = {
        DecisionAction.ALLOW_QWEN38:
            "local-qwen38",
        DecisionAction.ALLOW_QWEN36:
            "local-qwen36",
    }

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
                cloud_egress_allowed=(
                    cloud_egress_allowed
                ),
                cloud_provider_ready=(
                    cloud_provider_ready
                ),
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

    @classmethod
    def _exact_heavy_target(
        cls,
        plan: WorkloadRoutingPlan,
    ) -> str:
        decision = plan.routing_decision

        expected = cls._HEAVY_TARGETS.get(
            decision.action
        )

        if expected is None:
            raise WorkloadExecutionDeferred(
                decision.action,
                decision.reason,
            )

        if decision.profile_id != expected:
            raise WorkloadExecutionError(
                "PLANNER_PROFILE_ACTION_MISMATCH"
            )

        return expected

    def _revalidate_exact_target(
        self,
        *,
        required_profile_id: str,
        task_type: str,
        deployment_mode: DeploymentMode | str,
        small_local_qualified_for_workload: bool,
        small_local_capability_ready: bool,
        cloud_egress_allowed: bool,
        cloud_provider_ready: bool,
    ) -> WorkloadRoutingPlan:
        plan = self._fresh_plan(
            task_type=task_type,
            deployment_mode=deployment_mode,
            small_local_qualified_for_workload=(
                small_local_qualified_for_workload
            ),
            small_local_capability_ready=(
                small_local_capability_ready
            ),
            cloud_egress_allowed=(
                cloud_egress_allowed
            ),
            cloud_provider_ready=(
                cloud_provider_ready
            ),
        )

        current_target = self._exact_heavy_target(
            plan
        )

        if current_target != required_profile_id:
            raise WorkloadExecutionDeferred(
                plan.routing_decision.action,
                "EXECUTION_REVALIDATION_CHANGED_TARGET:"
                f"{plan.routing_decision.reason}",
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
                cloud_egress_allowed=(
                    cloud_egress_allowed
                ),
                cloud_provider_ready=(
                    cloud_provider_ready
                ),
            )

            expected = self._exact_heavy_target(
                plan
            )

            def execution_validate():
                self._revalidate_exact_target(
                    required_profile_id=expected,
                    task_type=plan.task_type,
                    deployment_mode=deployment_mode,
                    small_local_qualified_for_workload=(
                        small_local_qualified_for_workload
                    ),
                    small_local_capability_ready=(
                        small_local_capability_ready
                    ),
                    cloud_egress_allowed=(
                        cloud_egress_allowed
                    ),
                    cloud_provider_ready=(
                        cloud_provider_ready
                    ),
                )

            with self.runtime.exact_profile_session(
                expected,
                plan.task_type,
                execution_validate=execution_validate,
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
        with self.session(
            task_type=task_type,
            deployment_mode=deployment_mode,
            small_local_qualified_for_workload=(
                small_local_qualified_for_workload
            ),
            small_local_capability_ready=(
                small_local_capability_ready
            ),
            cloud_egress_allowed=(
                cloud_egress_allowed
            ),
            cloud_provider_ready=(
                cloud_provider_ready
            ),
        ) as provider:
            return provider.generate(
                prompt,
                max_output_tokens=max_output_tokens,
            )
