import pytest

from mato_loop_backend.integrations.service import _clear_integration_policies
from mato_loop_backend.settings import GENERAL_PROFILE_ID, PLAN_PROFILE_ID, IntegrationInstance
from mato_loop_backend.settings.models import IntegrationPermissions


@pytest.mark.parametrize("available", [False, True])
def test_binding_cleanup_resets_permissions_but_preserves_profile_availability(available):
    rules = {
        "privatePermissions": {"policies": {"calendar.read": "auto"}},
        "publicPermissions": {"resourcePolicies": {"calendar:old": {"calendar.read": "auto"}}},
        "resourceAvailability": {"calendar:old": True},
    }
    instance = IntegrationInstance(
        name="Calendar", integration_type="calendar", adapter_type="caldav",
        permissions=rules,
        profile_permissions={
            GENERAL_PROFILE_ID: {**rules, "available": available},
            PLAN_PROFILE_ID: rules,
        },
    )

    _clear_integration_policies(instance)

    assert (instance.permissions, instance.profile_permissions) == (
        IntegrationPermissions(),
        {GENERAL_PROFILE_ID: IntegrationPermissions(available=available)},
    )
